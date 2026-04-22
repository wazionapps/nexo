#!/usr/bin/env python3
"""PreToolUse unified handler.

v7.3.0 wires the Block K Guardian gates (G3 destructive + G3 SSH + G4
guard_check + conditioned-file blocks + automation live-repo guard)
into Claude Code's PreToolUse event. Without this handler, the
``process_pre_tool_event`` logic in ``hook_guardrails.py`` was code
that never ran in production — the post-v7.2.0 bug Francisco found
on 2026-04-22.

Responsibility:
    - Read the hook payload from stdin.
    - Resolve the NEXO sid from the payload / env.
    - Delegate to ``hook_guardrails.process_pre_tool_event``.
    - If the result carries ``status == "blocked"`` AND at least one
      block has severity error (hard mode), emit a PreToolUse denial
      response so Claude Code refuses to execute the tool.
    - Otherwise exit cleanly.

The hook NEVER crashes the tool pipeline: any exception drops to a
safe no-op return and the tool is allowed (fail-open for robustness;
a hard denial requires a successful evaluation that saw a hard block).
Observability hooks still record the run via ``hook_observability``.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


_DIR = Path(__file__).resolve().parent
if str(_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_DIR.parent))


def _read_stdin_json() -> dict:
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except Exception:
        return {}


def _record(duration_ms: int, exit_code: int, summary: str) -> None:
    try:
        sys.path.insert(0, str(_DIR.parent))
        import hook_observability  # type: ignore
        hook_observability.record_hook_run(
            "pre_tool_use",
            duration_ms=duration_ms,
            exit_code=exit_code,
            summary=summary,
            session_id=os.environ.get("CLAUDE_SESSION_ID", ""),
        )
    except Exception:
        pass


def _format_block_reason(result: dict) -> str:
    """Build a human-readable reason for the deny response."""
    blocks = result.get("blocks") or []
    if not isinstance(blocks, list) or not blocks:
        return "Guardian: tool execution blocked by a hard-mode gate."
    first = blocks[0] if isinstance(blocks[0], dict) else {}
    reason_code = str(first.get("reason_code") or first.get("debt_type") or "")
    pattern = str(first.get("pattern") or "")
    file_token = str(first.get("file") or "")
    severity = str(first.get("severity") or "")
    parts = ["Guardian gate blocked this tool call"]
    if reason_code:
        parts.append(f"reason={reason_code}")
    if pattern:
        parts.append(f"pattern={pattern}")
    if file_token:
        parts.append(f"file={file_token}")
    if severity:
        parts.append(f"severity={severity}")
    tail = " | ".join(parts)
    tail += (
        ". Run nexo_guard_check and nexo_task_open + nexo_cortex_decide "
        "with explicit evidence before retrying. "
        "Override per-gate at operator's risk: export NEXO_<GATE>=shadow."
    )
    return tail


def _has_hard_block(result: dict) -> bool:
    if not isinstance(result, dict):
        return False
    if str(result.get("status") or "") != "blocked":
        return False
    blocks = result.get("blocks") or []
    if not isinstance(blocks, list):
        return False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        severity = str(block.get("severity") or "").lower()
        if severity == "error":
            return True
    return False


def main() -> int:
    started = time.time()
    payload = _read_stdin_json()
    exit_code = 0
    summary = "skipped"

    try:
        sys.path.insert(0, str(_DIR.parent))
        from hook_guardrails import process_pre_tool_event  # type: ignore
        result = process_pre_tool_event(payload)
    except Exception as exc:
        # Fail-open: never block the tool pipeline on an internal hook
        # crash. Observability still records the error.
        summary = f"error:{exc.__class__.__name__}"
        result = {}

    if _has_hard_block(result):
        reason = _format_block_reason(result)
        response = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            },
        }
        try:
            print(json.dumps(response))
        except Exception:
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Guardian gate blocked this tool call.",
                },
            }))
        summary = "blocked"
        exit_code = 0  # JSON response is the canonical path; non-zero is redundant

    elif isinstance(result, dict) and result.get("skipped"):
        summary = f"skipped:{result.get('reason', '')[:40]}"
    elif isinstance(result, dict) and str(result.get("status") or "") == "blocked":
        # Shadow-mode block: debt recorded but tool allowed.
        summary = "shadow_debt"

    duration_ms = int((time.time() - started) * 1000)
    _record(duration_ms, exit_code, summary)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
