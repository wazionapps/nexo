"""Runtime events bus — append-only NDJSON stream at ~/.nexo/runtime/events.ndjson.

NEXO Brain writes events that external UIs (NEXO Desktop, mobile, web)
can tail for real-time attention signals, proactive messages, health
alerts, and general notifications.

Contract:
  - One JSON object per line. No multi-line JSON.
  - Monotonic `id` (integer) and `ts` (unix seconds, float).
  - Stable event envelope keys: id, ts, type, priority, text, reason,
    source, extra. Unknown keys are preserved.
  - File is append-only. Rotation happens at 5 MB: current file is
    renamed to events-<ts>.ndjson and a fresh empty file is created.
  - Readers tail the current file; rotation is transparent because the
    file is reopened after rename detection.

Event types (stable):
  attention_required  — user should look at something
  proactive_message   — Brain wants to initiate dialogue
  followup_alert      — overdue or urgent followup
  health_alert        — a core system is degraded
  info                — general update, no attention needed

Priorities: "low" | "normal" | "high" | "urgent"
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any

EVENT_TYPES = {
    "attention_required",
    "proactive_message",
    "followup_alert",
    "health_alert",
    "info",
}
PRIORITIES = {"low", "normal", "high", "urgent"}
ROTATION_BYTES = 5 * 1024 * 1024  # 5 MB


def _nexo_home() -> Path:
    return Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))


def events_path() -> Path:
    return _nexo_home() / "runtime" / "events.ndjson"


def _next_id(path: Path) -> int:
    """Return the next monotonic id by reading the last line's id, or 1."""
    if not path.is_file():
        return 1
    try:
        # Read last 4 KB — more than enough for the tail line
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 4096))
            tail = fh.read().decode("utf-8", errors="ignore")
        lines = [ln for ln in tail.splitlines() if ln.strip()]
        if not lines:
            return 1
        last = json.loads(lines[-1])
        return int(last.get("id", 0)) + 1
    except Exception:
        return int(time.time())


def _rotate_if_needed(path: Path) -> None:
    try:
        if path.is_file() and path.stat().st_size > ROTATION_BYTES:
            stamp = int(time.time())
            rotated = path.with_name(f"events-{stamp}.ndjson")
            path.rename(rotated)
    except Exception:
        # Rotation failure is non-fatal; worst case the file grows
        pass


def emit(
    event_type: str,
    *,
    text: str = "",
    reason: str = "",
    priority: str = "normal",
    source: str = "nexo-brain",
    extra: dict[str, Any] | None = None,
) -> dict:
    """Append a new event to the bus. Returns the full event dict."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event_type: {event_type}")
    if priority not in PRIORITIES:
        raise ValueError(f"unknown priority: {priority}")

    path = events_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_if_needed(path)

    event = {
        "id": _next_id(path),
        "ts": time.time(),
        "type": event_type,
        "priority": priority,
        "text": text,
        "reason": reason,
        "source": source,
        "extra": extra or {},
    }

    line = json.dumps(event, ensure_ascii=False) + "\n"
    # fcntl flock for cross-process safety on macOS/Linux
    with path.open("a", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(line)
            fh.flush()
        finally:
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            except Exception:
                pass

    return event


def tail(lines: int = 50, since_id: int | None = None) -> list[dict]:
    """Return the most recent events, newest last. Optionally filter by id."""
    path = events_path()
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            raw = fh.readlines()
    except Exception:
        return []

    events: list[dict] = []
    for ln in raw[-max(lines, 1) * 4:]:  # generous buffer for malformed lines
        ln = ln.strip()
        if not ln:
            continue
        try:
            evt = json.loads(ln)
        except Exception:
            continue
        if since_id is not None and int(evt.get("id", 0)) <= since_id:
            continue
        events.append(evt)

    return events[-lines:]
