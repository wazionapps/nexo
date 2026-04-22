"""Guardian runtime config resolver — single source of truth for enforcer flags.

v7.2.0 introduces ``~/.nexo/personal/config/guardian-runtime-overrides.json``
as the persistent operator-owned default for Guardian gate modes:

    {
      "G1_ENFORCER_ACTIVE": "hard",
      "G3_ENFORCE_DESTRUCTIVE": "hard",
      "G3_SSH_ENFORCE_REMOTE_WRITE": "hard",
      "G4_ENFORCE_GUARD_CHECK": "hard"
    }

The JSON values match the ``NEXO_<FLAG>`` env-var semantics exactly
(``off`` / ``shadow`` / ``hard``). Env vars always win over the file so
an ad-hoc ``NEXO_G4_ENFORCE_GUARD_CHECK=shadow`` during debugging still
takes effect. The file is loaded lazily and cached per-process; callers
should not rely on edits to take effect without a restart.

Public API:
    ``resolve_guardian_flag(name, default='shadow')`` -> normalized value.

``name`` is the short key (``G1_ENFORCER_ACTIVE``) without the ``NEXO_``
prefix. Resolution order:
    1. Environment variable ``NEXO_<name>`` if set and non-empty.
    2. Override file entry.
    3. ``default``.

All returned values are lowercased and whitespace-stripped.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


_CACHE: dict[str, str] | None = None


def _overrides_path() -> Path:
    home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
    return home / "personal" / "config" / "guardian-runtime-overrides.json"


def _load_overrides() -> dict[str, str]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    path = _overrides_path()
    if not path.is_file():
        _CACHE = {}
        return _CACHE
    try:
        raw = json.loads(path.read_text())
    except Exception:
        _CACHE = {}
        return _CACHE
    if not isinstance(raw, dict):
        _CACHE = {}
        return _CACHE
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            continue
        normalized[str(key).strip().upper()] = value.strip().lower()
    _CACHE = normalized
    return _CACHE


def invalidate_cache() -> None:
    """Drop the cached override file so the next call re-reads from disk.

    Intended for tests and for the updater right after it writes a new
    version of the file. Production code should not need to call this.
    """
    global _CACHE
    _CACHE = None


def resolve_guardian_flag(name: str, default: str = "shadow") -> str:
    """Resolve a Guardian gate mode (``off`` / ``shadow`` / ``hard``).

    Env var ``NEXO_<name>`` has priority; falls back to the overrides
    file; falls back to ``default`` last.
    """
    clean = str(name or "").strip().upper()
    if not clean:
        return str(default or "shadow").strip().lower()

    env_value = os.environ.get(f"NEXO_{clean}", "").strip()
    if env_value:
        return env_value.lower()

    file_value = _load_overrides().get(clean, "")
    if file_value:
        return file_value

    return str(default or "shadow").strip().lower()
