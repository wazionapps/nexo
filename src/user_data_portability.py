"""Portable export/import helpers for operator user data."""

from __future__ import annotations

import json
import os
import paths
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import time
import re
from datetime import datetime, timezone
from pathlib import Path

from runtime_home import export_resolved_nexo_home

NEXO_HOME = export_resolved_nexo_home()
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent)))
EXPORTS_DIR = paths.exports_dir()
STAGING_DIR = EXPORTS_DIR / ".staging"
IGNORED_FILENAMES = {".DS_Store"}
IGNORED_DIRS = {"__pycache__"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}
PORTABLE_CONFIG_EXCLUDE_NAMES = {".keychain-pass"}

# v5.5.6: rate-limit the whole-bundle export so a runaway MCP client cannot
# loop this tool. Each export snapshots the entire NEXO state through
# sqlite3.Connection.backup() plus a tree copy — in the v5.5.4 incident a
# similar loop wrote 8.5 GB in 37 minutes. Overridable for tests / deliberate
# batch exports via NEXO_EXPORT_MIN_INTERVAL_SECS.
EXPORT_MIN_INTERVAL_SECS = int(os.environ.get("NEXO_EXPORT_MIN_INTERVAL_SECS", "120"))
_export_rate_lock = threading.Lock()
_export_last_call_ts = [0.0]


def _check_export_rate_limit() -> str | None:
    now = time.time()
    with _export_rate_lock:
        last = _export_last_call_ts[0]
        elapsed = now - last
        if last > 0 and elapsed < EXPORT_MIN_INTERVAL_SECS:
            remaining = int(EXPORT_MIN_INTERVAL_SECS - elapsed)
            return (
                f"Rate-limited: export_user_bundle called {int(elapsed)}s ago "
                f"(min {EXPORT_MIN_INTERVAL_SECS}s between calls). Wait {remaining}s. "
                "If you see this repeatedly, a client may be stuck in a tool-use loop."
            )
        _export_last_call_ts[0] = now
    return None


def _reset_export_rate_limit_state_for_tests() -> None:
    with _export_rate_lock:
        _export_last_call_ts[0] = 0.0


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _runtime_version() -> str:
    for candidate, key in (
        (NEXO_HOME / "version.json", "version"),
        (NEXO_CODE.parent / "version.json", "version"),
        (NEXO_CODE.parent / "package.json", "version"),
        (NEXO_HOME / "package.json", "version"),
    ):
        try:
            if candidate.is_file():
                return str(json.loads(candidate.read_text()).get(key, "?"))
        except Exception:
            continue
    return "?"


def _parse_version_tuple(value: str) -> tuple[int, ...] | None:
    text = str(value or "").strip()
    if not text:
        return None
    parts: list[int] = []
    for token in text.split("."):
        match = re.match(r"^(\d+)", token.strip())
        if not match:
            return None
        parts.append(int(match.group(1)))
    return tuple(parts) if parts else None


def _version_relation(bundle_version: str, current_version: str) -> str:
    bundle_tuple = _parse_version_tuple(bundle_version)
    current_tuple = _parse_version_tuple(current_version)
    if not bundle_tuple or not current_tuple:
        return "unknown"
    width = max(len(bundle_tuple), len(current_tuple))
    bundle_norm = bundle_tuple + (0,) * (width - len(bundle_tuple))
    current_norm = current_tuple + (0,) * (width - len(current_tuple))
    if bundle_norm == current_norm:
        return "match"
    return "bundle_newer" if bundle_norm > current_norm else "bundle_older"


def _sqlite_backup(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def _should_skip_file(path: Path, exclude_names: set[str] | None = None) -> bool:
    exclude = exclude_names or set()
    if path.name in exclude:
        return True
    if path.name in IGNORED_FILENAMES:
        return True
    if path.suffix in IGNORED_SUFFIXES:
        return True
    return False


def _copy_tree_filtered(src: Path, dest: Path, *, exclude_names: set[str] | None = None) -> int:
    if not src.is_dir():
        return 0
    copied = 0
    for root, dirs, files in os.walk(src):
        root_path = Path(root)
        rel = root_path.relative_to(src)
        dirs[:] = [item for item in dirs if item not in IGNORED_DIRS]
        target_root = dest / rel
        target_root.mkdir(parents=True, exist_ok=True)
        for name in files:
            file_path = root_path / name
            if _should_skip_file(file_path, exclude_names=exclude_names):
                continue
            shutil.copy2(str(file_path), str(target_root / name))
            copied += 1
    return copied


def _copy_file_if_present(src: Path, dest: Path) -> bool:
    if not src.is_file():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dest))
    return True


def _user_section_source(dirname: str) -> Path:
    mapping = {
        "coordination": paths.coordination_dir(),
        "nexo-email": paths.nexo_email_dir(),
        "assets": NEXO_HOME / "assets",
    }
    return mapping.get(dirname, NEXO_HOME / dirname)


def _user_section_target(dirname: str) -> Path:
    return _user_section_source(dirname)


def _portable_tree_sections() -> tuple[tuple[str, Path, Path, set[str]], ...]:
    return (
        ("coordination", paths.coordination_dir(), paths.coordination_dir(), set()),
        ("nexo-email", paths.nexo_email_dir(), paths.nexo_email_dir(), set()),
        ("assets", NEXO_HOME / "assets", NEXO_HOME / "assets", set()),
        ("personal-plugins", paths.personal_plugins_dir(), paths.personal_plugins_dir(), set()),
        ("personal-hooks", paths.personal_hooks_dir(), paths.personal_hooks_dir(), set()),
        ("personal-rules", paths.personal_rules_dir(), paths.personal_rules_dir(), set()),
        ("personal-skills", paths.personal_skills_dir(), paths.personal_skills_dir(), set()),
        ("personal-config", paths.personal_config_dir(), paths.personal_config_dir(), set(PORTABLE_CONFIG_EXCLUDE_NAMES)),
        ("personal-lib", paths.personal_lib_dir(), paths.personal_lib_dir(), set()),
        ("personal-overrides", paths.personal_overrides_dir(), paths.personal_overrides_dir(), set()),
    )


def _safe_extract(archive_path: Path, dest_dir: Path) -> None:
    resolved_dest = dest_dir.resolve()
    with tarfile.open(archive_path, "r:*") as tar:
        members = tar.getmembers()
        for member in members:
            target = (dest_dir / member.name).resolve()
            if target != resolved_dest and resolved_dest not in target.parents:
                raise ValueError(f"archive path escapes destination: {member.name}")
            if member.issym() or member.islnk():
                raise ValueError(f"archive contains unsupported link member: {member.name}")

        for member in members:
            target = (dest_dir / member.name).resolve()
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(member.mode & 0o777)
                continue
            if not member.isfile():
                raise ValueError(f"archive contains unsupported member type: {member.name}")

            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            if extracted is None:
                raise ValueError(f"archive member could not be read: {member.name}")
            with extracted, target.open("wb") as handle:
                shutil.copyfileobj(extracted, handle)
            target.chmod(member.mode & 0o777)


def _stage_bundle(archive_path: Path) -> tuple[Path, Path, dict]:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix="nexo-import-", dir=str(STAGING_DIR)))
    try:
        _safe_extract(archive_path, stage_dir)
        bundle_root = stage_dir / "bundle"
        manifest_path = bundle_root / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("bundle manifest missing")
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("kind") != "nexo-user-data-bundle":
            raise ValueError(f"unsupported bundle kind: {manifest.get('kind', 'unknown')}")
        return stage_dir, bundle_root, manifest
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise


def _inspect_manifest(manifest: dict, archive_path: Path) -> dict:
    current_version = _runtime_version()
    bundle_version = str(manifest.get("version") or "?")
    section_names = sorted(
        str(name).strip()
        for name in (manifest.get("sections") or {}).keys()
        if str(name).strip()
    )
    warning_codes: list[str] = []
    relation = _version_relation(bundle_version, current_version)
    if relation == "bundle_newer":
        warning_codes.append("bundle_newer")
    elif relation == "bundle_older":
        warning_codes.append("bundle_older")
    elif relation == "unknown" and bundle_version != current_version:
        warning_codes.append("version_unknown")
    if not section_names:
        warning_codes.append("no_sections")
    return {
        "ok": True,
        "path": str(archive_path),
        "kind": str(manifest.get("kind") or ""),
        "bundle_version": bundle_version,
        "current_version": current_version,
        "created_at": str(manifest.get("created_at") or ""),
        "section_names": section_names,
        "section_count": len(section_names),
        "version_relation": relation,
        "warning_codes": warning_codes,
    }


def _load_personal_scripts() -> tuple[list[dict], list[dict]]:
    from script_registry import classify_scripts_dir, discover_personal_schedules

    classification = classify_scripts_dir()
    scripts = [entry for entry in classification.get("entries", []) if entry.get("classification") == "personal"]
    script_paths = {entry["path"] for entry in scripts}
    schedules = [
        schedule for schedule in discover_personal_schedules()
        if schedule.get("script_path") in script_paths
    ]
    return scripts, schedules


def export_user_bundle(output_path: str = "", *, enforce_rate_limit: bool = True) -> dict:
    if enforce_rate_limit:
        err = _check_export_rate_limit()
        if err is not None:
            return {"ok": False, "error": err, "rate_limited": True}
    output = Path(output_path).expanduser() if output_path.strip() else (EXPORTS_DIR / f"nexo-user-data-{_now_stamp()}.tar.gz")
    output.parent.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix="nexo-export-", dir=str(STAGING_DIR)))
    bundle_root = stage_dir / "bundle"
    bundle_root.mkdir(parents=True, exist_ok=True)

    sections: dict[str, dict] = {}

    try:
        data_db = paths.db_path()
        if data_db.is_file():
            _sqlite_backup(data_db, bundle_root / "data" / "nexo.db")
            sections["data_db"] = {"path": "data/nexo.db"}

        brain_dir = paths.brain_dir()
        if brain_dir.is_dir():
            copied = _copy_tree_filtered(brain_dir, bundle_root / "brain", exclude_names={"nexo.db"})
            brain_db = brain_dir / "nexo.db"
            if brain_db.is_file():
                _sqlite_backup(brain_db, bundle_root / "brain" / "nexo.db")
                copied += 1
            sections["brain"] = {"path": "brain", "files": copied}

        for dirname, src_dir, _target_dir, exclude_names in _portable_tree_sections():
            if not src_dir.is_dir():
                continue
            copied = _copy_tree_filtered(src_dir, bundle_root / dirname, exclude_names=exclude_names)
            sections[dirname] = {"path": dirname, "files": copied}

        scripts, schedules = _load_personal_scripts()
        exported_scripts: list[dict] = []
        scripts_dir = bundle_root / "personal-scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        for entry in scripts:
            src_path = Path(entry["path"])
            if not src_path.is_file():
                continue
            shutil.copy2(str(src_path), str(scripts_dir / src_path.name))
            exported_scripts.append(
                {
                    "name": entry.get("name", src_path.stem),
                    "path": f"personal-scripts/{src_path.name}",
                    "runtime": entry.get("runtime", "unknown"),
                    "description": entry.get("description", ""),
                }
            )
        (scripts_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "scripts": exported_scripts,
                    "schedules": schedules,
                },
                indent=2,
                ensure_ascii=False,
            ) + "\n"
        )
        sections["personal_scripts"] = {
            "path": "personal-scripts",
            "files": len(exported_scripts),
            "schedules": len(schedules),
        }

        manifest = {
            "kind": "nexo-user-data-bundle",
            "version": _runtime_version(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "nexo_home": str(NEXO_HOME),
            "sections": sections,
        }
        (bundle_root / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

        with tarfile.open(output, "w:gz") as tar:
            tar.add(bundle_root, arcname="bundle")

        return {
            "ok": True,
            "path": str(output),
            "kind": manifest["kind"],
            "version": manifest["version"],
            "sections": sections,
        }
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


def inspect_user_bundle(bundle_path: str) -> dict:
    archive_path = Path(bundle_path).expanduser()
    if not archive_path.is_file():
        return {"ok": False, "error": f"bundle not found: {archive_path}", "path": str(archive_path)}

    stage_dir: Path | None = None
    try:
        stage_dir, _bundle_root, manifest = _stage_bundle(archive_path)
        return _inspect_manifest(manifest, archive_path)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": str(archive_path)}
    finally:
        if stage_dir is not None:
            shutil.rmtree(stage_dir, ignore_errors=True)


def import_user_bundle(bundle_path: str) -> dict:
    archive_path = Path(bundle_path).expanduser()
    if not archive_path.is_file():
        return {"ok": False, "error": f"bundle not found: {archive_path}"}

    inspection = inspect_user_bundle(str(archive_path))
    if not inspection.get("ok"):
        return inspection

    safety_backup = paths.create_backup_path("pre-import-user-data", ".tar.gz")
    safety_result = export_user_bundle(str(safety_backup), enforce_rate_limit=False)
    if not safety_result.get("ok"):
        return {"ok": False, "error": "failed to create safety backup", "safety_backup": str(safety_backup)}
    paths.finalize_backup_snapshot(safety_backup)

    stage_dir: Path | None = None

    try:
        stage_dir, bundle_root, manifest = _stage_bundle(archive_path)

        restored: dict[str, dict] = {}

        data_db = bundle_root / "data" / "nexo.db"
        if data_db.is_file():
            _sqlite_backup(data_db, paths.db_path())
            restored["data_db"] = {"path": "data/nexo.db"}

        brain_dir = bundle_root / "brain"
        if brain_dir.is_dir():
            copied = _copy_tree_filtered(brain_dir, paths.brain_dir(), exclude_names={"nexo.db"})
            brain_db = brain_dir / "nexo.db"
            if brain_db.is_file():
                _sqlite_backup(brain_db, paths.brain_dir() / "nexo.db")
                copied += 1
            restored["brain"] = {"path": "brain", "files": copied}

        for dirname, _src_dir, target_dir, _exclude_names in _portable_tree_sections():
            src_dir = bundle_root / dirname
            if not src_dir.is_dir():
                continue
            copied = _copy_tree_filtered(src_dir, target_dir)
            restored[dirname] = {"path": dirname, "files": copied}

        imported_scripts = 0
        scripts_dir = bundle_root / "personal-scripts"
        target_scripts_dir = paths.personal_scripts_dir()
        target_scripts_dir.mkdir(parents=True, exist_ok=True)
        if scripts_dir.is_dir():
            for script_path in sorted(scripts_dir.iterdir()):
                if not script_path.is_file() or script_path.name == "manifest.json":
                    continue
                shutil.copy2(str(script_path), str(target_scripts_dir / script_path.name))
                imported_scripts += 1
        restored["personal_scripts"] = {"path": "personal-scripts", "files": imported_scripts}

        from db import init_db, sync_skill_directories, retire_superseded_personal_skills
        from script_registry import (
            reconcile_personal_scripts,
            retire_superseded_personal_scripts,
        )

        init_db()
        skill_sync_result = sync_skill_directories()
        retired_skills_result = retire_superseded_personal_skills(dry_run=False)
        retired_scripts_result = retire_superseded_personal_scripts(dry_run=False)
        reconcile_result = reconcile_personal_scripts(dry_run=False)

        return {
            "ok": True,
            "path": str(archive_path),
            "kind": manifest.get("kind"),
            "bundle_version": manifest.get("version"),
            "current_version": inspection.get("current_version"),
            "version_relation": inspection.get("version_relation"),
            "warning_codes": inspection.get("warning_codes", []),
            "safety_backup": str(safety_backup),
            "restored": restored,
            "skill_sync": skill_sync_result,
            "retired_superseded_skills": retired_skills_result,
            "retired_superseded_scripts": retired_scripts_result,
            "reconciled": reconcile_result,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "safety_backup": str(safety_backup),
        }
    finally:
        if stage_dir is not None:
            shutil.rmtree(stage_dir, ignore_errors=True)
