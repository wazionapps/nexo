from __future__ import annotations

import json
import time


def test_pre_answer_router_intent_matrix_multilingual():
    import pre_answer_router as router

    cases = [
        ("ya hiciste el fix del router ayer?", "prior_work"),
        ("where is src/pre_answer_router.py located?", "file_location"),
        ("modifica lo anterior en ese archivo", "modify_existing"),
        ("recuerdas la decision sobre local context?", "memory_question"),
        ("did you do that in another terminal?", "identity_authorship"),
        ("recuérdame mañana revisar el deadline", "schedule_commitment"),
        ("diagnostica por que NEXO MCP runtime no arranca", "runtime_diagnosis"),
        ("hola, dime una frase corta", "general"),
    ]

    for text, expected in cases:
        result = router.classify_intent(text)
        assert result.intent == expected, (text, result)

    prior_plan = router.plan_sources("prior_work")
    assert [step.name for step in prior_plan.primary] == [
        "recent_context",
        "evidence_ledger",
        "protocol_tasks",
        "workflows",
        "change_log",
        "diary",
    ]
    assert prior_plan.fallback[0].name == "transcripts"


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


def test_pre_answer_router_prior_work_uses_operational_stores_before_transcript():
    import pre_answer_router as router

    calls = []

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
            "protocol_tasks": empty_source("protocol_tasks"),
            "workflows": empty_source("workflows"),
            "change_log": empty_source("change_log"),
            "diary": empty_source("diary"),
            "transcripts": transcript_source,
            "memory": empty_source("memory"),
        },
    )

    assert result.intent == "prior_work"
    assert calls[:6] == [
        "recent_context",
        "evidence_ledger",
        "protocol_tasks",
        "workflows",
        "change_log",
        "diary",
    ]
    assert calls.index("transcripts") > calls.index("diary")
    assert result.should_inject is True
    assert result.evidence_refs == ["transcript:1"]


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
