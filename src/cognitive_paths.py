"""Canonical cognitive.db path resolution and legacy shadow-DB guard."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paths


class CognitiveDbPathConflict(RuntimeError):
    """Raised when canonical and legacy cognitive DBs could both receive writes."""


def _configured_override() -> Path | None:
    value = os.environ.get("NEXO_COGNITIVE_DB", "").strip()
    return Path(value).expanduser() if value else None


def canonical_cognitive_dir() -> Path:
    return paths.runtime_dir() / "cognitive"


def canonical_cognitive_db_path() -> Path:
    override = _configured_override()
    if override is not None:
        return override
    return canonical_cognitive_dir() / "cognitive.db"


def legacy_cognitive_db_paths() -> list[Path]:
    canonical = canonical_cognitive_db_path()
    candidates = [
        paths.runtime_dir() / "data" / "cognitive.db",
        paths.legacy_data_dir() / "cognitive.db",
        paths.home() / "cognitive" / "cognitive.db",
    ]
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve())
            canonical_key = str(canonical.resolve())
        except Exception:
            key = str(candidate)
            canonical_key = str(canonical)
        if key == canonical_key or key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_signature(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    stat = path.stat()
    signature: dict[str, Any] = {
        "exists": True,
        "path": str(path),
        "size_bytes": stat.st_size,
        "mtime_epoch": stat.st_mtime,
        "sha256": _sha256(path),
    }
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        schema_blob = json.dumps(rows, sort_keys=True, default=str)
        signature.update({
            "sqlite_ok": True,
            "user_version": int(user_version or 0),
            "schema_sha256": hashlib.sha256(schema_blob.encode("utf-8")).hexdigest(),
            "tables": [row[1] for row in rows if row[0] == "table"],
        })
    except Exception as exc:
        signature.update({
            "sqlite_ok": False,
            "sqlite_error": str(exc)[:240],
        })
    return signature


def _migration_marker_path() -> Path:
    return paths.runtime_state_dir() / "cognitive-db-migration.json"


def _cleanup_marker_path() -> Path:
    return paths.runtime_state_dir() / "cognitive-db-cleanup.jsonl"


def _write_migration_marker(source: Path, target: Path) -> None:
    marker = {
        "at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "target": str(target),
        "source_sha256": _sha256(source) if source.exists() else "",
        "target_sha256": _sha256(target) if target.exists() else "",
        "legacy_retained": True,
    }
    marker_path = _migration_marker_path()
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_cleanup_marker(event: dict[str, Any]) -> None:
    marker_path = _cleanup_marker_path()
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    with marker_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _sidecar_paths(db_path: Path) -> list[Path]:
    return [db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")]


def _existing_sidecars(db_path: Path) -> list[Path]:
    return [path for path in _sidecar_paths(db_path) if path.exists()]


def _wal_has_uncheckpointed_data(db_path: Path) -> bool:
    wal_path = Path(f"{db_path}-wal")
    try:
        return wal_path.is_file() and wal_path.stat().st_size > 0
    except OSError:
        return True


def _canonical_supersedes_legacy(canonical_sig: dict[str, Any], legacy_sig: dict[str, Any]) -> bool:
    if not canonical_sig.get("exists") or not legacy_sig.get("exists"):
        return False
    if not canonical_sig.get("sqlite_ok") or not legacy_sig.get("sqlite_ok"):
        return False
    if float(canonical_sig.get("mtime_epoch") or 0) < float(legacy_sig.get("mtime_epoch") or 0):
        return False
    canonical_tables = set(canonical_sig.get("tables") or [])
    legacy_tables = set(legacy_sig.get("tables") or [])
    if canonical_tables and legacy_tables and not legacy_tables.issubset(canonical_tables):
        return False
    return True


def _remove_paths(paths_to_remove: list[Path]) -> list[str]:
    removed: list[str] = []
    for path in paths_to_remove:
        try:
            if path.exists():
                path.unlink()
                removed.append(str(path))
        except FileNotFoundError:
            continue
    return removed


def _archive_and_remove_legacy_db(
    legacy_db: Path,
    *,
    canonical_sig: dict[str, Any],
    legacy_sig: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    files = _existing_sidecars(legacy_db)
    backup_root = paths.create_backup_dir("legacy-cognitive-db")
    backup_dir = Path(backup_root)
    archive_path = backup_dir / "cognitive-legacy.tar.gz"
    manifest_path = backup_dir / "manifest.json"
    with tarfile.open(archive_path, "w:gz") as archive:
        for file_path in files:
            archive.add(file_path, arcname=file_path.name)
    with tarfile.open(archive_path, "r:gz") as archive:
        archived_names = sorted(archive.getnames())
    archive_sha = _sha256(archive_path)
    manifest = {
        "reason": reason,
        "source": str(legacy_db),
        "archived_files": [str(path) for path in files],
        "archive": str(archive_path),
        "archive_sha256": archive_sha,
        "archive_members": archived_names,
        "canonical": canonical_sig,
        "legacy": legacy_sig,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    removed = _remove_paths(files)
    paths.finalize_backup_snapshot(backup_dir)
    _append_cleanup_marker({
        "action": "archive_superseded_legacy",
        "source": str(legacy_db),
        "archive": str(archive_path),
        "archive_sha256": archive_sha,
        "removed": removed,
        "reason": reason,
    })
    return {
        "path": str(legacy_db),
        "action": "archived",
        "archive_path": str(archive_path),
        "manifest_path": str(manifest_path),
        "removed": removed,
        "reason": reason,
    }


def cleanup_legacy_cognitive_db_artifacts(*, dry_run: bool = False) -> dict[str, Any]:
    """Remove or archive safe legacy cognitive DB shadows.

    Identical legacy duplicates are deleted directly. Divergent legacy DBs are
    only archived when the canonical DB is valid, newer, and has a compatible
    schema. Ambiguous cases are left in place so write callers still block.
    """
    override = _configured_override()
    report: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "removed": [],
        "archived": [],
        "skipped": [],
        "errors": [],
    }
    if override is not None:
        report["skipped"].append({"reason": "env_override", "path": str(override)})
        return report

    canonical = canonical_cognitive_db_path()
    canonical_sig = _sqlite_signature(canonical)
    if not canonical_sig.get("exists"):
        report["skipped"].append({"reason": "canonical_missing", "path": str(canonical)})
        return report
    if not canonical_sig.get("sqlite_ok"):
        report["ok"] = False
        report["skipped"].append({"reason": "canonical_not_sqlite_ok", "path": str(canonical)})
        return report

    for legacy_db in legacy_cognitive_db_paths():
        legacy_sig = _sqlite_signature(legacy_db)
        if not legacy_sig.get("exists"):
            continue
        files = _existing_sidecars(legacy_db)
        if _wal_has_uncheckpointed_data(legacy_db):
            report["skipped"].append({"path": str(legacy_db), "reason": "legacy_wal_has_data"})
            continue
        if legacy_sig.get("sha256") == canonical_sig.get("sha256"):
            item = {
                "path": str(legacy_db),
                "action": "removed-identical-duplicate",
                "files": [str(path) for path in files],
            }
            if not dry_run:
                item["removed"] = _remove_paths(files)
                _append_cleanup_marker({
                    "action": "remove_identical_duplicate",
                    "source": str(legacy_db),
                    "removed": item["removed"],
                    "legacy_sha256": legacy_sig.get("sha256"),
                })
            report["removed"].append(item)
            continue
        if _canonical_supersedes_legacy(canonical_sig, legacy_sig):
            if dry_run:
                report["archived"].append({
                    "path": str(legacy_db),
                    "action": "would-archive-superseded-legacy",
                    "reason": "canonical_newer_schema_compatible",
                })
                continue
            try:
                report["archived"].append(_archive_and_remove_legacy_db(
                    legacy_db,
                    canonical_sig=canonical_sig,
                    legacy_sig=legacy_sig,
                    reason="canonical_newer_schema_compatible",
                ))
            except Exception as exc:
                report["ok"] = False
                report["errors"].append({"path": str(legacy_db), "error": str(exc)})
            continue
        report["skipped"].append({
            "path": str(legacy_db),
            "reason": "divergent_requires_manual_review",
            "canonical_mtime_epoch": canonical_sig.get("mtime_epoch"),
            "legacy_mtime_epoch": legacy_sig.get("mtime_epoch"),
        })
    return report


def audit_cognitive_db_paths() -> dict[str, Any]:
    canonical = canonical_cognitive_db_path()
    canonical_sig = _sqlite_signature(canonical)
    legacy = [
        {
            "path": str(candidate),
            "signature": _sqlite_signature(candidate),
        }
        for candidate in legacy_cognitive_db_paths()
    ]
    existing_legacy = [entry for entry in legacy if entry["signature"].get("exists")]
    divergent = [
        entry for entry in existing_legacy
        if canonical_sig.get("exists")
        and entry["signature"].get("sha256")
        and entry["signature"].get("sha256") != canonical_sig.get("sha256")
    ]
    if divergent:
        status = "error"
        reason = "canonical_and_legacy_diverge"
    elif not canonical_sig.get("exists") and existing_legacy:
        status = "warning"
        reason = "legacy_only"
    elif existing_legacy:
        status = "ok"
        reason = "legacy_duplicate_retained"
    else:
        status = "ok"
        reason = "canonical_only"
    return {
        "status": status,
        "reason": reason,
        "canonical": {"path": str(canonical), "signature": canonical_sig},
        "legacy": legacy,
        "migration_marker": str(_migration_marker_path()),
    }


def _first_existing_legacy() -> Path | None:
    for candidate in legacy_cognitive_db_paths():
        if candidate.is_file():
            return candidate
    return None


def migrate_legacy_cognitive_db_if_needed() -> dict[str, Any]:
    override = _configured_override()
    if override is not None:
        return {"migrated": False, "reason": "env_override", "path": str(override)}
    canonical = canonical_cognitive_db_path()
    if canonical.exists():
        return {"migrated": False, "reason": "canonical_exists", "path": str(canonical)}
    source = _first_existing_legacy()
    if source is None:
        return {"migrated": False, "reason": "no_legacy", "path": str(canonical)}
    canonical.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, canonical)
    _write_migration_marker(source, canonical)
    cleanup = cleanup_legacy_cognitive_db_artifacts()
    return {
        "migrated": True,
        "reason": "legacy_copied",
        "source": str(source),
        "path": str(canonical),
        "cleanup": cleanup,
    }


def resolve_cognitive_db(*, for_write: bool = True, migrate: bool = True, create_parent: bool = True) -> Path:
    """Return the cognitive DB path; block writes when legacy shadows diverge."""
    target = canonical_cognitive_db_path()
    if create_parent:
        target.parent.mkdir(parents=True, exist_ok=True)
    if migrate:
        migrate_legacy_cognitive_db_if_needed()
        cleanup_legacy_cognitive_db_artifacts()
    audit = audit_cognitive_db_paths()
    if for_write and audit["status"] == "error":
        raise CognitiveDbPathConflict(
            "Refusing to write cognitive.db while canonical and legacy databases diverge. "
            f"Canonical: {audit['canonical']['path']}; legacy: "
            + ", ".join(entry["path"] for entry in audit["legacy"] if entry["signature"].get("exists"))
        )
    return target
