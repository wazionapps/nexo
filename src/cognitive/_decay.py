"""NEXO Cognitive — Decay, promotion, garbage collection, dream consolidation."""
import math
import numpy as np
from datetime import datetime, timedelta
from cognitive._core import _get_db, embed, cosine_similarity, _blob_to_array, _array_to_blob, LAMBDA_STM, LAMBDA_LTM, EMBEDDING_DIM


def _hnsw_invalidate():
    """Invalidate HNSW indices after bulk operations (best-effort)."""
    try:
        import hnsw_index
        if hnsw_index.is_available():
            hnsw_index.invalidate("both")
    except Exception:
        pass


def apply_decay(adaptive: bool = True):
    """Apply Ebbinghaus decay to all memories. Mark LTM as dormant if strength < 0.1.

    Args:
        adaptive: If True, protect unique memories (no similar neighbors) from aggressive decay.
                  Unique memories decay at 25% of normal rate. This prevents information loss
                  in sparse memory stores where there's no redundancy to compensate.
    """
    db = _get_db()
    now = datetime.utcnow()

    # Build redundancy map if adaptive mode — check which memories have similar siblings
    _protected_stm = set()
    _protected_ltm = set()
    if adaptive:
        # A memory is "protected" if it has no siblings in memory_siblings table
        # (meaning no other memory covers similar content)
        sibling_ids = set()
        for row in db.execute("SELECT memory_a_id, memory_b_id FROM memory_siblings").fetchall():
            sibling_ids.add(row["memory_a_id"])
            sibling_ids.add(row["memory_b_id"])

        # STM memories NOT in sibling_ids are unique → protect
        for row in db.execute("SELECT id FROM stm_memories WHERE promoted_to_ltm = 0").fetchall():
            if row["id"] not in sibling_ids:
                _protected_stm.add(row["id"])

        # LTM memories NOT in sibling_ids are unique → protect
        for row in db.execute("SELECT id FROM ltm_memories WHERE is_dormant = 0").fetchall():
            if row["id"] not in sibling_ids:
                _protected_ltm.add(row["id"])

    # STM decay (skip pinned)
    rows = db.execute("SELECT id, last_accessed, strength FROM stm_memories WHERE promoted_to_ltm = 0 AND (lifecycle_state IS NULL OR lifecycle_state != 'pinned')").fetchall()
    for row in rows:
        last = datetime.fromisoformat(row["last_accessed"])
        hours = (now - last).total_seconds() / 3600.0
        decay_rate = LAMBDA_STM * 0.25 if (adaptive and row["id"] in _protected_stm) else LAMBDA_STM
        new_strength = row["strength"] * math.exp(-decay_rate * hours)
        db.execute("UPDATE stm_memories SET strength = ? WHERE id = ?", (new_strength, row["id"]))

    # LTM decay (skip pinned)
    rows = db.execute("SELECT id, last_accessed, strength FROM ltm_memories WHERE is_dormant = 0 AND (lifecycle_state IS NULL OR lifecycle_state != 'pinned')").fetchall()
    for row in rows:
        last = datetime.fromisoformat(row["last_accessed"])
        hours = (now - last).total_seconds() / 3600.0
        decay_rate = LAMBDA_LTM * 0.25 if (adaptive and row["id"] in _protected_ltm) else LAMBDA_LTM
        new_strength = row["strength"] * math.exp(-decay_rate * hours)
        if new_strength < 0.1:
            db.execute("UPDATE ltm_memories SET strength = ?, is_dormant = 1 WHERE id = ?", (new_strength, row["id"]))
        else:
            db.execute("UPDATE ltm_memories SET strength = ? WHERE id = ?", (new_strength, row["id"]))

    db.commit()


def promote_stm_to_ltm():
    """Promote STM memories to LTM based on multiple criteria.

    Promotion rules (any one is sufficient):
    1. access_count >= 3 (actively retrieved = important)
    2. age > 5 days AND strength > 0.4 (survived decay = worth keeping)
    3. source_type in ('learning', 'decision', 'feedback') (high-value by nature)
    """
    db = _get_db()
    now = datetime.utcnow()
    age_cutoff = (now - timedelta(days=5)).isoformat()

    # Rule 1: frequently accessed
    # Rule 2: old + strong (survived decay)
    # Rule 3: high-value source types (always promote if in STM)
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
    if promoted > 0:
        _hnsw_invalidate()
    return promoted


def gc_stm():
    """Garbage collect STM: delete weak old memories and anything > 45 days.
    Pinned memories are never deleted.
    """
    db = _get_db()
    now = datetime.utcnow()
    cutoff_7d = (now - timedelta(days=7)).isoformat()
    cutoff_45d = (now - timedelta(days=45)).isoformat()

    # Delete STM with strength < 0.3 and older than 7 days (skip pinned)
    cur1 = db.execute(
        "DELETE FROM stm_memories WHERE strength < 0.3 AND created_at < ? AND promoted_to_ltm = 0 "
        "AND (lifecycle_state IS NULL OR lifecycle_state != 'pinned')",
        (cutoff_7d,)
    )
    # Delete any STM older than 45 days (skip pinned)
    cur2 = db.execute(
        "DELETE FROM stm_memories WHERE created_at < ? AND promoted_to_ltm = 0 "
        "AND (lifecycle_state IS NULL OR lifecycle_state != 'pinned')",
        (cutoff_45d,)
    )
    db.commit()
    deleted = (cur1.rowcount or 0) + (cur2.rowcount or 0)
    if deleted > 0:
        _hnsw_invalidate()
    return deleted


def gc_test_memories() -> int:
    """Purge STM memories from test/dev sessions that pollute strength metrics.
    Removes memories with test domains or known test content patterns.
    Returns count of deleted memories.
    """
    db = _get_db()
    test_domains = ("test", "test_session")
    deleted = 0

    # 1. Delete by test domain
    for domain in test_domains:
        cur = db.execute(
            "DELETE FROM stm_memories WHERE domain = ? "
            "AND (lifecycle_state IS NULL OR lifecycle_state != 'pinned')",
            (domain,)
        )
        deleted += cur.rowcount or 0

    # 2. Delete known test content patterns (empty domain, test-like content)
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
    """Garbage collect sensory memories older than max_age_hours. Returns count deleted."""
    db = _get_db()
    cutoff = (datetime.utcnow() - timedelta(hours=max_age_hours)).isoformat()
    cur = db.execute(
        "DELETE FROM stm_memories WHERE source_type = 'sensory' AND created_at < ? AND promoted_to_ltm = 0",
        (cutoff,)
    )
    db.commit()
    return cur.rowcount or 0


def gc_ltm_dormant(min_age_days: int = 30) -> int:
    """Delete dormant LTM memories with strength < 0.1 older than min_age_days."""
    db = _get_db()
    cutoff = (datetime.utcnow() - timedelta(days=min_age_days)).isoformat()
    cur = db.execute(
        "DELETE FROM ltm_memories WHERE is_dormant = 1 AND strength < 0.1 AND created_at < ?",
        (cutoff,)
    )
    db.commit()
    return cur.rowcount or 0

def dream_cycle(max_insights: int = 50) -> dict:
    """Memory Dreaming — discover hidden connections between recent memories.

    Retrieves memories accessed in the last 24h (STM + LTM), finds pairs with
    moderate similarity (0.4-0.7 — related but not duplicates), and creates
    'dream_insight' LTM memories linking them. Skips pairs already dreamed about.

    Uses pure vector math — no LLM calls.

    Returns:
        Dict with 'insights_created' count and 'insights' list of details.
    """
    db = _get_db()
    cutoff_24h = (datetime.utcnow() - timedelta(hours=24)).isoformat()

    # 1. Gather all memories accessed in the last 24 hours
    recent_memories = []

    stm_rows = db.execute(
        """SELECT id, content, embedding, source_type, source_title, domain, 'stm' as store
           FROM stm_memories
           WHERE last_accessed >= ? AND promoted_to_ltm = 0""",
        (cutoff_24h,)
    ).fetchall()

    ltm_rows = db.execute(
        """SELECT id, content, embedding, source_type, source_title, domain, 'ltm' as store
           FROM ltm_memories
           WHERE last_accessed >= ? AND is_dormant = 0""",
        (cutoff_24h,)
    ).fetchall()

    for row in stm_rows + ltm_rows:
        recent_memories.append({
            "id": row["id"],
            "content": row["content"],
            "vec": _blob_to_array(row["embedding"]),
            "source_type": row["source_type"],
            "source_title": row["source_title"] or "",
            "domain": row["domain"] or "",
            "store": row["store"],
        })

    if len(recent_memories) < 2:
        return {"insights_created": 0, "insights": [], "memories_scanned": len(recent_memories)}

    # 2. Get already-dreamed pairs to skip
    dreamed = set()
    for row in db.execute("SELECT memory_a_id, memory_b_id FROM dreamed_pairs").fetchall():
        dreamed.add((row["memory_a_id"], row["memory_b_id"]))
        dreamed.add((row["memory_b_id"], row["memory_a_id"]))

    # 3. Batch compute all pairwise cosine similarities
    #    Build matrix for fast numpy dot product
    n = len(recent_memories)
    vecs = np.array([m["vec"] for m in recent_memories], dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0  # avoid division by zero
    normalized = vecs / norms
    sim_matrix = normalized @ normalized.T  # (n x n) cosine similarity matrix

    # 4. Find pairs in the sweet spot (0.4-0.7) — related but not duplicates
    candidate_pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            score = float(sim_matrix[i, j])
            if 0.4 <= score <= 0.7:
                # Use composite key for dreamed check (store:id to disambiguate stm vs ltm)
                pair_key = (
                    f"{recent_memories[i]['store']}:{recent_memories[i]['id']}",
                    f"{recent_memories[j]['store']}:{recent_memories[j]['id']}",
                )
                # For DB tracking we use LTM IDs when both are LTM, else skip dreamed check
                a_id, b_id = recent_memories[i]["id"], recent_memories[j]["id"]
                if (a_id, b_id) in dreamed or (b_id, a_id) in dreamed:
                    continue
                candidate_pairs.append((i, j, score))

    # Sort by similarity descending (strongest connections first)
    candidate_pairs.sort(key=lambda x: x[2], reverse=True)

    # 5. Generate insights (capped at max_insights)
    insights = []
    for i, j, score in candidate_pairs[:max_insights]:
        mem_a = recent_memories[i]
        mem_b = recent_memories[j]

        # Build titles — use source_title if available, else first 60 chars of content
        title_a = mem_a["source_title"] or mem_a["content"][:60].replace("\n", " ").strip()
        title_b = mem_b["source_title"] or mem_b["content"][:60].replace("\n", " ").strip()

        # Build domain context
        domains = set(filter(None, [mem_a["domain"], mem_b["domain"]]))
        domain_str = ", ".join(domains) if domains else "general"

        # Create insight content
        insight_content = (
            f"[Dream Insight] Connection found between:\n"
            f"  A: {title_a}\n"
            f"  B: {title_b}\n"
            f"Similarity: {score:.3f} | Domains: {domain_str}\n"
            f"These memories appeared together in the same 24h window and share moderate semantic overlap, "
            f"suggesting a potential relationship worth investigating."
        )

        # Create embedding as average of the two source vectors (midpoint in vector space)
        insight_vec = (mem_a["vec"] + mem_b["vec"]) / 2.0
        insight_vec = insight_vec / (np.linalg.norm(insight_vec) or 1.0)  # re-normalize
        blob = _array_to_blob(insight_vec)

        # Store as LTM with dream_insight tag
        cur = db.execute(
            """INSERT INTO ltm_memories (content, embedding, source_type, source_id, source_title, domain, tags, strength)
               VALUES (?, ?, 'dream_insight', ?, ?, ?, 'dream_insight', 0.5)""",
            (insight_content, blob,
             f"{mem_a['store']}:{mem_a['id']},{mem_b['store']}:{mem_b['id']}",
             f"Dream: {title_a[:30]} <-> {title_b[:30]}",
             domain_str)
        )
        insight_id = cur.lastrowid

        # Track the dreamed pair
        a_id, b_id = mem_a["id"], mem_b["id"]
        try:
            db.execute(
                "INSERT OR IGNORE INTO dreamed_pairs (memory_a_id, memory_b_id, insight_id) VALUES (?, ?, ?)",
                (min(a_id, b_id), max(a_id, b_id), insight_id)
            )
        except Exception:
            pass

        insights.append({
            "insight_id": insight_id,
            "title_a": title_a[:80],
            "title_b": title_b[:80],
            "similarity": round(score, 4),
            "domain": domain_str,
        })

    db.commit()

    # Dream cap: archive oldest dream_insights if total exceeds MAX_DREAM_INSIGHTS
    MAX_DREAM_INSIGHTS = 50
    dream_count = db.execute(
        "SELECT COUNT(*) FROM ltm_memories WHERE source_type = 'dream_insight' AND is_dormant = 0"
    ).fetchone()[0]
    archived_dreams = 0
    if dream_count > MAX_DREAM_INSIGHTS:
        excess = dream_count - MAX_DREAM_INSIGHTS
        oldest = db.execute(
            "SELECT id FROM ltm_memories WHERE source_type = 'dream_insight' AND is_dormant = 0 "
            "ORDER BY strength ASC, created_at ASC LIMIT ?", (excess,)
        ).fetchall()
        for row in oldest:
            db.execute(
                "UPDATE ltm_memories SET lifecycle_state = 'archived' WHERE id = ?", (row["id"],)
            )
            archived_dreams += 1
        db.commit()

    result = {
        "insights_created": len(insights),
        "insights": insights,
        "memories_scanned": len(recent_memories),
        "candidates_found": len(candidate_pairs),
    }
    if archived_dreams > 0:
        result["dreams_archived"] = archived_dreams
        result["dreams_total"] = dream_count
    return result
