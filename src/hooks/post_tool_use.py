#!/usr/bin/env python3
"""PostToolUse unified handler.

Runs the five shell scripts that used to be registered individually for this
event (capture-tool-logs, capture-session, inbox-hook, protocol-guardrail,
heartbeat-posttool). Also pipes the tool result through auto_capture.py so
decision/correction/explicit facts from tool outputs reach the cognitive
layer.

v6.0.1 adds an inbox-autodetect stage at the end: when the session has
unread ``nexo_send`` messages AND has gone for ≥60s without a heartbeat,
the hook emits a ``systemMessage`` telling the agent to run
``nexo_heartbeat`` and pick them up. Rate-limited to one reminder per
minute per SID via the ``hook_inbox_reminders`` table (migration m42).

Failures in one sub-step do not cancel the others. Hook is best-effort;
exit code is always 0 so Claude Code never sees a PostToolUse failure.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


_DIR = Path(__file__).resolve().parent
_NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))

INBOX_CHECK_THRESHOLD_SECONDS = int(
    os.environ.get("NEXO_INBOX_CHECK_THRESHOLD_SECONDS", "60")
)


def _resolve_sid_from_payload(payload: dict) -> str:
    """Resolve the NEXO SID from the hook payload or fall back to env.

    Claude Code delivers its own ``session_id`` in the payload; we map
    it back to our SID via ``sessions.external_session_id``. The
    fallback is ``NEXO_SID`` in the environment, which headless crons
    export directly.
    """
    candidates: list[str] = []
    if isinstance(payload, dict):
        for key in ("nexo_sid", "sid", "session_id", "sessionId"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
    env_sid = os.environ.get("NEXO_SID", "").strip()
    if env_sid:
        candidates.append(env_sid)
    env_claude = os.environ.get("CLAUDE_SESSION_ID", "").strip()
    if env_claude:
        candidates.append(env_claude)

    # Try each candidate: first as a NEXO-shaped SID (nexo-<epoch>-<pid>),
    # then as a Claude external id we need to translate.
    try:
        sys.path.insert(0, str(_DIR.parent))
        from db import (  # type: ignore
            resolve_sid_from_external,
            get_last_heartbeat_ts,
        )
    except Exception:
        return ""

    for cand in candidates:
        if cand.startswith("nexo-"):
            return cand
        resolved = resolve_sid_from_external(cand)
        if resolved:
            return resolved
    return ""


def check_inbox_and_emit_reminder(sid: str, now: float | None = None) -> str | None:
    """Return the systemMessage string when a reminder should be surfaced.

    Returns ``None`` when any gate fails (no sid, no pending messages,
    heartbeat too recent, rate-limited on reminders).
    """
    if not sid:
        return None
    try:
        sys.path.insert(0, str(_DIR.parent))
        from db import (  # type: ignore
            count_pending_inbox_messages,
            get_last_heartbeat_ts,
            get_last_reminder_ts,
            mark_reminder_sent,
        )
    except Exception:
        return None

    pending = count_pending_inbox_messages(sid)
    if pending <= 0:
        return None
    last_hb = get_last_heartbeat_ts(sid)
    if last_hb is None:
        return None  # pre-v6.0.1 row or brand-new session
    current = float(now) if now is not None else time.time()
    if current - last_hb < INBOX_CHECK_THRESHOLD_SECONDS:
        return None
    last_rem = get_last_reminder_ts(sid) or 0.0
    if current - last_rem < INBOX_CHECK_THRESHOLD_SECONDS:
        return None  # rate limit: max 1 reminder/min/session
    mark_reminder_sent(sid, current)
    return (
        f"[NEXO Protocol Enforcer] You have {pending} unread inbox message(s) "
        f"sent by other NEXO sessions. Run nexo_heartbeat with your SID now "
        f"to receive them before continuing — other sessions may be blocked "
        f"waiting on your response."
    )


def _record(duration_ms: int, exit_code: int, summary: str) -> None:
    try:
        sys.path.insert(0, str(_DIR.parent))
        import hook_observability  # type: ignore
        hook_observability.record_hook_run(
            "post_tool_use",
            duration_ms=duration_ms,
            exit_code=exit_code,
            summary=summary,
            session_id=os.environ.get("CLAUDE_SESSION_ID", ""),
        )
    except Exception:
        pass


def _run(cmd: list[str], timeout: int) -> int:
    try:
        return subprocess.run(cmd, timeout=timeout, capture_output=True).returncode
    except Exception:
        return 1


def _read_stdin_json() -> dict:
    """Read the Claude Code hook payload from stdin. Never raises."""
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except Exception:
        return {}


def _extract_tool_text(payload: dict) -> str:
    """Pull the bit we actually care about from the tool result envelope."""
    if not isinstance(payload, dict):
        return ""
    result = payload.get("tool_result") or payload.get("result") or {}
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content") or result.get("output") or result.get("text") or ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                str(item.get("text", "")) if isinstance(item, dict) else str(item)
                for item in content
            )
    return ""


def _run_auto_capture(payload: dict) -> int:
    """Pipe the tool result into auto_capture for post-output classification."""
    text = _extract_tool_text(payload)
    if not text or len(text) < 15:
        return 0
    try:
        proc = subprocess.run(
            ["python3", str(_DIR / "auto_capture.py")],
            input=text,
            capture_output=True,
            text=True,
            timeout=4,
        )
        return proc.returncode
    except Exception:
        return 1


def main() -> int:
    started = time.time()
    payload = _read_stdin_json()

    steps = [
        (["bash", str(_DIR / "capture-tool-logs.sh")], 5),
        (["bash", str(_DIR / "capture-session.sh")],   3),
        (["bash", str(_DIR / "inbox-hook.sh")],        5),
        (["bash", str(_DIR / "protocol-guardrail.sh")],5),
        (["bash", str(_DIR / "heartbeat-posttool.sh")],3),
    ]
    exits = []
    for cmd, timeout in steps:
        script = Path(cmd[-1])
        if not script.is_file():
            continue
        exits.append(_run(cmd, timeout))

    exits.append(_run_auto_capture(payload))

    # v6.0.1 — inbox autodetect runs LAST so it sees the latest DB state
    # (including any writes the previous steps may have done). Emits a
    # single-line JSON systemMessage so Claude Code surfaces it to the
    # agent without breaking the tool pipeline.
    try:
        sid = _resolve_sid_from_payload(payload)
        reminder = check_inbox_and_emit_reminder(sid)
        if reminder:
            print(json.dumps({"systemMessage": reminder}))
    except Exception:
        pass

    final_exit = max(exits) if exits else 0
    duration_ms = int((time.time() - started) * 1000)
    _record(duration_ms, final_exit, f"steps={len(exits)}")
    return 0  # never block the tool pipeline


if __name__ == "__main__":
    sys.exit(main())
