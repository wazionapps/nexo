"""NEXO Cognitive — Search, retrieval, ranking."""
import math
import numpy as np
from datetime import datetime
from cognitive._core import _get_db, embed, cosine_similarity, _blob_to_array, _array_to_blob, _get_model, _get_reranker, rerank_results, EMBEDDING_DIM

def bm25_search(query_text: str, stores: str = "both", top_k: int = 20,
                source_type_filter: str = "") -> list[dict]:
    """BM25 keyword search using SQLite FTS5. Returns ranked results by relevance."""
    db = _get_db()
    results = []

    # Sanitize query for FTS5 (escape special chars, use OR for multi-word)
    words = [w.strip() for w in query_text.split() if w.strip() and len(w.strip()) > 1]
    if not words:
        return []
    fts_query = " OR ".join(f'"{w}"' for w in words)

    for store in ("stm", "ltm"):
        if stores == "stm" and store == "ltm":
            continue
        if stores == "ltm" and store == "stm":
            continue

        table = f"{store}_memories"
        fts_table = f"{store}_fts"

        try:
            sql = f"""
                SELECT m.id, m.content, m.source_type, m.source_id, m.source_title,
                       m.domain, m.created_at, m.strength, m.access_count
                FROM {fts_table}
                JOIN {table} m ON m.id = {fts_table}.rowid
                WHERE {fts_table} MATCH ?
            """
            params = [fts_query]

            if source_type_filter:
                sql += " AND m.source_type = ?"
                params.append(source_type_filter)

            if store == "stm":
                sql += " AND m.promoted_to_ltm = 0"
            else:
                sql += " AND m.is_dormant = 0"

            sql += f" ORDER BY {fts_table}.rank LIMIT ?"
            params.append(top_k)

            rows = db.execute(sql, params).fetchall()

            for rank_pos, row in enumerate(rows):
                results.append({
                    "store": store,
                    "id": row["id"],
                    "content": row["content"],
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "source_title": row["source_title"],
                    "domain": row["domain"],
                    "created_at": row["created_at"],
                    "strength": row["strength"],
                    "access_count": row["access_count"],
                    "bm25_rank": rank_pos + 1,
                    "lifecycle_state": "active",
                })
        except Exception:
            # FTS5 table might not exist yet or query syntax error
            pass

    return results


def _rrf_fuse(vector_results: list[dict], bm25_results: list[dict],
              k: int = 60, alpha: float = 0.7) -> list[dict]:
    """Reciprocal Rank Fusion: merge vector and BM25 results.

    Unlike the old version that only boosted vector-found results, this now
    ALSO ADDS BM25-only results. This is critical for vocabulary mismatches
    where semantic search misses but keyword search finds the right memory
    (e.g., user says 'backend', memory contains 'FastAPI dashboard localhost:6174').

    RRF score = alpha * 1/(k + vec_rank) + (1-alpha) * 1/(k + bm25_rank)
    Items found by only one source get a penalty rank for the missing source.
    """
    # Build lookups by (store, id)
    vec_lookup = {}
    for rank, r in enumerate(vector_results):
        key = (r["store"], r["id"])
        vec_lookup[key] = (rank + 1, r)

    bm25_lookup = {}
    for rank, r in enumerate(bm25_results):
        key = (r["store"], r["id"])
        if key not in bm25_lookup:  # keep best rank
            bm25_lookup[key] = (rank + 1, r)

    # Merge all unique keys
    all_keys = set(vec_lookup.keys()) | set(bm25_lookup.keys())
    miss_rank = max(len(vector_results), len(bm25_results)) + 10  # penalty rank for missing source

    fused = []
    for key in all_keys:
        vec_rank, vec_result = vec_lookup.get(key, (miss_rank, None))
        bm25_rank, bm25_result = bm25_lookup.get(key, (miss_rank, None))

        # Use whichever result has the data
        base = vec_result if vec_result else bm25_result
        result = base.copy()

        rrf_score = alpha * (1.0 / (k + vec_rank)) + (1 - alpha) * (1.0 / (k + bm25_rank))

        # If we have the original cosine score, blend it in to preserve semantic confidence
        if vec_result and "score" in vec_result:
            # Weighted blend: RRF for ranking + cosine for confidence
            result["score"] = 0.6 * vec_result["score"] + 0.4 * (rrf_score * k * 3)
        else:
            # BM25-only result: use RRF score scaled to ~0.5-0.7 range
            result["score"] = min(0.85, rrf_score * k * 3)

        result["bm25_boosted"] = key in bm25_lookup
        result["bm25_only"] = key not in vec_lookup
        result["rrf_score"] = rrf_score
        fused.append(result)

    # Sort by score descending
    fused.sort(key=lambda x: x["score"], reverse=True)
    return fused


# ── Temporal Boosting ────────────────────────────────────────────────
# Recent memories get a bounded additive boost at query time.
# Design from multi-AI debate (GPT-5.4 + Gemini 3.1 Pro + Claude Opus 4.6):
# - Additive, not multiplicative (preserves old strong matches)
# - Relevance-gated (only boost if already above threshold)
# - Query-adaptive alpha (operational queries get more boost)

# Operational keywords that suggest the user wants recent/active things
_OPERATIONAL_CUES = frozenset({
    "current", "latest", "now", "running", "active", "today", "yesterday",
    "tonight", "backend", "server", "dashboard", "service", "localhost",
    "anoche", "ayer", "ahora", "actual", "corriendo", "activo", "hoy",
    "madrugada", "esta mañana", "last night", "this morning",
})

# Historical keywords that suggest the user wants old things
_HISTORICAL_CUES = frozenset({
    "ago", "month", "months", "year", "years", "previous", "earlier",
    "cuando", "hace", "meses", "año", "anterior", "antes",
})


def _apply_temporal_boost(results: list[dict], query_text: str) -> list[dict]:
    """Apply bounded temporal boost to retrieval results.

    Recent memories (hours/days) get a small additive bonus, but only if they
    already have a reasonable relevance score (gated at 0.45). This prevents
    recent junk from outranking strong old matches.

    The boost decays with a 3-day half-life:
        boost = alpha * exp(-ln(2) * age_days / 3)

    Alpha is query-adaptive:
        - Operational queries ('backend', 'active', 'today'): alpha = 0.06
        - Default queries: alpha = 0.02
        - Historical queries ('ago', 'months', 'year'): alpha = 0.0 (disabled)
    """
    if not results:
        return results

    # Determine alpha based on query intent
    query_tokens = set(query_text.lower().split())
    if query_tokens & _HISTORICAL_CUES:
        return results  # No temporal boost for historical queries
    elif query_tokens & _OPERATIONAL_CUES:
        alpha = 0.06
    else:
        alpha = 0.02

    now = datetime.now()
    ln2 = math.log(2)
    half_life_days = 3.0

    for r in results:
        # Only boost if already reasonably relevant (relevance gate)
        if r.get("score", 0) < 0.45:
            continue

        # Calculate age in days
        created_str = r.get("created_at", "")
        if not created_str:
            continue
        try:
            created = datetime.fromisoformat(created_str.replace("Z", "+00:00").replace("+00:00", ""))
            age_days = max(0, (now - created).total_seconds() / 86400)
        except (ValueError, TypeError):
            continue

        # Bounded exponential decay boost
        boost = alpha * math.exp(-ln2 * age_days / half_life_days)

        # Apply boost (capped at 1.0)
        r["score"] = min(1.0, r["score"] + boost)
        if boost > 0.001:
            r["temporal_boost"] = round(boost, 4)

    return results


# ============================================================================
# FEATURE 0.5: Knowledge Graph Boost
# Memories connected to more KG nodes (files, areas, other learnings) are
# more structurally important. Apply a small additive boost proportional to
# their connection count. This bridges the vector (semantic) and graph
# (structural) worlds.
# ============================================================================

def _kg_boost_results(results: list[dict], max_boost: float = 0.08) -> list[dict]:
    """Boost search results based on Knowledge Graph connectivity.

    For each result whose source (learning, change, decision, entity) has a
    corresponding KG node, add a logarithmic boost based on connection count.
    More connected memories = more structurally important = slight score lift.

    Boost formula: min(max_boost, 0.015 * log2(connections + 1))
    - 1 connection  → +0.015
    - 4 connections → +0.034
    - 16 connections → +0.060
    - 32+ connections → capped at +0.08
    """
    if not results:
        return results

    try:
        db = _get_db()
    except Exception:
        return results

    # Collect KG node refs from results
    # KG node_refs use format "learning:212", "change:39", "decision:14"
    # Memory source_ids use format "L464", "C39", "D14" or raw IDs
    _prefix_map = {"learning": "L", "change": "C", "decision": "D", "entity": "E"}
    ref_map = {}  # node_ref -> list of result indices
    for i, r in enumerate(results):
        source_type = r.get("source_type", "")
        source_id = r.get("source_id", "")
        if not source_type or not source_id:
            continue
        # Convert memory source_id to KG node_ref
        prefix = _prefix_map.get(source_type, "")
        if prefix and source_id.startswith(prefix):
            numeric_id = source_id[len(prefix):]
            node_ref = f"{source_type}:{numeric_id}"
        else:
            node_ref = f"{source_type}:{source_id}"
        ref_map.setdefault(node_ref, []).append(i)

    if not ref_map:
        return results

    # Batch query: get connection counts for all relevant KG nodes
    try:
        placeholders = ",".join(["?"] * len(ref_map))
        rows = db.execute(f"""
            SELECT n.node_ref, COUNT(e.id) as connections
            FROM kg_nodes n
            LEFT JOIN kg_edges e ON (e.source_id = n.id OR e.target_id = n.id)
                AND e.valid_until IS NULL
            WHERE n.node_ref IN ({placeholders})
            GROUP BY n.id
        """, list(ref_map.keys())).fetchall()
    except Exception:
        return results

    # Apply boosts
    for row in rows:
        node_ref = row["node_ref"]
        connections = row["connections"]
        if connections <= 0:
            continue
        boost = min(max_boost, 0.015 * math.log2(connections + 1))
        for idx in ref_map.get(node_ref, []):
            r = results[idx]
            if r.get("score", 0) >= 0.45:  # Same relevance gate as temporal
                r["score"] = min(1.0, r["score"] + boost)
                r["kg_boost"] = round(boost, 4)
                r["kg_connections"] = connections

    return results


# ============================================================================
# FEATURE 1: HyDE Query Expansion (adapted from Vestige hyde.rs)
# Template-based Hypothetical Document Embeddings for improved search recall.
# Classifies query intent, generates 3-5 semantic variants, embeds all,
# averages into centroid embedding for broader semantic coverage.
# ============================================================================

def _classify_query_intent(query: str) -> str:
    """Classify query intent into one of 6 categories (Vestige-style)."""
    lower = query.lower().strip()
    if lower.startswith(("how to", "how do", "steps", "cómo")):
        return "howto"
    if lower.startswith(("what is", "what are", "define", "explain", "qué es")):
        return "definition"
    if lower.startswith(("why", "por qué")) or "reason" in lower or "porque" in lower:
        return "reasoning"
    if lower.startswith(("when", "cuándo")) or "date" in lower or "timeline" in lower or "fecha" in lower:
        return "temporal"
    if any(c in query for c in ("(", "{", "::", "def ", "class ", "fn ", "function ")):
        return "technical"
    return "lookup"


def _expand_query_variants(query: str) -> list[str]:
    """Generate 3-5 expanded query variants based on intent (Vestige-style)."""
    intent = _classify_query_intent(query)
    clean = query.strip().rstrip("?.!")
    variants = [query]

    templates = {
        "definition": [
            f"{clean} is a concept that involves",
            f"The definition of {clean} in the context of this project",
            f"{clean} refers to a type of",
        ],
        "howto": [
            f"The steps to {clean} are as follows",
            f"To accomplish {clean}, you need to",
            f"A guide for {clean} including",
        ],
        "reasoning": [
            f"The reason {clean} is because",
            f"{clean} happens due to the following factors",
            f"The explanation for {clean} involves",
        ],
        "temporal": [
            f"{clean} occurred at a specific time",
            f"The timeline of {clean} shows",
            f"Events related to {clean} in chronological order",
        ],
        "lookup": [
            f"Information about {clean} including details",
            f"{clean} is related to the following topics",
            f"Key facts about {clean}",
            f"Previously we handled {clean} by",
        ],
        "technical": [
            f"{clean} implementation details and code",
            f"Code pattern for {clean}",
        ],
    }

    variants.extend(templates.get(intent, templates["lookup"]))
    return variants


def hyde_expand_query(query: str) -> np.ndarray:
    """HyDE: embed expanded query variants and return their centroid.

    Instead of embedding just the raw query, generates 3-5 semantic
    variants and returns the averaged (centroid) embedding. This gives
    ~60% of full LLM-based HyDE quality with zero latency overhead.

    Based on Vestige's template-based HyDE (hyde.rs) and the original
    HyDE paper (Gao et al., 2022).
    """
    variants = _expand_query_variants(query)
    model = _get_model()
    embeddings = list(model.embed(variants))
    arrays = [np.array(e, dtype=np.float32) for e in embeddings]

    centroid = np.mean(arrays, axis=0).astype(np.float32)
    norm = np.linalg.norm(centroid)
    if norm > 0:
        centroid = centroid / norm

    return centroid


# ============================================================================
# FEATURE 2: Spreading Activation / Co-Activation Reinforcement
# Adapted from Vestige spreading_activation.rs and ClawMem store.ts
# Memories retrieved together get co-activation links that boost
# future retrievals of associated memories.
# ============================================================================

CO_ACTIVATION_DECAY = 0.7
CO_ACTIVATION_BOOST = 0.05
CO_ACTIVATION_MIN_STRENGTH = 0.1


def _canonical_co_id(store: str, mid: int) -> int:
    """Create a canonical hash ID for co-activation tracking."""
    return hash(f"{store}:{mid}") % (2**31)


def record_co_activation(memory_ids: list[tuple[str, int]]):
    """Record co-activation between all pairs of retrieved memories.

    Called after search returns results. Memories surfaced together
    get their co-activation links reinforced (ClawMem pattern).
    """
    if len(memory_ids) < 2:
        return

    db = _get_db()
    now = datetime.utcnow().isoformat()

    hashes = [_canonical_co_id(store, mid) for store, mid in memory_ids]

    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            a, b = min(hashes[i], hashes[j]), max(hashes[i], hashes[j])
            db.execute("""
                INSERT INTO co_activation (memory_a_id, memory_b_id, strength, co_access_count, last_co_access)
                VALUES (?, ?, 1.0, 1, ?)
                ON CONFLICT(memory_a_id, memory_b_id) DO UPDATE SET
                    strength = MIN(5.0, strength + 0.3),
                    co_access_count = co_access_count + 1,
                    last_co_access = excluded.last_co_access
            """, (a, b, now))

    db.commit()


def _get_co_activated_neighbors(memory_ids: list[tuple[str, int]], depth: int = 1) -> dict[int, float]:
    """Get co-activated neighbor boosts for a set of memory IDs.

    Returns {canonical_hash: boost_score} for neighbor memories.
    Uses BFS spreading with decay per hop (Vestige pattern).
    """
    db = _get_db()
    boosts = {}

    source_hashes = set(_canonical_co_id(s, m) for s, m in memory_ids)
    current_level = list(source_hashes)

    for hop in range(depth):
        decay = CO_ACTIVATION_DECAY ** (hop + 1)
        next_level = []

        for src_hash in current_level:
            rows = db.execute("""
                SELECT memory_a_id, memory_b_id, strength FROM co_activation
                WHERE (memory_a_id = ? OR memory_b_id = ?) AND strength >= ?
            """, (src_hash, src_hash, CO_ACTIVATION_MIN_STRENGTH)).fetchall()

            for row in rows:
                neighbor_id = row["memory_b_id"] if row["memory_a_id"] == src_hash else row["memory_a_id"]
                if neighbor_id in source_hashes:
                    continue

                boost = row["strength"] * decay * CO_ACTIVATION_BOOST
                if neighbor_id not in boosts or boosts[neighbor_id] < boost:
                    boosts[neighbor_id] = boost
                    next_level.append(neighbor_id)

        current_level = next_level

    return boosts


# ============================================================================
# FEATURE 3: Prospective Memory (adapted from Vestige prospective_memory.rs)
# "Remember to do X when Y happens" — intention-based triggers that fire
# when incoming text matches a pattern (keyword or semantic).
# ============================================================================

def create_trigger(pattern: str, action: str, context: str = "") -> int:
    """Create a prospective memory trigger.

    Args:
        pattern: Keywords or phrase to match (case-insensitive, comma-separated for multiple)
        action: What to do when the trigger fires
        context: Optional context about why this trigger was created
    Returns:
        Trigger ID
    """
    db = _get_db()
    cur = db.execute(
        "INSERT INTO prospective_triggers (trigger_pattern, action, context) VALUES (?, ?, ?)",
        (pattern, action, context)
    )
    db.commit()
    return cur.lastrowid


def check_triggers(text: str, use_semantic: bool = False, semantic_threshold: float = 0.7) -> list[dict]:
    """Check text against all armed triggers. Fires matches.

    Uses keyword matching by default. If use_semantic=True, also checks
    semantic similarity (Vestige TriggerPattern.matches pattern).

    Args:
        text: Input text to check
        use_semantic: Also do embedding similarity matching
        semantic_threshold: Min cosine similarity for semantic match
    Returns:
        List of fired triggers with actions
    """
    if not text or not text.strip():
        return []

    db = _get_db()
    armed = db.execute(
        "SELECT * FROM prospective_triggers WHERE status = 'armed'"
    ).fetchall()

    if not armed:
        return []

    text_lower = text.lower()
    text_vec = None
    if use_semantic:
        text_vec = embed(text)

    fired = []
    now = datetime.utcnow().isoformat()

    for trigger in armed:
        pattern = trigger["trigger_pattern"].lower()
        matched = False
        match_type = ""

        # Keyword match (comma-separated OR)
        keywords = [kw.strip() for kw in pattern.split(",") if kw.strip()]
        if any(kw in text_lower for kw in keywords):
            matched = True
            match_type = "keyword"

        # Semantic match (optional, more expensive)
        if not matched and use_semantic and text_vec is not None:
            pattern_vec = embed(trigger["trigger_pattern"])
            sim = cosine_similarity(text_vec, pattern_vec)
            if sim >= semantic_threshold:
                matched = True
                match_type = f"semantic({sim:.3f})"

        if matched:
            db.execute(
                "UPDATE prospective_triggers SET status = 'fired', fired_at = ? WHERE id = ?",
                (now, trigger["id"])
            )
            fired.append({
                "id": trigger["id"],
                "pattern": trigger["trigger_pattern"],
                "action": trigger["action"],
                "context": trigger["context"],
                "match_type": match_type,
                "created_at": trigger["created_at"],
            })

    if fired:
        db.commit()

    return fired


def list_triggers(status: str = "armed") -> list[dict]:
    """List prospective triggers filtered by status."""
    db = _get_db()
    if status == "all":
        rows = db.execute("SELECT * FROM prospective_triggers ORDER BY created_at DESC").fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM prospective_triggers WHERE status = ? ORDER BY created_at DESC",
            (status,)
        ).fetchall()
    return [dict(row) for row in rows]


def delete_trigger(trigger_id: int) -> str:
    """Delete a prospective trigger by ID."""
    db = _get_db()
    cur = db.execute("DELETE FROM prospective_triggers WHERE id = ?", (trigger_id,))
    db.commit()
    return f"Trigger #{trigger_id} {'deleted' if cur.rowcount else 'not found'}."


def rearm_trigger(trigger_id: int) -> str:
    """Re-arm a fired trigger so it can fire again."""
    db = _get_db()
    cur = db.execute(
        "UPDATE prospective_triggers SET status = 'armed', fired_at = NULL WHERE id = ?",
        (trigger_id,)
    )
    db.commit()
    return f"Trigger #{trigger_id} {'re-armed' if cur.rowcount else 'not found'}."


def _auto_restore_snoozed(db: sqlite3.Connection):
    """Restore snoozed memories whose snooze_until date has passed."""
    now = datetime.utcnow().isoformat()
    for table in ("stm_memories", "ltm_memories"):
        db.execute(
            f"UPDATE {table} SET lifecycle_state = 'active', snooze_until = NULL "
            f"WHERE lifecycle_state = 'snoozed' AND snooze_until IS NOT NULL AND snooze_until <= ?",
            (now,)
        )
    db.commit()


def _rehearse_results(results: list[dict], skip_ids: set = None):
    """Update strength and access_count for retrieved results (rehearsal)."""
    if not results:
        return
    db = _get_db()
    now = datetime.utcnow().isoformat()
    skip = skip_ids or set()
    for r in results:
        if (r["store"], r["id"]) in skip:
            continue
        table = "stm_memories" if r["store"] == "stm" else "ltm_memories"
        db.execute(
            f"UPDATE {table} SET strength = 1.0, access_count = access_count + 1, last_accessed = ? WHERE id = ?",
            (now, r["id"])
        )
    db.commit()


def search(
    query_text: str,
    top_k: int = 10,
    min_score: float = 0.5,
    stores: str = "both",
    exclude_dormant: bool = True,
    rehearse: bool = True,
    source_type_filter: str = "",
    include_archived: bool = False,
    use_hyde: bool = False,
    hybrid: bool = True,
    hybrid_alpha: float = 0.6,
    spreading_depth: int = 0,
    decompose: bool = True,
    exclude_dreams: bool = True,
) -> list[dict]:
    """Full vector search across STM and/or LTM with rehearsal and dormant reactivation.

    Args:
        use_hyde: If True, use HyDE query expansion for richer embedding (default False)
        spreading_depth: If >0, fetch co-activated neighbors and boost their scores (default 0)
        exclude_dreams: If True (default), exclude dream_insight memories from results.
                        Dream insights are 21% of LTM and dilute search precision.
                        Set to False only when explicitly looking for cross-domain patterns.
        hybrid: If True, boost results with BM25 keyword matches (default True)
        hybrid_alpha: Weight for vector vs BM25. Higher = more vector. (default 0.6)
        decompose: If True, decompose complex queries into sub-queries for better multi-hop (default True)
    """
    # Multi-query decomposition: for complex questions, search sub-parts and merge
    if decompose and query_text:
        _connectors = [" after ", " before ", " because ", " and then ", " when ", " while "]
        for conn in _connectors:
            if conn in query_text.lower():
                parts = query_text.lower().split(conn, 1)
                if len(parts) == 2 and len(parts[0]) > 10 and len(parts[1]) > 10:
                    # Search each sub-query separately, merge results by max score
                    all_results = {}
                    for sub_q in [query_text, parts[0].strip("? "), parts[1].strip("? ")]:
                        sub_results = search(
                            sub_q, top_k=top_k, min_score=min_score, stores=stores,
                            exclude_dormant=exclude_dormant, rehearse=False,
                            source_type_filter=source_type_filter,
                            include_archived=include_archived, use_hyde=use_hyde,
                            hybrid=hybrid, hybrid_alpha=hybrid_alpha,
                            spreading_depth=spreading_depth, decompose=False,  # No recursion
                        )
                        for r in sub_results:
                            key = (r["store"], r["id"])
                            if key not in all_results or r["score"] > all_results[key]["score"]:
                                all_results[key] = r
                    merged = sorted(all_results.values(), key=lambda x: x["score"], reverse=True)[:top_k]
                    if rehearse:
                        _rehearse_results(merged)
                    return merged

    db = _get_db()

    # Detect temporal queries — boost results with temporal_date
    _temporal_keywords = {"when", "date", "time", "first", "last", "before", "after",
                          "cuándo", "cuando", "fecha", "primero", "último", "antes", "después"}
    query_lower = query_text.lower().split()
    is_temporal_query = bool(_temporal_keywords & set(query_lower))

    if use_hyde:
        query_vec = hyde_expand_query(query_text)
    else:
        query_vec = embed(query_text)
    if np.linalg.norm(query_vec) == 0:
        return []

    # Auto-restore snoozed memories whose snooze_until has passed
    _auto_restore_snoozed(db)

    # HNSW fast-path: use approximate nearest neighbors when available
    _hnsw_candidates = None
    try:
        import hnsw_index
        if hnsw_index.is_available() and hnsw_index.should_activate(stores):
            _hnsw_candidates = {}
            for s in (["stm", "ltm"] if stores == "both" else [stores]):
                hits = hnsw_index.search(query_vec, store=s, top_k=top_k * 4)
                if hits:
                    for db_id, score in hits:
                        _hnsw_candidates[(s, db_id)] = score
    except Exception:
        _hnsw_candidates = None

    results = []
    reactivated_ids = set()

    # Lifecycle filter: exclude snoozed always; exclude archived unless requested
    _lc = " AND (lifecycle_state IS NULL OR lifecycle_state = 'active' OR lifecycle_state = 'pinned'"
    if include_archived:
        _lc += " OR lifecycle_state = 'archived'"
    _lc += ")"

    # Search STM
    if stores in ("both", "stm"):
        where = "WHERE promoted_to_ltm = 0" + _lc
        params = []
        if source_type_filter:
            where += " AND source_type = ?"
            params.append(source_type_filter)
        rows = db.execute(f"SELECT * FROM stm_memories {where}", params).fetchall()

        for row in rows:
            # HNSW fast-path: skip rows not in candidate set
            if _hnsw_candidates is not None and ("stm", row["id"]) not in _hnsw_candidates:
                continue
            vec = _blob_to_array(row["embedding"])
            score = cosine_similarity(query_vec, vec)
            lifecycle = row["lifecycle_state"] or "active"
            if lifecycle == "pinned":
                score = min(1.0, score + 0.2)
            if score >= min_score:
                temporal = ""
                try:
                    temporal = row["temporal_date"] or ""
                except (IndexError, KeyError):
                    pass
                results.append({
                    "store": "stm",
                    "id": row["id"],
                    "content": row["content"],
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "source_title": row["source_title"],
                    "domain": row["domain"],
                    "created_at": row["created_at"],
                    "strength": row["strength"],
                    "access_count": row["access_count"],
                    "score": score,
                    "lifecycle_state": lifecycle,
                    "temporal_date": temporal,
                })

    # Search LTM (active)
    if stores in ("both", "ltm"):
        where = "WHERE is_dormant = 0" + _lc
        params = []
        if source_type_filter:
            where += " AND source_type = ?"
            params.append(source_type_filter)
        if exclude_dreams and not source_type_filter:
            where += " AND source_type != 'dream_insight'"
        rows = db.execute(f"SELECT * FROM ltm_memories {where}", params).fetchall()

        for row in rows:
            # HNSW fast-path: skip rows not in candidate set
            if _hnsw_candidates is not None and ("ltm", row["id"]) not in _hnsw_candidates:
                continue
            vec = _blob_to_array(row["embedding"])
            score = cosine_similarity(query_vec, vec)
            lifecycle = row["lifecycle_state"] or "active"
            if lifecycle == "pinned":
                score = min(1.0, score + 0.2)
            if score >= min_score:
                results.append({
                    "store": "ltm",
                    "id": row["id"],
                    "content": row["content"],
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "source_title": row["source_title"],
                    "domain": row["domain"],
                    "created_at": row["created_at"],
                    "strength": row["strength"],
                    "access_count": row["access_count"],
                    "score": score,
                    "tags": row["tags"],
                    "lifecycle_state": lifecycle,
                })

    # Check dormant LTM for reactivation
    if stores in ("both", "ltm") and not exclude_dormant:
        dormant_rows = db.execute("SELECT * FROM ltm_memories WHERE is_dormant = 1").fetchall()
        for row in dormant_rows:
            vec = _blob_to_array(row["embedding"])
            score = cosine_similarity(query_vec, vec)
            if score > 0.8:
                # Reactivate
                db.execute(
                    "UPDATE ltm_memories SET is_dormant = 0, strength = 0.5, last_accessed = datetime('now') WHERE id = ?",
                    (row["id"],)
                )
                reactivated_ids.add(("ltm", row["id"]))
                results.append({
                    "store": "ltm",
                    "id": row["id"],
                    "content": row["content"],
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "source_title": row["source_title"],
                    "domain": row["domain"],
                    "created_at": row["created_at"],
                    "strength": 0.5,
                    "access_count": row["access_count"],
                    "score": score,
                    "tags": row["tags"],
                    "reactivated": True,
                })
        if reactivated_ids:
            db.commit()

    # Hybrid search: boost vector results with BM25 keyword matches
    if hybrid and query_text:
        bm25_results = bm25_search(query_text, stores=stores, top_k=top_k * 4,
                                    source_type_filter=source_type_filter)
        if bm25_results:
            results = _rrf_fuse(results, bm25_results, alpha=hybrid_alpha)

    # Temporal boost: for "when" queries, boost results that have temporal_date
    if is_temporal_query:
        for r in results:
            if r.get("temporal_date"):
                r["score"] = min(1.0, r["score"] + 0.05)

    # Recency temporal boost: recent memories get additive bonus (query-adaptive)
    results = _apply_temporal_boost(results, query_text)

    # Knowledge Graph structural boost: connected memories rank higher
    results = _kg_boost_results(results)

    # Sort by score descending, take top-20 for reranking
    results.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Cross-encoder reranking: precise top-k from top-20 candidates
    if len(results) > top_k:
        results = rerank_results(query_text, results[:top_k * 4], top_k=top_k)
    else:
        results = results[:top_k]

    # Spreading activation: boost co-activated neighbors (Feature 2)
    co_activation_applied = False
    if spreading_depth > 0 and results:
        memory_ids = [(r["store"], r["id"]) for r in results]
        neighbor_boosts = _get_co_activated_neighbors(memory_ids, depth=spreading_depth)

        if neighbor_boosts:
            co_activation_applied = True
            # Boost existing results that are neighbors
            existing_hashes = set()
            for r in results:
                co_hash = _canonical_co_id(r["store"], r["id"])
                existing_hashes.add(co_hash)
                if co_hash in neighbor_boosts:
                    boost = neighbor_boosts[co_hash]
                    r["score"] = min(1.0, r["score"] + boost)
                    r["co_activation_boost"] = boost

            # Add neighbor memories not already in results
            new_neighbor_hashes = set(neighbor_boosts.keys()) - existing_hashes
            if new_neighbor_hashes:
                for store_name, table in [("stm", "stm_memories"), ("ltm", "ltm_memories")]:
                    rows = db.execute(f"SELECT * FROM {table}").fetchall()
                    for row in rows:
                        nh = _canonical_co_id(store_name, row["id"])
                        if nh in new_neighbor_hashes:
                            boost = neighbor_boosts[nh]
                            results.append({
                                "store": store_name,
                                "id": row["id"],
                                "content": row["content"],
                                "source_type": row.get("source_type", ""),
                                "source_id": row.get("source_id", ""),
                                "tags": row.get("tags", ""),
                                "domain": row.get("domain", ""),
                                "created_at": row.get("created_at", ""),
                                "strength": row.get("strength", 0.0),
                                "access_count": row.get("access_count", 0),
                                "score": min(1.0, boost),
                                "co_activation_boost": boost,
                                "lifecycle_state": row.get("lifecycle_state", "active"),
                            })
                            new_neighbor_hashes.discard(nh)

            # Re-sort after applying boosts
            results.sort(key=lambda x: x["score"], reverse=True)

    # Add rank explanations
    for rank, r in enumerate(results, 1):
        score = r["score"]
        store = r["store"].upper()
        strength = r.get("strength", 0.0)
        access_count = r.get("access_count", 0)
        created = r.get("created_at", "")
        tags = r.get("tags", "")
        reactivated = r.get("reactivated", False)

        ranking_desc = "semantic_similarity"
        if use_hyde:
            ranking_desc = "hyde_centroid_similarity"
        parts = [f"Ranked #{rank}: {ranking_desc}={score:.3f}"]
        parts.append(f"store={store}, strength={strength:.2f}, accesses={access_count}")
        if r.get("kg_boost"):
            parts.append(f"kg_boost=+{r['kg_boost']:.3f} ({r.get('kg_connections', 0)} edges)")
        if r.get("co_activation_boost"):
            parts.append(f"co_activation_boost=+{r['co_activation_boost']:.3f}")
        if created:
            parts.append(f"created={created[:10]}")
        if tags:
            parts.append(f"tags={tags}")
        if reactivated:
            parts.append("REACTIVATED (was dormant, score>0.8 triggered revival)")
        r["explanation"] = " | ".join(parts)

    # Rehearsal: update strength and access_count for returned results
    if rehearse and results:
        _rehearse_results(results, skip_ids=reactivated_ids)

    # Record co-activation for future spreading (Feature 2)
    if results and len(results) >= 2:
        try:
            record_co_activation([(r["store"], r["id"]) for r in results])
        except Exception:
            pass  # Non-critical — don't break search

    # Log retrieval
    top_score = results[0]["score"] if results else 0.0
    db.execute(
        "INSERT INTO retrieval_log (query_text, results_count, top_score) VALUES (?, ?, ?)",
        (query_text[:500], len(results), top_score)
    )
    db.commit()

    return results
