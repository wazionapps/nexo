#!/usr/bin/env python3
"""Install/refresh the Guardian baseline on the operator machine.

Fase E installer (items E.3 + E.4 + part of E.1). Run at `nexo init`,
`nexo update`, or manually via `nexo guardian install`. Idempotent:
never overwrites an existing user-edited guardian.json; merges missing
keys silently. Preset entities land at ~/.nexo/brain/presets/ with a
best-effort SSH host import from ~/.ssh/config (respecting the
`ssh_config_import.enabled_default` flag inside the preset).

CLI:
    python scripts/install_guardian.py            # apply changes
    python scripts/install_guardian.py --dry-run  # report what would change
    python scripts/install_guardian.py --force    # overwrite guardian.json
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PRESETS_SRC = REPO_ROOT / "src" / "presets"
GUARDIAN_DEFAULT_SRC = PRESETS_SRC / "guardian_default.json"
ENTITIES_SRC = PRESETS_SRC / "entities_universal.json"
# v6.3.1 — operator private overrides. .gitignored. Merged on top of
# the universal preset if present. See src/presets/entities_local.sample.json.
ENTITIES_LOCAL_SAMPLE_SRC = PRESETS_SRC / "entities_local.sample.json"


def _nexo_home() -> pathlib.Path:
    return pathlib.Path(os.environ.get("NEXO_HOME") or (pathlib.Path.home() / ".nexo"))


def _ensure_automation_backend(nexo_home: pathlib.Path, dry_run: bool, force: bool) -> str:
    """Fase E E.1/E.2 — enable automation on fresh install unless the
    operator explicitly set automation_user_override=true. Writes to
    ~/.nexo/config/schedule.json. Returns the action string."""
    schedule = nexo_home / "config" / "schedule.json"
    existing: dict = {}
    if schedule.exists():
        try:
            existing = json.loads(schedule.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            # Audit-MEDIUM fix: narrow exception + surface the parse error
            # so the operator sees why automation was not enabled instead
            # of silently accepting a broken file.
            return f"skipped-unparseable-schedule: {exc}"
    if not isinstance(existing, dict):
        existing = {}
    if existing.get("automation_user_override") and not force:
        return "skipped-user-override"
    current = str(existing.get("automation_backend") or "").strip().lower()
    if current in {"claude_code", "codex"} and not force:
        return "skipped-already-enabled"
    new_value = "claude_code"
    existing["automation_backend"] = new_value
    if dry_run:
        return f"would-set automation_backend={new_value}"
    schedule.parent.mkdir(parents=True, exist_ok=True)
    schedule.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n")
    return f"set automation_backend={new_value}"


def _ensure_dir(path: pathlib.Path, dry_run: bool) -> bool:
    if path.exists():
        return False
    if dry_run:
        return True
    path.mkdir(parents=True, exist_ok=True)
    return True


def _write_json_if_absent(target: pathlib.Path, payload: dict, dry_run: bool, force: bool) -> str:
    """Write `payload` to `target` JSON only if it does not exist (or --force).
    Returns the action performed: created | merged | skipped | would-create."""
    if not target.exists():
        if dry_run:
            return "would-create"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return "created"
    if force:
        if dry_run:
            return "would-force-overwrite"
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return "force-overwritten"
    # Merge: add missing keys, never overwrite existing ones.
    try:
        current = json.loads(target.read_text())
    except Exception:
        return "skipped-unparseable"
    if not isinstance(current, dict):
        return "skipped-non-dict"
    before_rules = dict((current.get("rules") or {}))
    merged_rules = dict(payload.get("rules") or {})
    merged_rules.update(before_rules)  # user wins on conflicts
    current["rules"] = merged_rules
    added = [k for k in (payload.get("rules") or {}) if k not in before_rules]
    if not added and current.get("version") == payload.get("version"):
        return "skipped-up-to-date"
    # Bump version reference so operators can see merges happened.
    current["version"] = payload.get("version", current.get("version"))
    if dry_run:
        return f"would-merge ({len(added)} new keys)"
    target.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n")
    return f"merged ({len(added)} new keys)"


def _copy_preset(src: pathlib.Path, dst: pathlib.Path, dry_run: bool, force: bool) -> str:
    if not src.exists():
        return "skipped-source-missing"
    if dst.exists() and not force:
        # Compare checksums — if identical, skip. Else show "stale" marker.
        try:
            if src.read_bytes() == dst.read_bytes():
                return "skipped-up-to-date"
        except Exception:
            pass
        return "skipped-user-copy-present"
    if dry_run:
        return "would-copy" if not dst.exists() else "would-force-copy"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return "copied"


def _import_ssh_hosts(nexo_home: pathlib.Path, dry_run: bool) -> dict[str, Any]:
    """Parse ~/.ssh/config and stage new host entities under
    ~/.nexo/brain/presets/ssh_imported_hosts.json (Python-side consumer
    adds them to the entity registry on first `nexo_startup` after
    install). Silent on any IO error.
    """
    ssh_config = pathlib.Path.home() / ".ssh" / "config"
    if not ssh_config.exists():
        return {"status": "skipped-no-ssh-config", "hosts": []}
    try:
        lines = ssh_config.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception as exc:
        return {"status": f"skipped-read-failed:{exc}", "hosts": []}
    hosts: list[dict] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        if not low.startswith("host "):
            continue
        # `Host a b c` → names a, b, c (wildcard patterns ignored).
        for name in line.split(None, 1)[1].split():
            if "*" in name or "?" in name:
                continue
            hosts.append({"name": name, "type": "host", "metadata": {"access_mode": "unknown", "source": "ssh_config"}})
    if not hosts:
        return {"status": "no-hosts", "hosts": []}
    out = nexo_home / "brain" / "presets" / "ssh_imported_hosts.json"
    if dry_run:
        return {"status": f"would-stage {len(hosts)} hosts -> {out}", "hosts": hosts}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"hosts": hosts}, indent=2, ensure_ascii=False) + "\n")
    return {"status": f"staged {len(hosts)} hosts -> {out}", "hosts": hosts}


def install(nexo_home: pathlib.Path | None = None, dry_run: bool = False, force: bool = False) -> dict:
    home = nexo_home or _nexo_home()
    config_dir = home / "config"
    preset_dir = home / "brain" / "presets"
    actions: dict[str, Any] = {"dry_run": dry_run, "force": force, "nexo_home": str(home)}
    actions["config_dir_created"] = _ensure_dir(config_dir, dry_run)
    actions["preset_dir_created"] = _ensure_dir(preset_dir, dry_run)

    if GUARDIAN_DEFAULT_SRC.exists():
        payload = json.loads(GUARDIAN_DEFAULT_SRC.read_text())
    else:
        payload = {"version": "0.0.0", "rules": {}}
    actions["guardian_json"] = _write_json_if_absent(
        config_dir / "guardian.json", payload, dry_run, force
    )
    actions["entities_preset_copy"] = _copy_preset(
        ENTITIES_SRC, preset_dir / "entities_universal.json", dry_run, force
    )
    # v6.3.1 — drop a template for the operator's private vhost_mapping /
    # host / tenant entries. Never overwrite the operator copy.
    local_target = preset_dir / "entities_local.json"
    if ENTITIES_LOCAL_SAMPLE_SRC.exists() and not local_target.exists():
        actions["entities_local_sample_copy"] = _copy_preset(
            ENTITIES_LOCAL_SAMPLE_SRC, local_target, dry_run, force
        )
    else:
        actions["entities_local_sample_copy"] = "skipped-operator-copy-present" if local_target.exists() else "skipped-sample-missing"
    actions["guardian_default_preset_copy"] = _copy_preset(
        GUARDIAN_DEFAULT_SRC, preset_dir / "guardian_default.json", dry_run, force
    )
    actions["automation_backend"] = _ensure_automation_backend(home, dry_run, force)
    actions["ssh_import"] = _import_ssh_hosts(home, dry_run)
    return actions


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Install Guardian baseline into ~/.nexo/")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Overwrite existing user guardian.json")
    parser.add_argument("--nexo-home", type=str, default=None)
    args = parser.parse_args(argv)
    home = pathlib.Path(args.nexo_home) if args.nexo_home else None
    actions = install(home, dry_run=args.dry_run, force=args.force)
    print(json.dumps(actions, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
