"""Entity live profile plugin tools."""

from __future__ import annotations

import json

from entity_live_profile import (
    build_entity_profile,
    record_asset_context_updated,
    upsert_managed_asset,
)


def _json_arg(value: str, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def handle_entity_live_profile(
    query: str,
    surface: str = "pre_answer",
    budget_tier: str = "standard",
    cache: bool = False,
    include_local_context: bool = False,
) -> str:
    """Build a redacted non-authoritative EntityLiveProfile."""
    profile = build_entity_profile(
        query,
        surface=surface,
        budget_tier=budget_tier,
        cache=bool(cache),
        include_local_context=bool(include_local_context),
    )
    return json.dumps(profile, ensure_ascii=False)


def handle_managed_asset_upsert(
    entity_key: str,
    artifact_id: int = 0,
    project_key: str = "",
    asset_kind: str = "other",
    provider_ref: str = "",
    external_ref: str = "",
    status: str = "planned",
    source_refs: str = "[]",
    privacy_level: str = "normal",
    metadata: str = "{}",
) -> str:
    """Link a managed asset to existing owners without creating artifacts."""
    result = upsert_managed_asset(
        entity_key=entity_key,
        artifact_id=int(artifact_id) if int(artifact_id or 0) > 0 else None,
        project_key=project_key,
        asset_kind=asset_kind,
        provider_ref=provider_ref,
        external_ref=external_ref,
        status=status,
        source_refs=_json_arg(source_refs, []),
        privacy_level=privacy_level,
        metadata=_json_arg(metadata, {}),
    )
    return json.dumps(result, ensure_ascii=False)


def handle_asset_context_updated(
    entity_key: str,
    asset_uid: str,
    change_type: str,
    artifact_id: int = 0,
    project_key: str = "",
    source_refs: str = "[]",
    privacy_level: str = "normal",
    session_id: str = "",
    idempotency_key: str = "",
    metadata: str = "{}",
) -> str:
    """Record an idempotent redacted asset_context_updated event."""
    result = record_asset_context_updated(
        entity_key=entity_key,
        asset_uid=asset_uid,
        change_type=change_type,
        artifact_id=int(artifact_id) if int(artifact_id or 0) > 0 else None,
        project_key=project_key,
        source_refs=_json_arg(source_refs, []),
        privacy_level=privacy_level,
        session_id=session_id,
        idempotency_key=idempotency_key,
        metadata=_json_arg(metadata, {}),
    )
    return json.dumps(result, ensure_ascii=False)


TOOLS = [
    (handle_entity_live_profile, "nexo_entity_live_profile", "Build a redacted non-authoritative EntityLiveProfile"),
    (handle_managed_asset_upsert, "nexo_managed_asset_upsert", "Link a managed asset to an existing artifact/entity"),
    (handle_asset_context_updated, "nexo_asset_context_updated", "Record a redacted managed asset context update"),
]
