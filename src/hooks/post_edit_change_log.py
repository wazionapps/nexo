#!/usr/bin/env python3
from __future__ import annotations

"""Best-effort PostToolUse change_log recorder for write tools.

This hook records file edit visibility directly in the DB layer. It never
calls MCP tools and never blocks the client pipeline.
"""

import json
import os
import sys
from pathlib import Path


_DIR = Path(__file__).resolve().parent
_SRC = _DIR.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _short_tool_name(tool_name: str) -> str:
    clean = str(tool_name or "").strip()
    return clean.rsplit("__", 1)[-1] if "__" in clean else clean


def _read_stdin_json() -> dict:
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _tool_input(payload: dict) -> dict:
    value = payload.get("tool_input") or payload.get("toolInput") or payload.get("input") or {}
    return value if isinstance(value, dict) else {}


def _extract_file_paths(payload: dict) -> list[str]:
    tool_input = _tool_input(payload)
    candidates: list[str] = []
    for key in ("file_path", "filePath", "path", "notebook_path", "notebookPath"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
    for key in ("file_paths", "filePaths", "paths"):
        value = tool_input.get(key)
        if isinstance(value, list):
            candidates.extend(str(item).strip() for item in value if str(item).strip())
    seen: set[str] = set()
    paths: list[str] = []
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        paths.append(item)
    return paths


def _resolve_sid_from_payload(payload: dict) -> str:
    candidates: list[str] = []
    for key in ("nexo_sid", "sid", "session_id", "sessionId"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
    for key in ("NEXO_SID", "CLAUDE_SESSION_ID"):
        value = os.environ.get(key, "").strip()
        if value:
            candidates.append(value)
    try:
        from db import resolve_sid_from_external
    except Exception:
        return ""
    for candidate in candidates:
        if candidate.startswith("nexo-"):
            return candidate
        resolved = resolve_sid_from_external(candidate)
        if resolved:
            return resolved
    return ""


def record_post_edit_change(payload: dict) -> dict:
    """Record a minimal change_log row for write-like tool payloads."""
    tool_name = _short_tool_name(str(payload.get("tool_name") or payload.get("toolName") or ""))
    if tool_name not in WRITE_TOOLS:
        return {"ok": True, "skipped": True, "reason": "tool_not_write"}
    if os.environ.get("NEXO_AUTO_CHANGE_LOG", "1").strip().lower() in {"0", "false", "no", "off"}:
        return {"ok": True, "skipped": True, "reason": "disabled"}

    paths = _extract_file_paths(payload)
    if not paths:
        return {"ok": True, "skipped": True, "reason": "missing_file_path"}

    try:
        from db import init_db, log_change, record_memory_event
    except Exception as exc:
        return {"ok": False, "error": f"db_import_failed: {exc}"}

    try:
        init_db()
        sid = _resolve_sid_from_payload(payload) or "unknown"
        files = ", ".join(paths)
        result = log_change(
            sid,
            files,
            f"Auto-recorded PostToolUse {tool_name} file edit",
            "PostToolUse observed a file write; recording traceability even if the agent forgets nexo_change_log.",
            triggered_by="post_edit_change_log.py",
            affects=files,
            risks="Automatic trace only; verify the actual diff and tests separately.",
            verify="Inspect git diff and run the relevant tests for the edited file.",
            commit_ref="",
        )
        if "error" in result:
            return {"ok": False, "error": result["error"]}
        memory_event = None
        try:
            memory_event = record_memory_event(
                event_type="tool_write",
                source_type="tool",
                source_id=str(payload.get("tool_use_id") or result.get("id") or ""),
                session_id=sid,
                external_session_id=str(payload.get("session_id") or ""),
                actor=sid,
                tool_name=tool_name,
                file_paths=paths,
                tool_input=_tool_input(payload),
                tool_output=payload.get("tool_response") or payload.get("tool_result") or payload.get("result") or "",
                raw_ref=f"change_log:{result.get('id')}",
                privacy_level="normal",
                confidence=1.0,
                metadata={
                    "change_log_id": result.get("id"),
                    "hook": "post_edit_change_log.py",
                    "summary": f"{tool_name} wrote {len(paths)} file(s): {', '.join(paths[:4])}",
                    "path_count": len(paths),
                },
                idempotency_key=f"tool_write:{payload.get('tool_use_id') or result.get('id')}",
            )
        except Exception as exc:
            memory_event = {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "change_log_id": result.get("id"),
            "files": paths,
            "memory_event": memory_event,
            "memory_event_ok": bool(memory_event and memory_event.get("ok")),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def main() -> int:
    record_post_edit_change(_read_stdin_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
