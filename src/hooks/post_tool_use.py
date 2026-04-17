#!/usr/bin/env python3
"""PostToolUse unified handler.

Runs the five shell scripts that used to be registered individually for this
event (capture-tool-logs, capture-session, inbox-hook, protocol-guardrail,
heartbeat-posttool). Also pipes the tool result through auto_capture.py so
decision/correction/explicit facts from tool outputs reach the cognitive
layer.

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

    final_exit = max(exits) if exits else 0
    duration_ms = int((time.time() - started) * 1000)
    _record(duration_ms, final_exit, f"steps={len(exits)}")
    return 0  # never block the tool pipeline


if __name__ == "__main__":
    sys.exit(main())
