import json


def _conn():
    import db

    return db.get_db()


def _seed_session(conn, sid="s1"):
    conn.execute(
        """
        INSERT INTO sessions (
            sid, task, started_epoch, last_update_epoch, local_time,
            session_client, external_session_id, conversation_id
        ) VALUES (?, 'Continue release work', 1, 2, 'now', 'codex', '', 'conv1')
        """,
        (sid,),
    )


def _seed_workflow(conn, run_id="WF-SL-1", next_action="ship next"):
    conn.execute(
        """
        INSERT INTO workflow_runs (
            run_id, session_id, goal, status, next_action, current_step_key,
            updated_at
        ) VALUES (?, 's1', 'Brain release', 'running', ?, 'spec10', '2026-06-02T10:00:00')
        """,
        (run_id, next_action),
    )
    conn.execute(
        """
        INSERT INTO workflow_checkpoints (
            run_id, step_key, checkpoint_label, run_status, step_status,
            summary, evidence, next_action, created_at
        ) VALUES (?, 'spec10', 'checkpoint', 'running', 'active',
                  'Spec10 checkpoint summary', 'pytest:test_semantic_layers', ?, '2026-06-02T10:00:01')
        """,
        (run_id, next_action),
    )


def test_semantic_layers_idempotent_and_stale_on_source_change(isolated_db):
    import semantic_layers

    conn = _conn()
    _seed_session(conn)
    _seed_workflow(conn)

    first = semantic_layers.build_semantic_layers("workflow", "WF-SL-1", layers=["next_action"])
    second = semantic_layers.build_semantic_layers("workflow", "WF-SL-1", layers=["next_action"])

    assert first["ok"] is True
    assert second["layers"][0]["layer_uid"] == first["layers"][0]["layer_uid"]
    assert conn.execute("SELECT COUNT(*) FROM semantic_layers WHERE layer_kind='next_action'").fetchone()[0] == 1

    conn.execute(
        "UPDATE workflow_runs SET next_action='ship final release', updated_at='2026-06-02T11:00:00' WHERE run_id='WF-SL-1'"
    )
    third = semantic_layers.build_semantic_layers("workflow", "WF-SL-1", layers=["next_action"])

    assert third["layers"][0]["layer_uid"] != first["layers"][0]["layer_uid"]
    assert conn.execute("SELECT COUNT(*) FROM semantic_layers WHERE status='stale'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM semantic_layers WHERE status='fresh'").fetchone()[0] == 1


def test_semantic_layers_rebuild_clears_stale_metadata_for_same_fingerprint(isolated_db):
    import semantic_layers

    conn = _conn()
    _seed_session(conn)
    _seed_workflow(conn)

    built = semantic_layers.build_semantic_layers("workflow", "WF-SL-1", layers=["next_action"])
    uid = built["layers"][0]["layer_uid"]
    assert semantic_layers.mark_semantic_layers_stale("workflow_run:WF-SL-1", reason="probe")["stale_count"] == 1

    semantic_layers.build_semantic_layers("workflow", "WF-SL-1", layers=["next_action"])

    row = conn.execute("SELECT status, stale_at, stale_reason FROM semantic_layers WHERE layer_uid=?", (uid,)).fetchone()
    assert dict(row) == {"status": "fresh", "stale_at": 0.0, "stale_reason": ""}


def test_semantic_layers_surface_filter_before_content(isolated_db):
    import semantic_layers

    result = semantic_layers.build_semantic_layers(
        "release",
        "7.28",
        layers=["headline"],
        source_refs=["test:tests/test_semantic_layers.py::surface_filter"],
        values={"headline": "Private release summary"},
        privacy_level="secret",
        allowed_surfaces=["pre_answer", "audit"],
    )

    layer = result["layers"][0]
    assert "pre_answer" not in layer["allowed_surfaces"]
    denied = semantic_layers.get_semantic_layer("release", "7.28", "headline", "pre_answer")
    allowed = semantic_layers.get_semantic_layer("release", "7.28", "headline", "audit")
    assert denied["ok"] is False
    assert denied["error"] == "surface_not_allowed"
    assert allowed["ok"] is True


def test_semantic_layers_inherit_secret_privacy_from_source(isolated_db):
    import semantic_layers

    conn = _conn()
    conn.execute(
        """
        INSERT INTO memory_events (
            event_uid, created_at, source_type, event_type, input_hash,
            output_digest, privacy_level
        ) VALUES ('ME-secret', 1780409000.0, 'test', 'note', 'in', 'out', 'secret')
        """
    )

    result = semantic_layers.build_semantic_layers(
        "release",
        "7.28-secret",
        layers=["headline"],
        source_refs=["memory_event:ME-secret"],
        values={"headline": "Secret source summary"},
        privacy_level="normal",
        allowed_surfaces=["pre_answer", "audit"],
    )

    layer = result["layers"][0]
    assert layer["privacy_level"] == "secret"
    assert "pre_answer" not in layer["allowed_surfaces"]
    assert semantic_layers.get_semantic_layer("release", "7.28-secret", "headline", "pre_answer")["ok"] is False
    assert semantic_layers.get_semantic_layer("release", "7.28-secret", "headline", "audit")["ok"] is True


def test_semantic_layers_inherit_secret_privacy_from_evidence_refs(isolated_db):
    import semantic_layers

    conn = _conn()
    conn.execute(
        """
        INSERT INTO memory_events (
            event_uid, created_at, source_type, event_type, input_hash,
            output_digest, privacy_level
        ) VALUES ('ME-secret-evidence', 1780409000.0, 'test', 'note', 'in', 'out', 'secret')
        """
    )

    result = semantic_layers.build_semantic_layers(
        "release",
        "7.28-secret-evidence",
        layers=["headline"],
        source_refs=["test:tests/test_semantic_layers.py::secret_evidence"],
        evidence_refs=["memory_event:ME-secret-evidence"],
        values={"headline": "Evidence-backed summary"},
        privacy_level="normal",
        allowed_surfaces=["pre_answer", "audit"],
    )

    layer = result["layers"][0]
    assert layer["privacy_level"] == "secret"
    assert "pre_answer" not in layer["allowed_surfaces"]
    assert semantic_layers.get_semantic_layer("release", "7.28-secret-evidence", "headline", "pre_answer")["ok"] is False
    assert semantic_layers.get_semantic_layer("release", "7.28-secret-evidence", "headline", "audit")["ok"] is True


def test_semantic_layers_detect_evidence_privacy_change_on_get(isolated_db):
    import semantic_layers

    conn = _conn()
    conn.execute(
        """
        INSERT INTO memory_events (
            event_uid, created_at, source_type, event_type, input_hash,
            output_digest, privacy_level
        ) VALUES ('ME-evidence-privacy-change', 1780409000.0, 'test', 'note', 'in', 'out', 'normal')
        """
    )
    built = semantic_layers.build_semantic_layers(
        "release",
        "7.28-evidence-privacy-change",
        layers=["headline"],
        source_refs=["test:tests/test_semantic_layers.py::evidence_privacy_change"],
        evidence_refs=["memory_event:ME-evidence-privacy-change"],
        values={"headline": "Evidence-backed summary"},
        privacy_level="normal",
        allowed_surfaces=["pre_answer", "audit"],
    )
    assert semantic_layers.get_semantic_layer(
        "release", "7.28-evidence-privacy-change", "headline", "pre_answer"
    )["ok"] is True

    conn.execute(
        "UPDATE memory_events SET privacy_level='secret' WHERE event_uid='ME-evidence-privacy-change'"
    )
    got = semantic_layers.get_semantic_layer(
        "release", "7.28-evidence-privacy-change", "headline", "pre_answer"
    )

    assert got["ok"] is False
    assert got["error"] == "layer_stale"
    evidence_ref = conn.execute(
        "SELECT required_for_layer FROM semantic_layer_source_refs WHERE layer_uid=? AND source_ref='memory_event:ME-evidence-privacy-change'",
        (built["layers"][0]["layer_uid"],),
    ).fetchone()
    assert evidence_ref["required_for_layer"] == 0
    row = conn.execute(
        "SELECT status, stale_reason FROM semantic_layers WHERE layer_uid=?",
        (built["layers"][0]["layer_uid"],),
    ).fetchone()
    assert row["status"] == "stale"
    assert row["stale_reason"] == "source_validation_changed"


def test_semantic_layers_reject_missing_evidence_refs_before_persisting(isolated_db):
    import semantic_layers

    result = semantic_layers.build_semantic_layers(
        "release",
        "7.28-missing-evidence-ref",
        layers=["headline"],
        source_refs=["test:tests/test_semantic_layers.py::missing_evidence_ref"],
        evidence_refs=["evidence:does-not-exist"],
        values={"headline": "Release headline"},
    )

    assert result["ok"] is False
    assert result["error"] == "invalid_evidence_refs"
    assert _conn().execute("SELECT COUNT(*) FROM semantic_layers").fetchone()[0] == 0


def test_semantic_layers_inherit_causal_edge_candidate_privacy(isolated_db):
    import semantic_layers

    conn = _conn()
    conn.execute(
        """
        INSERT INTO causal_edge_candidates (
            candidate_uid, created_at, updated_at, source_type, source_ref,
            relation, target_type, target_ref, producer, privacy_level,
            confidence, status
        ) VALUES (
            'CEC-secret', 1780409000.0, 1780409000.0, 'memory_event',
            'ME-1', 'causes', 'workflow', 'WF-1', 'pytest',
            'secret', 0.8, 'proposed'
        )
        """
    )

    result = semantic_layers.build_semantic_layers(
        "release",
        "7.28-causal-secret",
        layers=["headline"],
        source_refs=["causal_edge_candidate:CEC-secret"],
        values={"headline": "Causal candidate summary"},
        privacy_level="normal",
        allowed_surfaces=["pre_answer", "audit"],
    )

    layer = result["layers"][0]
    assert layer["privacy_level"] == "secret"
    assert "pre_answer" not in layer["allowed_surfaces"]
    assert semantic_layers.get_semantic_layer("release", "7.28-causal-secret", "headline", "pre_answer")["ok"] is False
    assert semantic_layers.get_semantic_layer("release", "7.28-causal-secret", "headline", "audit")["ok"] is True


def test_semantic_layers_reject_sensitive_evidence_refs_before_persisting(isolated_db):
    import semantic_layers

    result = semantic_layers.build_semantic_layers(
        "release",
        "7.28-sensitive-ref",
        layers=["headline"],
        source_refs=["test:tests/test_semantic_layers.py::sensitive_ref"],
        evidence_refs=["evidence:password=verysecretvalue"],
        values={"headline": "Release headline"},
    )

    assert result["ok"] is False
    assert result["error"] == "invalid_evidence_refs"
    assert _conn().execute("SELECT COUNT(*) FROM semantic_layers").fetchone()[0] == 0


def test_semantic_layers_missing_evidence_ref_is_source_missing_not_fresh(isolated_db):
    import semantic_layers

    result = semantic_layers.build_semantic_layers(
        "release",
        "7.28-missing-evidence",
        layers=["headline"],
        source_refs=["evidence:does-not-exist"],
        values={"headline": "Unsupported evidence should not be fresh truth"},
    )

    assert result["ok"] is True
    layer = result["layers"][0]
    assert layer["status"] == "invalid"
    assert layer["quality_state"] == "source_missing"
    assert semantic_layers.get_semantic_layer("release", "7.28-missing-evidence", "headline", "pre_answer")["ok"] is False


def test_semantic_layers_redact_raw_transcript_payloads(isolated_db):
    import semantic_layers

    conn = _conn()
    conn.execute(
        """
        INSERT INTO transcript_index (
            source_client, conversation_id, session_id, path_ref,
            content_hash, modified_at, message_count, sanitized_summary
        ) VALUES ('codex', 'conv1', 's1', 'opaque-path', 'h1', '2026-06-02T10:00:00', 3, 'safe summary')
        """
    )
    transcript_id = conn.execute("SELECT id FROM transcript_index").fetchone()[0]
    result = semantic_layers.build_semantic_layers(
        "conversation",
        "conv1",
        layers=["brief"],
        source_refs=[f"transcript_index:{transcript_id}"],
        values={"brief": "transcript: raw provider_payload Bearer abcdefghijklmnop"},
    )

    assert result["layers"][0]["value_redacted"] == "[redacted_payload]"


def test_semantic_layers_detect_source_version_change_on_get(isolated_db):
    import semantic_layers

    conn = _conn()
    _seed_session(conn)
    _seed_workflow(conn)
    built = semantic_layers.build_semantic_layers("workflow", "WF-SL-1", layers=["next_action"])

    conn.execute(
        "UPDATE workflow_runs SET next_action='changed after cache', updated_at='2026-06-02T12:00:00' WHERE run_id='WF-SL-1'"
    )
    got = semantic_layers.get_semantic_layer("workflow", "WF-SL-1", "next_action", "pre_answer")

    assert got["ok"] is False
    assert got["error"] == "layer_stale"
    row = conn.execute("SELECT status, stale_reason FROM semantic_layers WHERE layer_uid=?", (built["layers"][0]["layer_uid"],)).fetchone()
    assert row["status"] == "stale"
    assert row["stale_reason"] == "source_validation_changed"


def test_semantic_layers_select_validates_without_writing_stale_status(isolated_db):
    import semantic_layers

    conn = _conn()
    _seed_session(conn)
    _seed_workflow(conn)
    built = semantic_layers.build_semantic_layers("workflow", "WF-SL-1", layers=["next_action"])

    conn.execute(
        "UPDATE workflow_runs SET next_action='changed after cache', updated_at='2026-06-02T12:00:00' WHERE run_id='WF-SL-1'"
    )
    selected = semantic_layers.select_semantic_layers(
        query="continue",
        intent_bundle={"intent_kind": "resume_workflow"},
        budget_policy={"budget_tier": "quick"},
        surface="pre_answer",
        scope_hint={"scope_type": "workflow", "scope_id": "WF-SL-1"},
        requested_layers=["next_action"],
    )

    assert selected["layers"] == []
    assert selected["errors"][0]["error"] == "layer_stale"
    row = conn.execute(
        "SELECT status, stale_at, stale_reason FROM semantic_layers WHERE layer_uid=?",
        (built["layers"][0]["layer_uid"],),
    ).fetchone()
    assert dict(row) == {"status": "fresh", "stale_at": 0.0, "stale_reason": ""}


def test_semantic_layers_use_structured_intent_not_keywords(isolated_db):
    import semantic_layers

    conn = _conn()
    _seed_session(conn)
    conn.execute(
        "INSERT INTO session_diary (session_id, created_at, decisions, summary, pending, context_next) VALUES ('s1', '2026-06-02T10:00:00', '', 'Layer summary', '', 'Next from layer')"
    )
    semantic_layers.build_semantic_layers("session", "s1", layers=["brief", "next_action"])

    selected = semantic_layers.select_semantic_layers(
        query="palabras sin relacion con resumen",
        intent_bundle={"intent_kind": "resume_workflow"},
        budget_policy={"budget_tier": "quick"},
        surface="pre_answer",
        scope_hint={"scope_type": "session", "scope_id": "s1"},
    )

    assert selected["layers"]
    assert "Layer summary" in selected["rendered"]


def test_pre_answer_reads_fresh_layers_but_instant_does_not_generate(isolated_db):
    import pre_answer_router
    import semantic_layers

    conn = _conn()
    _seed_session(conn)
    conn.execute(
        "INSERT INTO session_diary (session_id, created_at, decisions, summary, pending, context_next) VALUES ('s1', '2026-06-02T10:00:00', '', 'Fresh layer summary', '', 'Next action')"
    )
    semantic_layers.build_semantic_layers("session", "s1", layers=["brief", "next_action"])

    quick = pre_answer_router.route_pre_answer(
        "continue work",
        intent="memory_question",
        sid="s1",
        budget_policy={
            "surface": "pre_answer",
            "budget_tier": "quick",
            "allowed_sources": ["semantic_layers"],
            "max_sources": 1,
            "max_rendered_chars": 1000,
            "max_source_timeout_ms": 120,
        },
    )
    assert quick.should_inject is True
    assert "Fresh layer summary" in quick.rendered
    assert quick.sources[0].source == "semantic_layers"

    instant = pre_answer_router.route_pre_answer(
        "continue work",
        intent="memory_question",
        sid="s1",
        budget_policy={
            "surface": "pre_answer",
            "budget_tier": "instant",
            "allowed_sources": [],
            "max_sources": 0,
            "max_rendered_chars": 0,
        },
    )
    assert instant.should_inject is False
    assert instant.sources == []


def test_session_portable_context_reads_existing_layers_without_raw_transcript(isolated_db):
    import semantic_layers
    from tools_sessions import handle_session_portable_context

    conn = _conn()
    _seed_session(conn)
    conn.execute(
        "INSERT INTO session_diary (session_id, created_at, decisions, summary, pending, context_next) VALUES ('s1', '2026-06-02T10:00:00', '', 'Portable summary', '', 'Portable next')"
    )
    semantic_layers.build_semantic_layers(
        "session",
        "s1",
        layers=["headline", "brief", "next_action", "risks", "source_map"],
    )
    before_count = conn.execute("SELECT COUNT(*) FROM semantic_layers").fetchone()[0]

    rendered = handle_session_portable_context("s1")

    assert "Semantic layers:" in rendered
    assert "Portable summary" in rendered
    assert "transcript:" not in rendered.lower()
    assert conn.execute("SELECT COUNT(*) FROM semantic_layers").fetchone()[0] == before_count
    assert semantic_layers.list_semantic_layers(scope_type="session", scope_id="s1")


def test_semantic_layers_plugin_returns_json(isolated_db):
    from plugins import semantic_layers as plugin

    payload = json.loads(
        plugin.handle_semantic_layers_build(
            "release",
            "7.28",
            layers='["headline"]',
            source_refs='["test:tests/test_semantic_layers.py::plugin"]',
            values='{"headline":"Release headline"}',
            surface_allowlist='["audit"]',
        )
    )
    assert payload["ok"] is True

    selected = json.loads(plugin.handle_semantic_layers_select("release", "7.28", surface="audit", requested_layers='["headline"]'))
    assert selected["layers"][0]["value_redacted"] == "Release headline"


def test_semantic_layers_plugin_list_filters_by_surface(isolated_db):
    import semantic_layers
    from plugins import semantic_layers as plugin

    semantic_layers.build_semantic_layers(
        "release",
        "7.28-secret-list",
        layers=["headline"],
        source_refs=["test:tests/test_semantic_layers.py::plugin_list_secret"],
        values={"headline": "Secret plugin list payload"},
        privacy_level="secret",
        allowed_surfaces=["audit"],
    )

    pre_answer = json.loads(
        plugin.handle_semantic_layers_list("release", "7.28-secret-list", surface="pre_answer")
    )
    audit = json.loads(
        plugin.handle_semantic_layers_list("release", "7.28-secret-list", surface="audit")
    )

    assert pre_answer["layers"] == []
    assert audit["layers"][0]["value_redacted"] == "Secret plugin list payload"


def test_semantic_layers_plugin_list_validates_sources_before_returning(isolated_db):
    import semantic_layers
    from plugins import semantic_layers as plugin

    conn = _conn()
    conn.execute(
        """
        INSERT INTO memory_events (
            event_uid, created_at, source_type, event_type, input_hash,
            output_digest, privacy_level
        ) VALUES ('ME-plugin-list-privacy-change', 1780409000.0, 'test', 'note', 'in', 'out', 'normal')
        """
    )
    semantic_layers.build_semantic_layers(
        "release",
        "7.28-plugin-list-privacy-change",
        layers=["headline"],
        source_refs=["test:tests/test_semantic_layers.py::plugin_list_privacy_change"],
        evidence_refs=["memory_event:ME-plugin-list-privacy-change"],
        values={"headline": "Plugin list should hide this after privacy change"},
        privacy_level="normal",
        allowed_surfaces=["pre_answer", "audit"],
    )
    assert json.loads(
        plugin.handle_semantic_layers_list("release", "7.28-plugin-list-privacy-change", surface="pre_answer")
    )["layers"]

    conn.execute(
        "UPDATE memory_events SET privacy_level='secret' WHERE event_uid='ME-plugin-list-privacy-change'"
    )
    listed = json.loads(
        plugin.handle_semantic_layers_list("release", "7.28-plugin-list-privacy-change", surface="pre_answer")
    )

    assert listed["layers"] == []
