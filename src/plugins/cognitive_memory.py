"""Cognitive Memory plugin — RAG retrieval over NEXO's Atkinson-Shiffrin memory stores."""

import sys
import os

# Ensure site-packages is in path for numpy/fastembed
_site = "/opt/homebrew/lib/python{}.{}/site-packages".format(sys.version_info.major, sys.version_info.minor)
if os.path.isdir(_site) and _site not in sys.path:
    sys.path.insert(0, _site)

import cognitive


def handle_cognitive_retrieve(
    query: str,
    top_k: int = 10,
    min_score: float = 0.5,
    stores: str = "both",
    source_type: str = "",
    domain: str = "",
    include_archived: bool = False,
    use_hyde: bool | None = None,
    spreading_depth: int | None = None,
) -> str:
    """RAG query over cognitive memory (STM + LTM). Triggers rehearsal on retrieved memories.

    Args:
        query: Natural language query to search for
        top_k: Maximum number of results to return (default 10)
        min_score: Minimum cosine similarity score (default 0.5)
        stores: Which store to search — "both", "stm", or "ltm" (default "both")
        source_type: Filter by source type e.g. "change", "learning", "diary" (default: all)
        domain: Filter by domain e.g. "project-a", "shopify" (default: all)
        include_archived: If True, also search archived memories (default False)
        use_hyde: If True/False, force HyDE on/off. If omitted, NEXO auto-enables it for conceptual queries.
        spreading_depth: If >0, boost co-activated neighbors directly. If omitted, NEXO may auto-enable shallow spreading for multi-hop queries.
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
        include_archived=include_archived,
        use_hyde=use_hyde,
        spreading_depth=spreading_depth,
    )

    # Apply domain filter post-search (cognitive.search doesn't filter by domain natively)
    if domain:
        results = [r for r in results if r.get("domain", "") == domain]

    formatted = cognitive.format_results(results)
    mode_parts = [f"stores={stores}", f"min_score={min_score}"]
    if use_hyde is True:
        mode_parts.append("hyde=ON")
    elif use_hyde is None:
        mode_parts.append("hyde=AUTO")
    if spreading_depth and spreading_depth > 0:
        mode_parts.append(f"spreading={spreading_depth}")
    elif spreading_depth is None:
        mode_parts.append("spreading=AUTO")
    if results:
        top_score = float(results[0].get("score", 0.0) or 0.0)
        confidence = "high" if top_score >= 0.82 else "medium" if top_score >= 0.66 else "low"
        mode_parts.append(f"top_confidence={confidence}")
    header = f"COGNITIVE RETRIEVE — query: '{query}' | {len(results)} results ({', '.join(mode_parts)})\n\n"
    return header + formatted


def handle_cognitive_stats() -> str:
    """Return cognitive memory system metrics: STM/LTM counts, strengths, retrieval stats, top domains."""
    stats = cognitive.get_stats()

    lines = [
        "COGNITIVE MEMORY STATS",
        f"  STM active:          {stats['stm_active']} (+ {stats.get('stm_promoted', 0)} promoted to LTM, {stats.get('stm_total', 0)} total)",
        f"  LTM active:          {stats['ltm_active']}",
        f"  LTM dormant:         {stats['ltm_dormant']}",
        f"  Avg STM strength:    {stats['avg_stm_strength']:.3f}",
        f"  Avg LTM strength:    {stats['avg_ltm_strength']:.3f}",
        f"  Avg STM stability:   {stats.get('avg_stm_stability', 0.0):.3f}",
        f"  Avg LTM stability:   {stats.get('avg_ltm_stability', 0.0):.3f}",
        f"  Avg STM difficulty:  {stats.get('avg_stm_difficulty', 0.0):.3f}",
        f"  Avg LTM difficulty:  {stats.get('avg_ltm_difficulty', 0.0):.3f}",
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

    if "quarantine" in stats:
        q = stats["quarantine"]
        lines.append(f"  Quarantine pending:  {q.get('pending', 0)}")
        lines.append(f"  Quarantine promoted: {q.get('promoted', 0)}")
        lines.append(f"  Quarantine rejected: {q.get('rejected', 0)}")
        lines.append(f"  Quarantine expired:  {q.get('expired', 0)}")

    if "prediction_error_gate" in stats:
        g = stats["prediction_error_gate"]
        lines.append("  PE Gate (session):")
        lines.append(f"    Accepted (novel):     {g['accepted_novel']}")
        lines.append(f"    Accepted (refine):    {g['accepted_refinement']}")
        lines.append(f"    Rejected (redundant): {g['rejected']}")
        lines.append(f"    Rejection rate:       {g['rejection_rate_pct']}%")

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

    # Lifecycle state
    lifecycle = row["lifecycle_state"] or "active"
    lines.append(f"  lifecycle:     {lifecycle}")
    if row["snooze_until"]:
        lines.append(f"  snooze_until:  {row['snooze_until']}")

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
    """Cognitive memory performance metrics (spec section 9).

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
        lines.append("⚠ RECOMMENDATION: Switch to multilingual model (intfloat/multilingual-e5-small)")
        lines.append(f"  Reason: relevance {metrics['retrieval_relevance_pct']}% < 70% with {metrics['total_retrievals']}+ retrievals")

    if repeats["duplicates"]:
        lines.append("")
        lines.append("Top duplicates:")
        for d in repeats["duplicates"][:5]:
            lines.append(f"  [{d['score']}] STM#{d['new_stm_id']}: {d['new_content'][:60]}...")
            lines.append(f"         ≈ LTM#{d['ltm_id']}: {d['ltm_content'][:60]}...")

    # Prediction Error Gate stats
    gate = cognitive.get_gate_stats()
    if gate["total_evaluated"] > 0:
        lines.append("")
        lines.append("Prediction Error Gate (session):")
        lines.append(f"  Novel accepted:      {gate['accepted_novel']}")
        lines.append(f"  Refinements:         {gate['accepted_refinement']}")
        lines.append(f"  Rejected redundant:  {gate['rejected']}")
        lines.append(f"  Rejection rate:      {gate['rejection_rate_pct']}%")

    return "\n".join(lines)


def handle_cognitive_sentiment(text: str) -> str:
    """Detect user's sentiment from his text. Returns mood, intensity, and guidance.

    Call this with user's recent message to adapt NEXO's tone and behavior.
    Also logs the sentiment for historical tracking.

    Args:
        text: user's recent message or instruction
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

    Use BEFORE applying a new preference or rule from user that might contradict
    existing knowledge. If conflicts found, verbalize them and ask user to resolve.

    Args:
        instruction: The new instruction or preference to check against LTM
        force: If True, skip discussion — execute instruction, auto-resolve all conflicts as
               'exception', and flag for review in the nocturnal process (23:30).
    """
    conflicts = cognitive.detect_dissonance(instruction)
    if not conflicts:
        return f"No dissonance detected. Instruction '{instruction[:80]}' is consistent with existing LTM."

    if force:
        # Auto-resolve all as exceptions, log for nocturnal review
        for c in conflicts:
            cognitive.resolve_dissonance(
                c["memory_id"], "exception",
                f"[FORCE] {instruction[:200]} — auto-exception, pending nocturnal review"
            )
        return (f"FORCE: {len(conflicts)} conflicts auto-resolved as exceptions. "
                f"Instruction executed. Flagged for review at 23:30.")

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
    lines.append("  - 'paradigm_shift': user changed his mind permanently.")
    lines.append("  - 'exception': One-time override. Old memory stays.")
    lines.append("  - 'override': Old memory was wrong.")

    return "\n".join(lines)


def handle_cognitive_resolve(memory_id: int, resolution: str, context: str = '') -> str:
    """Resolve a cognitive dissonance by applying user's decision.

    Args:
        memory_id: The LTM memory ID from the dissonance detection
        resolution: 'paradigm_shift' (permanent change), 'exception' (one-time), or 'override' (old was wrong)
        context: Optional context about why this resolution was chosen
    """
    return cognitive.resolve_dissonance(memory_id, resolution, context)


def handle_cognitive_pin(memory_id: int, store: str = "auto") -> str:
    """Pin a memory so it NEVER decays and gets boosted in search results (+0.2 similarity).

    Args:
        memory_id: Integer ID of the memory to pin
        store: Which store — "stm", "ltm", or "auto" (tries both, default "auto")
    """
    return cognitive.set_lifecycle(memory_id, "pinned", store)


def handle_cognitive_snooze(memory_id: int, until_date: str, store: str = "auto") -> str:
    """Snooze a memory — hidden from searches until the given date, then auto-restores to active.

    Args:
        memory_id: Integer ID of the memory to snooze
        until_date: Date to restore the memory (YYYY-MM-DD format)
        store: Which store — "stm", "ltm", or "auto" (tries both, default "auto")
    """
    return cognitive.set_lifecycle(memory_id, "snoozed", store, snooze_until=until_date)


def handle_cognitive_archive(memory_id: int, store: str = "auto") -> str:
    """Archive a memory — stored but excluded from normal searches. Can be restored later.

    Args:
        memory_id: Integer ID of the memory to archive
        store: Which store — "stm", "ltm", or "auto" (tries both, default "auto")
    """
    return cognitive.set_lifecycle(memory_id, "archived", store)


def handle_cognitive_restore(memory_id: int, store: str = "auto") -> str:
    """Restore a memory to active state (from pinned, snoozed, or archived).

    Args:
        memory_id: Integer ID of the memory to restore
        store: Which store — "stm", "ltm", or "auto" (tries both, default "auto")
    """
    return cognitive.set_lifecycle(memory_id, "active", store)


def handle_cognitive_quarantine_list(status: str = "pending", limit: int = 20) -> str:
    """List quarantine queue items. Shows memories awaiting promotion to STM.

    Args:
        status: Filter — 'pending', 'promoted', 'rejected', 'expired', or 'all' (default 'pending')
        limit: Max items to return (default 20)
    """
    items = cognitive.quarantine_list(status=status, limit=limit)
    stats = cognitive.quarantine_stats()

    lines = [
        f"QUARANTINE QUEUE — {stats['pending']} pending | {stats['promoted']} promoted | {stats['rejected']} rejected | {stats['expired']} expired",
        f"Showing: {status} (limit {limit})",
        "",
    ]

    if not items:
        lines.append("No items found.")
    else:
        for item in items:
            lines.append(f"  #{item['id']} [{item['status']}] source={item['source']} type={item['source_type']} domain={item['domain'] or '-'}")
            lines.append(f"    confidence={item['confidence']:.1f} checks={item['promotion_checks']} created={item['created_at'][:16]}")
            if item['promoted_at']:
                lines.append(f"    promoted_at={item['promoted_at'][:16]}")
            lines.append(f"    {item['content']}")
            lines.append("")

    return "\n".join(lines)


def handle_cognitive_quarantine_promote(quarantine_id: int) -> str:
    """Manually promote a quarantine item to STM, bypassing the automatic promotion policy.

    Args:
        quarantine_id: ID of the quarantine entry to promote
    """
    return cognitive.quarantine_promote(quarantine_id)


def handle_cognitive_quarantine_reject(quarantine_id: int, reason: str = "") -> str:
    """Manually reject a quarantine item.

    Args:
        quarantine_id: ID of the quarantine entry to reject
        reason: Optional reason for rejection
    """
    return cognitive.quarantine_reject(quarantine_id, reason)


def handle_cognitive_quarantine_process() -> str:
    """Run the quarantine promotion cycle. Evaluates all pending items against the promotion policy.

    Promotion rules:
    - source='user_direct' → already promoted at ingest
    - source='inferred' + second occurrence found → promote
    - source='agent_observation' + >24h old + no LTM contradiction → promote
    - Contradicts LTM (cosine >0.8) → reject
    - >7 days old → expire
    """
    result = cognitive.process_quarantine()
    lines = [
        "QUARANTINE PROCESSING COMPLETE",
        f"  Promoted:      {result['promoted']}",
        f"  Rejected:      {result['rejected']}",
        f"  Expired:       {result['expired']}",
        f"  Still pending: {result['still_pending']}",
        f"  Total:         {result['total_processed']}",
    ]
    return "\n".join(lines)


# ============================================================================
# Prospective Memory trigger handlers (Feature 3)
# ============================================================================

def handle_cognitive_trigger_create(pattern: str, action: str, context: str = "") -> str:
    """Create a prospective memory trigger — fires when text matches pattern.

    Args:
        pattern: Keywords to match (case-insensitive, comma-separated for OR matching)
        action: What to do / remind about when the trigger fires
        context: Optional context about why this trigger was created
    """
    trigger_id = cognitive.create_trigger(pattern, action, context)
    return f"Trigger #{trigger_id} created — armed. Pattern: '{pattern}' | Action: '{action}'"


def handle_cognitive_trigger_list(status: str = "armed") -> str:
    """List prospective memory triggers.

    Args:
        status: Filter — 'armed' (active, waiting), 'fired' (already triggered), 'all'
    """
    triggers = cognitive.list_triggers(status)
    if not triggers:
        return f"No {status} triggers found."

    lines = [f"PROSPECTIVE TRIGGERS ({status}) — {len(triggers)} total", ""]
    for t in triggers:
        status_icon = "+" if t["status"] == "armed" else "x"
        lines.append(f"  [{status_icon}] #{t['id']} pattern='{t['trigger_pattern']}'")
        lines.append(f"      action: {t['action']}")
        if t.get("context"):
            lines.append(f"      context: {t['context']}")
        lines.append(f"      created: {t['created_at'][:16]}")
        if t.get("fired_at"):
            lines.append(f"      fired: {t['fired_at'][:16]}")
        lines.append("")

    return "\n".join(lines)


def handle_cognitive_trigger_check(text: str, use_semantic: bool = False) -> str:
    """Check text against all armed triggers and fire matching ones.

    Args:
        text: Text to check against triggers (e.g. user message, heartbeat context)
        use_semantic: Also use embedding similarity (slower but catches conceptual matches)
    """
    fired = cognitive.check_triggers(text, use_semantic=use_semantic)
    if not fired:
        return "No triggers fired."

    lines = [f"TRIGGERS FIRED: {len(fired)}", ""]
    for t in fired:
        lines.append(f"  #{t['id']} [{t['match_type']}] pattern='{t['pattern']}'")
        lines.append(f"    ACTION: {t['action']}")
        if t.get("context"):
            lines.append(f"    context: {t['context']}")
        lines.append("")

    return "\n".join(lines)


def handle_cognitive_trigger_preview(text: str, use_semantic: bool = False) -> str:
    """Preview prospective trigger matches without firing them."""
    matches = cognitive.preview_triggers(text, use_semantic=use_semantic)
    if not matches:
        return "No anticipatory warnings."

    lines = [f"ANTICIPATORY WARNINGS: {len(matches)}", ""]
    for match in matches:
        lines.append(f"  #{match['id']} [{match['match_type']}] pattern='{match['pattern']}'")
        lines.append(f"    ACTION: {match['action']}")
        if match.get("context"):
            lines.append(f"    context: {match['context']}")
        lines.append("")

    return "\n".join(lines)


def handle_cognitive_trigger_delete(trigger_id: int) -> str:
    """Delete a prospective memory trigger.

    Args:
        trigger_id: ID of the trigger to delete
    """
    return cognitive.delete_trigger(trigger_id)


def handle_cognitive_trigger_rearm(trigger_id: int) -> str:
    """Re-arm a fired trigger so it can fire again.

    Args:
        trigger_id: ID of the trigger to re-arm
    """
    return cognitive.rearm_trigger(trigger_id)


TOOLS = [
    (handle_cognitive_retrieve, "nexo_cognitive_retrieve", "RAG query over cognitive memory (STM+LTM). Triggers rehearsal on retrieved results."),
    (handle_cognitive_stats, "nexo_cognitive_stats", "Cognitive memory system metrics: STM/LTM counts, strengths, retrieval stats, quarantine counts"),
    (handle_cognitive_inspect, "nexo_cognitive_inspect", "Inspect a specific memory by ID (debug). Does NOT trigger rehearsal."),
    (handle_cognitive_metrics, "nexo_cognitive_metrics", "Performance metrics: retrieval relevance %, repeat error rate, multilingual recommendation (spec section 9)"),
    (handle_cognitive_dissonance, "nexo_cognitive_dissonance", "Detect conflicts between a new instruction and established LTM memories. force=True to skip discussion."),
    (handle_cognitive_resolve, "nexo_cognitive_resolve", "Resolve a cognitive dissonance: paradigm_shift, exception, or override."),
    (handle_cognitive_sentiment, "nexo_cognitive_sentiment", "Detect user's sentiment and get tone guidance. Also logs for tracking."),
    (handle_cognitive_trust, "nexo_cognitive_trust", "View or adjust trust score (0-100). Without args: view. With event: adjust."),
    (handle_cognitive_pin, "nexo_cognitive_pin", "Pin a memory — never decays, boosted +0.2 in search results."),
    (handle_cognitive_snooze, "nexo_cognitive_snooze", "Snooze a memory — hidden from searches until a date, then auto-restores."),
    (handle_cognitive_archive, "nexo_cognitive_archive", "Archive a memory — excluded from searches, can be restored."),
    (handle_cognitive_restore, "nexo_cognitive_restore", "Restore a memory to active state (from pinned/snoozed/archived)."),
    (handle_cognitive_quarantine_list, "nexo_cognitive_quarantine_list", "List quarantine queue items awaiting promotion to STM."),
    (handle_cognitive_quarantine_promote, "nexo_cognitive_quarantine_promote", "Manually promote a quarantine item to STM."),
    (handle_cognitive_quarantine_reject, "nexo_cognitive_quarantine_reject", "Manually reject a quarantine item."),
    (handle_cognitive_quarantine_process, "nexo_cognitive_quarantine_process", "Run quarantine promotion cycle — evaluate pending items against policy."),
    (handle_cognitive_trigger_create, "nexo_cognitive_trigger_create", "Create a prospective memory trigger — 'when X is mentioned, remind about Y'."),
    (handle_cognitive_trigger_list, "nexo_cognitive_trigger_list", "List prospective triggers by status (armed/fired/all)."),
    (handle_cognitive_trigger_preview, "nexo_cognitive_trigger_preview", "Preview anticipatory trigger matches without firing them."),
    (handle_cognitive_trigger_check, "nexo_cognitive_trigger_check", "Check text against armed triggers. Returns fired triggers with actions."),
    (handle_cognitive_trigger_delete, "nexo_cognitive_trigger_delete", "Delete a prospective trigger by ID."),
    (handle_cognitive_trigger_rearm, "nexo_cognitive_trigger_rearm", "Re-arm a fired trigger so it can fire again."),
]
