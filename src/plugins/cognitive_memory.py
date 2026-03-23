"""Cognitive Memory plugin — RAG retrieval over NEXO's Atkinson-Shiffrin memory stores."""

import cognitive


def handle_cognitive_retrieve(
    query: str,
    top_k: int = 10,
    min_score: float = 0.5,
    stores: str = "both",
    source_type: str = "",
    domain: str = "",
) -> str:
    """RAG query over cognitive memory (STM + LTM). Triggers rehearsal on retrieved memories.

    Args:
        query: Natural language query to search for
        top_k: Maximum number of results to return (default 10)
        min_score: Minimum cosine similarity score (default 0.5)
        stores: Which store to search — "both", "stm", or "ltm" (default "both")
        source_type: Filter by source type e.g. "change", "learning", "diary" (default: all)
        domain: Filter by domain e.g. "infrastructure", "general" (default: all)
    """
    if not query or not query.strip():
        return "ERROR: query is required."

    results = cognitive.search(
        query_text=query,
        top_k=top_k,
        min_score=min_score,
        stores=stores,
        exclude_dormant=True,
        rehearse=True,
        source_type_filter=source_type,
    )

    # Apply domain filter post-search (cognitive.search doesn't filter by domain natively)
    if domain:
        results = [r for r in results if r.get("domain", "") == domain]

    formatted = cognitive.format_results(results)
    header = f"COGNITIVE RETRIEVE — query: '{query}' | {len(results)} results (stores={stores}, min_score={min_score})\n\n"
    return header + formatted


def handle_cognitive_stats() -> str:
    """Return cognitive memory system metrics: STM/LTM counts, strengths, retrieval stats, top domains."""
    stats = cognitive.get_stats()

    lines = [
        "COGNITIVE MEMORY STATS",
        f"  STM active:          {stats['stm_active']}",
        f"  LTM active:          {stats['ltm_active']}",
        f"  LTM dormant:         {stats['ltm_dormant']}",
        f"  Avg STM strength:    {stats['avg_stm_strength']:.3f}",
        f"  Avg LTM strength:    {stats['avg_ltm_strength']:.3f}",
        f"  Total retrievals:    {stats['total_retrievals']}",
        f"  Avg retrieval score: {stats['avg_retrieval_score']:.3f}",
    ]

    if stats["top_domains_stm"]:
        lines.append("  Top STM domains:")
        for domain, cnt in stats["top_domains_stm"]:
            lines.append(f"    {domain}: {cnt}")

    if stats["top_domains_ltm"]:
        lines.append("  Top LTM domains:")
        for domain, cnt in stats["top_domains_ltm"]:
            lines.append(f"    {domain}: {cnt}")

    return "\n".join(lines)


def handle_cognitive_inspect(memory_id: int, store: str = "ltm") -> str:
    """Inspect a specific memory by ID without triggering rehearsal.

    Args:
        memory_id: Integer ID of the memory to inspect
        store: Which store to read from — "stm" or "ltm" (default "ltm")
    """
    if store not in ("stm", "ltm"):
        return "ERROR: store must be 'stm' or 'ltm'."

    db = cognitive._get_db()
    table = "stm_memories" if store == "stm" else "ltm_memories"

    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (memory_id,)).fetchone()
    if row is None:
        return f"ERROR: Memory #{memory_id} not found in {store.upper()}."

    content_preview = row["content"][:500]
    if len(row["content"]) > 500:
        content_preview += "..."

    lines = [
        f"COGNITIVE INSPECT — {store.upper()} #{memory_id}",
        f"  source_type:   {row['source_type']}",
        f"  source_id:     {row['source_id']}",
        f"  source_title:  {row['source_title']}",
        f"  domain:        {row['domain']}",
        f"  strength:      {row['strength']:.4f}",
        f"  access_count:  {row['access_count']}",
        f"  created_at:    {row['created_at']}",
        f"  last_accessed: {row['last_accessed']}",
    ]

    if store == "ltm":
        dormant_label = "YES" if row["is_dormant"] else "no"
        lines.append(f"  dormant:       {dormant_label}")
        if row["tags"]:
            lines.append(f"  tags:          {row['tags']}")

    if store == "stm":
        promoted_label = "YES" if row["promoted_to_ltm"] else "no"
        lines.append(f"  promoted:      {promoted_label}")

    lines.append(f"  content:\n    {content_preview}")

    return "\n".join(lines)


def handle_cognitive_metrics(days: int = 7) -> str:
    """Cognitive memory performance metrics.

    Returns retrieval relevance %, repeat error rate, score distribution,
    and whether multilingual model switch is recommended.

    Args:
        days: Period to analyze in days (default 7)
    """
    metrics = cognitive.get_metrics(days=days)
    repeats = cognitive.check_repeat_errors()

    lines = [
        f"COGNITIVE METRICS — last {days} days",
        "",
        "Retrieval Performance:",
        f"  Total retrievals:    {metrics['total_retrievals']}",
        f"  Retrievals/day:      {metrics['retrievals_per_day']}",
        f"  Relevance (>=0.6):   {metrics['retrieval_relevance_pct']}%  (target: >60%)",
        f"  Avg top score:       {metrics['avg_top_score']}",
        "",
        "Score Distribution:",
        f"  >0.8  (excellent):   {metrics['score_distribution']['above_80']}",
        f"  0.7-0.8 (good):     {metrics['score_distribution']['70_80']}",
        f"  0.6-0.7 (ok):       {metrics['score_distribution']['60_70']}",
        f"  0.5-0.6 (weak):     {metrics['score_distribution']['50_60']}",
        f"  <0.5  (irrelevant): {metrics['score_distribution']['below_50']}",
        "",
        "Repeat Error Rate:",
        f"  New learnings (7d):  {repeats['new_count']}",
        f"  Duplicates found:    {repeats['duplicate_count']}",
        f"  Repeat rate:         {repeats['repeat_rate_pct']}%  (target: <10%)",
    ]

    if metrics["needs_multilingual"]:
        lines.append("")
        lines.append("RECOMMENDATION: Switch to multilingual model (intfloat/multilingual-e5-small)")
        lines.append(f"  Reason: relevance {metrics['retrieval_relevance_pct']}% < 70% with {metrics['total_retrievals']}+ retrievals")

    if repeats["duplicates"]:
        lines.append("")
        lines.append("Top duplicates:")
        for d in repeats["duplicates"][:5]:
            lines.append(f"  [{d['score']}] STM#{d['new_stm_id']}: {d['new_content'][:60]}...")
            lines.append(f"         ≈ LTM#{d['ltm_id']}: {d['ltm_content'][:60]}...")

    return "\n".join(lines)


def handle_cognitive_sentiment(text: str) -> str:
    """Detect user sentiment from their text. Returns mood, intensity, and guidance.

    Call this with the user's recent message to adapt NEXO's tone and behavior.
    Also logs the sentiment for historical tracking.

    Args:
        text: User's recent message or instruction
    """
    result = cognitive.log_sentiment(text)
    trust = cognitive.get_trust_score()

    lines = [
        f"SENTIMENT: {result['sentiment'].upper()} (intensity: {result['intensity']})",
        f"Trust Score: {trust:.0f}/100",
    ]
    if result["signals"]:
        lines.append(f"Signals: {', '.join(result['signals'])}")
    if result["guidance"]:
        lines.append(f"Guidance: {result['guidance']}")

    return "\n".join(lines)


def handle_cognitive_trust(event: str = '', context: str = '', delta: float = None) -> str:
    """View or adjust the trust score (alignment index 0-100).

    Without arguments: shows current score and recent history.
    With event: adjusts score based on event type.

    Args:
        event: Event type — explicit_thanks, delegation, paradigm_shift, sibling_detected,
               proactive_action, correction, repeated_error, override, correction_fatigue,
               forgot_followup. Or empty to just view.
        context: Description of what happened
        delta: Custom point value (overrides default for the event type)
    """
    if not event:
        # View mode
        trust = cognitive.get_trust_score()
        history = cognitive.get_trust_history(days=7)

        lines = [
            f"TRUST SCORE: {trust:.0f}/100",
            f"7-day change: {history['net_change']:+.0f} (from {history['period_start_score']:.0f})",
            "",
        ]

        if history["sentiment_distribution"]:
            lines.append("Sentiment (7d):")
            for sent, data in history["sentiment_distribution"].items():
                lines.append(f"  {sent}: {data['count']}x (avg intensity {data['avg_intensity']})")
            lines.append("")

        if history["events"]:
            lines.append("Recent events:")
            for e in history["events"][-10:]:
                lines.append(f"  [{e['delta']:+.0f}] {e['event']}: {e['context'][:60]} ({e['at'][:16]})")

        return "\n".join(lines)

    # Adjust mode
    result = cognitive.adjust_trust(event, context, delta)
    if "error" in result:
        valid = ", ".join(sorted(cognitive.TRUST_EVENTS.keys()))
        return f"Unknown event '{event}'. Valid: {valid}"

    return f"Trust: {result['old_score']:.0f} → {result['new_score']:.0f} ({result['delta']:+.0f}) [{event}]"


def handle_cognitive_dissonance(instruction: str, force: bool = False) -> str:
    """Detect cognitive dissonance: find established memories that conflict with a new instruction.

    Use BEFORE applying a new preference or rule that might contradict existing knowledge.
    If conflicts found, verbalize them and ask to resolve.

    Args:
        instruction: The new instruction or preference to check against LTM
        force: If True, skip discussion — execute instruction, auto-resolve all conflicts as
               'exception', and flag for review.
    """
    conflicts = cognitive.detect_dissonance(instruction)
    if not conflicts:
        return f"No dissonance detected. Instruction '{instruction[:80]}' is consistent with existing LTM."

    if force:
        # Auto-resolve all as exceptions
        for c in conflicts:
            cognitive.resolve_dissonance(
                c["memory_id"], "exception",
                f"[FORCE] {instruction[:200]} — auto-exception, pending review"
            )
        return (f"FORCE: {len(conflicts)} conflicts auto-resolved as exceptions. "
                f"Instruction executed. Flagged for review.")

    lines = [
        f"COGNITIVE DISSONANCE DETECTED — {len(conflicts)} conflicting memories:",
        f"New instruction: \"{instruction[:200]}\"",
        "",
    ]
    for c in conflicts:
        lines.append(f"  LTM #{c['memory_id']} [{c['source_type']}] (strength={c['strength']:.2f}, {c['access_count']} accesses)")
        lines.append(f"    Similarity: {c['similarity']}")
        lines.append(f"    Content: {c['content'][:200]}")
        lines.append("")

    lines.append("RESOLVE with nexo_cognitive_resolve, or use force=True to skip:")
    lines.append("  - 'paradigm_shift': Permanent preference change.")
    lines.append("  - 'exception': One-time override. Old memory stays.")
    lines.append("  - 'override': Old memory was wrong.")

    return "\n".join(lines)


def handle_cognitive_resolve(memory_id: int, resolution: str, context: str = '') -> str:
    """Resolve a cognitive dissonance.

    Args:
        memory_id: The LTM memory ID from the dissonance detection
        resolution: 'paradigm_shift' (permanent change), 'exception' (one-time), or 'override' (old was wrong)
        context: Optional context about why this resolution was chosen
    """
    return cognitive.resolve_dissonance(memory_id, resolution, context)


TOOLS = [
    (handle_cognitive_retrieve, "nexo_cognitive_retrieve", "RAG query over cognitive memory (STM+LTM). Triggers rehearsal on retrieved results."),
    (handle_cognitive_stats, "nexo_cognitive_stats", "Cognitive memory system metrics: STM/LTM counts, strengths, retrieval stats"),
    (handle_cognitive_inspect, "nexo_cognitive_inspect", "Inspect a specific memory by ID (debug). Does NOT trigger rehearsal."),
    (handle_cognitive_metrics, "nexo_cognitive_metrics", "Performance metrics: retrieval relevance %, repeat error rate, multilingual recommendation"),
    (handle_cognitive_dissonance, "nexo_cognitive_dissonance", "Detect conflicts between a new instruction and established LTM memories. force=True to skip discussion."),
    (handle_cognitive_resolve, "nexo_cognitive_resolve", "Resolve a cognitive dissonance: paradigm_shift, exception, or override."),
    (handle_cognitive_sentiment, "nexo_cognitive_sentiment", "Detect user sentiment and get tone guidance. Also logs for tracking."),
    (handle_cognitive_trust, "nexo_cognitive_trust", "View or adjust trust score (0-100). Without args: view. With event: adjust."),
]
