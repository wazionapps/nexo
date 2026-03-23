"""Session management tools: startup, heartbeat, status."""

import time
import secrets
from db import (
    register_session, update_session, complete_session,
    get_active_sessions, clean_stale_sessions, search_sessions,
    get_inbox, get_pending_questions, now_epoch,
    SESSION_STALE_SECONDS, check_session_has_diary,
)


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


def handle_startup(task: str = "Startup") -> str:
    """Full startup sequence: register, clean, report."""
    sid = _generate_sid()
    cleaned = clean_stale_sessions()
    register_session(sid, task)
    active = get_active_sessions()
    other_sessions = [s for s in active if s["sid"] != sid]
    inbox = get_inbox(sid)

    lines = [f"SID: {sid}"]

    if cleaned > 0:
        lines.append(f"Limpiadas {cleaned} sesiones stale.")

    if other_sessions:
        lines.append("")
        lines.append("SESIONES ACTIVAS:")
        for s in other_sessions:
            age = _format_age(s["last_update_epoch"])
            lines.append(f"  {s['sid']} ({age}) — {s['task']}")
    else:
        lines.append("Sin otras sesiones activas.")

    if inbox:
        lines.append("")
        lines.append("MENSAJES PENDIENTES:")
        for m in inbox:
            age = _format_age(m["created_epoch"])
            lines.append(f"  [{m['from_sid']}] ({age}): {m['text']}")

    return "\n".join(lines)


def handle_heartbeat(sid: str, task: str, context_hint: str = '') -> str:
    """Update session, check inbox + questions. Optionally detect context shift and retrieve fresh memories.

    Args:
        sid: Session ID
        task: Current task description
        context_hint: Optional — last 2-3 sentences from user or current topic. If provided AND
                      it diverges from startup memories, returns fresh cognitive memories for the new context.
    """
    from db import get_db
    update_session(sid, task)
    parts = [f"OK: {sid} — {task}"]

    inbox = get_inbox(sid)
    if inbox:
        parts.append("")
        parts.append("MENSAJES:")
        for m in inbox:
            age = _format_age(m["created_epoch"])
            parts.append(f"  [{m['from_sid']}] ({age}): {m['text']}")

    questions = get_pending_questions(sid)
    if questions:
        parts.append("")
        parts.append("PREGUNTAS PENDIENTES (responder con nexo_answer):")
        for q in questions:
            age = _format_age(q["created_epoch"])
            parts.append(f"  {q['qid']} de {q['from_sid']} ({age}): {q['question']}")

    # Sentiment detection: analyze context_hint for user's mood
    if context_hint and len(context_hint.strip()) >= 10:
        try:
            import cognitive
            sentiment = cognitive.detect_sentiment(context_hint)
            if sentiment["sentiment"] != "neutral":
                parts.append("")
                parts.append(f"VIBE: {sentiment['sentiment'].upper()} (intensity: {sentiment['intensity']})")
                if sentiment["guidance"]:
                    parts.append(f"  {sentiment['guidance']}")
                cognitive.log_sentiment(context_hint)
        except Exception:
            pass

    # Mid-session RAG: if context_hint provided, check for context shift
    if context_hint and len(context_hint.strip()) >= 15:
        try:
            import cognitive
            # Get the last retrieval query to compare
            db_cog = cognitive._get_db()
            last_query = db_cog.execute(
                "SELECT query_text FROM retrieval_log ORDER BY id DESC LIMIT 1"
            ).fetchone()

            do_retrieve = True
            if last_query:
                # Compare current hint with last query — if similar (>0.7), skip
                hint_vec = cognitive.embed(context_hint[:300])
                last_vec = cognitive.embed(last_query[0][:300])
                similarity = cognitive.cosine_similarity(hint_vec, last_vec)
                if similarity > 0.7:
                    do_retrieve = False  # Same context, no need for fresh memories

            if do_retrieve:
                results = cognitive.search(
                    query_text=context_hint[:300],
                    top_k=5,
                    min_score=0.55,
                    stores="both",
                    exclude_dormant=False,  # Allow reactivating dormant memories
                    rehearse=True,
                )
                if results:
                    parts.append("")
                    parts.append("COGNITIVE CONTEXT SHIFT — nuevas memorias relevantes:")
                    parts.append(cognitive.format_results(results))
        except Exception:
            pass  # Mid-session RAG is best-effort

    # Diary reminder: after 30 min active with no diary entry
    conn = get_db()
    row = conn.execute("SELECT started_epoch FROM sessions WHERE sid = ?", (sid,)).fetchone()
    if row:
        age_seconds = now_epoch() - row["started_epoch"]
        if age_seconds >= 1800 and not check_session_has_diary(sid):
            parts.append("")
            parts.append("⚠ DIARY REMINDER: Session active 30+ min without diary. Write nexo_session_diary_write before closing.")

    return "\n".join(parts)


def handle_status(keyword: str | None = None) -> str:
    """List active sessions, optionally filtered by keyword."""
    clean_stale_sessions()
    if keyword:
        sessions = search_sessions(keyword)
        if not sessions:
            return f"Nadie trabaja en '{keyword}'."
    else:
        sessions = get_active_sessions()

    if not sessions:
        return "Sin sesiones activas."

    lines = ["SESIONES ACTIVAS:"]
    for s in sessions:
        age = _format_age(s["last_update_epoch"])
        lines.append(f"  {s['sid']} ({age}) — {s['task']}")
    return "\n".join(lines)
