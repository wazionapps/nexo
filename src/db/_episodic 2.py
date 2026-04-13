from __future__ import annotations
"""NEXO DB — Episodic module."""
import datetime, time, json
from db._core import get_db, now_epoch, _multi_word_like
from db._fts import fts_upsert, fts_search

# ── Change Log ───────────────────────────────────────────────────

def cleanup_old_changes(retention_days: int = 90) -> int:
    """Delete change_log entries older than retention_days. Returns count deleted."""
    conn = get_db()
    # Get IDs before deleting so we can clean FTS
    ids = [str(r[0]) for r in conn.execute(
        "SELECT id FROM change_log WHERE created_at < datetime('now', ?)",
        (f"-{retention_days} days",)
    ).fetchall()]
    cursor = conn.execute(
        "DELETE FROM change_log WHERE created_at < datetime('now', ?)",
        (f"-{retention_days} days",)
    )
    for cid in ids:
        conn.execute("DELETE FROM unified_search WHERE source = 'change' AND source_id = ?", (cid,))
    conn.commit()
    return cursor.rowcount


def log_change(session_id: str, files: str, what_changed: str, why: str,
               triggered_by: str = '', affects: str = '', risks: str = '',
               verify: str = '', commit_ref: str = '') -> dict:
    """Log a code/config change with full context."""
    conn = get_db()
    cleanup_old_changes()
    try:
        cursor = conn.execute(
            "INSERT INTO change_log (session_id, files, what_changed, why, triggered_by, affects, risks, verify, commit_ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, files, what_changed, why, triggered_by, affects, risks, verify, commit_ref)
        )
        conn.commit()
        cid = cursor.lastrowid
        body = f"{what_changed} {why} {triggered_by} {affects} {risks}"
        fts_upsert("change", str(cid), files, body, "change_log", commit=False)
        row = conn.execute("SELECT * FROM change_log WHERE id = ?", (cid,)).fetchone()
        return dict(row)
    except Exception as e:
        return {"error": str(e)}


def search_changes(query: str = '', files: str = '', days: int = 30) -> list[dict]:
    """Search change log by text and/or file path."""
    conn = get_db()
    days = max(1, int(days))
    conditions = []
    params = []
    if query:
        frag, qparams = _multi_word_like(query, ["what_changed", "why", "affects", "triggered_by"])
        conditions.append(f"({frag})")
        params.extend(qparams)
    if files:
        frag_f, fparams = _multi_word_like(files, ["files"])
        conditions.append(f"({frag_f})")
        params.extend(fparams)
    conditions.append("created_at >= datetime('now', ?)")
    params.append(f"-{days} days")
    where = " AND ".join(conditions)
    rows = conn.execute(
        f"SELECT * FROM change_log WHERE {where} ORDER BY created_at DESC",
        params
    ).fetchall()
    return [dict(r) for r in rows]


def auto_resolve_followups(change: dict) -> list[str]:
    """Cross-reference a change_log entry with open followups. Auto-completes matches.

    Matching logic:
    1. File overlap: if change touched files mentioned in followup description
    2. Keyword overlap: Jaccard similarity between change text and followup text
    3. ID reference: if followup ID appears in the change's triggered_by/why fields

    Returns list of followup IDs that were auto-resolved.
    """
    conn = get_db()
    open_followups = conn.execute(
        "SELECT * FROM followups WHERE status NOT LIKE 'COMPLETED%' "
        "AND status NOT IN ('DELETED','archived','blocked','waiting')"
    ).fetchall()

    if not open_followups:
        return []

    change_text = " ".join(str(change.get(f, "")) for f in
                           ["files", "what_changed", "why", "triggered_by", "affects"])
    change_files = set(change.get("files", "").replace(",", " ").split())
    change_tokens = {w.lower() for w in change_text.split() if len(w) > 3}

    resolved = []
    for f in open_followups:
        fid = f["id"]
        fdesc = f"{fid} {f['description']} {f['verification'] or ''}"
        ftokens = {w.lower() for w in fdesc.split() if len(w) > 3}

        # Check 1: followup ID explicitly in change trigger/why
        if fid.lower() in change_text.lower():
            resolved.append(fid)
            continue

        # Check 2: file overlap (any changed file mentioned in followup)
        if change_files:
            for cf in change_files:
                basename = cf.rsplit("/", 1)[-1] if "/" in cf else cf
                if basename and len(basename) > 4 and basename.lower() in fdesc.lower():
                    resolved.append(fid)
                    break
            if fid in resolved:
                continue

        # Check 3: keyword similarity (asymmetric overlap >= 0.35)
        if ftokens and change_tokens:
            intersection = ftokens & change_tokens
            smaller = min(len(ftokens), len(change_tokens))
            score = len(intersection) / smaller if smaller else 0
            if score >= 0.35:
                resolved.append(fid)

    # Auto-complete matched followups
    from db._reminders import complete_followup
    commit_ref = change.get("commit_ref", "")
    for fid in resolved:
        complete_followup(fid, result=f"Auto-resolved by change #{change.get('id', '?')} (commit {commit_ref[:8] if commit_ref else 'N/A'})")

    return resolved


def update_change_commit(id: int, commit_ref: str) -> dict:
    """Link a change log entry to its git commit after commit.

    After linking, auto-resolves any open followups that match the change.
    """
    conn = get_db()
    row = conn.execute("SELECT * FROM change_log WHERE id = ?", (id,)).fetchone()
    if not row:
        return {"error": f"Change {id} not found"}
    conn.execute("UPDATE change_log SET commit_ref = ? WHERE id = ?", (commit_ref, id))
    conn.commit()
    row = conn.execute("SELECT * FROM change_log WHERE id = ?", (id,)).fetchone()
    r = dict(row)
    body = f"{r.get('what_changed','')} {r.get('why','')} {r.get('triggered_by','')} {r.get('affects','')} {r.get('risks','')}"
    fts_upsert("change", str(id), r.get("files",""), body, "change_log", commit=False)

    # Auto-resolve followups that match this change
    r["_auto_resolved"] = auto_resolve_followups(r)
    return r


# ── Decisions (episodic memory) ──────────────────────────────────

def cleanup_old_decisions(retention_days: int = 90) -> int:
    """Delete decisions entries older than retention_days. Returns count deleted."""
    conn = get_db()
    ids = [str(r[0]) for r in conn.execute(
        "SELECT id FROM decisions WHERE created_at < datetime('now', ?)",
        (f"-{retention_days} days",)
    ).fetchall()]
    cursor = conn.execute(
        "DELETE FROM decisions WHERE created_at < datetime('now', ?)",
        (f"-{retention_days} days",)
    )
    for did in ids:
        conn.execute("DELETE FROM unified_search WHERE source = 'decision' AND source_id = ?", (did,))
    conn.commit()
    return cursor.rowcount


def log_decision(session_id: str, domain: str, decision: str,
                 alternatives: str = '', based_on: str = '',
                 confidence: str = 'medium', context_ref: str = '',
                 status: str = 'pending_review',
                 review_due_at: str | None = None) -> dict:
    """Log a decision with reasoning context."""
    conn = get_db()
    cleanup_old_decisions()
    try:
        cursor = conn.execute(
            "INSERT INTO decisions "
            "(session_id, domain, decision, alternatives, based_on, confidence, context_ref, status, review_due_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id, domain, decision, alternatives, based_on,
                confidence, context_ref, status, review_due_at,
            )
        )
        conn.commit()
        did = cursor.lastrowid
        body = f"{decision} {alternatives} {based_on}"
        fts_upsert("decision", str(did), decision[:200], body, domain or '', commit=False)
        row = conn.execute("SELECT * FROM decisions WHERE id = ?", (did,)).fetchone()
        return dict(row)
    except Exception as e:
        return {"error": str(e)}


def update_decision_outcome(id: int, outcome: str) -> dict:
    """Record the outcome of a past decision."""
    conn = get_db()
    row = conn.execute("SELECT * FROM decisions WHERE id = ?", (id,)).fetchone()
    if not row:
        return {"error": f"Decision {id} not found"}
    conn.execute(
        "UPDATE decisions "
        "SET outcome = ?, outcome_at = datetime('now'), status = 'reviewed', "
        "review_due_at = NULL, last_reviewed_at = datetime('now') "
        "WHERE id = ?",
        (outcome, id)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM decisions WHERE id = ?", (id,)).fetchone()
    r = dict(row)
    body = f"{r.get('decision','')} {r.get('alternatives','')} {r.get('based_on','')} {r.get('outcome','')}"
    fts_upsert("decision", str(id), r.get("decision","")[:200], body, r.get("domain",""), commit=False)
    return r


def get_memory_review_queue(days: int = 7) -> dict:
    """Return learnings and decisions whose review date falls within N days."""
    conn = get_db()
    learning_cutoff = now_epoch() + (days * 86400)
    learnings = conn.execute(
        "SELECT * FROM learnings "
        "WHERE review_due_at IS NOT NULL AND review_due_at <= ? "
        "ORDER BY review_due_at ASC, updated_at DESC",
        (learning_cutoff,)
    ).fetchall()
    decisions = conn.execute(
        "SELECT * FROM decisions "
        "WHERE review_due_at IS NOT NULL AND review_due_at <= datetime('now', ?) "
        "ORDER BY review_due_at ASC, created_at DESC",
        (f"+{days} days",)
    ).fetchall()
    return {
        "learnings": [dict(r) for r in learnings],
        "decisions": [dict(r) for r in decisions],
    }


def find_decisions_by_context_ref(ref: str) -> list[dict]:
    """Find decisions linked to a specific context_ref (e.g., followup ID)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM decisions WHERE context_ref = ? AND (outcome IS NULL OR outcome = '')",
        (ref,)
    ).fetchall()
    return [dict(r) for r in rows]


def search_decisions(query: str = '', domain: str = '', days: int = 30) -> list[dict]:
    """Search decisions by text and/or domain within a time window."""
    conn = get_db()
    days = max(1, int(days))
    conditions = []
    params = []
    if query:
        frag, qparams = _multi_word_like(query, ["decision", "alternatives", "based_on", "outcome"])
        conditions.append(f"({frag})")
        params.extend(qparams)
    if domain:
        conditions.append("domain = ?")
        params.append(domain)
    conditions.append("created_at >= datetime('now', ?)")
    params.append(f"-{days} days")

    where = " AND ".join(conditions)
    rows = conn.execute(
        f"SELECT * FROM decisions WHERE {where} ORDER BY created_at DESC",
        params
    ).fetchall()
    return [dict(r) for r in rows]


# ── Session Diary ────────────────────────────────────────────────

def cleanup_old_diaries(retention_days: int = 180) -> int:
    """Archive then delete session_diary entries older than retention_days.

    Diaries are moved to diary_archive (permanent) before being removed from
    the active session_diary table. Nothing is ever truly lost.
    """
    conn = get_db()
    cutoff = f"-{retention_days} days"

    # Archive before deleting — permanent subconscious memory
    try:
        conn.execute("""
            INSERT OR IGNORE INTO diary_archive
                (id, session_id, created_at, decisions, discarded, pending,
                 context_next, summary, mental_state, domain, user_signals,
                 self_critique, source)
            SELECT id, session_id, created_at, decisions, discarded, pending,
                   context_next, summary, mental_state, domain, user_signals,
                   self_critique, source
            FROM session_diary
            WHERE created_at < datetime('now', ?)
        """, (cutoff,))
    except Exception:
        pass  # Table may not exist yet (pre-migration)

    ids = [str(r[0]) for r in conn.execute(
        "SELECT id FROM session_diary WHERE created_at < datetime('now', ?)",
        (cutoff,)
    ).fetchall()]
    cursor = conn.execute(
        "DELETE FROM session_diary WHERE created_at < datetime('now', ?)",
        (cutoff,)
    )
    for did in ids:
        conn.execute("DELETE FROM unified_search WHERE source = 'diary' AND source_id = ?", (did,))
    conn.commit()
    return cursor.rowcount


def write_session_diary(session_id: str, decisions: str, summary: str,
                        discarded: str = '', pending: str = '',
                        context_next: str = '', mental_state: str = '',
                        domain: str = '', user_signals: str = '',
                        self_critique: str = '', source: str = 'claude') -> dict:
    """Write a session diary entry with mental state and self-critique for continuity."""
    conn = get_db()
    cleanup_old_diaries()
    cursor = conn.execute(
        "INSERT INTO session_diary (session_id, decisions, discarded, pending, context_next, mental_state, summary, domain, user_signals, self_critique, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, decisions, discarded, pending, context_next, mental_state, summary, domain, user_signals, self_critique, source)
    )
    conn.commit()
    did = cursor.lastrowid
    body = f"{summary} {decisions} {pending} {context_next} {mental_state} {self_critique}"
    fts_upsert("diary", str(did), (summary or '')[:200], body, domain or "general", commit=False)
    row = conn.execute("SELECT * FROM session_diary WHERE id = ?", (did,)).fetchone()
    return dict(row)


# ── Diary Archive (permanent subconscious) ──────────────────────


def diary_archive_search(query: str = '', domain: str = '',
                         year: int = 0, month: int = 0,
                         limit: int = 20) -> list[dict]:
    """Search the permanent diary archive. Supports text search, domain filter, and date filter.

    Args:
        query: Text to search in summary, decisions, mental_state, pending
        domain: Filter by domain (e.g. 'project-a', 'project-b')
        year: Filter by year (e.g. 2026)
        month: Filter by month (1-12), requires year
        limit: Max results (default 20)
    """
    conn = get_db()
    try:
        conn.execute("SELECT 1 FROM diary_archive LIMIT 1")
    except Exception:
        return []  # Table doesn't exist yet

    conditions = []
    params = []

    if query:
        words = query.strip().split()
        for word in words:
            conditions.append(
                "(summary LIKE ? OR decisions LIKE ? OR mental_state LIKE ? "
                "OR pending LIKE ? OR self_critique LIKE ?)"
            )
            w = f"%{word}%"
            params.extend([w, w, w, w, w])

    if domain:
        conditions.append("domain = ?")
        params.append(domain)

    if year:
        if month:
            date_start = f"{year:04d}-{month:02d}-01"
            if month == 12:
                date_end = f"{year + 1:04d}-01-01"
            else:
                date_end = f"{year:04d}-{month + 1:02d}-01"
            conditions.append("created_at >= ? AND created_at < ?")
            params.extend([date_start, date_end])
        else:
            conditions.append("created_at >= ? AND created_at < ?")
            params.extend([f"{year:04d}-01-01", f"{year + 1:04d}-01-01"])

    where = " AND ".join(conditions) if conditions else "1=1"

    rows = conn.execute(f"""
        SELECT id, session_id, created_at, summary, decisions, domain,
               mental_state, pending, self_critique, source
        FROM diary_archive
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ?
    """, params + [limit]).fetchall()
    return [dict(r) for r in rows]


def diary_archive_read(diary_id: int) -> dict | None:
    """Read a single archived diary entry by ID — full content."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM diary_archive WHERE id = ?", (diary_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def diary_archive_stats() -> dict:
    """Get archive statistics: count, date range, domains."""
    conn = get_db()
    try:
        count = conn.execute("SELECT COUNT(*) FROM diary_archive").fetchone()[0]
        if count == 0:
            return {"count": 0, "oldest": None, "newest": None, "domains": []}
        oldest = conn.execute("SELECT MIN(created_at) FROM diary_archive").fetchone()[0]
        newest = conn.execute("SELECT MAX(created_at) FROM diary_archive").fetchone()[0]
        domains = [r[0] for r in conn.execute(
            "SELECT DISTINCT domain FROM diary_archive WHERE domain IS NOT NULL AND domain != '' ORDER BY domain"
        ).fetchall()]
        return {"count": count, "oldest": oldest, "newest": newest, "domains": domains}
    except Exception:
        return {"count": 0, "oldest": None, "newest": None, "domains": []}


def check_session_has_diary(session_id: str) -> bool:
    """Return True if this session already has a diary entry."""
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM session_diary WHERE session_id = ? LIMIT 1",
        (session_id,)
    ).fetchone()
    return row is not None


# ── Session Diary Drafts ─────────────────────────────────────────


def upsert_diary_draft(sid: str, tasks_seen: str, change_ids: str,
                       decision_ids: str, last_context_hint: str,
                       heartbeat_count: int, summary_draft: str = '') -> dict:
    """UPSERT diary draft for a session. Called by heartbeat to accumulate context."""
    conn = get_db()
    conn.execute(
        """INSERT INTO session_diary_draft
           (sid, summary_draft, tasks_seen, change_ids, decision_ids,
            last_context_hint, heartbeat_count, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(sid) DO UPDATE SET
             summary_draft = excluded.summary_draft,
             tasks_seen = excluded.tasks_seen,
             change_ids = excluded.change_ids,
             decision_ids = excluded.decision_ids,
             last_context_hint = excluded.last_context_hint,
             heartbeat_count = excluded.heartbeat_count,
             updated_at = datetime('now')""",
        (sid, summary_draft, tasks_seen, change_ids, decision_ids,
         last_context_hint, heartbeat_count)
    )
    conn.commit()
    return {"sid": sid, "heartbeat_count": heartbeat_count}


def get_diary_draft(sid: str) -> dict | None:
    """Get diary draft for a session, or None."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM session_diary_draft WHERE sid = ?", (sid,)
    ).fetchone()
    return dict(row) if row else None


def delete_diary_draft(sid: str):
    """Delete diary draft after real diary is written."""
    conn = get_db()
    conn.execute("DELETE FROM session_diary_draft WHERE sid = ?", (sid,))
    conn.commit()


# ── Session Checkpoint operations ──────────────────────────────────

def save_checkpoint(sid: str, task: str = '', task_status: str = 'active',
                    active_files: str = '[]', current_goal: str = '',
                    decisions_summary: str = '', errors_found: str = '',
                    reasoning_thread: str = '', next_step: str = '') -> dict:
    """Save or update a session checkpoint. Called by PreCompact hook."""
    conn = get_db()
    # Get current compaction count
    existing = conn.execute(
        "SELECT compaction_count FROM session_checkpoints WHERE sid = ?", (sid,)
    ).fetchone()
    count = (existing["compaction_count"] + 1) if existing else 0

    conn.execute(
        """INSERT INTO session_checkpoints
           (sid, task, task_status, active_files, current_goal,
            decisions_summary, errors_found, reasoning_thread, next_step,
            compaction_count, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(sid) DO UPDATE SET
             task = excluded.task,
             task_status = excluded.task_status,
             active_files = excluded.active_files,
             current_goal = excluded.current_goal,
             decisions_summary = excluded.decisions_summary,
             errors_found = excluded.errors_found,
             reasoning_thread = excluded.reasoning_thread,
             next_step = excluded.next_step,
             compaction_count = excluded.compaction_count,
             updated_at = datetime('now')""",
        (sid, task, task_status, active_files, current_goal,
         decisions_summary, errors_found, reasoning_thread, next_step, count)
    )
    conn.commit()
    return {"sid": sid, "compaction_count": count}


def read_checkpoint(sid: str = '') -> dict | None:
    """Read the most recent session checkpoint. If no sid, returns the latest."""
    conn = get_db()
    if sid:
        row = conn.execute(
            "SELECT * FROM session_checkpoints WHERE sid = ?", (sid,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM session_checkpoints ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def increment_compaction_count(sid: str) -> int:
    """Increment and return the compaction count for a session."""
    conn = get_db()
    conn.execute(
        """UPDATE session_checkpoints
           SET compaction_count = compaction_count + 1, updated_at = datetime('now')
           WHERE sid = ?""",
        (sid,)
    )
    conn.commit()
    row = conn.execute(
        "SELECT compaction_count FROM session_checkpoints WHERE sid = ?", (sid,)
    ).fetchone()
    return row["compaction_count"] if row else 0


def get_orphan_sessions(ttl_seconds: int = 900) -> list[dict]:
    """Get sessions that exceeded TTL and have no diary."""
    conn = get_db()
    cutoff = now_epoch() - ttl_seconds
    rows = conn.execute(
        """SELECT s.sid, s.task, s.started_epoch, s.last_update_epoch
           FROM sessions s
           LEFT JOIN session_diary sd ON sd.session_id = s.sid
           WHERE s.last_update_epoch <= ? AND sd.id IS NULL""",
        (cutoff,)
    ).fetchall()
    return [dict(r) for r in rows]


def read_session_diary(session_id: str = '', last_n: int = 3, last_day: bool = False,
                       domain: str = '', include_automated: bool = False) -> list[dict]:
    """Read session diary entries.

    - session_id: returns entries for that specific session
    - last_day: returns the recent continuity window (~36h), including the previous evening
    - last_n: returns last N entries (default)
    - domain: filter by project context (nexo, other)
    - include_automated: if False (default), excludes automated sessions (auto-close,
      cron diaries, etc.). Only returns human-interactive sessions.
      Email sessions (user sends email, NEXO responds) ARE included — they're real interactions.
    """
    conn = get_db()
    domain_clause = " AND domain = ?" if domain else ""
    domain_params = (domain,) if domain else ()
    # By default, filter out automated sessions so startup shows human sessions only.
    # Keeps: interactive sessions + auto-closed sessions that had real user interaction.
    # An auto-close is human if it has heartbeats > 0 (heartbeat only fires on user messages).
    # Excludes: cron jobs, auto-closed crons (0 heartbeats or "Minimal diary").
    if include_automated:
        source_clause = ""
    else:
        source_clause = (
            " AND ("
            "  (source = 'claude' AND summary NOT LIKE '[AUTO-%')"
            "  OR (source = 'auto-close'"
            "      AND mental_state NOT LIKE '%0 heartbeats%'"
            "      AND mental_state NOT LIKE '%Minimal diary%')"
            ")"
        )

    if session_id:
        rows = conn.execute(
            f"SELECT * FROM session_diary WHERE session_id = ?{domain_clause} ORDER BY created_at DESC",
            (session_id,) + domain_params
        ).fetchall()
    elif last_day:
        rows = conn.execute(
            f"SELECT * FROM session_diary "
            f"WHERE created_at >= datetime('now', '-36 hours'){domain_clause}{source_clause} "
            f"ORDER BY created_at DESC",
            domain_params
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM session_diary WHERE 1=1{domain_clause}{source_clause} ORDER BY created_at DESC LIMIT ?",
            domain_params + (last_n,)
        ).fetchall()
    return [dict(r) for r in rows]


def _multi_word_like(query: str, columns: list[str]) -> tuple[str, list]:
    """Build AND-ed LIKE conditions: every word must appear in at least one of the columns.

    Returns (sql_fragment, params) ready for WHERE clause.
    Example: query="cron learn", columns=["title","content"]
    → "(title LIKE ? OR content LIKE ?) AND (title LIKE ? OR content LIKE ?)"
    with params ["%cron%","%cron%","%learn%","%learn%"]
    """
    words = query.strip().split()
    if not words:
        return "1=1", []
    word_conditions = []
    params = []
    for word in words:
        pattern = f"%{word}%"
        col_or = " OR ".join(f"{c} LIKE ?" for c in columns)
        word_conditions.append(f"({col_or})")
        params.extend([pattern] * len(columns))
    return " AND ".join(word_conditions), params


def recall(query: str, days: int = 30) -> list[dict]:
    """Cross-search ALL memory using FTS5: learnings, decisions, changes, diary, followups, entities, .md files.

    Returns up to 20 results ranked by relevance (FTS5 bm25).
    Falls back to LIKE-based search if FTS fails.
    """
    # Try FTS5 first (fast, ranked), then filter by days
    results = fts_search(query, limit=40)  # fetch extra to allow filtering
    if results:
        cutoff_epoch = now_epoch() - (days * 86400)
        filtered = []
        for r in results:
            ua = str(r.get('updated_at', ''))
            if not ua:
                filtered.append(r)
                continue
            # Normalize to epoch for comparison
            try:
                if ua[0].isdigit() and ('.' in ua or len(ua) > 12):
                    # Could be epoch float or ISO date
                    if '-' in ua[:5]:
                        # ISO datetime like "2026-03-13 16:17:40"
                        dt = datetime.datetime.fromisoformat(ua.replace(' ', 'T'))
                        ts = dt.timestamp()
                    else:
                        ts = float(ua)
                else:
                    ts = float(ua)
                if ts >= cutoff_epoch:
                    filtered.append(r)
            except (ValueError, TypeError):
                filtered.append(r)  # keep if can't parse
        if filtered:
            return filtered[:20]

    # Fallback to old LIKE-based search
    days = max(1, int(days))
    conn = get_db()
    cutoff_dt = datetime.datetime.now() - datetime.timedelta(days=days)
    cutoff_str = cutoff_dt.strftime("%Y-%m-%d")
    cutoff_epoch = now_epoch() - (days * 86400)

    results = []

    frag, params = _multi_word_like(query, ["files", "what_changed", "why", "triggered_by", "affects", "risks"])
    rows = conn.execute(f"""
        SELECT id, created_at, 'change' AS source,
               files AS title,
               (what_changed || ' | ' || why) AS snippet, 'change_log' AS category, 0 AS rank
        FROM change_log
        WHERE created_at >= ? AND ({frag})
        ORDER BY created_at DESC LIMIT 20
    """, [cutoff_str] + params).fetchall()
    results.extend([dict(r) for r in rows])

    frag, params = _multi_word_like(query, ["decision", "alternatives", "based_on", "outcome"])
    rows = conn.execute(f"""
        SELECT id, created_at, 'decision' AS source,
               decision AS title,
               (COALESCE(based_on,'') || ' | ' || COALESCE(alternatives,'')) AS snippet, domain AS category, 0 AS rank
        FROM decisions
        WHERE created_at >= ? AND ({frag})
        ORDER BY created_at DESC LIMIT 20
    """, [cutoff_str] + params).fetchall()
    results.extend([dict(r) for r in rows])

    frag, params = _multi_word_like(query, ["title", "content", "reasoning"])
    rows = conn.execute(f"""
        SELECT id, datetime(created_at, 'unixepoch') AS created_at, 'learning' AS source,
               title,
               (COALESCE(content,'') || ' | ' || COALESCE(reasoning,'')) AS snippet, category, 0 AS rank
        FROM learnings
        WHERE created_at >= ? AND ({frag})
        ORDER BY created_at DESC LIMIT 20
    """, [cutoff_epoch] + params).fetchall()
    results.extend([dict(r) for r in rows])

    frag, params = _multi_word_like(query, ["id", "description", "verification", "reasoning"])
    rows = conn.execute(f"""
        SELECT id, datetime(created_at, 'unixepoch') AS created_at, 'followup' AS source,
               id AS title,
               (COALESCE(description,'') || ' | ' || COALESCE(verification,'') || ' | ' || COALESCE(reasoning,'')) AS snippet,
               'followup' AS category, 0 AS rank
        FROM followups
        WHERE created_at >= ? AND ({frag})
        ORDER BY created_at DESC LIMIT 20
    """, [cutoff_epoch] + params).fetchall()
    results.extend([dict(r) for r in rows])

    frag, params = _multi_word_like(query, ["decisions", "discarded", "pending", "context_next", "mental_state", "summary"])
    rows = conn.execute(f"""
        SELECT id, created_at, 'diary' AS source,
               summary AS title,
               (COALESCE(decisions,'') || ' | ' || COALESCE(pending,'') || ' | ' || COALESCE(context_next,'')) AS snippet,
               COALESCE(domain, 'general') AS category, 0 AS rank
        FROM session_diary
        WHERE created_at >= ? AND ({frag})
        ORDER BY created_at DESC LIMIT 20
    """, [cutoff_str] + params).fetchall()
    results.extend([dict(r) for r in rows])

    # Skills
    try:
        frag, params = _multi_word_like(query, ["name", "description", "tags", "trigger_patterns"])
        rows = conn.execute(f"""
            SELECT id, created_at, 'skill' AS source,
                   name AS title,
                   (COALESCE(description,'') || ' | ' || COALESCE(tags,'') || ' | ' || COALESCE(trigger_patterns,'')) AS snippet,
                   level AS category, 0 AS rank
            FROM skills
            WHERE created_at >= ? AND ({frag})
            ORDER BY trust_score DESC LIMIT 10
        """, [cutoff_str] + params).fetchall()
        results.extend([dict(r) for r in rows])
    except Exception:
        pass  # Table may not exist yet during migration

    results.sort(key=lambda r: r.get('created_at', ''), reverse=True)
    return results[:20]

