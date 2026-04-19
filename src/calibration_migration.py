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
    import paths
    return paths.brain_dir() / "calibration.json"


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


# ---------------------------------------------------------------------------
# v6.0.0 purge
# ---------------------------------------------------------------------------
# v6.0.0 removed three user-facing knobs whose state now lives elsewhere:
#   - ``client_runtime_profiles.{claude_code,codex}.{model,reasoning_effort}``
#     in schedule.json → replaced by resonance_tiers.json (tier is the only
#     input, model+effort are resolved from the JSON).
#   - ``preferences.protocol_strictness`` in calibration.json → replaced by
#     the TTY/no-TTY detection in protocol_settings.py.
#   - ``preferences.show_pending_at_start`` in calibration.json → moved to
#     NEXO Desktop's electron-store; Brain no longer reads or writes it.
#
# The update path calls ``apply_v6_purge(nexo_home)`` exactly once per
# upgrade. It never maps legacy values to new ones (that would re-create
# learning #398, the reasoning_effort=max → maximo footgun) — legacy
# fields are dropped silently and callers fall back to the canonical
# defaults (strict or lenient via TTY, tier=alto).

_V6_LEGACY_RUNTIME_KEYS = ("model", "reasoning_effort")
_V6_LEGACY_PREFS_KEYS = ("protocol_strictness", "show_pending_at_start")
_V6_DEFAULT_TIER = "alto"


def _prune_client_runtime_profiles(schedule: dict) -> bool:
    """Remove model/reasoning_effort from every client_runtime_profile.

    Leaves the enclosing dict shape intact so downstream schedule.json
    readers that still iterate ``client_runtime_profiles`` do not need a
    guard — they just see an empty-ish profile for each client.
    """
    changed = False
    profiles = schedule.get("client_runtime_profiles")
    if not isinstance(profiles, dict):
        return False
    for client, profile in list(profiles.items()):
        if not isinstance(profile, dict):
            continue
        for key in _V6_LEGACY_RUNTIME_KEYS:
            if key in profile:
                profile.pop(key, None)
                changed = True
    return changed


def _prune_calibration_preferences(cal: dict) -> bool:
    """Drop protocol_strictness and show_pending_at_start from calibration."""
    changed = False
    # Pref dict may be absent or non-dict on pathological payloads.
    prefs = cal.get("preferences")
    if isinstance(prefs, dict):
        for key in _V6_LEGACY_PREFS_KEYS:
            if key in prefs:
                prefs.pop(key, None)
                changed = True
    # Some early v5.x installs wrote protocol_strictness at the top level.
    for key in _V6_LEGACY_PREFS_KEYS:
        if key in cal:
            cal.pop(key, None)
            changed = True
    return changed


def _ensure_default_resonance(cal: dict) -> bool:
    """Seed preferences.default_resonance='alto' when the user has none.

    Never overwrites an existing value — respecting a non-default choice
    is the whole point of making this idempotent.
    """
    prefs = cal.setdefault("preferences", {})
    if not isinstance(prefs, dict):
        # Reset to a sane shape without losing the rest of the payload.
        cal["preferences"] = prefs = {}
    current = str(prefs.get("default_resonance") or "").strip().lower()
    if current:
        return False
    prefs["default_resonance"] = _V6_DEFAULT_TIER
    return True


def apply_v6_purge(
    nexo_home: Path | None = None,
    *,
    dry_run: bool = False,
) -> dict:
    """Perform the v6.0.0 migration against an on-disk NEXO_HOME.

    Returns a dict describing what changed. Never raises — the update
    flow appends the result to the actions trail and keeps going.
    """
    home = Path(nexo_home) if nexo_home else Path(
        __import__("os").environ.get("NEXO_HOME", str(Path.home() / ".nexo"))
    )
    result: dict[str, Any] = {
        "status": "noop",
        "home": str(home),
        "calibration_changed": False,
        "schedule_changed": False,
        "seeded_default_resonance": False,
    }

    _brain_new = home / "personal" / "brain"
    _brain_legacy = home / "brain"
    cal_path = (_brain_new if _brain_new.is_dir() else _brain_legacy) / "calibration.json"
    sched_path = home / "config" / "schedule.json"

    # --- calibration.json ---
    if cal_path.is_file():
        try:
            cal = json.loads(cal_path.read_text())
        except Exception:
            cal = None
        if isinstance(cal, dict):
            pruned = _prune_calibration_preferences(cal)
            seeded = _ensure_default_resonance(cal)
            if (pruned or seeded) and not dry_run:
                cal_path.write_text(json.dumps(cal, ensure_ascii=False, indent=2))
            result["calibration_changed"] = bool(pruned or seeded)
            result["seeded_default_resonance"] = bool(seeded)

    # --- schedule.json ---
    if sched_path.is_file():
        try:
            sched = json.loads(sched_path.read_text())
        except Exception:
            sched = None
        if isinstance(sched, dict):
            pruned = _prune_client_runtime_profiles(sched)
            if pruned and not dry_run:
                sched_path.write_text(json.dumps(sched, ensure_ascii=False, indent=2))
            result["schedule_changed"] = bool(pruned)

    if any([result["calibration_changed"], result["schedule_changed"]]):
        result["status"] = "migrated"
    return result
