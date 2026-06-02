"""Tests for src/semantic_router.py — Plan ONEPASS LLM Coverage.

Do NOT download models or call remote APIs. Every test stubs the three
layer adapters (fast_local, semantic_reasoner, remote_fallback) so the
router's policy and dispatch logic is verified in isolation.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# Policy table
# ---------------------------------------------------------------------------


def test_all_textual_kinds_are_registered():
    import semantic_router as sr

    expected = {
        "session_end_intent",
        "autonomy_mandate",
        "guard_verbal_ack",
        "r14_correction",
        "r16_declared_done",
        "r17_promise_debt",
        "r34_identity_coherence",
        "followup_operator_attention",
        "drive_signal_type",
        "drive_area",
        "reply_event_type",
        "query_intent",
        "sentiment_intent",
        "pre_answer_intent",
    }
    assert set(sr.TEXTUAL_KINDS) == expected


def test_all_code_aware_kinds_are_registered():
    import semantic_router as sr

    expected = {
        "r20_constant_change",
        "t4_r15",
        "t4_r23e",
        "t4_r23f",
        "t4_r23h",
    }
    assert set(sr.CODE_AWARE_KINDS) == expected


def test_policy_covers_every_declared_kind():
    import semantic_router as sr

    for kind in sr.ALL_DECISION_KINDS:
        policy = sr.policy_for(kind)
        assert policy is not None, f"missing policy for {kind}"
        assert "family" in policy
        assert "reasoner_mode" in policy
        assert policy["reasoner_mode"] in {"multipass_local", "cached_llm"}


def test_textual_family_uses_multipass_local():
    import semantic_router as sr

    for kind in sr.TEXTUAL_KINDS:
        policy = sr.policy_for(kind)
        assert policy["family"] == "textual"
        assert policy["reasoner_mode"] == "multipass_local"
        assert policy["fast_local_threshold"] is not None


def test_code_aware_family_skips_fast_local_and_uses_cached_llm():
    import semantic_router as sr

    for kind in sr.CODE_AWARE_KINDS:
        policy = sr.policy_for(kind)
        assert policy["family"] == "code_aware"
        assert policy["reasoner_mode"] == "cached_llm"
        assert policy["fast_local_threshold"] is None


def test_pre_answer_intent_does_not_cold_load_local_classifier(monkeypatch):
    import semantic_router as sr

    calls = {"fast": None, "reasoner": False, "remote": False}

    def fake_fast_local(**kwargs):
        calls["fast"] = kwargs
        return None

    def fake_reasoner(**kwargs):
        calls["reasoner"] = True
        return _fake_router_result(
            ok=True,
            decision_kind=kwargs["decision_kind"],
            verdict="prior_work",
            label="prior_work",
            confidence=0.90,
            route_used="semantic_reasoner",
        )

    def fake_remote(**kwargs):
        calls["remote"] = True
        return None

    monkeypatch.setattr(sr, "_run_fast_local", fake_fast_local)
    monkeypatch.setattr(sr, "_run_semantic_reasoner", fake_reasoner)
    monkeypatch.setattr(sr, "_run_remote_fallback", fake_remote)
    monkeypatch.setattr(sr, "_local_classifier_warm", lambda: False)

    result = sr.route(
        decision_kind="pre_answer_intent",
        question="classify pre-answer",
        context="qué prometí para mañana",
        labels=("prior_work", "schedule_commitment", "general"),
        allow_remote_fallback=False,
    )

    assert calls["fast"]["allow_cold_load"] is False
    assert calls["reasoner"] is False
    assert calls["remote"] is False
    assert result.ok is False
    assert result.route_used == "no_route"


def test_pre_answer_intent_can_enable_cold_load_explicitly(monkeypatch):
    import semantic_router as sr

    calls = {"fast": None}

    def fake_fast_local(**kwargs):
        calls["fast"] = kwargs
        return _fake_router_result(
            ok=True,
            decision_kind="pre_answer_intent",
            verdict="schedule_commitment",
            label="schedule_commitment",
            confidence=0.88,
            route_used="fast_local",
        )

    monkeypatch.setenv("NEXO_PRE_ANSWER_ALLOW_COLD_SEMANTIC_LOAD", "1")
    monkeypatch.setattr(sr, "_run_fast_local", fake_fast_local)
    monkeypatch.setattr(sr, "_run_semantic_reasoner", lambda **kwargs: None)
    monkeypatch.setattr(sr, "_run_remote_fallback", lambda **kwargs: None)
    monkeypatch.setattr(sr, "_local_classifier_warm", lambda: False)

    result = sr.route(
        decision_kind="pre_answer_intent",
        question="classify pre-answer",
        context="qué prometí para mañana",
        labels=("prior_work", "schedule_commitment", "general"),
    )

    assert calls["fast"]["allow_cold_load"] is True
    assert result.ok is True
    assert result.label == "schedule_commitment"


def test_pre_answer_intent_can_enable_remote_fallback_explicitly(monkeypatch):
    import semantic_router as sr

    calls = {"remote": False}

    def fake_remote(**kwargs):
        calls["remote"] = True
        return _fake_router_result(
            ok=True,
            decision_kind=kwargs["decision_kind"],
            verdict="schedule_commitment",
            label="schedule_commitment",
            confidence=0.72,
            route_used="remote_fallback",
            degraded=True,
        )

    monkeypatch.setattr(sr, "_run_fast_local", lambda **kwargs: None)
    monkeypatch.setattr(sr, "_run_semantic_reasoner", lambda **kwargs: None)
    monkeypatch.setattr(sr, "_run_remote_fallback", fake_remote)
    monkeypatch.setattr(sr, "_local_classifier_warm", lambda: False)

    result = sr.route(
        decision_kind="pre_answer_intent",
        question="classify pre-answer",
        context="qué prometí para mañana",
        labels=("prior_work", "schedule_commitment", "general"),
        allow_remote_fallback=True,
    )

    assert calls["remote"] is True
    assert result.ok is True
    assert result.label == "schedule_commitment"


def test_policy_kinds_are_documented():
    """Drift check: every registered kind must be mentioned in the
    semantic-reasoner model notes so docs cannot silently go stale."""
    from pathlib import Path

    import semantic_router as sr

    notes = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "semantic-reasoner-model-notes.md"
    ).read_text(encoding="utf-8")
    for kind in sr.ALL_DECISION_KINDS:
        assert kind in notes, f"decision_kind {kind} is not documented"


# ---------------------------------------------------------------------------
# Dispatch logic
# ---------------------------------------------------------------------------


def _fake_router_result(**kwargs):
    import semantic_router as sr

    return sr.RouterResult(**kwargs)


def test_route_returns_fast_local_result_when_confidence_is_high(monkeypatch):
    import semantic_router as sr

    def fake_fast_local(*, question, context, labels, confidence_floor, allow_cold_load=True):
        return _fake_router_result(
            ok=True,
            decision_kind="",
            verdict="done_claim",
            label="done_claim",
            confidence=0.92,
            route_used="fast_local",
        )

    monkeypatch.setattr(sr, "_run_fast_local", fake_fast_local)
    result = sr.route(
        decision_kind="r16_declared_done",
        question="ya está listo",
        labels=("done_claim", "status_update", "noise"),
    )
    assert result.ok is True
    assert result.route_used == "fast_local"
    assert result.verdict == "done_claim"
    assert result.decision_kind == "r16_declared_done"


def test_route_falls_through_to_reasoner_when_fast_local_refuses(monkeypatch):
    import semantic_router as sr

    monkeypatch.setattr(sr, "_run_fast_local", lambda **kw: None)

    def fake_reasoner(**kw):
        return _fake_router_result(
            ok=True,
            decision_kind=kw["decision_kind"],
            verdict="correction",
            label="correction",
            confidence=0.80,
            route_used="semantic_reasoner",
            meta={"mode": "multipass_local"},
        )

    monkeypatch.setattr(sr, "_run_semantic_reasoner", fake_reasoner)
    monkeypatch.setattr(sr, "_run_remote_fallback", lambda **kw: None)

    result = sr.route(
        decision_kind="r14_correction",
        question="no, así no",
        labels=("correction", "noise"),
    )
    assert result.ok is True
    assert result.route_used == "semantic_reasoner"
    assert result.verdict == "correction"


def test_route_reaches_remote_fallback_when_both_local_layers_refuse(monkeypatch):
    import semantic_router as sr

    monkeypatch.setattr(sr, "_run_fast_local", lambda **kw: None)
    monkeypatch.setattr(sr, "_run_semantic_reasoner", lambda **kw: None)

    def fake_remote(**kw):
        return _fake_router_result(
            ok=True,
            decision_kind=kw["decision_kind"],
            verdict="promise",
            label="promise",
            confidence=0.55,
            route_used="remote_fallback",
            degraded=True,
        )

    monkeypatch.setattr(sr, "_run_remote_fallback", fake_remote)

    result = sr.route(
        decision_kind="r17_promise_debt",
        question="I will fix it later",
        labels=("promise", "noise"),
    )
    assert result.ok is True
    assert result.route_used == "remote_fallback"
    assert result.degraded is True


def test_route_respects_allow_remote_fallback_false(monkeypatch):
    import semantic_router as sr

    monkeypatch.setattr(sr, "_run_fast_local", lambda **kw: None)
    monkeypatch.setattr(sr, "_run_semantic_reasoner", lambda **kw: None)

    called = {"remote": False}

    def fake_remote(**kw):
        called["remote"] = True
        return _fake_router_result(
            ok=True,
            decision_kind=kw["decision_kind"],
            route_used="remote_fallback",
        )

    monkeypatch.setattr(sr, "_run_remote_fallback", fake_remote)

    result = sr.route(
        decision_kind="r16_declared_done",
        question="ya lo tengo",
        labels=("done_claim", "noise"),
        allow_remote_fallback=False,
    )
    assert called["remote"] is False
    assert result.ok is False
    assert result.route_used == "no_route"


def test_route_rejects_unknown_decision_kind():
    import semantic_router as sr

    result = sr.route(
        decision_kind="made_up_kind",
        question="anything",
        labels=("a", "b"),
    )
    assert result.ok is False
    assert result.route_used == "no_route"
    assert "unknown decision_kind" in (result.error or "")


def test_code_aware_kind_skips_fast_local(monkeypatch):
    import semantic_router as sr

    called = {"fast": False}

    def fake_fast_local(**kw):
        called["fast"] = True
        return _fake_router_result(
            ok=True,
            decision_kind="",
            verdict="wrong",
            route_used="fast_local",
            confidence=0.99,
        )

    def fake_reasoner(**kw):
        return _fake_router_result(
            ok=True,
            decision_kind=kw["decision_kind"],
            verdict="t4_bypass",
            label="t4_bypass",
            confidence=0.70,
            route_used="semantic_reasoner",
            meta={"mode": "cached_llm"},
        )

    monkeypatch.setattr(sr, "_run_fast_local", fake_fast_local)
    monkeypatch.setattr(sr, "_run_semantic_reasoner", fake_reasoner)
    monkeypatch.setattr(sr, "_run_remote_fallback", lambda **kw: None)

    result = sr.route(
        decision_kind="t4_r15",
        question="destructive rm in build script",
        context="scripts/deploy.sh line 12",
        labels=("t4_bypass", "safe"),
    )
    assert called["fast"] is False, "code-aware kinds must skip fast_local"
    assert result.ok is True
    assert result.route_used == "semantic_reasoner"


def test_route_returns_no_route_when_every_layer_unavailable(monkeypatch):
    import semantic_router as sr

    monkeypatch.setattr(sr, "_run_fast_local", lambda **kw: None)
    monkeypatch.setattr(sr, "_run_semantic_reasoner", lambda **kw: None)
    monkeypatch.setattr(sr, "_run_remote_fallback", lambda **kw: None)

    result = sr.route(
        decision_kind="session_end_intent",
        question="hasta mañana",
        labels=("end", "continue"),
    )
    assert result.ok is False
    assert result.route_used == "no_route"
    assert result.degraded is True


# ---------------------------------------------------------------------------
# Remote fallback helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, labels, expected",
    [
        ("done_claim", ("done_claim", "noise"), "done_claim"),
        ("DONE_CLAIM", ("done_claim", "noise"), "done_claim"),
        ("unknown", ("done_claim", "noise"), None),
        ("", ("done_claim", "noise"), None),
        ("   noise  ", ("done_claim", "noise"), "noise"),
        ("I think done_claim", ("done_claim", "noise"), "done_claim"),
        ("completely irrelevant", ("done_claim", "noise"), None),
    ],
)
def test_normalize_remote_answer(raw, labels, expected):
    import semantic_router as sr

    # Accept either the helper's own name or a post-refactor alias.
    normalize = getattr(sr, "_normalize_remote_answer", None)
    assert normalize is not None
    assert normalize(raw, labels) == expected


# ---------------------------------------------------------------------------
# Audit-driven hardening — fail-closed contract
# ---------------------------------------------------------------------------


def test_remote_fallback_degrades_on_unexpected_exception(monkeypatch):
    """Audit A2: call_model_raw can raise exception types other than
    ClassifierUnavailableError (provider APIError, TimeoutError, etc.).
    The router MUST degrade instead of propagating.

    Both symbols are exposed on the stub so the unrelated exception
    type is NOT a subclass of the declared ClassifierUnavailableError,
    and must therefore be routed to the catch-all ``except Exception``
    branch.
    """
    import sys

    import semantic_router as sr

    class _ClassifierUnavailableError(RuntimeError):
        pass

    class _UnrelatedError(RuntimeError):
        pass

    def stub(*args, **kwargs):  # noqa: ARG001
        raise _UnrelatedError("unexpected")

    fake_module = type("m", (), {})()
    fake_module.call_model_raw = stub
    fake_module.ClassifierUnavailableError = _ClassifierUnavailableError
    monkeypatch.setitem(sys.modules, "call_model_raw", fake_module)

    result = sr._run_remote_fallback(
        decision_kind="t4_r15",
        question="rm -rf /",
        labels=("t4_bypass", "safe"),
        context="",
    )
    assert result is not None
    assert result.ok is False
    assert result.route_used == "remote_fallback"
    assert result.degraded is True
    assert "remote_error" in (result.error or "")


def test_remote_fallback_degrades_when_call_model_raw_missing(monkeypatch):
    """Stub module present but without call_model_raw attribute."""
    import sys

    import semantic_router as sr

    fake_module = type("m", (), {})()
    monkeypatch.setitem(sys.modules, "call_model_raw", fake_module)

    result = sr._run_remote_fallback(
        decision_kind="t4_r15",
        question="anything",
        labels=("t4_bypass", "safe"),
        context="",
    )
    assert result is not None
    assert result.ok is False
    assert "call_model_raw callable missing" in (result.error or "")


def test_fast_local_classifies_context_not_static_prompt(monkeypatch):
    """Guard callers pass stable prompt templates as ``question`` and the
    actual user/assistant text as ``context``. The fast-local layer must
    classify the live text, not the static prompt template."""
    import semantic_router as sr

    seen = {}

    class FakeClassifier:
        def __init__(self, confidence_floor):
            seen["confidence_floor"] = confidence_floor

        def classify(self, question, labels):
            seen["question"] = question
            seen["labels"] = labels
            return SimpleNamespace(
                label="yes",
                confidence=0.95,
                scores={"yes": 0.95, "no": 0.05},
                latency_ms=1.0,
            )

    fake_classifier = FakeClassifier(confidence_floor=0.0)
    fake_module = SimpleNamespace(
        get_shared_zero_shot_classifier=lambda confidence_floor: fake_classifier,
        is_local_classifier_warm=lambda: True,
    )
    monkeypatch.setitem(sys.modules, "classifier_local", fake_module)

    result = sr._run_fast_local(
        question="Is this a session-end intent?",
        context="hasta mañana, cerramos aquí",
        labels=("yes", "no"),
        confidence_floor=0.60,
    )
    assert result is not None and result.ok is True
    assert seen["question"] == "hasta mañana, cerramos aquí"
    assert seen["labels"] == ("yes", "no")
