"""Canonical cognitive.db path resolution and legacy shadow-DB guard."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
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
    signature: dict[str, Any] = {
        "exists": True,
        "path": str(path),
        "size_bytes": path.stat().st_size,
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
    return {"migrated": True, "reason": "legacy_copied", "source": str(source), "path": str(canonical)}


def resolve_cognitive_db(*, for_write: bool = True, migrate: bool = True, create_parent: bool = True) -> Path:
    """Return the cognitive DB path; block writes when legacy shadows diverge."""
    target = canonical_cognitive_db_path()
    if create_parent:
        target.parent.mkdir(parents=True, exist_ok=True)
    if migrate:
        migrate_legacy_cognitive_db_if_needed()
    audit = audit_cognitive_db_paths()
    if for_write and audit["status"] == "error":
        raise CognitiveDbPathConflict(
            "Refusing to write cognitive.db while canonical and legacy databases diverge. "
            f"Canonical: {audit['canonical']['path']}; legacy: "
            + ", ".join(entry["path"] for entry in audit["legacy"] if entry["signature"].get("exists"))
        )
    return target

