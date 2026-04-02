from __future__ import annotations
"""NEXO DB — Sessions module."""
import time, secrets, string, sqlite3
from datetime import datetime
from db._core import get_db, _gen_id, now_epoch, local_time_str, SESSION_STALE_SECONDS, MESSAGE_TTL_SECONDS, QUESTION_TTL_SECONDS

# ── Session operations ──────────────────────────────────────────────

def now_epoch() -> float:
    return time.time()


def local_time_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%H:%M")


def register_session(sid: str, task: str, claude_session_id: str = "") -> dict:
    """Register or re-register a session."""
    conn = get_db()
    now = now_epoch()
    conn.execute(
        "INSERT OR REPLACE INTO sessions (sid, task, started_epoch, last_update_epoch, local_time, claude_session_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (sid, task, now, now, local_time_str(), claude_session_id)
    )
    conn.commit()
    return {"sid": sid, "task": task}


def update_session(sid: str, task: str | None) -> dict:
    """Update session timestamp (and task if provided). Preserves started_epoch.

    Args:
        sid: Session ID.
        task: New task description, or None to keep current task (keepalive touch).
    """
    conn = get_db()
    now = now_epoch()
    row = conn.execute("SELECT started_epoch, task FROM sessions WHERE sid = ?", (sid,)).fetchone()
    if row:
        effective_task = task if task is not None else row["task"]
        conn.execute(
            "UPDATE sessions SET task = ?, last_update_epoch = ?, local_time = ? WHERE sid = ?",
            (effective_task, now, local_time_str(), sid)
        )
    else:
        effective_task = task or "Unknown"
        conn.execute(
            "INSERT INTO sessions (sid, task, started_epoch, last_update_epoch, local_time) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, effective_task, now, now, local_time_str())
        )
    conn.commit()
    return {"sid": sid, "task": effective_task}


def complete_session(sid: str):
    """Remove session and its tracked files."""
    conn = get_db()
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("DELETE FROM tracked_files WHERE sid = ?", (sid,))
    conn.execute("DELETE FROM sessions WHERE sid = ?", (sid,))
    conn.commit()


def get_active_sessions() -> list[dict]:
    """Get all sessions updated within STALE threshold."""
    conn = get_db()
    cutoff = now_epoch() - SESSION_STALE_SECONDS
    rows = conn.execute(
        "SELECT sid, task, started_epoch, last_update_epoch, local_time "
        "FROM sessions WHERE last_update_epoch > ?",
        (cutoff,)
    ).fetchall()
    return [dict(r) for r in rows]


def clean_stale_sessions() -> int:
    """Remove stale sessions. Returns count removed."""
    conn = get_db()
    cutoff = now_epoch() - SESSION_STALE_SECONDS
    stale = conn.execute(
        "SELECT sid FROM sessions WHERE last_update_epoch <= ?", (cutoff,)
    ).fetchall()
    for row in stale:
        conn.execute("DELETE FROM tracked_files WHERE sid = ?", (row["sid"],))
    result = conn.execute(
        "DELETE FROM sessions WHERE last_update_epoch <= ?", (cutoff,)
    )
    count = result.rowcount
    conn.commit()
    return count


def search_sessions(keyword: str) -> list[dict]:
    """Find sessions whose task contains keyword (case-insensitive)."""
    conn = get_db()
    cutoff = now_epoch() - SESSION_STALE_SECONDS
    rows = conn.execute(
        "SELECT sid, task, last_update_epoch, local_time FROM sessions "
        "WHERE last_update_epoch > ? AND LOWER(task) LIKE ?",
        (cutoff, f"%{keyword.lower()}%")
    ).fetchall()
    return [dict(r) for r in rows]


# ── File tracking ───────────────────────────────────────────────────

def track_files(sid: str, paths: list[str]) -> dict:
    """Track files for a session. Returns conflicts if any."""
    conn = get_db()
    now = now_epoch()
    session = conn.execute("SELECT sid FROM sessions WHERE sid = ?", (sid,)).fetchone()
    if not session:
            return {"error": f"Session {sid} not found. Register first."}

    for path in paths:
        conn.execute(
            "INSERT OR IGNORE INTO tracked_files (sid, path, tracked_at) VALUES (?, ?, ?)",
            (sid, path, now)
        )
    conn.commit()
    conflicts = _check_conflicts(conn, sid)
    return {"tracked": paths, "conflicts": conflicts}


def untrack_files(sid: str, paths: list[str] | None = None):
    """Untrack files. If paths is None, untrack all."""
    conn = get_db()
    if paths:
        for path in paths:
            conn.execute(
                "DELETE FROM tracked_files WHERE sid = ? AND path = ?",
                (sid, path)
            )
    else:
        conn.execute("DELETE FROM tracked_files WHERE sid = ?", (sid,))
    conn.commit()


def get_all_tracked_files() -> dict:
    """Get all tracked files grouped by session."""
    conn = get_db()
    cutoff = now_epoch() - SESSION_STALE_SECONDS
    rows = conn.execute(
        "SELECT tf.sid, tf.path, s.task FROM tracked_files tf "
        "JOIN sessions s ON tf.sid = s.sid "
        "WHERE s.last_update_epoch > ?",
        (cutoff,)
    ).fetchall()
    result = {}
    for r in rows:
        sid = r["sid"]
        if sid not in result:
            result[sid] = {"task": r["task"], "files": []}
        result[sid]["files"].append(r["path"])
    return result


def _check_conflicts(conn: sqlite3.Connection, sid: str) -> list[dict]:
    """Check if any of sid's files are tracked by other active sessions."""
    cutoff = now_epoch() - SESSION_STALE_SECONDS
    my_files = conn.execute(
        "SELECT path FROM tracked_files WHERE sid = ?", (sid,)
    ).fetchall()
    my_paths = {r["path"] for r in my_files}
    if not my_paths:
        return []

    conflicts = []
    others = conn.execute(
        "SELECT tf.sid, tf.path, s.task FROM tracked_files tf "
        "JOIN sessions s ON tf.sid = s.sid "
        "WHERE tf.sid != ? AND s.last_update_epoch > ?",
        (sid, cutoff)
    ).fetchall()
    by_sid = {}
    for r in others:
        if r["path"] in my_paths:
            osid = r["sid"]
            if osid not in by_sid:
                by_sid[osid] = {"sid": osid, "task": r["task"], "files": []}
            by_sid[osid]["files"].append(r["path"])
    return list(by_sid.values())


# ── Messages ────────────────────────────────────────────────────────

def send_message(from_sid: str, to_sid: str, text: str) -> str:
    """Send a message. to_sid can be 'all' for broadcast."""
    conn = get_db()
    _clean_old_messages(conn)
    msg_id = _gen_id("msg", 6)
    conn.execute(
        "INSERT INTO messages (id, from_sid, to_sid, text, created_epoch) "
        "VALUES (?, ?, ?, ?, ?)",
        (msg_id, from_sid, to_sid, text, now_epoch())
    )
    conn.commit()
    return msg_id


def get_inbox(sid: str) -> list[dict]:
    """Get unread messages for a session."""
    conn = get_db()
    _clean_old_messages(conn)
    rows = conn.execute(
        "SELECT m.id, m.from_sid, m.to_sid, m.text, m.created_epoch "
        "FROM messages m "
        "WHERE (m.to_sid = 'all' OR m.to_sid = ?) "
        "AND m.from_sid != ? "
        "AND m.id NOT IN (SELECT message_id FROM message_reads WHERE sid = ?)",
        (sid, sid, sid)
    ).fetchall()
    for r in rows:
        conn.execute(
            "INSERT OR IGNORE INTO message_reads (message_id, sid) VALUES (?, ?)",
            (r["id"], sid)
        )
    conn.commit()
    result = [dict(r) for r in rows]
    return result


def _clean_old_messages(conn: sqlite3.Connection):
    """Remove expired messages and commit immediately."""
    cutoff = now_epoch() - MESSAGE_TTL_SECONDS
    conn.execute("DELETE FROM messages WHERE created_epoch < ?", (cutoff,))
    conn.commit()


# ── Questions ───────────────────────────────────────────────────────

def ask_question(from_sid: str, to_sid: str, question: str) -> str:
    """Create a pending question. Returns qid."""
    conn = get_db()
    _expire_old_questions(conn)
    qid = _gen_id("q", 8)
    conn.execute(
        "INSERT INTO questions (qid, from_sid, to_sid, question, status, created_epoch) "
        "VALUES (?, ?, ?, ?, 'pending', ?)",
        (qid, from_sid, to_sid, question, now_epoch())
    )
    conn.commit()
    return qid


def answer_question(qid: str, answer: str) -> dict:
    """Answer a pending question."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM questions WHERE qid = ?", (qid,)
    ).fetchone()
    if not row:
            return {"error": f"Question {qid} not found"}
    if row["status"] != "pending":
            return {"error": f"Question {qid} is {row['status']}, not pending"}
    conn.execute(
        "UPDATE questions SET answer = ?, status = 'answered', answered_epoch = ? "
        "WHERE qid = ?",
        (answer, now_epoch(), qid)
    )
    conn.commit()
    return {"qid": qid, "status": "answered"}


def get_pending_questions(sid: str) -> list[dict]:
    """Get pending questions addressed to this session."""
    conn = get_db()
    _expire_old_questions(conn)
    rows = conn.execute(
        "SELECT qid, from_sid, question, created_epoch FROM questions "
        "WHERE to_sid = ? AND status = 'pending'",
        (sid,)
    ).fetchall()
    conn.commit()
    return [dict(r) for r in rows]


def check_answer(qid: str) -> dict | None:
    """Check if a question has been answered. Returns answer or None."""
    conn = get_db()
    row = conn.execute(
        "SELECT qid, answer, status FROM questions WHERE qid = ?", (qid,)
    ).fetchone()
    if not row:
        return None
    return dict(row)


def _expire_old_questions(conn: sqlite3.Connection):
    """Mark old pending questions as expired."""
    cutoff = now_epoch() - QUESTION_TTL_SECONDS
    conn.execute(
        "UPDATE questions SET status = 'expired' "
        "WHERE status = 'pending' AND created_epoch < ?",
        (cutoff,)
    )


