"""Guardian telemetry logger — Fase F F.1/F.2.

Writes one NDJSON entry per enforcement event to
~/.nexo/logs/guardian-telemetry.ndjson. Local-only by default.
External opt-in is gated by a preference flag `telemetry_external_optin`
that defaults to false; Fase F adds a shipping path later.

Entry shape:
  {
    "ts": 1776520000.123,             # epoch seconds
    "rule_id": "R13_pre_edit_guard",
    "event": "trigger" | "evaluated" | "injection" | "skipped" | "compliance" | "false_positive" | "classifier_unavailable",
    "mode": "hard" | "soft" | "shadow" | "off",
    "tool": "Edit",
    "session_id": "sid-...",          # optional, best-effort
    "details": { ... }                # rule-specific metadata
  }

Consumers read this file with the pandas/duckdb analytics layer in
Fase F.3 — the file format is append-only NDJSON so rotation is a
noop at log level; we rotate by size at 10 MB.
"""
from __future__ import annotations

import json
import os
import pathlib
import threading
import time
from typing import Any


DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file before rotation
_LOCK = threading.Lock()


def _telemetry_path() -> pathlib.Path:
    home = pathlib.Path(os.environ.get("NEXO_HOME") or (pathlib.Path.home() / ".nexo"))
    return home / "logs" / "guardian-telemetry.ndjson"


def _rotate_if_needed(path: pathlib.Path, max_bytes: int) -> None:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return
    if size < max_bytes:
        return
    # Rotate to .<epoch>.ndjson alongside; no gzip to keep tailing easy.
    rotated = path.with_suffix(f".ndjson.{int(time.time())}")
    try:
        path.rename(rotated)
    except OSError:
        pass  # fail-closed: keep appending to the oversized file rather than crash


def log_event(
    rule_id: str,
    event: str,
    *,
    mode: str = "",
    tool: str = "",
    session_id: str = "",
    details: dict | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    path: pathlib.Path | None = None,
) -> bool:
    """Append a single telemetry entry. Returns True if written.

    Fail-closed: any IO error returns False silently. The Guardian must
    never let telemetry failures block an inspection decision.
    """
    if not rule_id or not event:
        return False
    target = path or _telemetry_path()
    entry: dict[str, Any] = {
        "ts": round(time.time(), 3),
        "rule_id": rule_id,
        "event": event,
        "mode": mode or "",
        "tool": tool or "",
        "session_id": session_id or "",
        "details": details if isinstance(details, dict) else {},
    }
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
    try:
        with _LOCK:
            target.parent.mkdir(parents=True, exist_ok=True)
            _rotate_if_needed(target, max_bytes)
            with open(target, "a", encoding="utf-8") as fh:
                fh.write(line)
        return True
    except Exception:
        return False


def summarize_rule(rule_id: str, *, path: pathlib.Path | None = None) -> dict[str, int]:
    """Return counts per event type for a single rule. O(n) scan — for
    Fase F.3 we'll move this to duckdb; the script-level summary is
    cheap enough for spot-checks on dev machines.
    """
    target = path or _telemetry_path()
    counts: dict[str, int] = {
        "trigger": 0,
        "evaluated": 0,
        "injection": 0,
        "skipped": 0,
        "compliance": 0,
        "false_positive": 0,
        "classifier_unavailable": 0,
    }
    try:
        for line in target.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("rule_id") != rule_id:
                continue
            ev = str(entry.get("event") or "").strip()
            if ev in counts:
                counts[ev] += 1
    except FileNotFoundError:
        pass
    return counts


def efficacy(rule_id: str, *, path: pathlib.Path | None = None) -> float | None:
    """Fase F.4 style metric: compliance / max(trigger, 1). Returns
    None if no triggers observed yet (don't report misleading zeros).
    """
    counts = summarize_rule(rule_id, path=path)
    if counts["trigger"] == 0:
        return None
    return counts["compliance"] / counts["trigger"]
