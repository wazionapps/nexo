"""Plan Consolidado F0.6 — canonical path helpers.

Every module that needs to find a runtime directory should import from
here instead of hardcoding `NEXO_HOME / "scripts"` etc. The legacy
flat layout (`~/.nexo/scripts/`, `~/.nexo/brain/`, `~/.nexo/data/`,
...) is going away in v7.0.0; this module centralises the new tree
so the migration is a one-line change per call site.

New structure (post-F0.6):
    ~/.nexo/
    ├── core/                  ← shipped with the package
    │   ├── db/
    │   ├── cognitive/
    │   ├── dashboard/
    │   ├── doctor/
    │   ├── scripts/           (38 packaged automations)
    │   ├── skills/
    │   ├── plugins/
    │   ├── hooks/
    │   ├── rules/
    │   └── contracts/         (resonance_tiers.json, ...)
    ├── core-dev/              ← dev-only, off by default
    │   └── scripts/
    ├── personal/              ← operator. nexo update never touches.
    │   ├── scripts/
    │   ├── skills/
    │   ├── plugins/
    │   ├── hooks/
    │   ├── rules/
    │   ├── brain/             (calibration.json, project-atlas.json,
    │   │                       operator-routing-rules.json, ...)
    │   ├── config/
    │   ├── lib/
    │   └── overrides/
    └── runtime/               ← dynamic state
        ├── data/              (nexo.db, *.db)
        ├── logs/
        ├── operations/
        ├── state/             (lightweight runtime JSON state)
        ├── backups/
        ├── memory/
        ├── coordination/
        ├── exports/
        ├── nexo-email/
        ├── snapshots/
        └── crons/

Backwards compatibility: every helper has `legacy=True` mode that
returns the pre-F0.6 location (`NEXO_HOME / "<name>"`). The compat
layer disappears in v7.1.0; v7.0.0 keeps it so operator-edited code
that hardcoded the old paths keeps resolving via symlink during the
1-week observation window.
"""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))


def home() -> Path:
    """Return the active NEXO_HOME (recomputed every call so tests
    that monkeypatch the env var see the right path)."""
    return Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))


# ---------------------------------------------------------------------------
# Core (shipped with the package, replaced on every `nexo update`)
# ---------------------------------------------------------------------------
def core_dir() -> Path:
    container = home() / "core"
    live_markers = (
        "cli.py",
        "server.py",
        "db",
        "hooks",
        "plugins",
        "rules",
        "scripts",
        "package.json",
        "version.json",
    )
    if any((container / marker).exists() for marker in live_markers):
        return container
    current = container / "current"
    if current.exists():
        try:
            resolved = current.resolve(strict=False)
            if resolved.exists():
                return resolved
        except Exception:
            return current
        return current
    return container


def core_scripts_dir() -> Path:
    new = core_dir() / "scripts"
    legacy = home() / "scripts"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def core_plugins_dir() -> Path:
    new = core_dir() / "plugins"
    legacy = home() / "plugins"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def core_hooks_dir() -> Path:
    new = core_dir() / "hooks"
    legacy = home() / "hooks"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def core_rules_dir() -> Path:
    new = core_dir() / "rules"
    legacy = home() / "rules"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def core_contracts_dir() -> Path:
    return core_dir() / "contracts"


def core_skills_dir(*, allow_legacy_fallback: bool = True) -> Path:
    new = core_dir() / "skills"
    legacy = home() / "skills-core"
    if allow_legacy_fallback and not new.exists() and legacy.exists():
        return legacy
    return new


def core_db_dir(*, allow_legacy_fallback: bool = True) -> Path:
    new = core_dir() / "db"
    legacy = home() / "db"
    if allow_legacy_fallback and not new.exists() and legacy.exists():
        return legacy
    return new


def core_cognitive_dir(*, allow_legacy_fallback: bool = True) -> Path:
    new = core_dir() / "cognitive"
    legacy = home() / "cognitive"
    if allow_legacy_fallback and not new.exists() and legacy.exists():
        return legacy
    return new


def core_dashboard_dir(*, allow_legacy_fallback: bool = True) -> Path:
    new = core_dir() / "dashboard"
    legacy = home() / "dashboard"
    if allow_legacy_fallback and not new.exists() and legacy.exists():
        return legacy
    return new


def core_doctor_dir(*, allow_legacy_fallback: bool = True) -> Path:
    new = core_dir() / "doctor"
    legacy = home() / "doctor"
    if allow_legacy_fallback and not new.exists() and legacy.exists():
        return legacy
    return new


# ---------------------------------------------------------------------------
# Core-dev (off by default, only useful to product devs)
# ---------------------------------------------------------------------------
def core_dev_dir() -> Path:
    return home() / "core-dev"


def core_dev_scripts_dir() -> Path:
    return core_dev_dir() / "scripts"


# ---------------------------------------------------------------------------
# Personal (operator-owned, `nexo update` never touches)
# ---------------------------------------------------------------------------
def personal_dir() -> Path:
    return home() / "personal"


def personal_scripts_dir() -> Path:
    new = personal_dir() / "scripts"
    legacy = home() / "scripts"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def personal_plugins_dir() -> Path:
    new = personal_dir() / "plugins"
    legacy = home() / "plugins"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def personal_hooks_dir() -> Path:
    new = personal_dir() / "hooks"
    legacy = home() / "hooks"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def personal_rules_dir() -> Path:
    new = personal_dir() / "rules"
    legacy = home() / "rules"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def personal_skills_dir() -> Path:
    new = personal_dir() / "skills"
    legacy = home() / "skills"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def brain_dir() -> Path:
    """Operator brain: calibration, project atlas, routing rules, ..."""
    new = personal_dir() / "brain"
    legacy = home() / "brain"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def personal_config_dir() -> Path:
    new = personal_dir() / "config"
    legacy = home() / "config"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def personal_lib_dir() -> Path:
    return personal_dir() / "lib"


def personal_overrides_dir() -> Path:
    return personal_dir() / "overrides"


def config_dir() -> Path:
    """Canonical operator config dir.

    Post-F0.6 this lives in ``personal/config``. During the transition
    window we still honour a live legacy ``~/.nexo/config`` tree until
    the migrator has converted it into a shim/symlink.
    """
    return personal_config_dir()


# ---------------------------------------------------------------------------
# Runtime (dynamic state, never edited by hand)
# ---------------------------------------------------------------------------
def runtime_dir() -> Path:
    return home() / "runtime"


def data_dir() -> Path:
    new = runtime_dir() / "data"
    legacy = home() / "data"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def db_path() -> Path:
    new = data_dir() / "nexo.db"
    legacy = home() / "data" / "nexo.db"
    if not new.is_file() and legacy.is_file():
        return legacy
    return new


def logs_dir() -> Path:
    new = runtime_dir() / "logs"
    legacy = home() / "logs"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def operations_dir() -> Path:
    new = runtime_dir() / "operations"
    legacy = home() / "operations"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def runtime_state_dir() -> Path:
    new = runtime_dir() / "state"
    legacy = home() / "state"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def backups_dir() -> Path:
    new = runtime_dir() / "backups"
    legacy = home() / "backups"
    if not new.exists() and legacy.exists():
        return legacy
    return new


_GiB = 1024 ** 3
BACKUP_DEFAULT_MAX_BYTES = 50 * _GiB
BACKUP_MIN_CAP_BYTES = 10 * _GiB
BACKUP_MAX_CAP_BYTES = 50 * _GiB
BACKUP_DEFAULT_MIN_FREE_BYTES = 5 * _GiB


class BackupSnapshotPath:
    """Path returned by backup helpers; usable as a post-pruning context."""

    def __init__(self, value: str | Path, *, backups_root: Path | None = None):
        self._path = Path(value)
        self._nexo_backups_root = Path(backups_root) if backups_root is not None else None
        self._nexo_finalized = False

    def __fspath__(self) -> str:
        return os.fspath(self._path)

    def __str__(self) -> str:
        return str(self._path)

    def __repr__(self) -> str:
        return f"BackupSnapshotPath({self._path!r})"

    def __truediv__(self, key):
        return self._path / key

    def __eq__(self, other) -> bool:
        return self._path == Path(other)

    def __getattr__(self, name: str):
        return getattr(self._path, name)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.finalize()
        return False

    def finalize(self) -> dict:
        if self._nexo_finalized:
            return {"ok": True, "skipped": True, "reason": "already_finalized"}
        self._nexo_finalized = True
        return finalize_backup_snapshot(self, backups_root=self._nexo_backups_root)


def parse_size_bytes(value: str | int | None, *, default: int = BACKUP_DEFAULT_MAX_BYTES) -> int:
    """Parse size strings like ``10G`` / ``512M`` into bytes."""
    if value is None or value == "":
        return default
    if isinstance(value, int):
        return max(0, value)
    raw = str(value).strip().lower()
    if not raw:
        return default
    multiplier = 1
    if raw[-1:] in {"k", "m", "g", "t"}:
        unit = raw[-1]
        raw = raw[:-1].strip()
        multiplier = {"k": 1024, "m": 1024 ** 2, "g": _GiB, "t": 1024 ** 4}[unit]
    try:
        return max(0, int(float(raw) * multiplier))
    except ValueError:
        return default


def backup_min_free_bytes() -> int:
    return parse_size_bytes(os.environ.get("NEXO_BACKUP_MIN_FREE_BYTES"), default=BACKUP_DEFAULT_MIN_FREE_BYTES)


def backup_retention_cap_bytes(*, backups_root: Path | None = None, configured: str | int | None = None) -> int:
    """Return the effective technical-backup cap for this install.

    The default adapts to the user's disk: 5% of total capacity, floored at
    10 GiB and capped at 50 GiB. ``NEXO_BACKUP_MAX_BYTES`` remains an upper
    bound, and explicit lower caps are allowed for emergency prune steps.
    """
    raw = parse_size_bytes(
        configured if configured is not None else os.environ.get("NEXO_BACKUP_MAX_BYTES"),
        default=BACKUP_DEFAULT_MAX_BYTES,
    )
    if raw < BACKUP_MIN_CAP_BYTES:
        return raw
    root = Path(backups_root or backups_dir())
    probe = root if root.exists() else root.parent
    try:
        usage = shutil.disk_usage(str(probe))
        adaptive = int(usage.total * 0.05)
    except Exception:
        adaptive = BACKUP_DEFAULT_MAX_BYTES
    adaptive = max(BACKUP_MIN_CAP_BYTES, min(BACKUP_MAX_CAP_BYTES, adaptive))
    return min(raw, adaptive)


def backup_free_bytes(*, backups_root: Path | None = None) -> int | None:
    root = Path(backups_root or backups_dir())
    probe = root if root.exists() else root.parent
    try:
        return int(shutil.disk_usage(str(probe)).free)
    except Exception:
        return None


def _backup_pruner_script() -> Path | None:
    candidates = [
        Path(__file__).resolve().parent / "scripts" / "prune_runtime_backups.py",
        core_scripts_dir() / "prune_runtime_backups.py",
    ]
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate
    return None


def _append_backup_retention_event(event: dict) -> None:
    try:
        log_path = operations_dir() / "backup-retention-events.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **event,
            "os": sys.platform,
        }
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")
    except Exception:
        pass


def run_runtime_backup_prune(
    *,
    max_bytes: str | int | None = None,
    backups_root: Path | None = None,
    delete_all_technical: bool = False,
    protect_paths: Iterable[str | Path] | None = None,
    timeout: int = 120,
) -> dict:
    """Run the technical-backup pruner. Safe no-op when the script is absent."""
    script = _backup_pruner_script()
    root = Path(backups_root or backups_dir())
    cap = parse_size_bytes(max_bytes, default=backup_retention_cap_bytes(backups_root=root))
    if max_bytes is None:
        cap = backup_retention_cap_bytes(backups_root=root)
    if script is None:
        return {"ok": False, "skipped": True, "reason": "pruner_missing", "root": str(root)}
    args = [
        sys.executable,
        str(script),
        "--root",
        str(root),
        "--apply",
        "--json",
        "--max-bytes",
        str(cap),
    ]
    if delete_all_technical:
        args.append("--delete-all-technical")
    for protected in protect_paths or ():
        args.extend(["--protect", str(protected)])
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        report = json.loads(proc.stdout or "{}") if proc.stdout.strip().startswith("{") else {}
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "max_bytes": cap,
            "root": str(root),
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
            "report": report,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "max_bytes": cap, "root": str(root)}


def aggressive_runtime_backup_prune(
    *,
    min_free_bytes: int | None = None,
    backups_root: Path | None = None,
    reason: str = "",
) -> dict:
    """Escalate NEXO-owned backup pruning before any user-facing disk alert.

    Escalation never targets protected business/weekly restore classes. Hourly
    DB dumps are pruned only down to their configured restore floor.
    """
    root = Path(backups_root or backups_dir())
    floor = int(min_free_bytes if min_free_bytes is not None else backup_min_free_bytes())
    steps: list[dict] = []
    escalated = False
    plan = [
        ("standard", None, False),
        ("cap-10gb", 10 * _GiB, False),
        ("cap-5gb", 5 * _GiB, False),
        ("delete-all-technical", 0, True),
    ]
    for label, cap, delete_all in plan:
        before = backup_free_bytes(backups_root=root)
        if before is not None and before >= floor and steps:
            break
        if label != "standard":
            escalated = True
        result = run_runtime_backup_prune(max_bytes=cap, backups_root=root, delete_all_technical=delete_all)
        after = backup_free_bytes(backups_root=root)
        steps.append({
            "step": label,
            "before_free_bytes": before,
            "after_free_bytes": after,
            "ok": result.get("ok") is True,
            "delete_count": (((result.get("report") or {}).get("totals") or {}).get("delete_count")),
            "delete_bytes": (((result.get("report") or {}).get("totals") or {}).get("delete_bytes")),
        })
        if after is not None and after >= floor:
            break
    final_free = backup_free_bytes(backups_root=root)
    if escalated:
        dominant = ""
        try:
            deletes = []
            for step in steps:
                # Detailed family data is emitted by the script report; keep
                # this anonymous and compact for product telemetry.
                if step.get("delete_bytes"):
                    deletes.append((int(step.get("delete_bytes") or 0), step.get("step") or ""))
            dominant = max(deletes)[1] if deletes else ""
        except Exception:
            dominant = ""
        _append_backup_retention_event({
            "event": "backup_prune_escalated",
            "reason": reason,
            "steps": len(steps),
            "dominant_prefix": dominant,
            "final_free_bytes": final_free,
        })
    return {
        "ok": final_free is None or final_free >= floor,
        "root": str(root),
        "min_free_bytes": floor,
        "final_free_bytes": final_free,
        "steps": steps,
    }


def backup_space_error(
    *,
    reason: str = "",
    min_free_bytes: int | None = None,
    backups_root: Path | None = None,
) -> str | None:
    report = aggressive_runtime_backup_prune(
        min_free_bytes=min_free_bytes,
        backups_root=backups_root,
        reason=reason,
    )
    free = report.get("final_free_bytes")
    floor = int(report.get("min_free_bytes") or backup_min_free_bytes())
    if free is not None and free < floor:
        return (
            "free disk below NEXO backup safety floor after NEXO self-cleanup "
            f"({free}B < {floor}B)"
        )
    return None


def create_backup_dir(prefix: str, *, backups_root: Path | None = None) -> Path:
    """Create a technical backup directory through the universal guard."""
    clean = str(prefix or "").strip().strip("-")
    if not clean or any(sep in clean for sep in ("/", "\\")):
        raise ValueError("backup prefix must be a single path segment")
    err = backup_space_error(reason=f"create_backup_dir:{clean}", backups_root=backups_root)
    if err:
        raise RuntimeError(err)
    root = Path(backups_root or backups_dir())
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d-%H%M%S", time.gmtime())
    candidate = root / f"{clean}-{stamp}"
    suffix = 2
    while candidate.exists():
        candidate = root / f"{clean}-{stamp}-{suffix}"
        suffix += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return BackupSnapshotPath(candidate, backups_root=root)


def create_backup_path(prefix: str, suffix: str = "", *, backups_root: Path | None = None) -> Path:
    """Reserve a backup file path under runtime/backups via the same guard."""
    clean = str(prefix or "").strip().strip("-")
    if not clean or any(sep in clean for sep in ("/", "\\")):
        raise ValueError("backup prefix must be a single path segment")
    err = backup_space_error(reason=f"create_backup_path:{clean}", backups_root=backups_root)
    if err:
        raise RuntimeError(err)
    root = Path(backups_root or backups_dir())
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d-%H%M%S", time.gmtime())
    candidate = root / f"{clean}-{stamp}{suffix}"
    index = 2
    while candidate.exists():
        candidate = root / f"{clean}-{stamp}-{index}{suffix}"
        index += 1
    return BackupSnapshotPath(candidate, backups_root=root)


def finalize_backup_snapshot(_path: Path | str | None = None, *, backups_root: Path | None = None) -> dict:
    """Post-snapshot cleanup; callers invoke after writing large artifacts."""
    root = Path(backups_root) if backups_root is not None else None
    protect_paths = []
    if root is None and _path is not None:
        snapshot = Path(_path)
        root = snapshot.parent
    if _path is not None:
        protect_paths.append(Path(_path))
    return run_runtime_backup_prune(backups_root=root, protect_paths=protect_paths)


def memory_dir() -> Path:
    new = runtime_dir() / "memory"
    legacy = home() / "memory"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def cognitive_dir() -> Path:
    return runtime_dir() / "cognitive"


def models_dir() -> Path:
    new = runtime_dir() / "models"
    legacy = home() / "models"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def coordination_dir() -> Path:
    new = runtime_dir() / "coordination"
    legacy = home() / "coordination"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def exports_dir() -> Path:
    new = runtime_dir() / "exports"
    legacy = home() / "exports"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def nexo_email_dir() -> Path:
    new = runtime_dir() / "nexo-email"
    legacy = home() / "nexo-email"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def doctor_dir() -> Path:
    new = runtime_dir() / "doctor"
    legacy = home() / "doctor"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def snapshots_dir() -> Path:
    new = runtime_dir() / "snapshots"
    legacy = home() / "snapshots"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def crons_dir() -> Path:
    new = runtime_dir() / "crons"
    legacy = home() / "crons"
    if not new.exists() and legacy.exists():
        return legacy
    return new


# ---------------------------------------------------------------------------
# Combined views (for callers that need to scan core+personal merged)
# ---------------------------------------------------------------------------
def all_scripts_dirs() -> list[Path]:
    """Return every directory `nexo scripts list` should scan."""
    return [core_scripts_dir(), personal_scripts_dir(), core_dev_scripts_dir()]


def all_plugins_dirs() -> list[Path]:
    return [core_plugins_dir(), personal_plugins_dir()]


def all_hooks_dirs() -> list[Path]:
    return [core_hooks_dir(), personal_hooks_dir()]


def all_rules_dirs() -> list[Path]:
    return [core_rules_dir(), personal_rules_dir()]


# ---------------------------------------------------------------------------
# Legacy compat (PRE-F0.6 paths). Every shipped runtime keeps these as
# symlinks to the new locations until v7.1.0, so operator code that
# hardcoded the flat layout continues to resolve.
# ---------------------------------------------------------------------------
def legacy_scripts_dir() -> Path:
    return home() / "scripts"


def legacy_brain_dir() -> Path:
    return home() / "brain"


def legacy_data_dir() -> Path:
    return home() / "data"


def legacy_logs_dir() -> Path:
    return home() / "logs"


def legacy_operations_dir() -> Path:
    return home() / "operations"


def legacy_db_path() -> Path:
    return legacy_data_dir() / "nexo.db"


def legacy_watchdog_hashes_path() -> Path:
    # Pre-F0.6 watchdog hash registry landed at ``~/.nexo/scripts/.watchdog-hashes``.
    # Post-F0.6 the canonical location is ``core_scripts_dir() / ".watchdog-hashes"``.
    # A migration-aware consumer should check this legacy path and fold entries
    # into the canonical file before deleting, exactly like the rest of the
    # pre-F0.6 compat shims above.
    return legacy_scripts_dir() / ".watchdog-hashes"


# ---------------------------------------------------------------------------
# Smart resolver: prefer new location if it exists, fall back to legacy.
# Used during the v7.0.0 / v7.1.0 transition window.
# ---------------------------------------------------------------------------
def resolve_db_path() -> Path:
    """Return the active SQLite DB path, preferring the new location
    but falling back to the legacy one when an older runtime hasn't
    migrated yet."""
    new = db_path()
    if new.is_file():
        return new
    legacy = legacy_db_path()
    if legacy.is_file():
        return legacy
    return new  # default: new layout for fresh installs


__all__ = [
    "NEXO_HOME",
    "home",
    "core_dir",
    "core_scripts_dir",
    "core_plugins_dir",
    "core_hooks_dir",
    "core_rules_dir",
    "core_contracts_dir",
    "core_dev_dir",
    "core_dev_scripts_dir",
    "personal_dir",
    "personal_scripts_dir",
    "personal_plugins_dir",
    "personal_hooks_dir",
    "personal_rules_dir",
    "personal_skills_dir",
    "brain_dir",
    "personal_config_dir",
    "personal_lib_dir",
    "personal_overrides_dir",
    "runtime_dir",
    "data_dir",
    "db_path",
    "logs_dir",
    "operations_dir",
    "backups_dir",
    "memory_dir",
    "cognitive_dir",
    "models_dir",
    "coordination_dir",
    "exports_dir",
    "nexo_email_dir",
    "doctor_dir",
    "snapshots_dir",
    "crons_dir",
    "all_scripts_dirs",
    "all_plugins_dirs",
    "all_hooks_dirs",
    "all_rules_dirs",
    "legacy_scripts_dir",
    "legacy_brain_dir",
    "legacy_data_dir",
    "legacy_logs_dir",
    "legacy_operations_dir",
    "legacy_db_path",
    "legacy_watchdog_hashes_path",
    "resolve_db_path",
]
