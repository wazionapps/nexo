"""NEXO Cognitive — Ingest, prediction error gate, quarantine, security."""
import json, math, re, base64
import numpy as np
from datetime import datetime, timedelta
from typing import Optional
from cognitive._core import (
    _get_db, embed, cosine_similarity, _blob_to_array, _array_to_blob,
    redact_secrets, extract_temporal_date, EMBEDDING_DIM,
    PE_GATE_REJECT, PE_GATE_REFINE, _gate_stats,
    initial_memory_profile, rehearsal_profile_update,
)


def _hnsw_notify_insert(store: str, db_id: int, vec: np.ndarray):
    """Notify HNSW index of a new memory insertion (best-effort)."""
    try:
        import hnsw_index
        if hnsw_index.is_available():
            hnsw_index.add_item(store, db_id, vec)
    except Exception:
        pass

def ingest(
    content: str,
    source_type: str,
    source_id: str = "",
    source_title: str = "",
    domain: str = "",
    source: str = "inferred",
    skip_quarantine: bool = False,
    bypass_gate: bool = False,
    bypass_security: bool = False,
    auto_pin: bool = False,
) -> int:
    """Embed and store content. Routes through quarantine unless skip_quarantine=True or source='user_direct'.

    Security scan runs FIRST (unless bypass_security=True).
    Prediction Error Gate runs BEFORE storage unless bypass_gate=True.
    If gate rejects (content too similar to existing memory), returns 0.
    If gate says 'refinement', merges into existing memory and returns its ID.

    Args:
        content: Text content to store
        source_type: Type of source (e.g. 'learning', 'change', 'diary')
        source_id: Optional source identifier
        source_title: Optional title
        domain: Optional domain tag
        source: Origin — 'user_direct', 'inferred', or 'agent_observation'
        skip_quarantine: If True, bypass quarantine and store directly in STM (backward compat)
        bypass_gate: If True, skip prediction error gate and store regardless
        bypass_security: If True, skip security scan (for trusted sources)

    Returns:
        Row ID (negative if quarantined, 0 if gate-rejected, positive if stored/refined)
    """
    # Security scan BEFORE prediction error gate (adapted from ShieldCortex pipeline)
    if not bypass_security:
        scan = security_scan(content)
        if scan["risk_score"] >= 0.8:
            # High risk — reject with reason logged
            return 0
        if scan["sanitized_content"] != content:
            # Use sanitized content going forward
            content = scan["sanitized_content"]

    # Run prediction error gate unless bypassed
    if not bypass_gate:
        should_store, novelty, reason, match = prediction_error_gate(content)
        if not should_store:
            return 0  # Gate rejected — content is redundant
        if reason == "refinement" and match:
            return _refine_memory(match, content)

    db = _get_db()
    clean_content = redact_secrets(content)
    was_redacted = 1 if clean_content != content else 0
    vec = embed(clean_content)
    blob = _array_to_blob(vec)
    temporal = extract_temporal_date(content)
    stability, difficulty = initial_memory_profile(source_type, store="stm")

    # Auto-pin: corrections and blocking learnings get pinned (zero decay, +0.2 boost)
    # This ensures user's corrections NEVER fade away
    _pin_lifecycle = 'active'
    if auto_pin or (source_type in ('learning', 'feedback') and
                    any(kw in content.upper() for kw in ('BLOCKING', 'CRÍTICO', 'CRITICAL', 'NUNCA', 'NEVER', 'PROHIBIDO'))):
        _pin_lifecycle = 'pinned'

    # user_direct = fast-track: quarantine then immediate promote
    if source == "user_direct" and not skip_quarantine:
        cur = db.execute(
            """INSERT INTO quarantine (content, embedding, source, source_type, source_id, source_title, domain, confidence, status, promoted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, 'promoted', datetime('now'))""",
            (clean_content, blob, source, source_type, source_id, source_title, domain)
        )
        db.commit()
        # Now actually store in STM
        cur2 = db.execute(
            """INSERT INTO stm_memories (content, embedding, source_type, source_id, source_title, domain, redaction_applied, temporal_date, lifecycle_state, stability, difficulty)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (clean_content, blob, source_type, source_id, source_title, domain, was_redacted, temporal, _pin_lifecycle, stability, difficulty)
        )
        db.commit()
        _hnsw_notify_insert("stm", cur2.lastrowid, vec)
        return cur2.lastrowid

    # skip_quarantine = direct STM (backward compatibility)
    if skip_quarantine:
        cur = db.execute(
            """INSERT INTO stm_memories (content, embedding, source_type, source_id, source_title, domain, redaction_applied, temporal_date, lifecycle_state, stability, difficulty)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (clean_content, blob, source_type, source_id, source_title, domain, was_redacted, temporal, _pin_lifecycle, stability, difficulty)
        )
        db.commit()
        _hnsw_notify_insert("stm", cur.lastrowid, vec)
        return cur.lastrowid

    # Route to quarantine
    cur = db.execute(
        """INSERT INTO quarantine (content, embedding, source, source_type, source_id, source_title, domain)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (clean_content, blob, source, source_type, source_id, source_title, domain)
    )
    db.commit()
    return -cur.lastrowid  # Negative = quarantined


def ingest_session(
    turns: list[dict],
    session_title: str = "",
    domain: str = "",
    chunk_size: int = 3,
) -> dict:
    """Ingest a conversation session with intelligent chunking and summary.

    Stores: (1) individual turns, (2) overlapping chunks for multi-hop context,
    (3) an extractive session summary.

    Args:
        turns: List of dicts with keys: content (required), source_id (optional), speaker (optional)
        session_title: Title for the session (e.g., "Session 5")
        domain: Domain tag
        chunk_size: Number of turns per chunk (default 3, with overlap of 1)

    Returns:
        Dict with counts: {"turns": N, "chunks": N, "summary": 1}
    """
    turn_ids = []
    turn_contents = []
    ingested_turns = 0

    # 1. Ingest individual turns
    for turn in turns:
        content = turn.get("content", "")
        source_id = turn.get("source_id", "")
        if not content:
            continue

        ingest(
            content=content,
            source_type="dialog",
            source_id=source_id,
            source_title=session_title,
            domain=domain,
            bypass_gate=True,
            skip_quarantine=True,
            bypass_security=True,
        )
        turn_ids.append(source_id)
        turn_contents.append(content)
        ingested_turns += 1

    # 2. Overlapping chunks for multi-hop context
    ingested_chunks = 0
    for i in range(0, len(turn_contents) - chunk_size + 1):
        chunk_content = "\n".join(turn_contents[i:i + chunk_size])
        chunk_ids = ",".join(turn_ids[i:i + chunk_size])
        ingest(
            content=chunk_content,
            source_type="dialog_chunk",
            source_id=chunk_ids,
            source_title=f"{session_title} chunk",
            domain=domain,
            bypass_gate=True,
            skip_quarantine=True,
            bypass_security=True,
        )
        ingested_chunks += 1

    # 3. Session summary (extractive)
    if turn_contents:
        speakers = set()
        topics = []
        for t in turn_contents:
            if ": " in t:
                parts = t.split(": ", 1)
                # Try to extract speaker from "[date] Speaker: text" pattern
                speaker_part = parts[0].split("] ")[-1] if "] " in parts[0] else parts[0]
                speakers.add(speaker_part.strip())
                topics.append(parts[1][:100])
            else:
                topics.append(t[:100])

        summary = f"{session_title} summary ({', '.join(speakers)}): "
        summary += " | ".join(topics[:5])
        if len(topics) > 5:
            summary += f" | ... ({len(topics)} total turns)"

        all_ids = ",".join(turn_ids)
        ingest(
            content=summary,
            source_type="session_summary",
            source_id=all_ids,
            source_title=f"{session_title} summary",
            domain=domain,
            bypass_gate=True,
            skip_quarantine=True,
            bypass_security=True,
        )

    return {"turns": ingested_turns, "chunks": ingested_chunks, "summary": 1 if turn_contents else 0}


def ingest_to_ltm(
    content: str,
    source_type: str,
    source_id: str = "",
    source_title: str = "",
    domain: str = "",
    tags: str = "",
    bypass_gate: bool = False
) -> int:
    """Embed and store content directly in LTM. Returns row ID.

    Prediction Error Gate runs BEFORE storage unless bypass_gate=True.
    If gate rejects, returns 0. If refinement, merges and returns existing ID.
    """
    # Run prediction error gate unless bypassed
    if not bypass_gate:
        should_store, novelty, reason, match = prediction_error_gate(content)
        if not should_store:
            return 0  # Gate rejected
        if reason == "refinement" and match:
            return _refine_memory(match, content)

    db = _get_db()
    clean_content = redact_secrets(content)
    was_redacted = 1 if clean_content != content else 0
    vec = embed(clean_content)
    blob = _array_to_blob(vec)
    stability, difficulty = initial_memory_profile(source_type, store="ltm")
    cur = db.execute(
        """INSERT INTO ltm_memories (content, embedding, source_type, source_id, source_title, domain, tags, redaction_applied, stability, difficulty)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (clean_content, blob, source_type, source_id, source_title, domain, tags, was_redacted, stability, difficulty)
    )
    db.commit()
    return cur.lastrowid

def ingest_sensory(
    content: str,
    source_id: str = "",
    domain: str = "",
    created_at: str = ""
) -> int:
    """Embed and store a sensory register event in STM with source_type='sensory'."""
    db = _get_db()
    clean_content = redact_secrets(content)
    was_redacted = 1 if clean_content != content else 0
    vec = embed(clean_content)
    blob = _array_to_blob(vec)
    ts = created_at or datetime.utcnow().isoformat()
    stability, difficulty = initial_memory_profile("sensory", store="stm")
    cur = db.execute(
        """INSERT INTO stm_memories (content, embedding, source_type, source_id, domain, created_at, redaction_applied, stability, difficulty)
           VALUES (?, ?, 'sensory', ?, ?, ?, ?, ?, ?)""",
        (clean_content, blob, source_id, domain, ts, was_redacted, stability, difficulty)
    )
    db.commit()
    return cur.lastrowid

# ---------------------------------------------------------------------------
# Prediction Error Gate — hippocampal novelty filter
# ---------------------------------------------------------------------------

def prediction_error_gate(
    content: str,
    threshold: float = PE_GATE_REJECT,
    refine_threshold: float = PE_GATE_REFINE,
) -> tuple[bool, float, str, Optional[dict]]:
    """Prediction Error Gate — hippocampal novelty filter for memory ingestion.

    Compares incoming content against ALL existing memories (STM + LTM).
    Decides whether the content is novel enough to store, a refinement of
    something existing, or redundant.

    Based on the neuroscience principle that prediction errors (mismatches
    between expected and actual input) gate what gets encoded into memory.
    High prediction error = novel = store. Low prediction error = redundant = reject.

    Args:
        content: The text content to evaluate
        threshold: Similarity above this -> reject as redundant (default 0.85)
        refine_threshold: Similarity between this and threshold -> refinement (default 0.70)

    Returns:
        Tuple of (should_store, novelty_score, reason, best_match_info)
        - should_store: True if content should be stored
        - novelty_score: 1.0 = completely novel, 0.0 = exact duplicate
        - reason: 'novel', 'refinement', 'rejected', or 'novel_sibling'
        - best_match_info: dict with best matching memory details, or None
    """
    global _gate_stats

    if not content or not content.strip():
        return (False, 0.0, "rejected", None)

    content_vec = embed(content[:500])
    if np.linalg.norm(content_vec) == 0:
        return (False, 0.0, "rejected", None)

    db = _get_db()
    best_score = 0.0
    best_match = None

    # Scan both STM and LTM for the closest match
    for table, store_name in [("stm_memories", "stm"), ("ltm_memories", "ltm")]:
        extra_where = ""
        if table == "stm_memories":
            extra_where = " AND promoted_to_ltm = 0"
        elif table == "ltm_memories":
            extra_where = " AND is_dormant = 0"

        rows = db.execute(
            f"SELECT id, content, embedding, source_type, domain FROM {table} WHERE 1=1{extra_where}"
        ).fetchall()

        for row in rows:
            vec = _blob_to_array(row["embedding"])
            score = cosine_similarity(content_vec, vec)
            if score > best_score:
                best_score = score
                best_match = {
                    "store": store_name,
                    "id": row["id"],
                    "content": row["content"],
                    "source_type": row["source_type"],
                    "domain": row["domain"],
                    "similarity": round(score, 4),
                }

    novelty_score = round(1.0 - best_score, 4)

    if best_score > threshold:
        # Check for siblings before rejecting -- if discriminating entities differ,
        # this is NOT a duplicate, it's a sibling (same fix for different platforms)
        if best_match:
            is_sibling, discriminators = _memories_are_siblings(content, best_match["content"])
            if is_sibling:
                _gate_stats["accepted_novel"] += 1
                best_match["discriminators"] = discriminators
                return (True, novelty_score, "novel_sibling", best_match)

        _gate_stats["rejected"] += 1
        return (False, novelty_score, "rejected", best_match)

    elif best_score >= refine_threshold:
        # Refinement zone -- similar but has enough new info to warrant update
        _gate_stats["accepted_refinement"] += 1
        return (True, novelty_score, "refinement", best_match)

    else:
        # Novel content -- no close match found
        _gate_stats["accepted_novel"] += 1
        return (True, novelty_score, "novel", best_match)


def _refine_memory(match_info: dict, new_content: str) -> int:
    """Merge new content into an existing memory (refinement, not replacement).

    Appends genuinely new information to the existing memory and re-embeds.

    Args:
        match_info: Dict from prediction_error_gate with store, id, content
        new_content: The new content that refines the existing memory

    Returns:
        The ID of the updated memory
    """
    db = _get_db()
    table = "stm_memories" if match_info["store"] == "stm" else "ltm_memories"
    memory_id = match_info["id"]
    profile_row = db.execute(
        f"SELECT stability, difficulty FROM {table} WHERE id = ?",
        (memory_id,),
    ).fetchone()
    current_stability = profile_row["stability"] if profile_row else 1.0
    current_difficulty = profile_row["difficulty"] if profile_row else 0.5

    # Check word-level diff to avoid appending near-identical text
    existing_words = set(match_info["content"].lower().split())
    new_words = set(new_content.lower().split())
    unique_new = new_words - existing_words

    if len(unique_new) < 3:
        # Almost no new words -- just strengthen the existing memory
        now = datetime.utcnow().isoformat()
        new_stability, new_difficulty = rehearsal_profile_update(
            current_stability,
            current_difficulty,
            score=0.7,
            refinement=True,
        )
        db.execute(
            f"UPDATE {table} SET strength = MIN(1.0, strength + 0.1), "
            f"access_count = access_count + 1, last_accessed = ?, stability = ?, difficulty = ? WHERE id = ?",
            (now, new_stability, new_difficulty, memory_id)
        )
        db.commit()
        return memory_id

    # Append new content as refinement
    merged_content = match_info["content"] + "\n\n[REFINED]: " + new_content
    new_vec = embed(merged_content)
    new_blob = _array_to_blob(new_vec)
    now = datetime.utcnow().isoformat()
    new_stability, new_difficulty = rehearsal_profile_update(
        current_stability,
        current_difficulty,
        score=0.82,
        refinement=True,
    )

    db.execute(
        f"UPDATE {table} SET content = ?, embedding = ?, strength = MIN(1.0, strength + 0.15), "
        f"access_count = access_count + 1, last_accessed = ?, stability = ?, difficulty = ? WHERE id = ?",
        (merged_content, new_blob, now, new_stability, new_difficulty, memory_id)
    )
    db.commit()
    return memory_id


def get_gate_stats() -> dict:
    """Return prediction error gate statistics for the current session."""
    total = sum(_gate_stats.values())
    return {
        "accepted_novel": _gate_stats["accepted_novel"],
        "accepted_refinement": _gate_stats["accepted_refinement"],
        "rejected": _gate_stats["rejected"],
        "total_evaluated": total,
        "rejection_rate_pct": round(_gate_stats["rejected"] / total * 100, 1) if total > 0 else 0.0,
    }


def detect_patterns(content_vec: np.ndarray, threshold: float = 0.65) -> list[dict]:
    """Compare a vector against LTM to find matching patterns (potential repetitions)."""
    db = _get_db()
    rows = db.execute("SELECT id, content, embedding, source_type, domain FROM ltm_memories WHERE is_dormant = 0").fetchall()
    matches = []
    for row in rows:
        vec = _blob_to_array(row["embedding"])
        score = cosine_similarity(content_vec, vec)
        if score >= threshold:
            matches.append({
                "ltm_id": row["id"],
                "content": row["content"][:200],
                "source_type": row["source_type"],
                "domain": row["domain"],
                "score": score,
            })
    matches.sort(key=lambda x: x["score"], reverse=True)
    return matches[:5]


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


def _check_quarantine_contradiction(content_vec: np.ndarray, new_content: str = "") -> list[dict]:
    """Check if a quarantined memory contradicts existing LTM.

    High cosine similarity (>0.8) means the topics are related, but that could be
    CONFIRMATION (same claim) or CONTRADICTION (opposite claim). We distinguish by
    checking for negation/opposition markers in the content.
    """
    db = _get_db()
    rows = db.execute(
        "SELECT id, content, embedding, strength FROM ltm_memories WHERE is_dormant = 0 AND strength > 0.5"
    ).fetchall()

    # Opposition markers — if the new content negates what LTM says
    NEGATION_MARKERS = {"not", "never", "don't", "doesn't", "no longer", "wrong",
                        "incorrect", "false", "opposite", "instead", "but actually",
                        "nunca", "no", "incorrecto", "falso", "contrario"}

    contradictions = []
    new_lower = new_content.lower() if new_content else ""

    for row in rows:
        vec = _blob_to_array(row["embedding"])
        score = cosine_similarity(content_vec, vec)
        if score >= 0.8:
            # High similarity — but is it confirmation or contradiction?
            existing_lower = row["content"].lower()

            # Check for negation markers in the difference between texts
            has_opposition = False
            if new_lower:
                # If new content has negation words about the same topic, likely contradiction
                for marker in NEGATION_MARKERS:
                    if marker in new_lower and marker not in existing_lower:
                        has_opposition = True
                        break
                    if marker in existing_lower and marker not in new_lower:
                        has_opposition = True
                        break

            if has_opposition:
                contradictions.append({
                    "ltm_id": row["id"],
                    "content": row["content"][:200],
                    "similarity": round(score, 3),
                    "strength": row["strength"],
                    "reason": "semantic_opposition",
                })
            # If no opposition markers → it's confirmation, not contradiction → skip

    return contradictions


def _check_quarantine_second_occurrence(content_vec: np.ndarray, exclude_id: int) -> bool:
    """Check if a similar memory already exists in quarantine (promoted or pending) — confirms the pattern."""
    db = _get_db()
    rows = db.execute(
        "SELECT id, embedding FROM quarantine WHERE id != ? AND status IN ('pending', 'promoted')",
        (exclude_id,)
    ).fetchall()
    for row in rows:
        vec = _blob_to_array(row["embedding"])
        score = cosine_similarity(content_vec, vec)
        if score >= 0.75:
            return True

    # Also check STM for existing similar memories
    stm_rows = db.execute(
        "SELECT embedding FROM stm_memories WHERE promoted_to_ltm = 0"
    ).fetchall()
    for row in stm_rows:
        vec = _blob_to_array(row["embedding"])
        score = cosine_similarity(content_vec, vec)
        if score >= 0.75:
            return True

    return False


def process_quarantine() -> dict:
    """Process the quarantine queue — promote, reject, or expire items based on policy.

    Promotion policy:
    - source='user_direct' → already promoted at ingest time
    - source='inferred' + confirmed by second occurrence → promote
    - source='agent_observation' + no LTM contradiction + >24h old → promote
    - Contradicts existing LTM → status='rejected', flag for dissonance check
    - >7 days without promotion → status='expired'

    Returns:
        Dict with counts: promoted, rejected, expired, still_pending
    """
    db = _get_db()
    now = datetime.utcnow()
    expire_cutoff = (now - timedelta(days=7)).isoformat()
    age_24h = (now - timedelta(hours=24)).isoformat()

    pending = db.execute(
        "SELECT * FROM quarantine WHERE status = 'pending'"
    ).fetchall()

    promoted = 0
    rejected = 0
    expired = 0
    still_pending = 0

    for row in pending:
        q_id = row["id"]
        content = row["content"]
        source = row["source"]
        created_at = row["created_at"]
        content_vec = _blob_to_array(row["embedding"])

        # Check expiration first
        if created_at < expire_cutoff:
            db.execute("UPDATE quarantine SET status = 'expired' WHERE id = ?", (q_id,))
            expired += 1
            continue

        # Check for contradiction with LTM
        contradictions = _check_quarantine_contradiction(content_vec, content)
        if contradictions:
            db.execute("UPDATE quarantine SET status = 'rejected', promotion_checks = promotion_checks + 1 WHERE id = ?", (q_id,))
            rejected += 1
            continue

        should_promote = False

        if source == "inferred":
            # Promote if confirmed by second occurrence
            if _check_quarantine_second_occurrence(content_vec, q_id):
                should_promote = True

        elif source == "agent_observation":
            # Promote after 24h if no contradiction (already checked above)
            if created_at <= age_24h:
                should_promote = True

        if should_promote:
            # Promote to STM
            cur = db.execute(
                """INSERT INTO stm_memories (content, embedding, source_type, source_id, source_title, domain, redaction_applied, stability, difficulty)
                   VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (content, row["embedding"], row["source_type"], row["source_id"],
                 row["source_title"], row["domain"], *initial_memory_profile(row["source_type"], store="stm"))
            )
            db.execute(
                "UPDATE quarantine SET status = 'promoted', promoted_at = datetime('now'), confidence = 1.0 WHERE id = ?",
                (q_id,)
            )
            promoted += 1
        else:
            db.execute("UPDATE quarantine SET promotion_checks = promotion_checks + 1 WHERE id = ?", (q_id,))
            still_pending += 1

    db.commit()

    return {
        "promoted": promoted,
        "rejected": rejected,
        "expired": expired,
        "still_pending": still_pending,
        "total_processed": promoted + rejected + expired + still_pending,
    }


def quarantine_list(status: str = "pending", limit: int = 20) -> list[dict]:
    """List quarantine items by status.

    Args:
        status: Filter by status — 'pending', 'promoted', 'rejected', 'expired', or 'all'
        limit: Max results
    """
    db = _get_db()
    if status == "all":
        rows = db.execute(
            "SELECT * FROM quarantine ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM quarantine WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit)
        ).fetchall()

    results = []
    for row in rows:
        results.append({
            "id": row["id"],
            "content": row["content"][:200],
            "source": row["source"],
            "source_type": row["source_type"],
            "domain": row["domain"],
            "confidence": row["confidence"],
            "promotion_checks": row["promotion_checks"],
            "status": row["status"],
            "created_at": row["created_at"],
            "promoted_at": row["promoted_at"],
        })
    return results


def quarantine_promote(quarantine_id: int) -> str:
    """Manually promote a quarantine item to STM.

    Args:
        quarantine_id: ID of the quarantine entry to promote
    """
    db = _get_db()
    row = db.execute("SELECT * FROM quarantine WHERE id = ?", (quarantine_id,)).fetchone()
    if row is None:
        return f"ERROR: Quarantine item #{quarantine_id} not found."
    if row["status"] == "promoted":
        return f"Quarantine item #{quarantine_id} is already promoted."

    # Insert into STM
    db.execute(
        """INSERT INTO stm_memories (content, embedding, source_type, source_id, source_title, domain, redaction_applied, stability, difficulty)
           VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
        (row["content"], row["embedding"], row["source_type"], row["source_id"],
         row["source_title"], row["domain"], *initial_memory_profile(row["source_type"], store="stm"))
    )
    db.execute(
        "UPDATE quarantine SET status = 'promoted', promoted_at = datetime('now'), confidence = 1.0 WHERE id = ?",
        (quarantine_id,)
    )
    db.commit()
    return f"Quarantine item #{quarantine_id} promoted to STM."


def quarantine_reject(quarantine_id: int, reason: str = "") -> str:
    """Manually reject a quarantine item.

    Args:
        quarantine_id: ID of the quarantine entry to reject
        reason: Optional rejection reason
    """
    db = _get_db()
    row = db.execute("SELECT * FROM quarantine WHERE id = ?", (quarantine_id,)).fetchone()
    if row is None:
        return f"ERROR: Quarantine item #{quarantine_id} not found."
    if row["status"] in ("promoted", "rejected"):
        return f"Quarantine item #{quarantine_id} is already {row['status']}."

    db.execute("UPDATE quarantine SET status = 'rejected' WHERE id = ?", (quarantine_id,))
    db.commit()
    return f"Quarantine item #{quarantine_id} rejected.{' Reason: ' + reason if reason else ''}"


def quarantine_stats() -> dict:
    """Return quarantine queue statistics."""
    db = _get_db()
    counts = {}
    for status in ("pending", "promoted", "rejected", "expired"):
        counts[status] = db.execute(
            "SELECT COUNT(*) FROM quarantine WHERE status = ?", (status,)
        ).fetchone()[0]
    counts["total"] = sum(counts.values())
    return counts


def _sanitize_memory_content(content: str) -> str:
    """Sanitize retrieved memory content to prevent prompt injection.

    Memories are USER DATA, not instructions. This prevents stored content
    from containing directives like 'ignore previous instructions'.
    """
    # Wrap in evidence markers so the LLM treats it as data, not commands
    # Strip any attempt to break out of the evidence context
    content = content.replace("<system>", "[system]").replace("</system>", "[/system]")
    content = content.replace("<human>", "[human]").replace("</human>", "[/human]")
    content = content.replace("<assistant>", "[assistant]").replace("</assistant>", "[/assistant]")
    return content


# Injection patterns (adapted from ShieldCortex instruction-detector.ts)
_INJECTION_PATTERNS = [
    (re.compile(r'\[SYSTEM:', re.IGNORECASE), "system_prompt_marker", 0.9),
    (re.compile(r'<<SYS>>', re.IGNORECASE), "system_prompt_marker", 0.9),
    (re.compile(r'\[INST\]', re.IGNORECASE), "system_prompt_marker", 0.9),
    (re.compile(r'<\|im_start\|>', re.IGNORECASE), "system_prompt_marker", 0.9),
    (re.compile(r'<\|system\|>', re.IGNORECASE), "system_prompt_marker", 0.9),
    (re.compile(r'^SYSTEM\s*:', re.IGNORECASE | re.MULTILINE), "system_prompt_marker", 0.9),
    (re.compile(r'ignore\s+(all\s+)?previous\s+(instructions?|prompts?|context)', re.IGNORECASE), "hidden_instruction", 0.8),
    (re.compile(r'forget\s+everything', re.IGNORECASE), "hidden_instruction", 0.8),
    (re.compile(r'new\s+instructions?\s*:', re.IGNORECASE), "hidden_instruction", 0.8),
    (re.compile(r'you\s+are\s+now\b', re.IGNORECASE), "hidden_instruction", 0.8),
    (re.compile(r'disregard\s+(all\s+)?(previous|above|prior)', re.IGNORECASE), "hidden_instruction", 0.8),
    (re.compile(r'override\s+(previous|all|system)', re.IGNORECASE), "hidden_instruction", 0.8),
    (re.compile(r'save\s+(this\s+)?to\s+memory', re.IGNORECASE), "memory_manipulation", 0.7),
    (re.compile(r'remember\s+this\s+(instruction|command|rule)', re.IGNORECASE), "memory_manipulation", 0.7),
    (re.compile(r'from\s+now\s+on\s*(,\s*)?always', re.IGNORECASE), "memory_manipulation", 0.7),
    (re.compile(r'inject\s+(into\s+)?memory', re.IGNORECASE), "memory_manipulation", 0.7),
    (re.compile(r'your\s+new\s+rule\s+is', re.IGNORECASE), "behavioral_mod", 0.7),
    (re.compile(r'always\s+respond\s+with', re.IGNORECASE), "behavioral_mod", 0.7),
    (re.compile(r'when\s+(the\s+)?user\s+asks', re.IGNORECASE), "behavioral_mod", 0.7),
    (re.compile(r'\n{5,}[\s\S]{0,500}\b(instruction|command|system|ignore)\b', re.IGNORECASE), "delimiter_attack", 0.75),
    (re.compile(r'<!--[\s\S]{0,200}?(instruction|command|system|ignore|inject|override)[\s\S]{0,200}?-->', re.IGNORECASE), "delimiter_attack", 0.75),
]
_MAX_SECURITY_SCAN_LENGTH = 50000


def security_scan(content: str) -> dict:
    """Security scan for memory poisoning defense.

    Adapted from ShieldCortex's 6-layer defence pipeline. Checks:
    1. Input sanitization — strip injection patterns
    2. Pattern detection — base64, homoglyphs, invisible chars
    3. Behavioral scoring — content trying to modify NEXO behavior
    4. Credential detection — reuses existing redact_secrets()

    Args:
        content: Text content to scan

    Returns:
        Dict with safe (bool), flags (list), sanitized_content (str),
        risk_score (float 0-1)
    """
    if not content or not content.strip():
        return {"safe": True, "flags": [], "sanitized_content": "", "risk_score": 0.0}

    flags = []
    max_weight = 0.0
    total_weight = 0.0
    matches_count = 0
    sanitized = content

    # Truncate for safety (ShieldCortex pattern)
    _max_scan = 10000
    scan_text = content[:_max_scan] if len(content) > _max_scan else content

    # --- Layer 1: Injection pattern detection ---
    for pattern, category, weight in _INJECTION_PATTERNS:
        if pattern.search(scan_text):
            flag = f"{category}:{pattern.pattern[:50]}"
            flags.append(flag)
            max_weight = max(max_weight, weight)
            total_weight += weight
            matches_count += 1
            # Sanitize: remove the matched pattern
            sanitized = pattern.sub("[SANITIZED]", sanitized)

    # --- Layer 2: Encoding/obfuscation detection (from ShieldCortex encoding-detector.ts) ---

    # Base64 blocks > 100 chars
    b64_pattern = re.compile(r'(?:[A-Za-z0-9+/]{4}){25,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?')
    b64_matches = b64_pattern.findall(scan_text)
    for b64_match in b64_matches:
        try:
            decoded = base64.b64decode(b64_match).decode("utf-8", errors="ignore")
            printable_ratio = len(re.sub(r'[^\x20-\x7E]', '', decoded)) / max(len(decoded), 1)
            if printable_ratio > 0.7 and len(decoded) > 10:
                flags.append(f"base64_encoded:{decoded[:60]}")
                max_weight = max(max_weight, 0.6)
                total_weight += 0.6
                matches_count += 1
        except Exception:
            pass

    # Zero-width / invisible characters (from ShieldCortex)
    invisible_chars = re.findall(r'[\u200B\u200C\u200D\uFEFF\u202E]', scan_text)
    if len(invisible_chars) > 2:
        flags.append(f"invisible_chars:{len(invisible_chars)}_found")
        max_weight = max(max_weight, 0.5)
        total_weight += 0.5
        matches_count += 1
        # Remove invisible chars
        sanitized = re.sub(r'[\u200B\u200C\u200D\uFEFF\u202E]', '', sanitized)

    # Unicode homoglyphs — Cyrillic chars that look like Latin (from ShieldCortex)
    homoglyphs = re.findall(
        r'[\u0430\u0435\u043E\u0440\u0441\u0443\u0445\u0410\u0412\u0415\u041A\u041C\u041D\u041E\u0420\u0421\u0422\u0423\u0425]',
        scan_text
    )
    if len(homoglyphs) > 3:
        flags.append(f"unicode_homoglyphs:{len(homoglyphs)}_cyrillic")
        max_weight = max(max_weight, 0.5)
        total_weight += 0.5
        matches_count += 1

    # --- Layer 3: Behavioral scoring ---
    behavioral_patterns = [
        (re.compile(r'\balways\s+do\b', re.IGNORECASE), "behavioral:always_do"),
        (re.compile(r'\bnever\s+do\b', re.IGNORECASE), "behavioral:never_do"),
        (re.compile(r'\byour\s+new\s+rule\b', re.IGNORECASE), "behavioral:new_rule"),
        (re.compile(r'\byou\s+must\s+always\b', re.IGNORECASE), "behavioral:must_always"),
        (re.compile(r'\bchange\s+your\s+behavior\b', re.IGNORECASE), "behavioral:change_behavior"),
    ]
    for bp, label in behavioral_patterns:
        if bp.search(scan_text):
            flags.append(label)
            max_weight = max(max_weight, 0.4)
            total_weight += 0.4
            matches_count += 1

    # --- Layer 4: Credential detection (reuse existing redact_secrets) ---
    redacted = redact_secrets(scan_text)
    if redacted != scan_text:
        flags.append("credentials_detected")
        sanitized = redact_secrets(sanitized)
        # Don't increase risk score for creds — still store (redacted)
        # but flag for awareness

    # Calculate risk score (0-1): weighted by max_weight and count
    if matches_count == 0:
        risk_score = 0.0
    else:
        # ShieldCortex approach: max weight dominates, count adds diminishing returns
        risk_score = min(1.0, max_weight + (matches_count - 1) * 0.05)

    safe = risk_score < 0.5

    return {
        "safe": safe,
        "flags": flags,
        "sanitized_content": sanitized,
        "risk_score": round(risk_score, 3),
    }
