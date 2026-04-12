#!/usr/bin/env python3
"""Heartbeat enforcement for NEXO sessions.

Tracks user messages vs heartbeat calls. Emits a warning when more than two
user messages pass without a heartbeat call.

Modes:
- HEARTBEAT_MODE=user_msg: increment counter on UserPromptSubmit
- HEARTBEAT_MODE=post_tool: inspect PostToolUse payload, reset on heartbeat,
  warn when other tools keep running without one
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

STATE_FILE = Path(os.environ.get("NEXO_HOME", Path.home() / ".nexo")) / "operations" / ".heartbeat-state.json"
THRESHOLD = 2
HEARTBEAT_TOOL = "nexo_heartbeat"
SKIP_TOOLS = {"nexo_startup", "nexo_stop", "nexo_smart_startup"}


def _read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"user_msgs": 0, "last_heartbeat_ts": 0.0, "last_user_msg_ts": 0.0}


def _write_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state))
    except Exception:
        pass


def handle_user_message() -> int:
    state = _read_state()
    state["user_msgs"] = state.get("user_msgs", 0) + 1
    state["last_user_msg_ts"] = time.time()
    _write_state(state)
    return 0


def handle_post_tool(payload: dict) -> int:
    tool_name = str(payload.get("tool_name", "")).strip()
    short_name = tool_name.rsplit("__", 1)[-1] if "__" in tool_name else tool_name
    state = _read_state()

    if short_name == HEARTBEAT_TOOL:
        state["user_msgs"] = 0
        state["last_heartbeat_ts"] = time.time()
        _write_state(state)
        return 0

    if short_name in SKIP_TOOLS:
        return 0

    user_msgs = state.get("user_msgs", 0)
    if user_msgs > THRESHOLD:
        print(
            f"\nWARNING: HEARTBEAT OVERDUE ({user_msgs} user messages without nexo_heartbeat). "
            "Call nexo_heartbeat(sid=SID, task='...') before continuing."
        )
    return 0


def main() -> int:
    mode = os.environ.get("HEARTBEAT_MODE", "").strip()
    if mode == "user_msg":
        return handle_user_message()
    if mode == "post_tool":
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        try:
            payload = json.loads(raw)
        except Exception:
            return 0
        return handle_post_tool(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
