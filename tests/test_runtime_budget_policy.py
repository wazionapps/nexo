from __future__ import annotations

import json


def test_budget_policy_general_instant_no_sources():
    from pre_answer_runtime import select_budget_policy
    from pre_answer_router import route_pre_answer

    policy = select_budget_policy(query="hola", intent="general")
    calls = []
    result = route_pre_answer(
        "hola",
        intent="general",
        budget_policy=policy.to_dict(),
        source_adapters={"memory": lambda request: calls.append("memory")},
    )

    assert policy.budget_tier == "instant"
    assert policy.deadline_ms == 80
    assert policy.token_budget == 0
    assert policy.max_sources == 0
    assert result.sources == []
    assert calls == []


def test_budget_policy_quick_for_simple_file_location():
    from pre_answer_runtime import select_budget_policy

    policy = select_budget_policy(query="where is app.py?", intent="file_location")

    assert policy.budget_tier == "quick"
    assert policy.deadline_ms == 300
    assert "project_atlas" in policy.allowed_sources
    assert {"memory", "cognitive", "local_context", "transcripts"} <= set(policy.forbidden_sources)
    assert policy.can_use_remote_llm is False


def test_budget_policy_standard_commitments_first():
    from pre_answer_runtime import select_budget_policy
    from pre_answer_router import route_pre_answer

    calls = []

    def source(name):
        def _inner(request):
            calls.append(name)
            return {"source": name, "rendered": name, "evidence_refs": [f"{name}:1"], "result_count": 1}

        return _inner

    policy = select_budget_policy(query="que prometi?", intent="schedule_commitment")
    result = route_pre_answer(
        "que prometi?",
        intent="schedule_commitment",
        budget_policy=policy.to_dict(),
        source_adapters={
            "commitments": source("commitments"),
            "reminders": source("reminders"),
            "followups": source("followups"),
            "workflows": source("workflows"),
            "transcripts": source("transcripts"),
        },
    )

    assert policy.budget_tier == "standard"
    assert calls[0] == "commitments"
    assert result.should_inject is True


def test_budget_policy_deep_prior_work_allows_indexed_transcripts():
    from pre_answer_runtime import select_budget_policy

    policy = select_budget_policy(query="por que tocaste el release?", intent="prior_work", area="brain")

    assert policy.budget_tier == "deep"
    assert "transcripts" in policy.allowed_sources
    assert "local_context" in policy.allowed_sources
    assert policy.max_source_timeout_ms == 1200


def test_budget_policy_critical_requires_guard_atlas_release():
    from pre_answer_runtime import select_budget_policy

    policy = select_budget_policy(
        query="lanza release",
        intent="modify_existing",
        area="release",
        operational_state={"verification_requirement": "release_gate"},
    )

    assert policy.budget_tier == "critical"
    assert set(policy.required_sources) == {"guard_context", "project_atlas", "evidence_ledger"}
    assert "release_readiness" in policy.required_checks
    assert policy.fallback_policy == "mandatory_fail_closed"


def test_budget_policy_critical_required_timeout_defer_or_gap():
    from pre_answer_runtime import select_budget_policy
    from pre_answer_router import route_pre_answer

    policy = select_budget_policy(query="release", intent="modify_existing", area="release")
    result = route_pre_answer(
        "release",
        intent="modify_existing",
        budget_policy=policy.to_dict(),
        source_adapters={
            "guard_context": lambda request: {"source": "guard_context"},
            "project_atlas": lambda request: {"source": "project_atlas"},
            "evidence_ledger": lambda request: (_ for _ in ()).throw(RuntimeError("required unavailable")),
        },
    )

    assert result.decision_signal == "defer"
    assert result.must_disclose_gap is True
    assert result.missing_required_sources_count >= 1


def test_budget_policy_optional_timeout_does_not_block():
    from pre_answer_router import route_pre_answer

    policy = {
        "budget_tier": "standard",
        "max_sources": 1,
        "max_source_timeout_ms": 1,
        "allowed_sources": ["recent_context"],
        "required_sources": [],
        "fallback_policy": "fallback_if_no_evidence",
    }

    def timeout_source(request):
        raise TimeoutError("synthetic optional timeout")

    result = route_pre_answer(
        "que hice?",
        intent="prior_work",
        budget_policy=policy,
        source_adapters={"recent_context": timeout_source},
    )

    assert result.decision_signal == ""
    assert result.missing_required_sources_count == 0


def test_budget_policy_single_escalation_only():
    from pre_answer_runtime import run_pre_answer_route

    result = run_pre_answer_route(
        {"query": "where is missing-file.md", "intent": "file_location", "source": "test"},
        source_adapters={
            "project_atlas": lambda request: {"source": "project_atlas"},
            "filesystem": lambda request: {
                "source": "filesystem",
                "rendered": "found after escalation",
                "evidence_refs": ["filesystem:missing-file.md"],
                "result_count": 1,
            },
            "memory": lambda request: (_ for _ in ()).throw(AssertionError("quick escalation must not call memory")),
            "transcripts": lambda request: (_ for _ in ()).throw(AssertionError("quick escalation must not call transcripts")),
            "local_context": lambda request: (_ for _ in ()).throw(AssertionError("quick escalation must not call local_context")),
        },
    )

    assert result["ok"] is True
    assert result["escalated_from"] == "quick"
    assert result["escalated_to"] == "standard"
    assert result["should_inject"] is True
    policy = result["runtime_budget_policy"]
    assert "filesystem" in policy["allowed_sources"]
    assert "memory" not in policy["allowed_sources"]
    assert "transcripts" not in policy["allowed_sources"]
    assert "local_context" not in policy["allowed_sources"]
    assert {"memory", "transcripts", "local_context"} <= set(policy["forbidden_sources"])


def test_budget_policy_no_query_preview_in_route_telemetry():
    from pre_answer_router import route_pre_answer

    events = []
    route_pre_answer(
        "token=sk_live_1234567890abcdef",
        intent="memory_question",
        telemetry_sink=events.append,
        source_adapters={"memory": lambda request: {"source": "memory"}},
    )

    blob = json.dumps(events, ensure_ascii=False)
    assert "query_preview" not in blob
    assert "sk_live_1234567890abcdef" not in blob


def test_budget_policy_source_names_are_canonical():
    from pre_answer_runtime import CANONICAL_ROUTER_SOURCES, select_budget_policy
    from pre_answer_router import default_source_adapters

    adapters = set(default_source_adapters())
    assert CANONICAL_ROUTER_SOURCES <= adapters
    for intent in ("general", "file_location", "schedule_commitment", "prior_work", "runtime_diagnosis"):
        policy = select_budget_policy(query=intent, intent=intent, area="brain")
        names = set(policy.allowed_sources) | set(policy.required_sources)
        assert names <= CANONICAL_ROUTER_SOURCES
