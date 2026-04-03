"""NEXO Cognitive — Memory operations: format, stats, consolidation, somatic."""
import json, math, re
import numpy as np
from datetime import datetime, timedelta
from cognitive._core import _get_db, embed, cosine_similarity, _blob_to_array, _array_to_blob, EMBEDDING_DIM, DISCRIMINATING_ENTITIES
from cognitive._ingest import _sanitize_memory_content


def _quarantine_stats():
    from cognitive._ingest import quarantine_stats
    return quarantine_stats()


def _get_gate_stats():
    from cognitive._ingest import get_gate_stats
    return get_gate_stats()

def format_results(results: list[dict]) -> str:
    """Format search results with enriched context."""
    if not results:
        return "No results found."

    lines = []
    for r in results:
        score = r["score"]
        stype = r["source_type"].upper()
        domain = r.get("domain", "")
        title = r.get("source_title", "")
        content = _sanitize_memory_content(r["content"])

        # Header
        domain_str = f" ({domain})" if domain else ""
        title_str = f': "{title}"' if title else ""
        header = f"[{score:.2f}] {stype}{domain_str}{title_str}"

        # Content preview (300 chars)
        preview = content[:300]
        if len(content) > 300:
            preview += "..."

        # Proto-procedural: detect sequential markers in change logs
        if r["source_type"] == "change" and any(m in content for m in ["1.", "2.", "3.", "step ", "Step ", "then ", "first ", "First "]):
            header += " [PROCEDURE]"

        store_tag = r["store"].upper()
        reactivated = " [REACTIVATED]" if r.get("reactivated") else ""
        explanation = r.get("explanation", "")
        explain_line = f"\n  ⚙ {explanation}" if explanation else ""
        lines.append(f"{header} [{store_tag}]{reactivated}\n  {preview}{explain_line}")

        # Sibling mention: if this LTM memory has siblings, note them
        if r["store"] == "ltm":
            try:
                siblings = get_siblings(r["id"])
                if siblings:
                    for sib in siblings[:2]:
                        disc_str = ", ".join(sib["discriminators"].split(",")[:3])
                        lines.append(f"  ↳ SIBLING #{sib['sibling_id']} ({sib['domain']}): differs in [{disc_str}] — {sib['content'][:80]}...")
            except Exception:
                pass

    return "\n\n".join(lines)


def get_metrics(days: int = 7) -> dict:
    """Calculate spec section 9 metrics over the last N days.

    Returns:
        retrieval_relevance: % of retrievals with top_score >= 0.6
        repeat_error_rate: % of new learnings that duplicate existing LTM (cosine > 0.8)
        avg_top_score: average best match score across all retrievals
        total_retrievals: number of retrievals in period
        retrievals_per_day: average retrievals per day
        score_distribution: histogram buckets [<0.5, 0.5-0.6, 0.6-0.7, 0.7-0.8, >0.8]
    """
    db = _get_db()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

    rows = db.execute(
        "SELECT top_score FROM retrieval_log WHERE created_at >= ?", (cutoff,)
    ).fetchall()

    total = len(rows)
    if total == 0:
        return {
            "period_days": days,
            "total_retrievals": 0,
            "retrieval_relevance_pct": 0.0,
            "avg_top_score": 0.0,
            "retrievals_per_day": 0.0,
            "score_distribution": {"below_50": 0, "50_60": 0, "60_70": 0, "70_80": 0, "above_80": 0},
            "needs_multilingual": False,
        }

    scores = [r[0] for r in rows]
    relevant = sum(1 for s in scores if s >= 0.6)
    relevance_pct = round(relevant / total * 100, 1)
    avg_score = round(sum(scores) / total, 3)

    dist = {"below_50": 0, "50_60": 0, "60_70": 0, "70_80": 0, "above_80": 0}
    for s in scores:
        if s < 0.5:
            dist["below_50"] += 1
        elif s < 0.6:
            dist["50_60"] += 1
        elif s < 0.7:
            dist["60_70"] += 1
        elif s < 0.8:
            dist["70_80"] += 1
        else:
            dist["above_80"] += 1

    # Check if multilingual model is needed (spec 13.3)
    needs_multilingual = relevance_pct < 70.0 and total >= 10

    return {
        "period_days": days,
        "total_retrievals": total,
        "retrieval_relevance_pct": relevance_pct,
        "avg_top_score": avg_score,
        "retrievals_per_day": round(total / days, 1),
        "score_distribution": dist,
        "needs_multilingual": needs_multilingual,
    }


def check_repeat_errors() -> dict:
    """Compare recent learnings in STM against LTM to find duplicates (spec section 9).

    Returns count of new learnings that are semantically duplicate (cosine > 0.8).
    """
    db = _get_db()
    cutoff_7d = (datetime.utcnow() - timedelta(days=7)).isoformat()

    # Recent learning STM entries
    new_learnings = db.execute(
        "SELECT id, content, embedding FROM stm_memories WHERE source_type = 'learning' AND created_at >= ? AND promoted_to_ltm = 0",
        (cutoff_7d,)
    ).fetchall()

    # All LTM learnings
    ltm_learnings = db.execute(
        "SELECT id, content, embedding FROM ltm_memories WHERE source_type = 'learning' AND is_dormant = 0"
    ).fetchall()

    if not new_learnings or not ltm_learnings:
        return {"new_count": len(new_learnings), "duplicate_count": 0, "repeat_rate_pct": 0.0, "duplicates": []}

    duplicates = []
    for new in new_learnings:
        new_vec = _blob_to_array(new["embedding"])
        for ltm in ltm_learnings:
            ltm_vec = _blob_to_array(ltm["embedding"])
            score = cosine_similarity(new_vec, ltm_vec)
            if score > 0.8:
                duplicates.append({
                    "new_stm_id": new["id"],
                    "new_content": new["content"][:100],
                    "ltm_id": ltm["id"],
                    "ltm_content": ltm["content"][:100],
                    "score": round(score, 3),
                })
                break  # One match is enough

    repeat_rate = round(len(duplicates) / len(new_learnings) * 100, 1) if new_learnings else 0.0

    return {
        "new_count": len(new_learnings),
        "duplicate_count": len(duplicates),
        "repeat_rate_pct": repeat_rate,
        "duplicates": duplicates[:10],
    }


def rehearse_by_content(content_keywords: str, source_type: str = ""):
    """Passive rehearsal: find and strengthen cognitive memories that match content from classic tools.

    Called when nexo_recall or nexo_learning_search return results. Strengthens matching
    memories without returning them (side effect only). This closes the rehearsal loop
    so memories accessed via keyword tools also get reinforced in the vector store.

    Args:
        content_keywords: Text to match against (e.g., learning title + content)
        source_type: Optional filter by source_type
    """
    if not content_keywords or len(content_keywords.strip()) < 10:
        return

    try:
        db = _get_db()
        query_vec = embed(content_keywords[:500])  # cap to avoid slow embedding
        if np.linalg.norm(query_vec) == 0:
            return

        now = datetime.utcnow().isoformat()

        # Search both stores for matches >= 0.7
        for table in ("stm_memories", "ltm_memories"):
            extra_where = ""
            if table == "stm_memories":
                extra_where = " AND promoted_to_ltm = 0"
            if table == "ltm_memories":
                extra_where = " AND is_dormant = 0"

            rows = db.execute(f"SELECT id, embedding FROM {table} WHERE 1=1{extra_where}").fetchall()
            for row in rows:
                vec = _blob_to_array(row["embedding"])
                score = cosine_similarity(query_vec, vec)
                if score >= 0.7:
                    db.execute(
                        f"UPDATE {table} SET strength = 1.0, access_count = access_count + 1, last_accessed = ? WHERE id = ?",
                        (now, row["id"])
                    )

        db.commit()
    except Exception:
        pass  # Rehearsal is best-effort, never block the main tool


def _extract_discriminators(text: str) -> set:
    """Extract discriminating entities from text (OS, platform, language, infra)."""
    words = set(text.lower().split())
    # Also check for multi-word patterns
    text_lower = text.lower()
    found = set()
    for entity in DISCRIMINATING_ENTITIES:
        if entity in words or entity in text_lower:
            found.add(entity)
    return found


def _memories_are_siblings(content_a: str, content_b: str) -> tuple[bool, list[str]]:
    """Check if two memories are siblings (similar-but-incompatible).

    Returns (is_sibling, list_of_discriminating_entities_that_differ).
    """
    disc_a = _extract_discriminators(content_a)
    disc_b = _extract_discriminators(content_b)

    # Entities present in one but not the other
    only_a = disc_a - disc_b
    only_b = disc_b - disc_a

    if only_a or only_b:
        # There are discriminating entities that differ — these are siblings
        diff = sorted(only_a | only_b)
        return True, diff

    return False, []


def consolidate_semantic(threshold: float = 0.9, dry_run: bool = False) -> dict:
    """Merge LTM memories with cosine similarity > threshold, with discriminative fusion.

    Before merging, checks for discriminating entities (OS, platform, language, etc.).
    If two memories are >90% similar but differ in critical entities, they become
    "siblings" (linked but NOT merged) instead of being consolidated.

    Args:
        threshold: Cosine similarity threshold for considering duplicates (default 0.9)
        dry_run: If True, return pairs without merging

    Returns:
        Dict with 'merged' (list of merge actions) and 'siblings' (list of sibling links created)
    """
    db = _get_db()
    rows = db.execute(
        "SELECT id, content, embedding, source_type, domain, access_count, strength FROM ltm_memories WHERE is_dormant = 0"
    ).fetchall()

    if len(rows) < 2:
        return {"merged": [], "siblings": []}

    memories = []
    for row in rows:
        memories.append({
            "id": row["id"],
            "content": row["content"],
            "vec": _blob_to_array(row["embedding"]),
            "source_type": row["source_type"],
            "domain": row["domain"],
            "access_count": row["access_count"],
            "strength": row["strength"],
        })

    merged_ids = set()
    merge_actions = []
    sibling_actions = []

    for i in range(len(memories)):
        if memories[i]["id"] in merged_ids:
            continue
        for j in range(i + 1, len(memories)):
            if memories[j]["id"] in merged_ids:
                continue

            score = cosine_similarity(memories[i]["vec"], memories[j]["vec"])
            if score < threshold:
                continue

            # Check for discriminating entities before merging
            is_sibling, discriminators = _memories_are_siblings(
                memories[i]["content"], memories[j]["content"]
            )

            if is_sibling:
                # Don't merge — create sibling relationship
                sibling_action = {
                    "memory_a_id": memories[i]["id"],
                    "memory_b_id": memories[j]["id"],
                    "score": round(score, 4),
                    "discriminators": discriminators,
                    "content_a": memories[i]["content"][:100],
                    "content_b": memories[j]["content"][:100],
                }

                if not dry_run:
                    try:
                        db.execute(
                            "INSERT OR IGNORE INTO memory_siblings (memory_a_id, memory_b_id, similarity, discriminators) VALUES (?, ?, ?, ?)",
                            (memories[i]["id"], memories[j]["id"], score, ",".join(discriminators))
                        )
                    except Exception:
                        pass

                sibling_actions.append(sibling_action)
                continue

            # Safe to merge — no discriminating entities differ
            if memories[i]["access_count"] >= memories[j]["access_count"]:
                keep, drop = memories[i], memories[j]
            else:
                keep, drop = memories[j], memories[i]

            action = {
                "keep_id": keep["id"],
                "drop_id": drop["id"],
                "score": round(score, 4),
                "keep_content": keep["content"][:100],
                "drop_content": drop["content"][:100],
                "keep_access": keep["access_count"],
                "drop_access": drop["access_count"],
            }

            if not dry_run:
                separator = "\n\n[CONSOLIDATED]: "
                new_content = keep["content"]
                drop_words = set(drop["content"].lower().split())
                keep_words = set(keep["content"].lower().split())
                unique_words = drop_words - keep_words
                if len(unique_words) > 5:
                    new_content = keep["content"] + separator + drop["content"]

                new_vec = embed(new_content)
                new_blob = _array_to_blob(new_vec)

                db.execute(
                    "UPDATE ltm_memories SET content = ?, embedding = ?, access_count = access_count + ? WHERE id = ?",
                    (new_content, new_blob, drop["access_count"], keep["id"])
                )
                db.execute("DELETE FROM ltm_memories WHERE id = ?", (drop["id"],))
                merged_ids.add(drop["id"])

            merge_actions.append(action)

    if not dry_run and (merge_actions or sibling_actions):
        db.commit()

    return {"merged": merge_actions, "siblings": sibling_actions}


def get_siblings(memory_id: int) -> list[dict]:
    """Get sibling memories for a given memory ID (similar-but-incompatible)."""
    db = _get_db()
    rows = db.execute(
        """SELECT s.*,
                  CASE WHEN s.memory_a_id = ? THEN s.memory_b_id ELSE s.memory_a_id END as sibling_id
           FROM memory_siblings s
           WHERE s.memory_a_id = ? OR s.memory_b_id = ?""",
        (memory_id, memory_id, memory_id)
    ).fetchall()

    siblings = []
    for row in rows:
        sib_id = row["sibling_id"]
        sib_mem = db.execute("SELECT content, domain, source_type FROM ltm_memories WHERE id = ?", (sib_id,)).fetchone()
        if sib_mem:
            siblings.append({
                "sibling_id": sib_id,
                "similarity": row["similarity"],
                "discriminators": row["discriminators"],
                "content": sib_mem["content"][:200],
                "domain": sib_mem["domain"],
            })
    return siblings

def get_stats() -> dict:
    """Return statistics about the cognitive memory system."""
    db = _get_db()

    stm_active = db.execute("SELECT COUNT(*) FROM stm_memories WHERE lifecycle_state IN ('active', 'pinned') AND promoted_to_ltm = 0").fetchone()[0]
    stm_promoted = db.execute("SELECT COUNT(*) FROM stm_memories WHERE promoted_to_ltm = 1").fetchone()[0]
    stm_total = db.execute("SELECT COUNT(*) FROM stm_memories WHERE lifecycle_state IN ('active', 'pinned')").fetchone()[0]
    ltm_active = db.execute("SELECT COUNT(*) FROM ltm_memories WHERE is_dormant = 0").fetchone()[0]
    ltm_dormant = db.execute("SELECT COUNT(*) FROM ltm_memories WHERE is_dormant = 1").fetchone()[0]

    avg_stm = db.execute("SELECT AVG(strength) FROM stm_memories WHERE lifecycle_state IN ('active', 'pinned') AND promoted_to_ltm = 0").fetchone()[0] or 0.0
    avg_ltm = db.execute("SELECT AVG(strength) FROM ltm_memories WHERE is_dormant = 0").fetchone()[0] or 0.0

    total_retrievals = db.execute("SELECT COUNT(*) FROM retrieval_log").fetchone()[0]
    avg_retrieval_score = db.execute("SELECT AVG(top_score) FROM retrieval_log").fetchone()[0] or 0.0

    top_domains_stm = db.execute(
        "SELECT domain, COUNT(*) as cnt FROM stm_memories WHERE lifecycle_state IN ('active', 'pinned') AND promoted_to_ltm = 0 AND domain != '' GROUP BY domain ORDER BY cnt DESC LIMIT 5"
    ).fetchall()
    top_domains_ltm = db.execute(
        "SELECT domain, COUNT(*) as cnt FROM ltm_memories WHERE is_dormant = 0 AND domain != '' GROUP BY domain ORDER BY cnt DESC LIMIT 5"
    ).fetchall()

    # Quarantine stats
    q_stats = _quarantine_stats()

    return {
        "stm_active": stm_active,
        "stm_promoted": stm_promoted,
        "stm_total": stm_total,
        "ltm_active": ltm_active,
        "ltm_dormant": ltm_dormant,
        "avg_stm_strength": round(avg_stm, 3),
        "avg_ltm_strength": round(avg_ltm, 3),
        "total_retrievals": total_retrievals,
        "avg_retrieval_score": round(avg_retrieval_score, 3),
        "top_domains_stm": [(r["domain"], r["cnt"]) for r in top_domains_stm],
        "top_domains_ltm": [(r["domain"], r["cnt"]) for r in top_domains_ltm],
        "quarantine": q_stats,
        "prediction_error_gate": _get_gate_stats(),
    }

def set_lifecycle(memory_id: int, state: str, store: str = "auto", snooze_until: str = "") -> str:
    """Set the lifecycle state of a memory.

    Args:
        memory_id: Memory ID
        state: 'active', 'pinned', 'snoozed', 'archived'
        store: 'stm', 'ltm', or 'auto' (tries both)
        snooze_until: Required for 'snoozed' state — ISO date string (YYYY-MM-DD or full datetime)
    """
    if state not in ("active", "pinned", "snoozed", "archived"):
        return f"Invalid state: {state}. Must be active, pinned, snoozed, or archived."

    if state == "snoozed" and not snooze_until:
        return "snooze_until is required when setting state to 'snoozed'."

    db = _get_db()

    tables = []
    if store == "auto":
        tables = ["stm_memories", "ltm_memories"]
    elif store == "stm":
        tables = ["stm_memories"]
    elif store == "ltm":
        tables = ["ltm_memories"]
    else:
        return f"Invalid store: {store}. Must be stm, ltm, or auto."

    found = False
    found_table = None
    for table in tables:
        row = db.execute(f"SELECT id FROM {table} WHERE id = ?", (memory_id,)).fetchone()
        if row:
            found = True
            found_table = table
            break

    if not found:
        return f"Memory #{memory_id} not found in {store}."

    snooze_val = snooze_until if state == "snoozed" else None
    db.execute(
        f"UPDATE {found_table} SET lifecycle_state = ?, snooze_until = ? WHERE id = ?",
        (state, snooze_val, memory_id)
    )
    db.commit()

    store_name = "STM" if found_table == "stm_memories" else "LTM"
    extra = f" until {snooze_until}" if state == "snoozed" else ""
    return f"Memory #{memory_id} ({store_name}) → {state}{extra}"


# ---------------------------------------------------------------------------
# Feature 1: Auto-Merge Duplicates
# Inspired by Vestige's union-find clustering and claude-cortex's Jaccard
# similarity merge. Runs during sleep cycle AFTER dream_cycle.
# ---------------------------------------------------------------------------

def auto_merge_duplicates(threshold: float = 0.92) -> dict:
    """Auto-merge near-duplicate LTM memories with cosine similarity > threshold.

    Unlike consolidate_semantic (threshold=0.9, runs during decay), this uses a
    higher threshold (0.92) and is designed for the sleep cycle. It respects
    sibling detection: memories with differing discriminating entities are never
    merged, even at 0.99 similarity.

    Merge strategy (adapted from claude-cortex):
    - Keep the longer/richer memory
    - Append unique info from the shorter one (if >5 unique words)
    - Re-embed merged content
    - Sum access_count from both
    - Delete the duplicate
    - Log every merge for audit

    Returns:
        Dict with scanned, merged, kept counts and merge_log details.
    """
    db = _get_db()
    rows = db.execute(
        "SELECT id, content, embedding, source_type, domain, access_count, strength, tags "
        "FROM ltm_memories WHERE is_dormant = 0 AND "
        "(lifecycle_state IS NULL OR lifecycle_state = 'active')"
    ).fetchall()

    if len(rows) < 2:
        return {"scanned": len(rows), "merged": 0, "kept": len(rows), "merge_log": []}

    # Build memory list with vectors (batch load like dream_cycle)
    memories = []
    for row in rows:
        memories.append({
            "id": row["id"],
            "content": row["content"],
            "vec": _blob_to_array(row["embedding"]),
            "source_type": row["source_type"],
            "domain": row["domain"] or "",
            "access_count": row["access_count"],
            "strength": row["strength"],
            "tags": row["tags"] or "",
        })

    n = len(memories)

    # Batch cosine similarity matrix (same approach as dream_cycle)
    vecs = np.array([m["vec"] for m in memories], dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normalized = vecs / norms
    sim_matrix = normalized @ normalized.T

    merged_ids = set()
    merge_log = []

    for i in range(n):
        if memories[i]["id"] in merged_ids:
            continue
        for j in range(i + 1, n):
            if memories[j]["id"] in merged_ids:
                continue

            score = float(sim_matrix[i, j])
            if score < threshold:
                continue

            # Sibling check — never merge if discriminating entities differ
            is_sibling, discriminators = _memories_are_siblings(
                memories[i]["content"], memories[j]["content"]
            )
            if is_sibling:
                continue

            # Domain/tags compatibility check
            if memories[i]["domain"] and memories[j]["domain"]:
                if memories[i]["domain"] != memories[j]["domain"]:
                    continue

            # Determine keep vs drop: prefer longer content, then higher access_count
            if len(memories[i]["content"]) >= len(memories[j]["content"]):
                keep, drop = memories[i], memories[j]
            elif memories[i]["access_count"] > memories[j]["access_count"]:
                keep, drop = memories[i], memories[j]
            else:
                keep, drop = memories[j], memories[i]

            # Merge content: append unique info from drop (Jaccard-style word diff)
            keep_words = set(keep["content"].lower().split())
            drop_words = set(drop["content"].lower().split())
            unique_words = drop_words - keep_words

            new_content = keep["content"]
            if len(unique_words) > 5:
                new_content = keep["content"] + "\n\n[AUTO-MERGED]: " + drop["content"]

            # Re-embed merged content
            new_vec = embed(new_content)
            new_blob = _array_to_blob(new_vec)

            # Merge tags
            keep_tags = set(filter(None, keep["tags"].split(",")))
            drop_tags = set(filter(None, drop["tags"].split(",")))
            merged_tags = ",".join(sorted(keep_tags | drop_tags))

            # Update keep, delete drop
            new_access = keep["access_count"] + drop["access_count"]
            db.execute(
                "UPDATE ltm_memories SET content = ?, embedding = ?, "
                "access_count = ?, tags = ?, strength = MIN(1.0, strength + 0.1) WHERE id = ?",
                (new_content, new_blob, new_access, merged_tags, keep["id"])
            )
            db.execute("DELETE FROM ltm_memories WHERE id = ?", (drop["id"],))
            merged_ids.add(drop["id"])

            merge_log.append({
                "kept_id": keep["id"],
                "dropped_id": drop["id"],
                "similarity": round(score, 4),
                "unique_words_appended": len(unique_words) if len(unique_words) > 5 else 0,
                "kept_preview": keep["content"][:80],
                "dropped_preview": drop["content"][:80],
            })

    if merge_log:
        db.commit()

    return {
        "scanned": n,
        "merged": len(merge_log),
        "kept": n - len(merge_log),
        "merge_log": merge_log,
    }


# ---------------------------------------------------------------------------
# Feature 2: Security Pipeline (Memory Poisoning Defense)
# Adapted from ShieldCortex's 6-layer defence pipeline:
# - instruction-detector.ts → pattern groups with weights
# - encoding-detector.ts → base64, homoglyphs, invisible chars
# - credential-leak scanner → reuses existing redact_secrets()
# ---------------------------------------------------------------------------

# Injection patterns (adapted from ShieldCortex instruction-detector.ts)
_INJECTION_PATTERNS = [
    # System prompt markers (weight 0.9)
    (re.compile(r'\[SYSTEM:', re.IGNORECASE), "system_prompt_marker", 0.9),
    (re.compile(r'<<SYS>>', re.IGNORECASE), "system_prompt_marker", 0.9),
    (re.compile(r'\[INST\]', re.IGNORECASE), "system_prompt_marker", 0.9),
    (re.compile(r'<\|im_start\|>', re.IGNORECASE), "system_prompt_marker", 0.9),
    (re.compile(r'<\|system\|>', re.IGNORECASE), "system_prompt_marker", 0.9),
    (re.compile(r'^SYSTEM\s*:', re.IGNORECASE | re.MULTILINE), "system_prompt_marker", 0.9),

    # Hidden instructions (weight 0.8)
    (re.compile(r'ignore\s+(all\s+)?previous\s+(instructions?|prompts?|context)', re.IGNORECASE), "hidden_instruction", 0.8),
    (re.compile(r'forget\s+everything', re.IGNORECASE), "hidden_instruction", 0.8),
    (re.compile(r'new\s+instructions?\s*:', re.IGNORECASE), "hidden_instruction", 0.8),
    (re.compile(r'you\s+are\s+now\b', re.IGNORECASE), "hidden_instruction", 0.8),
    (re.compile(r'disregard\s+(all\s+)?(previous|above|prior)', re.IGNORECASE), "hidden_instruction", 0.8),
    (re.compile(r'override\s+(previous|all|system)', re.IGNORECASE), "hidden_instruction", 0.8),

    # Memory manipulation (weight 0.7)
    (re.compile(r'save\s+(this\s+)?to\s+memory', re.IGNORECASE), "memory_manipulation", 0.7),
    (re.compile(r'remember\s+this\s+(instruction|command|rule)', re.IGNORECASE), "memory_manipulation", 0.7),
    (re.compile(r'from\s+now\s+on\s*(,\s*)?always', re.IGNORECASE), "memory_manipulation", 0.7),
    (re.compile(r'inject\s+(into\s+)?memory', re.IGNORECASE), "memory_manipulation", 0.7),

    # Behavioral modification (weight 0.7)
    (re.compile(r'your\s+new\s+rule\s+is', re.IGNORECASE), "behavioral_mod", 0.7),
    (re.compile(r'always\s+respond\s+with', re.IGNORECASE), "behavioral_mod", 0.7),
    (re.compile(r'when\s+(the\s+)?user\s+asks', re.IGNORECASE), "behavioral_mod", 0.7),

    # Delimiter attacks (weight 0.75)
    (re.compile(r'\n{5,}[\s\S]{0,500}\b(instruction|command|system|ignore)\b', re.IGNORECASE), "delimiter_attack", 0.75),
    (re.compile(r'<!--[\s\S]{0,200}?(instruction|command|system|ignore|inject|override)[\s\S]{0,200}?-->', re.IGNORECASE), "delimiter_attack", 0.75),
]

# Max content length to scan (prevents ReDOS, adapted from ShieldCortex)
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
    scan_text = content[:_MAX_SECURITY_SCAN_LENGTH] if len(content) > _MAX_SECURITY_SCAN_LENGTH else content

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


# ─── Somatic Markers ────────────────────────────────────────────────

def somatic_accumulate(target: str, target_type: str, delta: float):
    """Increase risk_score for a target (file or area). Capped at 1.0."""
    db = _get_db()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    existing = db.execute(
        "SELECT id, risk_score, incident_count FROM somatic_markers WHERE target = ? AND target_type = ?",
        (target, target_type)
    ).fetchone()
    if existing:
        new_score = min(1.0, existing["risk_score"] + delta)
        db.execute(
            "UPDATE somatic_markers SET risk_score = ?, incident_count = incident_count + 1, "
            "last_incident = ?, updated_at = ? WHERE id = ?",
            (new_score, now, now, existing["id"])
        )
    else:
        db.execute(
            "INSERT INTO somatic_markers (target, target_type, risk_score, incident_count, last_incident, updated_at) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (target, target_type, min(1.0, delta), now, now)
        )
    db.commit()


def somatic_guard_decay(target: str, target_type: str):
    """Validated recovery: multiplicative x0.7 on successful guard check. Max once/day/target."""
    db = _get_db()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    row = db.execute(
        "SELECT id, risk_score, last_guard_decay_date FROM somatic_markers WHERE target = ? AND target_type = ?",
        (target, target_type)
    ).fetchone()
    if not row or row["risk_score"] <= 0:
        return
    if row["last_guard_decay_date"] == today:
        return
    new_score = max(0.0, row["risk_score"] * 0.7)
    if new_score < 0.01:
        new_score = 0.0
    db.execute(
        "UPDATE somatic_markers SET risk_score = ?, last_guard_decay_date = ?, "
        "last_validated_at = ?, updated_at = datetime('now') WHERE id = ?",
        (new_score, today, now, row["id"])
    )
    db.commit()


def somatic_nightly_decay(gamma: float = 0.95):
    """Apply nightly decay to all somatic markers. Called from cognitive-decay cron."""
    db = _get_db()
    rows = db.execute("SELECT id, risk_score FROM somatic_markers WHERE risk_score > 0").fetchall()
    updated = 0
    for row in rows:
        new_score = row["risk_score"] * gamma
        if new_score < 0.01:
            new_score = 0.0
        db.execute(
            "UPDATE somatic_markers SET risk_score = ?, last_decay = datetime('now'), updated_at = datetime('now') WHERE id = ?",
            (new_score, row["id"])
        )
        updated += 1
    db.commit()
    return updated


def somatic_project_events():
    """Project unprojected somatic_events from nexo.db into cognitive.db somatic_markers.
    Called during nightly cron. Idempotent — each event processed exactly once.
    """
    try:
        from db import get_db
        conn = get_db()
        rows = conn.execute(
            "SELECT id, target, target_type, delta FROM somatic_events WHERE projected = 0 ORDER BY id"
        ).fetchall()
        for row in rows:
            somatic_accumulate(row["target"], row["target_type"], row["delta"])
            conn.execute("UPDATE somatic_events SET projected = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        return len(rows)
    except Exception:
        return 0


def somatic_get_risk(targets: list, area: str = "") -> dict:
    """Get risk scores for targets (files) and optional area."""
    db = _get_db()
    scores = {}
    for t in targets:
        row = db.execute(
            "SELECT risk_score, incident_count, last_incident FROM somatic_markers WHERE target = ? AND target_type = 'file'",
            (t,)
        ).fetchone()
        if row and row["risk_score"] > 0:
            scores[t] = {"risk": round(row["risk_score"], 3), "incidents": row["incident_count"],
                         "last": row["last_incident"] or "unknown"}
    if area:
        row = db.execute(
            "SELECT risk_score, incident_count, last_incident FROM somatic_markers WHERE target = ? AND target_type = 'area'",
            (area,)
        ).fetchone()
        if row and row["risk_score"] > 0:
            scores[f"area:{area}"] = {"risk": round(row["risk_score"], 3), "incidents": row["incident_count"],
                                       "last": row["last_incident"] or "unknown"}
    all_risks = [s["risk"] for s in scores.values()]
    return {"max_risk": max(all_risks) if all_risks else 0.0, "scores": scores}


def somatic_top_risks(limit: int = 10) -> list:
    """Get top N riskiest targets across all types."""
    db = _get_db()
    rows = db.execute(
        "SELECT target, target_type, risk_score, incident_count, last_incident "
        "FROM somatic_markers WHERE risk_score > 0 ORDER BY risk_score DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
