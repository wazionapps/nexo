from __future__ import annotations

import json
import sys
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def fake_pre_answer_semantic_router(monkeypatch):
    class RouterCalls(list):
        labels_by_text: dict[str, str] = {}

    calls = RouterCalls()
    calls.labels_by_text = {}

    def route(**kwargs):
        calls.append(kwargs)
        text = str(kwargs.get("context") or "").strip()
        label = calls.labels_by_text.get(text, "general")
        return SimpleNamespace(
            ok=True,
            label=label,
            verdict=label,
            confidence=0.93,
            route_used="semantic_reasoner",
            error=None,
        )

    monkeypatch.setitem(sys.modules, "semantic_router", SimpleNamespace(route=route))
    return calls


def test_pre_answer_router_intent_matrix_multilingual(fake_pre_answer_semantic_router):
    import pre_answer_router as router

    cases = [
        ("ya hiciste el fix del router ayer?", "prior_work"),
        ("where is src/pre_answer_router.py located?", "file_location"),
        ("modifica lo anterior en ese archivo", "modify_existing"),
        ("recuerdas la decision sobre local context?", "memory_question"),
        ("did you do that in another terminal?", "identity_authorship"),
        ("recuérdame mañana revisar el deadline", "schedule_commitment"),
        ("qué prometí hacer?", "schedule_commitment"),
        ("qué queda pendiente?", "schedule_commitment"),
        ("por qué toqué src/pre_answer_router.py", "prior_work"),
        ("diagnostica por que NEXO MCP runtime no arranca", "runtime_diagnosis"),
        ("hola, dime una frase corta", "general"),
    ]
    fake_pre_answer_semantic_router.labels_by_text = dict(cases)

    for text, expected in cases:
        result = router.classify_intent(text)
        assert result.intent == expected, (text, result)

    assert len(fake_pre_answer_semantic_router) == len(cases)
    assert all(call["decision_kind"] == "pre_answer_intent" for call in fake_pre_answer_semantic_router)
    assert all(tuple(call["labels"]) == router.PRE_ANSWER_INTENTS for call in fake_pre_answer_semantic_router)
    assert all(call["allow_remote_fallback"] is False for call in fake_pre_answer_semantic_router)

    prior_plan = router.plan_sources("prior_work")
    assert [step.name for step in prior_plan.primary] == [
        "recent_context",
        "evidence_ledger",
        "commitments",
        "protocol_tasks",
        "workflows",
        "change_log",
        "causal_graph",
        "diary",
    ]
    assert prior_plan.fallback[0].name == "transcripts"
    schedule_plan = router.plan_sources("schedule_commitment")
    assert schedule_plan.primary[0].name == "commitments"


def test_pre_answer_router_deadline_skips_slow_local_context():
    import pre_answer_router as router

    def empty_project_atlas(request):
        return router.SourceResult(source="project_atlas")

    def slow_local_context(request):
        time.sleep(0.35)
        return router.SourceResult(
            source="local_context",
            rendered="late local evidence",
            evidence_refs=["local:late"],
            result_count=1,
        )

    started = time.monotonic()
    result = router.route_pre_answer(
        "donde esta el archivo audit-router-memory.md",
        budget_ms=80,
        source_adapters={
            "project_atlas": empty_project_atlas,
            "local_context": slow_local_context,
            "filesystem": lambda request: router.SourceResult(source="filesystem"),
            "transcripts": lambda request: router.SourceResult(source="transcripts"),
        },
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.22
    local = next(source for source in result.sources if source.source == "local_context")
    assert local.skipped is True
    assert local.aborted_reason == "timeout"
    assert result.aborted_reason == "source_timeout"


def test_pre_answer_router_logs_route_event_without_raw_secret():
    import pre_answer_router as router

    events = []
    secret = "sk_live_1234567890abcdef"
    github_secret = "github_pat_1234567890abcdef1234567890abcdef"

    def diary_source(request):
        return router.SourceResult(
            source="diary",
            rendered=f"remembered value {secret}",
            evidence_refs=["diary:1"],
            result_count=1,
        )

    result = router.route_pre_answer(
        f"recuerdas el token {secret} y {github_secret} password=plain123?",
        intent="memory_question",
        budget_ms=500,
        source_adapters={
            "diary": diary_source,
            "memory": lambda request: router.SourceResult(source="memory"),
            "cognitive": lambda request: router.SourceResult(source="cognitive"),
        },
        telemetry_sink=events.append,
    )

    assert result.should_inject is True
    assert events
    telemetry_blob = json.dumps(events, ensure_ascii=False)
    result_blob = json.dumps(result.to_dict(), ensure_ascii=False)
    assert secret not in telemetry_blob
    assert github_secret not in telemetry_blob
    assert "plain123" not in telemetry_blob
    assert secret not in result_blob
    assert github_secret not in result_blob
    assert "[REDACTED_SECRET]" in telemetry_blob
    assert events[0]["query_hash"]
    assert "query" not in events[0]


def test_pre_answer_router_prior_work_uses_operational_stores_before_transcript(fake_pre_answer_semantic_router):
    import pre_answer_router as router

    calls = []
    fake_pre_answer_semantic_router.labels_by_text = {
        "ya hiciste lo del pre answer router?": "prior_work",
    }

    def empty_source(name):
        def _inner(request):
            calls.append(name)
            return router.SourceResult(source=name)

        return _inner

    def transcript_source(request):
        calls.append("transcripts")
        return router.SourceResult(
            source="transcripts",
            rendered="fallback transcript evidence",
            evidence_refs=["transcript:1"],
            result_count=1,
        )

    result = router.route_pre_answer(
        "ya hiciste lo del pre answer router?",
        budget_ms=1500,
        source_adapters={
            "recent_context": empty_source("recent_context"),
            "evidence_ledger": empty_source("evidence_ledger"),
            "commitments": empty_source("commitments"),
            "protocol_tasks": empty_source("protocol_tasks"),
            "workflows": empty_source("workflows"),
            "change_log": empty_source("change_log"),
            "diary": empty_source("diary"),
            "transcripts": transcript_source,
            "memory": empty_source("memory"),
        },
    )

    assert result.intent == "prior_work"
    assert calls[:7] == [
        "recent_context",
        "evidence_ledger",
        "commitments",
        "protocol_tasks",
        "workflows",
        "change_log",
        "diary",
    ]
    assert calls.index("transcripts") > calls.index("diary")
    assert result.should_inject is True
    assert result.evidence_refs == ["transcript:1"]


def test_prior_work_uses_causal_graph_without_new_intent_detector(fake_pre_answer_semantic_router):
    import causal_graph
    import pre_answer_router as router

    causal_graph.upsert_active_edge(
        source_type="file",
        source_ref="src/pre_answer_router.py",
        relation="causal:verified_by",
        target_type="test",
        target_ref="tests/test_pre_answer_router.py",
        reason_public="The pre-answer router change was verified by causal graph tests.",
        evidence_refs=["pytest:pre_answer_router"],
        project_key="nexo",
        confidence=0.95,
    )
    fake_pre_answer_semantic_router.labels_by_text = {
        "por que toque src/pre_answer_router.py": "prior_work",
    }

    def empty_source(name):
        return lambda request: router.SourceResult(source=name)

    def transcript_should_not_run(request):
        raise AssertionError("transcript fallback should not run when causal graph has evidence")

    result = router.route_pre_answer(
        "por que toque src/pre_answer_router.py",
        area="nexo",
        budget_ms=1500,
        source_adapters={
            "recent_context": empty_source("recent_context"),
            "evidence_ledger": empty_source("evidence_ledger"),
            "commitments": empty_source("commitments"),
            "protocol_tasks": empty_source("protocol_tasks"),
            "workflows": empty_source("workflows"),
            "change_log": empty_source("change_log"),
            "diary": empty_source("diary"),
            "transcripts": transcript_should_not_run,
        },
    )

    causal = next(source for source in result.sources if source.source == "causal_graph")
    assert result.intent == "prior_work"
    assert causal.has_evidence is True
    assert result.should_inject is True
    assert "pytest:pre_answer_router" in result.evidence_refs


def test_pre_answer_router_operator_continuity_questions_use_semantic_router(fake_pre_answer_semantic_router):
    import pre_answer_router as router

    cases = {
        "qué hice ayer?": "prior_work",
        "qué prometí hacer?": "schedule_commitment",
        "qué queda pendiente?": "schedule_commitment",
        "por qué toqué src/pre_answer_router.py": "prior_work",
    }
    fake_pre_answer_semantic_router.labels_by_text = dict(cases)

    for query, expected in cases.items():
        result = router.classify_intent(query)
        assert result.intent == expected, (query, result.intent, result.scores, result.features)
        assert result.features["semantic_route"] == 1.0

    assert [call["context"] for call in fake_pre_answer_semantic_router] == list(cases)


def test_pre_answer_cold_start_continuity_questions_do_not_route_general(monkeypatch):
    import pre_answer_router as router

    class NoRoute:
        ok = False
        label = None
        verdict = None
        confidence = 0.0
        route_used = "no_route"
        error = "semantic_unavailable"

    calls = []

    def no_route(**kwargs):
        calls.append(kwargs)
        return NoRoute()

    monkeypatch.setitem(sys.modules, "semantic_router", SimpleNamespace(route=no_route))

    for query in ("qué prometí hacer?", "qué queda pendiente?"):
        result = router.classify_intent(query)
        assert result.intent == "memory_question"
        assert result.features["conservative_continuity_fallback"] == 1.0

    for query in ("qué hora es?", "what is 2+2?", "did it rain yesterday?"):
        result = router.classify_intent(query)
        assert result.intent == "general"
        assert "conservative_continuity_fallback" not in result.features

    operator_past = router.classify_intent("what did you do yesterday?")
    assert operator_past.intent == "memory_question"
    assert operator_past.features["conservative_continuity_fallback"] == 1.0

    rationale = router.classify_intent("por qué toqué src/pre_answer_router.py")
    assert rationale.intent == "prior_work"
    assert rationale.features["conservative_continuity_fallback"] == 1.0

    location = router.classify_intent("where is src/pre_answer_router.py located?")
    assert location.intent == "file_location"

    assert all(call["decision_kind"] == "pre_answer_intent" for call in calls)


def test_pre_answer_cold_start_commitment_question_consults_commitments(monkeypatch, isolated_db):
    import db
    import pre_answer_router as router

    class NoRoute:
        ok = False
        label = None
        verdict = None
        confidence = 0.0
        route_used = "no_route"
        error = "semantic_unavailable"

    monkeypatch.setitem(sys.modules, "semantic_router", SimpleNamespace(route=lambda **kwargs: NoRoute()))
    created = db.create_commitment(
        statement="Enviar informe de auditoria multiagente",
        session_id="nexo-cold-commitment",
        project_key="nexo",
        source_type="test",
        source_id="cold-commitment",
    )

    result = router.route_pre_answer(
        "qué prometí?",
        sid="nexo-cold-commitment",
        area="nexo",
        budget_ms=1200,
    )

    assert created["ok"] is True
    assert result.intent == "memory_question"
    assert result.should_inject is True
    assert "commitments" in [source.source for source in result.sources]
    assert any(ref == f"commitments:{created['id']}" for ref in result.evidence_refs)


def test_pre_answer_cold_start_general_questions_do_not_inject_open_commitments(monkeypatch, isolated_db):
    import db
    import pre_answer_router as router

    class NoRoute:
        ok = False
        label = None
        verdict = None
        confidence = 0.0
        route_used = "no_route"
        error = "semantic_unavailable"

    monkeypatch.setitem(sys.modules, "semantic_router", SimpleNamespace(route=lambda **kwargs: NoRoute()))
    created = db.create_commitment(
        statement="Enviar informe de auditoria multiagente",
        session_id="nexo-cold-general",
        project_key="nexo",
        source_type="test",
        source_id="cold-general",
    )

    for query in ("qué hora es?", "what is 2+2?", "did it rain yesterday?"):
        result = router.route_pre_answer(
            query,
            sid="nexo-cold-general",
            area="nexo",
            budget_ms=1200,
        )
        assert created["ok"] is True
        assert result.intent == "general"
        assert result.should_inject is False
        assert "commitments" not in [source.source for source in result.sources]
        assert f"commitments:{created['id']}" not in result.evidence_refs


def test_pre_answer_cognitive_source_uses_memory_observations_with_evidence(isolated_db):
    import db
    import pre_answer_router as router

    event = db.record_memory_event(
        event_type="protocol_task_done",
        source_type="protocol_task",
        source_id="PT-COGNITIVE",
        session_id="nexo-cognitive",
        project_key="nexo",
        metadata={"goal": "Fix pre answer cognitive observations", "outcome": "done"},
        idempotency_key="pt-cognitive",
        created_at=1000.0,
    )
    db.process_memory_observation_queue(limit=10)

    result = router._source_cognitive(
        router.SourceRequest(query="cognitive observations", intent="memory_question", area="nexo")
    )

    assert result.result_count >= 1
    assert f"memory_event:{event['event_uid']}" in result.evidence_refs
    assert "Fix pre answer cognitive observations" in result.rendered


def test_pre_answer_schedule_commitment_consults_commitment_ledger(
    isolated_db,
    fake_pre_answer_semantic_router,
):
    import db
    import pre_answer_router as router

    created = db.create_commitment(
        statement="Revisar el release de Brain con benchmark CAS",
        session_id="nexo-commitment-router",
        project_key="nexo",
        source_type="test",
        source_id="commitment-router",
    )
    fake_pre_answer_semantic_router.labels_by_text = {
        "qué prometí revisar del release Brain?": "schedule_commitment",
    }

    result = router.route_pre_answer(
        "qué prometí revisar del release Brain?",
        sid="nexo-commitment-router",
        area="nexo",
        budget_ms=1200,
    )

    assert created["ok"] is True
    assert result.intent == "schedule_commitment"
    assert "commitments" in [source.source for source in result.sources]
    assert any(ref == f"commitments:{created['id']}" for ref in result.evidence_refs)


def test_pre_answer_schedule_commitment_falls_back_to_open_commitments(
    isolated_db,
    fake_pre_answer_semantic_router,
):
    import db
    import pre_answer_router as router

    created = db.create_commitment(
        statement="Enviar informe de auditoria multiagente",
        session_id="nexo-open-commitment",
        project_key="nexo",
        source_type="test",
        source_id="open-commitment",
    )
    fake_pre_answer_semantic_router.labels_by_text = {
        "qué prometí?": "schedule_commitment",
    }

    result = router.route_pre_answer(
        "qué prometí?",
        sid="nexo-open-commitment",
        area="nexo",
        budget_ms=1200,
    )

    assert created["ok"] is True
    assert result.intent == "schedule_commitment"
    assert any(ref == f"commitments:{created['id']}" for ref in result.evidence_refs)


def test_pre_answer_router_transcripts_use_index_before_raw_fallback(monkeypatch):
    import pre_answer_router as router
    import tools_transcripts
    import transcript_index

    transcript_index.index_transcript_session(
        {
            "client": "codex",
            "session_uid": "indexed-session",
            "session_file": "codex:indexed.jsonl",
            "display_name": "indexed.jsonl",
            "session_path": "/tmp/indexed.jsonl",
            "modified": "2026-05-19T12:00:00",
            "message_count": 2,
            "user_message_count": 1,
            "messages": [
                {"role": "user", "index": 1, "text": "needle indexed continuity request"},
                {"role": "assistant", "index": 2, "text": "indexed continuity answer"},
            ],
        }
    )
    monkeypatch.setattr(transcript_index, "index_recent_transcripts", lambda **kwargs: [])

    def raw_fallback_should_not_run(*args, **kwargs):
        raise AssertionError("raw transcript fallback should not run when index has evidence")

    monkeypatch.setattr(tools_transcripts, "handle_transcript_search", raw_fallback_should_not_run)

    result = router._source_transcripts(
        router.SourceRequest(query="needle indexed continuity", intent="prior_work")
    )

    assert result.source == "transcripts"
    assert result.result_count == 1
    assert "needle indexed continuity request" in result.rendered
    assert result.evidence_refs == ["transcript_index:1"]
