"""NEXO Cognitive Engine — Modular vector memory with Atkinson-Shiffrin model.

This package replaces the monolithic cognitive.py. All public functions and
constants are re-exported here for full backwards compatibility:
    import cognitive
    cognitive.search("query")
    cognitive.embed("text")
"""

# Core: DB, embedding, cosine, constants, tables, redaction
from cognitive._core import (
    COGNITIVE_DB, EMBEDDING_DIM, LAMBDA_STM, LAMBDA_LTM,
    DEFAULT_MEMORY_STABILITY, DEFAULT_MEMORY_DIFFICULTY,
    PE_GATE_REJECT, PE_GATE_REFINE, _gate_stats,
    DISCRIMINATING_ENTITIES,
    POSITIVE_SIGNALS, NEGATIVE_SIGNALS, URGENCY_SIGNALS,
    _get_db, _init_tables, _migrate_lifecycle, _migrate_co_activation,
    _migrate_memory_personalization,
    _auto_migrate_embeddings,
    _get_model, _get_reranker, rerank_results,
    embed, cosine_similarity, _array_to_blob, _blob_to_array,
    extract_temporal_date, redact_secrets,
    clamp_memory_stability, clamp_memory_difficulty,
    initial_memory_profile, personalize_decay_rate, rehearsal_profile_update,
)

# Search
from cognitive._search import (
    search, bm25_search, hyde_expand_query,
    record_co_activation,
    _kg_boost_results, _apply_temporal_boost,
    create_trigger, check_triggers, list_triggers, delete_trigger, rearm_trigger,
    # Constants
    CO_ACTIVATION_DECAY, CO_ACTIVATION_BOOST, CO_ACTIVATION_MIN_STRENGTH,
)

# Ingest
from cognitive._ingest import (
    ingest, ingest_session, ingest_to_ltm, ingest_sensory,
    prediction_error_gate, get_gate_stats, detect_patterns,
    process_quarantine, quarantine_list, quarantine_promote, quarantine_reject, quarantine_stats,
    security_scan,
)

# Decay and maintenance
from cognitive._decay import (
    apply_decay, promote_stm_to_ltm, gc_stm, gc_test_memories,
    gc_sensory, gc_ltm_dormant, dream_cycle,
)

# Trust and sentiment
from cognitive._trust import (
    get_trust_events, auto_detect_trust_events,
    detect_sentiment, log_sentiment,
    get_trust_score, adjust_trust, get_trust_history,
    detect_dissonance, resolve_dissonance, check_correction_fatigue,
)

# Memory operations
from cognitive._memory import (
    format_results, get_metrics, check_repeat_errors, rehearse_by_content,
    consolidate_semantic, get_siblings,
    get_stats, set_lifecycle, auto_merge_duplicates,
    somatic_accumulate, somatic_guard_decay, somatic_nightly_decay,
    somatic_project_events, somatic_get_risk, somatic_top_risks,
)
