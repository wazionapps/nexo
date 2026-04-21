"""Runtime-safe calibration view.

Reads the full on-disk ``calibration.json`` but returns only the subset
that runtime consumers actually need for identity, preferences, and
profile decisions. Heavy historical arrays stay on disk untouched.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from paths import brain_dir

_TOP_LEVEL_RUNTIME_KEYS = {
    "user",
    "preferences",
    "meta",
    "assistant_name",
    "operator_name",
    "identity",
    "language",
    "lang",
    "user_name",
    "name",
    "version",
    "created",
}


def load_runtime_calibration(path: Path | None = None) -> dict:
    target = path or (brain_dir() / "calibration.json")
    if not target.is_file():
        return {}
    try:
        payload = json.loads(target.read_text())
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    view: dict = {}
    for key in _TOP_LEVEL_RUNTIME_KEYS:
        if key in payload:
            view[key] = copy.deepcopy(payload[key])
    return view
