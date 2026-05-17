"""Local Context Layer runtime.

This package owns the local index used by Brain before Nero answers or acts.
It is intentionally local-only: no scanner, extractor, embedding or resolver
path calls an external API.
"""

from .api import (
    add_exclusion,
    add_root,
    clear_index,
    context_router,
    context_query,
    entity_dossier,
    diagnostics_tail,
    ensure_default_roots,
    get_asset,
    get_neighbors,
    list_exclusions,
    list_roots,
    local_index_hygiene,
    model_status,
    pause,
    performance_config,
    purge_asset,
    reconcile_live_changes,
    remove_exclusion,
    remove_root,
    resume,
    run_once,
    set_performance_profile,
    status,
    warmup_models,
)

__all__ = [
    "add_exclusion",
    "add_root",
    "clear_index",
    "context_router",
    "context_query",
    "entity_dossier",
    "diagnostics_tail",
    "ensure_default_roots",
    "get_asset",
    "get_neighbors",
    "list_exclusions",
    "list_roots",
    "local_index_hygiene",
    "model_status",
    "pause",
    "performance_config",
    "purge_asset",
    "reconcile_live_changes",
    "remove_exclusion",
    "remove_root",
    "resume",
    "run_once",
    "set_performance_profile",
    "status",
    "warmup_models",
]
