from __future__ import annotations

import json


def _artifact(canonical_name: str, *, aliases=None, paths=None, domain: str = "") -> int:
    import db

    conn = db.get_db()
    cur = conn.execute(
        """
        INSERT INTO artifact_registry(kind, canonical_name, aliases, paths, domain)
        VALUES ('service', ?, ?, ?, ?)
        """,
        (
            canonical_name,
            json.dumps(aliases or []),
            json.dumps(paths or []),
            domain,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def test_private_read_only_entity_is_not_injected_in_pre_answer(isolated_db):
    import db
    from entity_live_profile import build_entity_profile, load_cached_entity_profile

    db.create_entity(
        "Maria Server",
        "host",
        "ssh root@192.168.1.20 /Users/franciscoc/private/docroot token=sk-test-12345678901234567890",
        aliases=["maria-imac"],
        metadata={"privacy_level": "private"},
        access_mode="read_only",
    )

    profile = build_entity_profile("maria-imac", surface="pre_answer", budget_tier="standard", cache=True)
    blob = json.dumps(profile, ensure_ascii=False)

    assert profile["resolution"]["action_blocked"] is True
    assert all(field["privacy_level"] not in {"private", "sensitive", "secret"} for field in profile["fields"])
    assert "/Users/franciscoc/private" not in blob
    assert "192.168.1.20" not in blob
    assert "sk-test" not in blob

    cached = load_cached_entity_profile(profile["profile_uid"])
    assert cached is not None
    cached_blob = json.dumps(cached, ensure_ascii=False)
    assert "/Users/franciscoc/private" not in cached_blob
    assert "192.168.1.20" not in cached_blob


def test_release_public_surface_is_blocked_by_default(isolated_db):
    import db
    from entity_live_profile import build_entity_profile

    db.create_entity("NEXO Internal", "project", "internal", aliases=["nexo-internal"])

    profile = build_entity_profile("nexo-internal", surface="release_public", budget_tier="standard")

    assert profile["resolution"]["action_blocked"] is True
    assert "release_public" not in profile["allowed_surfaces"]


def test_ambiguous_alias_blocks_action_and_write(isolated_db):
    import db
    from entity_live_profile import build_entity_profile

    db.create_entity("Casa Roja", "client", "A", aliases=["cliente activo"])
    db.create_entity("Casa Azul", "client", "B", aliases=["cliente activo"])

    profile = build_entity_profile("cliente activo", surface="pre_action", budget_tier="standard")

    assert profile["resolution"]["needs_disambiguation"] is True
    assert profile["resolution"]["action_blocked"] is True
    assert profile["conflicts"][0]["conflict_type"] == "needs_disambiguation"


def test_project_atlas_wins_over_artifact_registry_location_conflict(isolated_db):
    import db
    from entity_live_profile import build_entity_profile

    db.create_entity("NEXO Brain", "project", "brain", aliases=["brain"])
    artifact_id = _artifact("NEXO Brain service", aliases=["brain"], paths=["/wrong/path"], domain="nexo")
    atlas = {
        "nexo": {
            "aliases": ["brain"],
            "description": "Sistema operativo NEXO",
            "locations": {"repo": "/right/path"},
        }
    }

    profile = build_entity_profile("brain", surface="pre_action", budget_tier="standard", atlas=atlas)

    conflict = next(item for item in profile["conflicts"] if item["conflict_type"] == "authority_conflict")
    assert conflict["winner"] == "project_atlas"
    assert conflict["loser"] == "artifact_registry"
    assert f"artifact_registry:{artifact_id}" in conflict["source_refs"]
    assert profile["resolution"]["action_blocked"] is True
    assert "/wrong/path" not in json.dumps(profile, ensure_ascii=False)


def test_quick_budget_never_uses_local_context_even_when_requested(isolated_db):
    import db
    from entity_live_profile import build_entity_profile

    db.create_entity("Fast Entity", "person", "visible", aliases=["fast"])

    profile = build_entity_profile(
        "fast",
        surface="pre_answer",
        budget_tier="quick",
        include_local_context=True,
    )

    assert "local_context" not in profile["budget"]["used_sources"]
    assert profile["budget"]["heavy_sources_used"] == []
    assert profile["budget"]["degraded"] is False


def test_managed_asset_bridge_and_asset_context_event_are_idempotent_and_redacted(isolated_db):
    import db
    from entity_live_profile import record_asset_context_updated, upsert_managed_asset

    entity_id = db.create_entity("Cloudflare Site", "project", "site", aliases=["site"])
    artifact_id = _artifact("Cloudflare Site deploy", aliases=["site"], paths=["/srv/site"], domain="site")

    first = upsert_managed_asset(
        entity_key=f"entity:{entity_id}",
        artifact_id=artifact_id,
        provider_ref="cloudflare",
        external_ref="zone-secret-123",
        source_refs=[f"artifact_registry:{artifact_id}"],
        metadata={"provider_payload": {"token": "Bearer abcdefghijklmnop123456"}},
    )
    second = upsert_managed_asset(
        entity_key=f"entity:{entity_id}",
        artifact_id=artifact_id,
        provider_ref="cloudflare",
        external_ref="zone-secret-123",
        source_refs=[f"artifact_registry:{artifact_id}"],
    )
    assert first["ok"] is True
    assert second["asset_uid"] == first["asset_uid"]

    conn = db.get_db()
    rows = conn.execute("SELECT * FROM nexo_managed_assets WHERE artifact_id=?", (artifact_id,)).fetchall()
    assert len(rows) == 1
    assert "zone-secret-123" not in json.dumps(dict(rows[0]), ensure_ascii=False)

    event1 = record_asset_context_updated(
        entity_key=f"entity:{entity_id}",
        asset_uid=first["asset_uid"],
        artifact_id=artifact_id,
        project_key="site",
        change_type="deployed /srv/site with token=Bearer abcdefghijklmnop123456",
        source_refs=[f"artifact_registry:{artifact_id}"],
        idempotency_key="ACU-test-idempotent",
        metadata={"raw": "/srv/site token=Bearer abcdefghijklmnop123456"},
    )
    event2 = record_asset_context_updated(
        entity_key=f"entity:{entity_id}",
        asset_uid=first["asset_uid"],
        artifact_id=artifact_id,
        project_key="site",
        change_type="deployed /srv/site with token=Bearer abcdefghijklmnop123456",
        source_refs=[f"artifact_registry:{artifact_id}"],
        idempotency_key="ACU-test-idempotent",
    )

    assert event1["event_uid"] == event2["event_uid"]
    assert conn.execute("SELECT COUNT(*) FROM asset_context_updated WHERE event_uid='ACU-test-idempotent'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM memory_events WHERE event_uid='ACU-test-idempotent'").fetchone()[0] == 1
    stored = conn.execute("SELECT change_type FROM asset_context_updated WHERE event_uid='ACU-test-idempotent'").fetchone()[0]
    assert "/srv/site" not in stored
    assert "Bearer" not in stored


def test_entity_live_profile_plugin_returns_redacted_json(isolated_db):
    import db
    from plugins.entity_live_profile import handle_entity_live_profile

    db.create_entity("Plugin Entity", "person", "visible", aliases=["plugin-person"])

    payload = json.loads(handle_entity_live_profile("plugin-person", surface="pre_answer", budget_tier="standard"))

    assert payload["profile_version"] == "entity_live_profile.v1"
    assert payload["entity_key"].startswith("entity:")
    assert payload["authority"]["non_authoritative_cache"] is True


def test_legacy_entity_list_redacts_non_public_values(isolated_db):
    from plugins.entities import handle_entity_create, handle_entity_list, handle_entity_search

    created = handle_entity_create(
        "Secret Service",
        "service",
        "token=raw-secret-value /Users/franciscoc/private",
        notes="note /Users/franciscoc/private",
        aliases="secret-service,svc-secret",
        metadata='{"source": "test"}',
    )

    assert "Entity created" in created
    listed = handle_entity_list()
    searched = handle_entity_search("Secret Service")
    combined = listed + "\n" + searched

    assert "raw-secret-value" not in combined
    assert "/Users/franciscoc" not in combined
    assert "[redacted_entity_value]" in combined
