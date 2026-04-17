#!/usr/bin/env python3
"""Notification hook — records live-session activity.

Claude Code fires a Notification hook every time the agent surfaces a notice
to the user (permission prompts, token-limit warnings, plugin alerts). For
NEXO this is a cheap signal that the session is alive — exactly what the
auto_close_sessions cron needs to avoid marking a busy session as stale.

Reads the JSON hook payload from stdin, records an activity row via
hook_observability.record_activity(), exits 0 regardless of outcome so the
notification pipeline is never blocked by us.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


_DIR = Path(__file__).resolve().parent


def _read_stdin_json() -> dict:
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _session_id(payload: dict) -> str:
    if isinstance(payload, dict):
        for key in ("session_id", "sessionId", "sid"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return os.environ.get("CLAUDE_SESSION_ID", "")


def main() -> int:
    started = time.time()
    payload = _read_stdin_json()
    session_id = _session_id(payload)

    try:
        sys.path.insert(0, str(_DIR.parent))
        import hook_observability  # type: ignore
        hook_observability.record_activity(
            session_id=session_id,
            activity_type="notification",
        )
    except Exception:
        pass

    # Also record the hook itself so we can see Notification coverage in the
    # hook_runs table, parallel to the other hooks.
    try:
        sys.path.insert(0, str(_DIR.parent))
        import hook_observability  # type: ignore
        hook_observability.record_hook_run(
            "notification",
            duration_ms=int((time.time() - started) * 1000),
            exit_code=0,
            session_id=session_id,
            summary="activity recorded",
        )
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
