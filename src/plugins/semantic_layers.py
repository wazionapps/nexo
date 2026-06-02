"""Semantic layers plugin tools."""

from __future__ import annotations

import json

from semantic_layers import (
    build_semantic_layers,
    get_semantic_layer,
    list_semantic_layers,
    mark_semantic_layers_stale,
    select_semantic_layers,
    validate_semantic_layer_sources,
)


def _json_arg(value: str, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def handle_semantic_layers_build(
    scope_type: str,
    scope_id: str,
    layers: str = "[]",
    source_refs: str = "[]",
    evidence_refs: str = "[]",
    values: str = "{}",
    surface_allowlist: str = "[]",
    privacy_level: str = "normal",
    producer: str = "plugin",
    budget_tier: str = "standard",
    metadata: str = "{}",
) -> str:
    """Build or refresh deterministic semantic layers for a scope."""
    result = build_semantic_layers(
        scope_type,
        scope_id,
        layers=_json_arg(layers, []),
        source_refs=_json_arg(source_refs, None),
        evidence_refs=_json_arg(evidence_refs, []),
        values=_json_arg(values, {}),
        allowed_surfaces=_json_arg(surface_allowlist, None),
        privacy_level=privacy_level,
        producer=producer,
        budget_tier=budget_tier,
        metadata=_json_arg(metadata, {}),
    )
    return _dump(result)


def handle_semantic_layer_get(
    scope_type: str,
    scope_id: str,
    layer_kind: str,
    surface: str = "pre_answer",
    budget_tier: str = "quick",
) -> str:
    """Return one fresh semantic layer after surface/privacy filtering."""
    return _dump(get_semantic_layer(scope_type, scope_id, layer_kind, surface, budget_tier=budget_tier))


def handle_semantic_layers_select(
    scope_type: str,
    scope_id: str,
    surface: str = "pre_answer",
    intent_kind: str = "",
    budget_tier: str = "quick",
    requested_layers: str = "[]",
    query: str = "",
) -> str:
    """Select fresh layers for a structured intent and concrete scope."""
    result = select_semantic_layers(
        query=query,
        intent_bundle={"intent_kind": intent_kind, "budget_tier": budget_tier},
        budget_policy={"budget_tier": budget_tier},
        surface=surface,
        scope_hint={"scope_type": scope_type, "scope_id": scope_id},
        requested_layers=_json_arg(requested_layers, []),
    )
    return _dump(result)


def handle_semantic_layers_list(scope_type: str = "", scope_id: str = "", status: str = "fresh", limit: int = 20, surface: str = "pre_answer") -> str:
    """List semantic layers visible on the requested surface."""
    return _dump({"layers": list_semantic_layers(scope_type=scope_type, scope_id=scope_id, status=status, limit=limit, surface=surface)})


def handle_semantic_layers_mark_stale(source_ref: str, reason: str = "source_changed") -> str:
    """Mark layers using a source ref as stale."""
    return _dump(mark_semantic_layers_stale(source_ref, reason=reason))


def handle_semantic_layers_validate(layer_uid: str) -> str:
    """Validate source versions for one semantic layer."""
    return _dump(validate_semantic_layer_sources(layer_uid))


TOOLS = [
    (
        handle_semantic_layers_build,
        "nexo_semantic_layers_build",
        "Build or refresh redacted non-authoritative semantic layers",
    ),
    (
        handle_semantic_layer_get,
        "nexo_semantic_layer_get",
        "Get one fresh semantic layer after surface/privacy filtering",
    ),
    (
        handle_semantic_layers_select,
        "nexo_semantic_layers_select",
        "Select semantic layers for a structured intent and scope",
    ),
    (
        handle_semantic_layers_list,
        "nexo_semantic_layers_list",
        "List semantic layers for inspection",
    ),
    (
        handle_semantic_layers_mark_stale,
        "nexo_semantic_layers_mark_stale",
        "Mark semantic layers stale by source reference",
    ),
    (
        handle_semantic_layers_validate,
        "nexo_semantic_layers_validate",
        "Validate semantic layer source references",
    ),
]
