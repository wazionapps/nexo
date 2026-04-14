"""Calibration migration — flat → nested schema for calibration.json.

Older NEXO installations wrote calibration.json with flat top-level keys
(user_name, autonomy, role…). The canonical shape is nested
(user.name, personality.autonomy, meta.role). This module detects the
flat shape and migrates to nested with a backup.

Design:
  - Backup lives at calibration.json.pre-migrate-<version>
  - Unknown fields are preserved verbatim under `legacy_unmapped`
  - Revert is just: cp backup → calibration.json
  - Idempotent: if already nested, returns OK without touching the file

No network, no DB. Pure file I/O so it can run from any runtime.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any


FLAT_TO_NESTED = {
    # user.*
    "user_name": ("user", "name"),
    "name": ("user", "name"),
    "language": ("user", "language"),
    "lang": ("user", "language"),
    "timezone": ("user", "timezone"),
    "tz": ("user", "timezone"),
    "assistant_name": ("user", "assistant_name"),
    # personality.*
    "autonomy": ("personality", "autonomy"),
    "communication": ("personality", "communication"),
    "honesty": ("personality", "honesty"),
    "proactivity": ("personality", "proactivity"),
    "error_handling": ("personality", "error_handling"),
    # preferences.*
    "menu_on_demand": ("preferences", "menu_on_demand"),
    "show_pending_items": ("preferences", "show_pending_items"),
    "execution_first": ("preferences", "execution_first"),
    "report_style": ("preferences", "report_style"),
    # meta.*
    "role": ("meta", "role"),
    "technical_level": ("meta", "technical_level"),
}

# Keys that always live at the top level regardless of shape
TOP_LEVEL_KEYS = {"version", "created", "mood_history", "operator_name"}


def _calibration_path(nexo_home: Path | None = None) -> Path:
    home = nexo_home or Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
    return home / "brain" / "calibration.json"


def _is_nested(cal: dict) -> bool:
    """Nested shape has at least one of {user, personality, preferences, meta} as dict."""
    for key in ("user", "personality", "preferences", "meta"):
        if isinstance(cal.get(key), dict):
            return True
    return False


def _has_flat_markers(cal: dict) -> bool:
    """Flat shape has top-level keys that normally belong inside nested groups."""
    for flat_key in FLAT_TO_NESTED:
        if flat_key in cal:
            return True
    return False


def detect(cal: dict | None = None, *, path: Path | None = None) -> dict:
    """Return {'shape': 'nested'|'flat'|'mixed'|'empty', 'reason': str}."""
    if cal is None:
        target = path or _calibration_path()
        if not target.is_file():
            return {"shape": "empty", "reason": "file does not exist"}
        try:
            cal = json.loads(target.read_text())
        except Exception as exc:
            return {"shape": "empty", "reason": f"unreadable: {exc}"}
    if not isinstance(cal, dict) or not cal:
        return {"shape": "empty", "reason": "empty or not an object"}

    nested = _is_nested(cal)
    flat = _has_flat_markers(cal)
    if nested and flat:
        return {"shape": "mixed", "reason": "both nested groups and flat keys present"}
    if nested:
        return {"shape": "nested", "reason": "already canonical"}
    if flat:
        return {"shape": "flat", "reason": "top-level flat keys, no nested groups"}
    return {"shape": "empty", "reason": "no recognizable keys"}


def migrate(
    cal: dict,
    *,
    preserve_unmapped: bool = True,
) -> dict:
    """Convert a flat calibration payload to nested. Returns a new dict."""
    if _is_nested(cal) and not _has_flat_markers(cal):
        return dict(cal)

    result: dict[str, Any] = {}
    legacy_unmapped: dict[str, Any] = {}

    # Preserve nested groups already present
    for group in ("user", "personality", "preferences", "meta"):
        if isinstance(cal.get(group), dict):
            result[group] = dict(cal[group])

    # Preserve top-level metadata
    for key in TOP_LEVEL_KEYS:
        if key in cal:
            result[key] = cal[key]

    # Walk flat keys
    for key, value in cal.items():
        if key in ("user", "personality", "preferences", "meta"):
            continue
        if key in TOP_LEVEL_KEYS:
            continue
        if key in FLAT_TO_NESTED:
            group, leaf = FLAT_TO_NESTED[key]
            result.setdefault(group, {})
            # Nested value wins over flat if both exist
            if leaf not in result[group]:
                result[group][leaf] = value
        else:
            legacy_unmapped[key] = value

    if legacy_unmapped and preserve_unmapped:
        result["legacy_unmapped"] = legacy_unmapped

    # Bump version marker if present
    if "version" not in result:
        result["version"] = 1

    return result


def backup_path(path: Path, version: str = "5.4.0") -> Path:
    return path.with_name(path.name + f".pre-migrate-{version}")


def apply_migration(
    path: Path | None = None,
    *,
    version: str = "5.4.0",
    dry_run: bool = False,
) -> dict:
    """Migrate calibration.json on disk. Returns a status dict."""
    target = path or _calibration_path()
    if not target.is_file():
        return {"status": "skipped", "reason": "calibration.json not found", "path": str(target)}

    try:
        original = json.loads(target.read_text())
    except Exception as exc:
        return {"status": "error", "reason": f"unreadable: {exc}", "path": str(target)}

    shape = detect(original)
    if shape["shape"] in ("nested", "empty"):
        return {
            "status": "noop",
            "reason": f"already {shape['shape']}",
            "path": str(target),
            "shape": shape["shape"],
        }

    migrated = migrate(original)
    if dry_run:
        return {
            "status": "preview",
            "reason": "dry run",
            "path": str(target),
            "shape": shape["shape"],
            "original": original,
            "migrated": migrated,
            "backup_would_be": str(backup_path(target, version)),
        }

    backup = backup_path(target, version)
    try:
        shutil.copy2(target, backup)
    except Exception as exc:
        return {"status": "error", "reason": f"backup failed: {exc}", "path": str(target)}

    try:
        target.write_text(json.dumps(migrated, ensure_ascii=False, indent=2))
    except Exception as exc:
        # Attempt revert
        try:
            shutil.copy2(backup, target)
        except Exception:
            pass
        return {"status": "error", "reason": f"write failed: {exc}", "path": str(target)}

    # Re-detect to confirm
    post = detect(migrated)
    if post["shape"] != "nested":
        # Revert
        try:
            shutil.copy2(backup, target)
        except Exception:
            pass
        return {
            "status": "error",
            "reason": f"post-migration shape is {post['shape']}",
            "path": str(target),
        }

    return {
        "status": "migrated",
        "reason": "flat → nested",
        "path": str(target),
        "backup": str(backup),
        "shape": post["shape"],
        "migrated_at": time.time(),
    }


def revert(
    path: Path | None = None,
    *,
    version: str = "5.4.0",
) -> dict:
    """Revert calibration.json to the most recent pre-migrate backup."""
    target = path or _calibration_path()
    backup = backup_path(target, version)
    if not backup.is_file():
        return {"status": "error", "reason": f"no backup found at {backup}", "path": str(target)}
    try:
        shutil.copy2(backup, target)
    except Exception as exc:
        return {"status": "error", "reason": f"copy failed: {exc}", "path": str(target)}
    return {"status": "reverted", "from": str(backup), "path": str(target)}
