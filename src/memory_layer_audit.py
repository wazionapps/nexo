"""Startup audit for client memory layers.

NEXO Brain is the durable memory authority. Client-local MEMORY files can
exist from older Claude/Codex installs, but they must not silently override
calibration/profile data.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import paths

LEGACY_MEMORY_PATHS = (
    (".claude", "MEMORY.md"),
    (".codex", "MEMORY.md"),
)
LEGACY_MEMORY_DIRS = (
    (".claude", "memories"),
    (".codex", "memories"),
)
BOOTSTRAP_PATHS = (
    (".claude", "CLAUDE.md"),
    (".codex", "AGENTS.md"),
)

AUTHORITY_ORDER = [
    "brain/calibration.json",
    "brain/profile.json",
    "NEXO Brain DB: followups, learnings, decisions, diary, outcomes",
    "managed client bootstrap CORE blocks",
    "legacy client MEMORY.md files (read-only, lowest authority)",
]

LOCATION_HINT_RE = re.compile(
    r"\b("
    r"lives?|resides?|resident|residence|location|city|from|based|"
    r"vive|reside|residencia|ubicaci[oó]n|ciudad|de\s+"
    r")\b",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"[A-Za-z0-9_ÁÉÍÓÚÜÑáéíóúüñ]+")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    except Exception:
        return {}
    return {}


def _brain_dir_candidates(nexo_home: Path) -> list[Path]:
    candidates = [nexo_home / "brain", nexo_home / "personal" / "brain"]
    try:
        current = paths.brain_dir()
        if current not in candidates:
            candidates.append(current)
    except Exception:
        pass
    unique: list[Path] = []
    seen = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _load_canonical_sources(nexo_home: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    for brain_dir in _brain_dir_candidates(nexo_home):
        calibration = _read_json(brain_dir / "calibration.json")
        profile = _read_json(brain_dir / "profile.json")
        if calibration or profile:
            return calibration, profile, {
                "calibration": str(brain_dir / "calibration.json"),
                "profile": str(brain_dir / "profile.json"),
            }
    fallback = _brain_dir_candidates(nexo_home)[0]
    return {}, {}, {
        "calibration": str(fallback / "calibration.json"),
        "profile": str(fallback / "profile.json"),
    }


def _iter_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_iter_strings(item))
        return out
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_iter_strings(item))
        return out
    return []


def _tokenize(text: str) -> set[str]:
    return {
        token.lower()
        for token in TOKEN_RE.findall(text or "")
        if len(token) >= 4
    }


def _canonical_location_tokens(calibration: dict[str, Any], profile: dict[str, Any]) -> set[str]:
    values: list[str] = []
    for container in (profile, calibration):
        for key in (
            "current_residence",
            "residence",
            "location",
            "base_location",
            "city",
            "country",
            "timezone",
        ):
            if key in container:
                values.extend(_iter_strings(container.get(key)))
        user = container.get("user")
        if isinstance(user, dict):
            for key in ("location", "city", "country", "timezone"):
                if key in user:
                    values.extend(_iter_strings(user.get(key)))
    tokens: set[str] = set()
    for value in values:
        tokens.update(_tokenize(value))
    return tokens


def _legacy_memory_paths(home: Path) -> list[Path]:
    found: list[Path] = []
    for parts in LEGACY_MEMORY_PATHS:
        candidate = home.joinpath(*parts)
        if candidate.exists():
            found.append(candidate)
    for parts in LEGACY_MEMORY_DIRS:
        candidate = home.joinpath(*parts)
        if candidate.exists() and any(candidate.iterdir()):
            found.append(candidate)
    return found


def _location_like_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or len(line) > 500:
            continue
        if LOCATION_HINT_RE.search(line):
            lines.append(line[:240])
    return lines


def audit_memory_layers(
    *,
    home: str | Path | None = None,
    nexo_home: str | Path | None = None,
    max_warnings: int = 5,
) -> dict[str, Any]:
    """Return a read-only audit of memory authority and legacy client files."""

    home_path = Path(home) if home is not None else Path.home()
    nexo_home_path = Path(nexo_home) if nexo_home is not None else Path(os.environ.get("NEXO_HOME", str(home_path / ".nexo")))
    calibration, profile, source_paths = _load_canonical_sources(nexo_home_path)
    canonical_location_tokens = _canonical_location_tokens(calibration, profile)

    warnings: list[dict[str, Any]] = []
    legacy_paths = _legacy_memory_paths(home_path)
    if legacy_paths:
        warnings.append({
            "type": "legacy_client_memory_present",
            "severity": "warn",
            "paths": [str(path) for path in legacy_paths],
            "message": "Legacy Claude/Codex MEMORY files exist. They are lower authority than NEXO Brain and should stay read-only.",
        })

    for parts in BOOTSTRAP_PATHS:
        candidate = home_path.joinpath(*parts)
        try:
            text = candidate.read_text(encoding="utf-8") if candidate.exists() else ""
        except Exception:
            text = ""
        if not text:
            continue
        for line in _location_like_lines(text):
            line_tokens = _tokenize(line)
            if canonical_location_tokens and line_tokens.isdisjoint(canonical_location_tokens):
                warnings.append({
                    "type": "possible_identity_location_conflict",
                    "severity": "warn",
                    "path": str(candidate),
                    "line": line,
                    "message": "Bootstrap contains a location-like profile fact that does not match canonical calibration/profile tokens.",
                })
                break

    return {
        "ok": True,
        "authority_order": AUTHORITY_ORDER,
        "canonical_sources": source_paths,
        "legacy_paths": [str(path) for path in legacy_paths],
        "warnings": warnings[:max(0, int(max_warnings or 0))],
        "warning_count": len(warnings),
    }


def format_memory_layer_warnings(report: dict[str, Any]) -> list[str]:
    warnings = report.get("warnings") if isinstance(report, dict) else None
    if not isinstance(warnings, list) or not warnings:
        return []
    lines = [
        "NEXO Brain/calibration/profile are authoritative; legacy client MEMORY files are read-only and lowest priority.",
    ]
    for warning in warnings:
        if not isinstance(warning, dict):
            continue
        kind = warning.get("type") or "memory_layer_warning"
        if kind == "legacy_client_memory_present":
            paths_text = ", ".join(warning.get("paths") or [])
            lines.append(f"Legacy MEMORY present: {paths_text}")
        elif kind == "possible_identity_location_conflict":
            lines.append(
                "Possible profile conflict in "
                f"{warning.get('path')}: {warning.get('line')}"
            )
        else:
            lines.append(str(warning.get("message") or kind))
    if report.get("warning_count", 0) > len(warnings):
        lines.append(f"{report['warning_count'] - len(warnings)} more memory-layer warning(s) omitted.")
    return lines
