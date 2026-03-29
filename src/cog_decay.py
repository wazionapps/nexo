"""NEXO Cognitive — Decay, promotion, garbage collection, and dream consolidation."""
import math
from datetime import datetime, timedelta

# Re-export constants
LAMBDA_STM = 0.1      # half-life ~7 days
LAMBDA_LTM = 0.012    # half-life ~60 days


def _get_db():
    import cognitive
    return cognitive._get_db()


def apply_decay(adaptive: bool = True):
    """Apply Ebbinghaus decay to all memories. Mark LTM as dormant if strength < 0.1."""
    db = _get_db()
    now = datetime.utcnow()

    _protected_stm = set()
    _protected_ltm = set()
    if adaptive:
        sibling_ids = set()
        for row in db.execute("SELECT memory_a_id, memory_b_id FROM memory_siblings").fetchall():
            sibling_ids.add(row["memory_a_id"])
            sibling_ids.add(row["memory_b_id"])
        for row in db.execute("SELECT id FROM stm_memories WHERE promoted_to_ltm = 0").fetchall():
            if row["id"] not in sibling_ids:
                _protected_stm.add(row["id"])
        for row in db.execute("SELECT id FROM ltm_memories WHERE is_dormant = 0").fetchall():
            if row["id"] not in sibling_ids:
                _protected_ltm.add(row["id"])

    # STM decay (skip pinned)
    rows = db.execute(
        "SELECT id, last_accessed, strength FROM stm_memories "
        "WHERE promoted_to_ltm = 0 AND (lifecycle_state IS NULL OR lifecycle_state != 'pinned')"
    ).fetchall()
    for row in rows:
        last = datetime.fromisoformat(row["last_accessed"])
        hours = (now - last).total_seconds() / 3600.0
        decay_rate = LAMBDA_STM * 0.25 if (adaptive and row["id"] in _protected_stm) else LAMBDA_STM
        new_strength = row["strength"] * math.exp(-decay_rate * hours)
        db.execute("UPDATE stm_memories SET strength = ? WHERE id = ?", (new_strength, row["id"]))

    # LTM decay (skip pinned)
    rows = db.execute(
        "SELECT id, last_accessed, strength FROM ltm_memories "
        "WHERE is_dormant = 0 AND (lifecycle_state IS NULL OR lifecycle_state != 'pinned')"
    ).fetchall()
    for row in rows:
        last = datetime.fromisoformat(row["last_accessed"])
        hours = (now - last).total_seconds() / 3600.0
        decay_rate = LAMBDA_LTM * 0.25 if (adaptive and row["id"] in _protected_ltm) else LAMBDA_LTM
        new_strength = row["strength"] * math.exp(-decay_rate * hours)
        if new_strength < 0.1:
            db.execute("UPDATE ltm_memories SET strength = ?, is_dormant = 1 WHERE id = ?",
                       (new_strength, row["id"]))
        else:
            db.execute("UPDATE ltm_memories SET strength = ? WHERE id = ?",
                       (new_strength, row["id"]))

    db.commit()


def promote_stm_to_ltm():
    """Promote STM memories to LTM based on access count, age+strength, or source type."""
    db = _get_db()
    now = datetime.utcnow()
    age_cutoff = (now - timedelta(days=5)).isoformat()

    rows = db.execute(
        """SELECT * FROM stm_memories
           WHERE promoted_to_ltm = 0
           AND (
               access_count >= 3
               OR (created_at < ? AND strength > 0.4)
               OR source_type IN ('learning', 'decision', 'feedback')
           )""",
        (age_cutoff,)
    ).fetchall()

    promoted = 0
    for row in rows:
        redacted = row["redaction_applied"] if "redaction_applied" in row.keys() else 0
        db.execute(
            """INSERT INTO ltm_memories (content, embedding, source_type, source_id, source_title, domain, original_stm_id, redaction_applied)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (row["content"], row["embedding"], row["source_type"], row["source_id"],
             row["source_title"], row["domain"], row["id"], redacted)
        )
        db.execute("UPDATE stm_memories SET promoted_to_ltm = 1 WHERE id = ?", (row["id"],))
        promoted += 1

    db.commit()
    return promoted


def gc_stm():
    """Garbage collect STM: delete weak old memories and anything > 45 days."""
    db = _get_db()
    now = datetime.utcnow()
    cutoff_7d = (now - timedelta(days=7)).isoformat()
    cutoff_45d = (now - timedelta(days=45)).isoformat()

    cur1 = db.execute(
        "DELETE FROM stm_memories WHERE strength < 0.3 AND created_at < ? AND promoted_to_ltm = 0 "
        "AND (lifecycle_state IS NULL OR lifecycle_state != 'pinned')",
        (cutoff_7d,)
    )
    cur2 = db.execute(
        "DELETE FROM stm_memories WHERE created_at < ? AND promoted_to_ltm = 0 "
        "AND (lifecycle_state IS NULL OR lifecycle_state != 'pinned')",
        (cutoff_45d,)
    )
    db.commit()
    return (cur1.rowcount or 0) + (cur2.rowcount or 0)


def gc_test_memories() -> int:
    """Purge STM memories from test/dev sessions."""
    db = _get_db()
    test_domains = ("test", "test_session")
    deleted = 0

    for domain in test_domains:
        cur = db.execute(
            "DELETE FROM stm_memories WHERE domain = ? "
            "AND (lifecycle_state IS NULL OR lifecycle_state != 'pinned')",
            (domain,)
        )
        deleted += cur.rowcount or 0

    test_patterns = [
        "%Secret redact test%",
        "%quarantine test fact%",
        "%Pin test memory%",
        "%API rate limit%AM10%",
        "%xyzzy server%",
        "%Quantum entanglement enables FTL%",
        "%Install Docker%AM10%",
        "%normal safe content about coding%",
        "%test diary%",
        "%test critique%",
        "%integration test diary%",
    ]
    for pattern in test_patterns:
        cur = db.execute(
            "DELETE FROM stm_memories WHERE content LIKE ? "
            "AND (lifecycle_state IS NULL OR lifecycle_state != 'pinned')",
            (pattern,)
        )
        deleted += cur.rowcount or 0

    if deleted > 0:
        db.commit()
    return deleted


def gc_sensory(max_age_hours: int = 48) -> int:
    """Remove sensory memories older than max_age_hours."""
    db = _get_db()
    cutoff = (datetime.utcnow() - timedelta(hours=max_age_hours)).isoformat()
    cur = db.execute(
        "DELETE FROM stm_memories WHERE source_type = 'sensory' AND created_at < ? "
        "AND promoted_to_ltm = 0 AND (lifecycle_state IS NULL OR lifecycle_state != 'pinned')",
        (cutoff,)
    )
    db.commit()
    return cur.rowcount or 0


def gc_ltm_dormant(min_age_days: int = 30) -> int:
    """Remove dormant LTM memories that have been dormant for > min_age_days."""
    db = _get_db()
    cutoff = (datetime.utcnow() - timedelta(days=min_age_days)).isoformat()
    cur = db.execute(
        "DELETE FROM ltm_memories WHERE is_dormant = 1 AND last_accessed < ?",
        (cutoff,)
    )
    db.commit()
    return cur.rowcount or 0
