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

# Ensure we can import from nexo-mcp
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["NEXO_SKIP_FS_INDEX"] = "1"  # Skip FTS rebuild on import

from db import (
    init_db, get_db, get_diary_draft, delete_diary_draft,
    get_orphan_sessions, write_session_diary, now_epoch,
    SESSION_STALE_SECONDS,
)

LOG_DIR = os.path.expanduser("~/claude/operations/tool-logs")
AUTO_CLOSE_LOG = os.path.expanduser("~/claude/coordination/auto-close.log")


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

    summary_parts = []
    if draft.get("summary_draft"):
        summary_parts.append(draft["summary_draft"])

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


def main():
    init_db()
    conn = get_db()

    orphans = get_orphan_sessions(SESSION_STALE_SECONDS)
    if not orphans:
        return

    for session in orphans:
        sid = session["sid"]
        draft = get_diary_draft(sid)

        if draft:
            promote_draft_to_diary(sid, draft, task=session.get("task", ""))
        else:
            write_session_diary(
                session_id=sid,
                decisions="No decisions logged",
                summary=f"Auto-closed session. Task: {session.get('task', 'unknown')}",
                context_next="",
                mental_state="[auto-close] No draft available. Minimal diary.",
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
        f.write(f"{ts} — auto-closed {len(orphans)} session(s): {[s['sid'] for s in orphans]}\n")


if __name__ == "__main__":
    main()
