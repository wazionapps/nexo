#!/usr/bin/env python3
"""SubagentStop hook — auto-closes protocol_tasks the subagent opened.

A subagent is dispatched with the Agent tool, inherits the parent session's
SID, and may open protocol_tasks via nexo_task_open. If the subagent
terminates without calling nexo_task_close (the common failure mode) its
tasks would stay ``status='open'`` forever, distorting every "open tasks"
dashboard and preventing the protocol debt ledger from draining.

This hook reads the JSON payload Claude Code delivers when a subagent
stops, extracts ``session_id`` and ``subagent_id``/``agent_id``, and closes
every matching open task with outcome='done' and a clear note explaining
it was auto-closed on SubagentStop. Idempotent — a second delivery finds
no open rows and is a no-op.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


_DIR = Path(__file__).resolve().parent


def _read_stdin_json() -> dict:
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _extract_ids(payload: Any) -> tuple[str, str]:
    """Return (session_id, subagent_id), both possibly empty."""
    if not isinstance(payload, dict):
        return "", ""
    session_id = ""
    subagent_id = ""
    for key in ("session_id", "sessionId", "sid"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            session_id = value.strip()
            break
    for key in ("subagent_id", "subagentId", "agent_id", "agentId"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            subagent_id = value.strip()
            break
    if not session_id:
        session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    return session_id, subagent_id


def _close_open_tasks(session_id: str, subagent_id: str) -> int:
    """Close every open protocol_task tied to this subagent. Returns count closed."""
    if not session_id:
        return 0
    try:
        sys.path.insert(0, str(_DIR.parent))
        from db import get_db  # type: ignore
    except Exception:
        return 0

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    note = "auto-closed on SubagentStop hook (subagent finished without explicit task_close)"

    try:
        conn = get_db()
        # Detect which columns actually exist so this hook survives schema drift.
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(protocol_tasks)").fetchall()
        }
        where = ["status = 'open'", "session_id = ?"]
        params: list[Any] = [session_id]
        if subagent_id and "subagent_origin" in columns:
            where.append("subagent_origin = ?")
            params.append(subagent_id)
        where_sql = " AND ".join(where)

        rows = conn.execute(
            f"SELECT id FROM protocol_tasks WHERE {where_sql}", params
        ).fetchall()
        task_ids = [row["id"] for row in rows]

        if not task_ids:
            return 0

        set_parts = ["status = 'closed'", "outcome = 'done'"]
        update_params: list[Any] = []
        if "outcome_notes" in columns:
            set_parts.append("outcome_notes = ?")
            update_params.append(note)
        if "closed_at" in columns:
            set_parts.append("closed_at = ?")
            update_params.append(now_iso)
        set_sql = ", ".join(set_parts)

        placeholders = ",".join("?" for _ in task_ids)
        conn.execute(
            f"UPDATE protocol_tasks SET {set_sql} WHERE id IN ({placeholders})",
            update_params + task_ids,
        )
        try:
            conn.commit()
        except Exception:
            pass
        return len(task_ids)
    except Exception:
        return 0


def _record(duration_ms: int, closed: int, session_id: str) -> None:
    try:
        sys.path.insert(0, str(_DIR.parent))
        import hook_observability  # type: ignore
        hook_observability.record_hook_run(
            "subagent_stop",
            duration_ms=duration_ms,
            exit_code=0,
            session_id=session_id,
            summary=f"auto_closed={closed}",
        )
    except Exception:
        pass


def main() -> int:
    started = time.time()
    payload = _read_stdin_json()
    session_id, subagent_id = _extract_ids(payload)
    closed = _close_open_tasks(session_id, subagent_id)
    _record(int((time.time() - started) * 1000), closed, session_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
