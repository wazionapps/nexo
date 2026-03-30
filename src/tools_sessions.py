"""Session management tools: startup, heartbeat, status."""

import time
import secrets
import threading
from db import (
    register_session, update_session, complete_session,
    get_active_sessions, clean_stale_sessions, search_sessions,
    get_inbox, get_pending_questions, now_epoch,
    SESSION_STALE_SECONDS, check_session_has_diary,
    save_checkpoint, read_checkpoint, increment_compaction_count,
)

# ── Session Keepalive ────────────────────────────────────────────────
# Background thread per session that auto-pings last_update_epoch every
# KEEPALIVE_INTERVAL seconds.  This prevents clean_stale_sessions from
# killing sessions that are alive but quiet (e.g. waiting on long Tasks).
# Threads are daemon=True so they die when the MCP server process exits.

KEEPALIVE_INTERVAL = 600  # 10 min — well inside the 15-min TTL

_keepalive_threads: dict[str, threading.Event] = {}  # sid → stop_event


def _keepalive_loop(sid: str, stop_event: threading.Event) -> None:
    """Periodically touch the session's last_update_epoch until stopped."""
    while not stop_event.wait(KEEPALIVE_INTERVAL):
        try:
            update_session(sid, None)  # None = keep current task, just touch timestamp
        except Exception:
            break  # DB gone or session deleted — exit silently


def _start_keepalive(sid: str) -> None:
    """Start a keepalive thread for the given session."""
    _stop_keepalive(sid)  # clean up any leftover
    stop_event = threading.Event()
    _keepalive_threads[sid] = stop_event
    t = threading.Thread(target=_keepalive_loop, args=(sid, stop_event), daemon=True)
    t.start()


def _stop_keepalive(sid: str) -> None:
    """Signal the keepalive thread for the given session to stop."""
    stop_event = _keepalive_threads.pop(sid, None)
    if stop_event is not None:
        stop_event.set()


def _generate_sid() -> str:
    """Generate unique session ID: nexo-{epoch}-{random}."""
    return f"nexo-{int(time.time())}-{secrets.randbelow(100000)}"


def _format_age(epoch: float) -> str:
    """Format seconds since epoch as human-readable age."""
    seconds = now_epoch() - epoch
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds / 60)}m"
    else:
        return f"{int(seconds / 3600)}h{int((seconds % 3600) / 60)}m"


def handle_startup(task: str = "Startup", claude_session_id: str = "") -> str:
    """Full startup sequence: register, clean, report.

    Args:
        task: Initial task description
        claude_session_id: UUID from Claude Code (passed via SessionStart hook file).
                          Enables automatic inbox detection via PostToolUse hook.
    """
    sid = _generate_sid()
    cleaned = clean_stale_sessions()
    register_session(sid, task, claude_session_id=claude_session_id)
    _start_keepalive(sid)
    active = get_active_sessions()
    other_sessions = [s for s in active if s["sid"] != sid]
    inbox = get_inbox(sid)

    lines = [f"SID: {sid}"]

    if cleaned > 0:
        lines.append(f"Cleaned {cleaned} stale sessions.")

    if other_sessions:
        lines.append("")
        lines.append("ACTIVE SESSIONS:")
        for s in other_sessions:
            age = _format_age(s["last_update_epoch"])
            lines.append(f"  {s['sid']} ({age}) — {s['task']}")
    else:
        lines.append("No other active sessions.")

    if inbox:
        lines.append("")
        lines.append("PENDING MESSAGES:")
        for m in inbox:
            age = _format_age(m["created_epoch"])
            lines.append(f"  [{m['from_sid']}] ({age}): {m['text']}")

    return "\n".join(lines)


def handle_heartbeat(sid: str, task: str, context_hint: str = '') -> str:
    """Update session, check inbox + questions. Lightweight — no embeddings, no RAG.

    For cognitive features (sentiment, trust, RAG), use dedicated tools on-demand:
    - nexo_cognitive_sentiment (sentiment detection)
    - nexo_cognitive_trust (trust adjustment)
    - nexo_cognitive_retrieve / nexo_recall (memory retrieval)
    - nexo_context_packet (area-specific learnings)

    Args:
        sid: Session ID
        task: Current task description
        context_hint: Optional — stored for diary draft context, not processed.
    """
    from db import get_db
    update_session(sid, task)
    parts = [f"OK: {sid} — {task}"]

    inbox = get_inbox(sid)
    if inbox:
        parts.append("")
        parts.append("MESSAGES:")
        for m in inbox:
            age = _format_age(m["created_epoch"])
            parts.append(f"  [{m['from_sid']}] ({age}): {m['text']}")

    questions = get_pending_questions(sid)
    if questions:
        parts.append("")
        parts.append("PENDING QUESTIONS (respond with nexo_answer):")
        for q in questions:
            age = _format_age(q["created_epoch"])
            parts.append(f"  {q['qid']} de {q['from_sid']} ({age}): {q['question']}")

    # Incremental diary draft — accumulate every heartbeat, full UPSERT every 5
    try:
        import json as _json
        from db import get_diary_draft, upsert_diary_draft

        draft = get_diary_draft(sid)
        hb_count = (draft["heartbeat_count"] + 1) if draft else 1

        existing_tasks = _json.loads(draft["tasks_seen"]) if draft else []
        if task and task not in existing_tasks:
            existing_tasks.append(task)

        _conn = get_db()
        if hb_count % 5 == 0 or hb_count == 1:
            change_rows = _conn.execute(
                "SELECT id FROM change_log WHERE session_id = ? ORDER BY id", (sid,)
            ).fetchall()
            change_ids = [r["id"] for r in change_rows]

            decision_rows = _conn.execute(
                "SELECT id FROM decisions WHERE session_id = ? ORDER BY id", (sid,)
            ).fetchall()
            decision_ids = [r["id"] for r in decision_rows]

            summary = f"Session tasks: {', '.join(existing_tasks[-10:])}"
            upsert_diary_draft(
                sid=sid,
                tasks_seen=_json.dumps(existing_tasks),
                change_ids=_json.dumps(change_ids),
                decision_ids=_json.dumps(decision_ids),
                last_context_hint=context_hint[:300] if context_hint else '',
                heartbeat_count=hb_count,
                summary_draft=summary,
            )
        else:
            upsert_diary_draft(
                sid=sid,
                tasks_seen=_json.dumps(existing_tasks),
                change_ids=draft["change_ids"] if draft else '[]',
                decision_ids=draft["decision_ids"] if draft else '[]',
                last_context_hint=context_hint[:300] if context_hint else (draft["last_context_hint"] if draft else ''),
                heartbeat_count=hb_count,
                summary_draft=draft["summary_draft"] if draft else f"Session task: {task}",
            )
    except Exception:
        pass  # Draft accumulation is best-effort, never block heartbeat

    # Update session checkpoint with current goal (lightweight, every heartbeat)
    try:
        save_checkpoint(
            sid=sid,
            task=task,
            current_goal=context_hint[:300] if context_hint else task,
        )
    except Exception:
        pass  # Checkpoint update is best-effort

    # Diary reminder: after 30 min active with no diary entry
    conn = get_db()
    row = conn.execute("SELECT started_epoch FROM sessions WHERE sid = ?", (sid,)).fetchone()
    if row:
        age_seconds = now_epoch() - row["started_epoch"]
        if age_seconds >= 1800 and not check_session_has_diary(sid):
            parts.append("")
            parts.append("⚠ DIARY REMINDER: Session active 30+ min without diary. Write nexo_session_diary_write before closing.")

    return "\n".join(parts)


def handle_context_packet(area: str, files: str = "") -> str:
    """Build a context packet for a specific area/project — designed for subagent injection.

    Returns: relevant learnings + last 5 changes + active followups + key preferences
    for the given area. Use this before delegating to a subagent.

    Args:
        area: Project/area name (e.g., 'ecommerce', 'shopify', 'backend', 'mobile-app', 'nexo')
        files: Optional comma-separated file paths for guard check
    """
    from db import get_db
    parts = []

    # 1. Learnings for this area (from nexo.db)
    conn = get_db()
    learnings = conn.execute(
        "SELECT id, title, content FROM learnings WHERE category LIKE ? OR content LIKE ? ORDER BY id DESC LIMIT 15",
        (f"%{area}%", f"%{area}%")
    ).fetchall()
    if learnings:
        parts.append("## KNOWN ERRORS — DO NOT REPEAT")
        for l in learnings:
            parts.append(f"  L#{l['id']}: {l['title']}")
            # First 200 chars of content
            parts.append(f"    {l['content'][:200]}")
        parts.append("")

    # 2. Last 5 changes in this area
    changes = conn.execute(
        "SELECT id, files, what_changed, why FROM change_log WHERE files LIKE ? OR what_changed LIKE ? ORDER BY id DESC LIMIT 5",
        (f"%{area}%", f"%{area}%")
    ).fetchall()
    if changes:
        parts.append("## RECENT CHANGES")
        for c in changes:
            parts.append(f"  C#{c['id']}: {c['what_changed'][:150]}")
            if c['why']:
                parts.append(f"    Why: {c['why'][:100]}")
        parts.append("")

    # 3. Active followups for this area
    followups = conn.execute(
        "SELECT id, description, date, verification FROM followups WHERE status = 'PENDING' AND (description LIKE ? OR verification LIKE ?) ORDER BY date ASC LIMIT 10",
        (f"%{area}%", f"%{area}%")
    ).fetchall()
    if followups:
        parts.append("## ACTIVE FOLLOWUPS")
        for f in followups:
            parts.append(f"  {f['id']}: {f['description'][:150]} (date: {f['date']})")
        parts.append("")

    # 4. Preferences related to this area
    try:
        prefs = conn.execute(
            "SELECT key, value FROM preferences WHERE key LIKE ? OR value LIKE ? LIMIT 10",
            (f"%{area}%", f"%{area}%")
        ).fetchall()
        if prefs:
            parts.append("## PREFERENCES")
            for p in prefs:
                parts.append(f"  {p['key']}: {p['value'][:150]}")
            parts.append("")
    except Exception:
        pass

    # 5. Cognitive memories for this area
    try:
        import cognitive
        results = cognitive.search(
            query_text=area,
            top_k=5,
            min_score=0.55,
            stores="ltm",
            rehearse=False,
        )
        if results:
            parts.append("## RELEVANT COGNITIVE MEMORIES")
            for r in results:
                parts.append(f"  [{r['source_type']}] {r['source_title'] or r['content'][:80]}")
            parts.append("")
    except Exception:
        pass

    # 6. Data flow tracing requirement (mandatory for all subagents)
    parts.append("## MANDATORY RULE: DATA FLOW TRACING")
    parts.append("BEFORE modifying any file or data, answer these 3 questions:")
    parts.append("  1. WHO PRODUCES this data? (which function/cron/endpoint generates it)")
    parts.append("  2. WHO CONSUMES this data? (what other files/functions read it)")
    parts.append("  3. WHAT BREAKS if I change it? (downstream effects)")
    parts.append("If you can't answer all 3 → READ the code that produces and consumes BEFORE touching.")
    parts.append("If you still can't → STOP and return the question. Do NOT guess.")
    parts.append("")

    if not parts:
        return f"No context found for area '{area}'. The subagent will start with no project-specific knowledge."

    header = f"CONTEXT PACKET — {area.upper()}\n{'='*40}\n\n"
    footer = f"\n{'='*40}\nINSTRUCTION: If you're not 100% sure about a fact, STOP and return the question. Do NOT invent."
    return header + "\n".join(parts) + footer


def handle_smart_startup_query() -> str:
    """Generate and execute a composite cognitive query from pending followups + diary topics + reminders.

    Called during startup to pre-load the most relevant context for this session.
    Returns cognitive memories that match the current operational state.
    """
    from db import get_db
    conn = get_db()
    query_parts = []

    # 1. Pending followups (what NEXO needs to do)
    followups = conn.execute(
        "SELECT description FROM followups WHERE status = 'PENDING' ORDER BY date ASC LIMIT 5"
    ).fetchall()
    for f in followups:
        query_parts.append(f['description'][:100])

    # 2. Due reminders (what Francisco needs to know)
    reminders = conn.execute(
        "SELECT description FROM reminders WHERE status = 'PENDING' AND date <= date('now', '+1 day') ORDER BY date ASC LIMIT 5"
    ).fetchall()
    for r in reminders:
        query_parts.append(r['description'][:100])

    # 3. Last session diary topics
    try:
        last_diary = conn.execute(
            "SELECT summary FROM session_diary ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last_diary and last_diary['summary']:
            query_parts.append(last_diary['summary'][:200])
    except Exception:
        pass

    if not query_parts:
        return "No pending context to pre-load."

    # Search per-part to avoid diffuse centroid that matches everything
    try:
        import cognitive
        all_results = []
        seen_ids = set()
        for part in query_parts[:6]:
            part_results = cognitive.search(
                query_text=part,
                top_k=3,
                min_score=0.6,
                stores="both",
                rehearse=False,  # Don't inflate strength on startup
            )
            for r in part_results:
                key = (r["store"], r["id"])
                if key not in seen_ids:
                    seen_ids.add(key)
                    all_results.append(r)
        # Sort by score descending, take top 10
        results = sorted(all_results, key=lambda x: x["score"], reverse=True)[:10]
        composite_query = " | ".join(query_parts[:6])
        if not results:
            return "Smart startup query: no relevant memories found."

        lines = [f"SMART STARTUP — {len(results)} memories pre-loaded from composite query:"]
        lines.append(f"Query: {composite_query[:200]}...")
        lines.append("")
        lines.append(cognitive.format_results(results))
        return "\n".join(lines)
    except Exception as e:
        return f"Smart startup query error: {e}"


def handle_stop(sid: str) -> str:
    """Cleanly close a session, removing it from active sessions immediately."""
    _stop_keepalive(sid)
    complete_session(sid)
    return f"Session {sid} closed."


def handle_status(keyword: str | None = None) -> str:
    """List active sessions, optionally filtered by keyword."""
    clean_stale_sessions()
    if keyword:
        sessions = search_sessions(keyword)
        if not sessions:
            return f"Nobody is working on '{keyword}'."
    else:
        sessions = get_active_sessions()

    if not sessions:
        return "No active sessions."

    lines = ["ACTIVE SESSIONS:"]
    for s in sessions:
        age = _format_age(s["last_update_epoch"])
        lines.append(f"  {s['sid']} ({age}) — {s['task']}")
    return "\n".join(lines)
