from __future__ import annotations

"""Shared protocol-discipline settings loaded from calibration.json."""

import json
import os
from pathlib import Path


DEFAULT_PROTOCOL_STRICTNESS = "lenient"
VALID_PROTOCOL_STRICTNESS = {"lenient", "strict", "learning"}


def _nexo_home() -> Path:
    return Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))).expanduser()


def _calibration_path() -> Path:
    return _nexo_home() / "brain" / "calibration.json"


def normalize_protocol_strictness(value: str | None) -> str:
    candidate = str(value or "").strip().lower()
    aliases = {
        "default": "lenient",
        "normal": "lenient",
        "off": "lenient",
        "warn": "lenient",
        "soft": "lenient",
        "hard": "strict",
        "guided": "learning",
    }
    candidate = aliases.get(candidate, candidate)
    if candidate in VALID_PROTOCOL_STRICTNESS:
        return candidate
    return DEFAULT_PROTOCOL_STRICTNESS


def get_protocol_strictness() -> str:
    env_override = os.environ.get("NEXO_PROTOCOL_STRICTNESS", "").strip()
    if env_override:
        return normalize_protocol_strictness(env_override)

    cal_path = _calibration_path()
    if not cal_path.is_file():
        return DEFAULT_PROTOCOL_STRICTNESS

    try:
        payload = json.loads(cal_path.read_text())
    except Exception:
        return DEFAULT_PROTOCOL_STRICTNESS

    preferences = payload.get("preferences") if isinstance(payload, dict) else {}
    candidate = ""
    if isinstance(preferences, dict):
        candidate = str(preferences.get("protocol_strictness", "") or "").strip()
    if not candidate and isinstance(payload, dict):
        candidate = str(payload.get("protocol_strictness", "") or "").strip()
    return normalize_protocol_strictness(candidate)
