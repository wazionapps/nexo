#!/usr/bin/env python3
"""Auto-close orphan sessions and promote diary drafts.

Runs every 5 minutes via LaunchAgent (com.nexo.auto-close-sessions).
Finds sessions that exceeded TTL without a diary and promotes their
draft to a real diary entry marked as source=auto-close.
"""

import json
import os
import sys
import datetime
from pathlib import Path

# Ensure imports work both from ``src/auto_close_sessions.py`` and from the
# packaged runtime copy under ``core/scripts/auto_close_sessions.py``.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_IMPORT_ROOTS = [_THIS_DIR]
if os.path.basename(_THIS_DIR) == "scripts":
    _IMPORT_ROOTS.append(os.path.dirname(_THIS_DIR))
for _candidate in _IMPORT_ROOTS:
    if _candidate and _candidate not in sys.path:
        sys.path.insert(0, _candidate)
os.environ["NEXO_SKIP_FS_INDEX"] = "1"  # Skip FTS rebuild on import

from db import (
    init_db, get_db, get_diary_draft, delete_diary_draft,
    get_orphan_sessions, read_checkpoint, write_session_diary, now_epoch,
    SESSION_STALE_SECONDS,
)
try:
    import paths
except ModuleNotFoundError as exc:
    if getattr(exc, "name", "") != "paths":
        raise

    class _PathsFallback:
        @staticmethod
        def operations_dir():
            return Path(os.environ.get("NEXO_HOME", os.path.expanduser("~/.nexo"))) / "operations"

        @staticmethod
        def coordination_dir():
            return Path(os.environ.get("NEXO_HOME", os.path.expanduser("~/.nexo"))) / "coordination"

    paths = _PathsFallback()

NEXO_HOME = os.environ.get("NEXO_HOME", os.path.expanduser("~/.nexo"))
LOG_DIR = str(paths.operations_dir() / "tool-logs")
AUTO_CLOSE_LOG = str(paths.coordination_dir() / "auto-close.log")


def get_tool_log_summary(sid: str) -> str:
    """Extract tool names from today's tool log for this session."""
    today = datetime.date.today().isoformat()
    log_path = os.path.join(LOG_DIR, f"{today}.jsonl")
    if not os.path.exists(log_path):
        return ""

    tools = []
    try:
        with open(log_path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if entry.get("session_id") == sid:
                        tool = entry.get("tool_name", "")
                        if tool and tool not in ("Read", "Grep", "Glob"):
                            tools.append(tool)
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass

    if tools:
        seen = set()
        unique = []
        for t in tools:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return f"Tools used: {', '.join(unique[-15:])}"
    return ""


def promote_draft_to_diary(sid: str, draft: dict, task: str = ""):
    """Promote a diary draft to a real session diary entry."""
    tasks = json.loads(draft.get("tasks_seen", "[]"))
    change_ids = json.loads(draft.get("change_ids", "[]"))
    decision_ids = json.loads(draft.get("decision_ids", "[]"))
    context_hint = draft.get("last_context_hint", "")
    hb_count = draft.get("heartbeat_count", 0)

    checkpoint = read_checkpoint(sid) or {}
    summary_parts = []
    if draft.get("summary_draft"):
        summary_parts.append(draft["summary_draft"])
    if task and task not in " ".join(summary_parts):
        summary_parts.append(f"Final task: {task}")
    if context_hint:
        summary_parts.append(f"Latest context: {context_hint[:300]}")
    if checkpoint.get("current_goal"):
        summary_parts.append(f"Current goal: {str(checkpoint['current_goal'])[:300]}")
    if checkpoint.get("next_step"):
        summary_parts.append(f"Next step was: {str(checkpoint['next_step'])[:240]}")

    tool_summary = get_tool_log_summary(sid)
    if tool_summary:
        summary_parts.append(tool_summary)

    summary = " | ".join(summary_parts) if summary_parts else f"Auto-closed session ({hb_count} heartbeats)"

    # Build decisions from actual decision records
    decisions_text = ""
    if decision_ids:
        conn = get_db()
        placeholders = ",".join("?" * len(decision_ids))
        rows = conn.execute(
            f"SELECT id, decision, domain FROM decisions WHERE id IN ({placeholders})",
            decision_ids
        ).fetchall()
        if rows:
            decisions_text = json.dumps([
                {"id": r["id"], "decision": r["decision"][:100], "domain": r["domain"]}
                for r in rows
            ])

    # Build context_next
    context_next = ""
    if context_hint:
        context_next = f"Last topic: {context_hint}"
    if tasks:
        context_next += f" | Tasks: {', '.join(tasks[-5:])}"
    if checkpoint.get("reasoning_thread"):
        context_next += f" | Reasoning: {str(checkpoint['reasoning_thread'])[:240]}"
    if checkpoint.get("active_files"):
        context_next += f" | Active files: {str(checkpoint['active_files'])[:180]}"

    write_session_diary(
        session_id=sid,
        decisions=decisions_text or "No decisions logged",
        summary=summary,
        discarded="",
        pending=f"Changes: {change_ids}" if change_ids else "",
        context_next=context_next,
        mental_state=f"[auto-close] Session ended without explicit diary. Draft promoted. {hb_count} heartbeats recorded.",
        domain="",
        user_signals="",
        self_critique="[auto-close] No self-critique available — session terminated without cleanup.",
        source="auto-close",
    )
    delete_diary_draft(sid)


def auto_close_open_protocol_tasks(conn, sid: str, task: str = "") -> list[str]:
    """Close stale open protocol tasks as partial when their session is reaped."""
    rows = conn.execute(
        """SELECT task_id, goal
           FROM protocol_tasks
           WHERE session_id = ? AND status = 'open'
           ORDER BY opened_at ASC""",
        (sid,),
    ).fetchall()
    closed: list[str] = []
    for row in rows:
        task_id = row["task_id"]
        goal = str(row["goal"] or "")
        evidence = (
            f"Auto-closed as partial because session {sid} became stale before an explicit nexo_task_close. "
            f"Session task: {task or 'unknown'}. Open goal: {goal[:240]}"
        )
        conn.execute(
            """UPDATE protocol_tasks
               SET status = 'partial',
                   close_evidence = ?,
                   outcome_notes = 'auto-close: stale session ended without explicit task_close',
                   closed_at = datetime('now')
               WHERE task_id = ? AND status = 'open'""",
            (evidence[:4000], task_id),
        )
        closed.append(task_id)
    return closed


def auto_close_abandoned_workflow_runs(conn, sid: str) -> dict:
    """Reap durable workflow_runs / workflow_goals abandoned by a stale session.

    auto_close only reaped protocol_tasks; a session that opened a durable
    workflow_run / workflow_goal and never closed it left a zombie 'running'
    row forever, polluting the resume surface (M10 gap). Move non-terminal ones
    to a terminal state when their owning session is reaped. closed_at/updated_at
    use datetime('now') to match the workflow tables' timestamp format.
    """
    note = "auto-close: stale session ended without explicit workflow close"
    runs = conn.execute(
        "SELECT run_id FROM workflow_runs "
        "WHERE session_id = ? AND status IN ('open','running','blocked','waiting_approval')",
        (sid,),
    ).fetchall()
    for row in runs:
        conn.execute(
            "UPDATE workflow_runs SET status='cancelled', next_action=?, "
            "closed_at=datetime('now'), updated_at=datetime('now') "
            "WHERE run_id=? AND status IN ('open','running','blocked','waiting_approval')",
            (note, row["run_id"]),
        )
    goals = conn.execute(
        "SELECT goal_id FROM workflow_goals "
        "WHERE session_id = ? AND status IN ('active','blocked')",
        (sid,),
    ).fetchall()
    for row in goals:
        conn.execute(
            "UPDATE workflow_goals SET status='abandoned', blocker_reason=?, "
            "closed_at=datetime('now'), updated_at=datetime('now') "
            "WHERE goal_id=? AND status IN ('active','blocked')",
            (note, row["goal_id"]),
        )
    return {"runs": len(runs), "goals": len(goals)}


def main():
    init_db()
    conn = get_db()

    orphans = get_orphan_sessions(SESSION_STALE_SECONDS)
    if not orphans:
        print(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] No stale sessions")
        return

    closed_task_ids: list[str] = []
    for session in orphans:
        sid = session["sid"]
        draft = get_diary_draft(sid)
        closed_tasks = auto_close_open_protocol_tasks(conn, sid, task=session.get("task", ""))
        closed_task_ids.extend(closed_tasks)
        auto_close_abandoned_workflow_runs(conn, sid)

        if draft:
            promote_draft_to_diary(sid, draft, task=session.get("task", ""))
        else:
            checkpoint = read_checkpoint(sid) or {}
            tool_summary = get_tool_log_summary(sid)
            summary_parts = [f"Auto-closed session. Task: {session.get('task', 'unknown')}"]
            if checkpoint.get("current_goal"):
                summary_parts.append(f"Current goal: {str(checkpoint['current_goal'])[:300]}")
            if tool_summary:
                summary_parts.append(tool_summary)
            write_session_diary(
                session_id=sid,
                decisions="No decisions logged",
                summary=" | ".join(summary_parts),
                context_next=str(checkpoint.get("next_step") or ""),
                mental_state="[auto-close] No draft available. Diary reconstructed from task/checkpoint/tool logs.",
                self_critique="[auto-close] Session terminated without diary or draft.",
                source="auto-close",
            )

        # Clean up the session
        conn.execute("DELETE FROM tracked_files WHERE sid = ?", (sid,))
        conn.execute("DELETE FROM sessions WHERE sid = ?", (sid,))
        conn.execute("DELETE FROM session_diary_draft WHERE sid = ?", (sid,))

    conn.commit()

    # Log what we did
    os.makedirs(os.path.dirname(AUTO_CLOSE_LOG), exist_ok=True)
    with open(AUTO_CLOSE_LOG, "a") as f:
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        f.write(
            f"{ts} — auto-closed {len(orphans)} session(s): {[s['sid'] for s in orphans]} "
            f"and {len(closed_task_ids)} protocol task(s): {closed_task_ids}\n"
        )


if __name__ == "__main__":
    main()
