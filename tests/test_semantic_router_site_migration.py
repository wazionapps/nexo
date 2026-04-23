from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture
def fake_semantic_router(monkeypatch):
    class RouterCalls(list):
        labels_by_kind: dict[str, str] = {}

    calls = RouterCalls()
    calls.labels_by_kind = {}

    def route(**kwargs):
        calls.append(kwargs)
        label = calls.labels_by_kind.get(
            kwargs.get("decision_kind"),
            tuple(kwargs.get("labels") or ("yes",))[0],
        )
        return SimpleNamespace(
            ok=True,
            label=label,
            verdict=label,
            confidence=0.91,
            route_used="fast_local",
            error=None,
        )

    fake = SimpleNamespace(route=route)
    monkeypatch.setitem(sys.modules, "semantic_router", fake)
    return calls


def _load_script_module(module_name: str, relative_path: str):
    path = Path(__file__).resolve().parents[1] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_session_end_intent_routes_through_semantic_router(fake_semantic_router):
    import session_end_intent as mod

    importlib.reload(mod)
    assert mod.detect_session_end_intent("hasta mañana, cerramos aquí") is True
    assert fake_semantic_router[-1]["decision_kind"] == "session_end_intent"
    assert fake_semantic_router[-1]["labels"] == ("session_end", "continue_session")
    assert "hasta mañana" in fake_semantic_router[-1]["context"]


def test_r14_correction_routes_through_semantic_router(fake_semantic_router):
    import r14_correction_learning as mod

    importlib.reload(mod)
    assert mod.detect_correction("no es así, te equivocaste en el contrato") is True
    assert fake_semantic_router[-1]["decision_kind"] == "r14_correction"
    assert fake_semantic_router[-1]["labels"] == ("negative_feedback", "ordinary_request")
    assert "te equivocaste" in fake_semantic_router[-1]["context"]


def test_r16_declared_done_routes_through_semantic_router(fake_semantic_router):
    import r16_declared_done as mod

    importlib.reload(mod)
    assert mod.detect_declared_done("The migration is finished and tests are green") is True
    assert fake_semantic_router[-1]["decision_kind"] == "r16_declared_done"
    assert fake_semantic_router[-1]["labels"] == ("declared_done", "not_done")
    assert "migration is finished" in fake_semantic_router[-1]["context"]


def test_r17_promise_debt_routes_through_semantic_router(fake_semantic_router):
    import r17_promise_debt as mod

    importlib.reload(mod)
    assert mod.detect_promise("I will create the migration in the next turn") is True
    assert fake_semantic_router[-1]["decision_kind"] == "r17_promise_debt"
    assert fake_semantic_router[-1]["labels"] == ("promise", "no_promise")
    assert "next turn" in fake_semantic_router[-1]["context"]


def test_autonomy_mandate_routes_through_semantic_router(tmp_path, monkeypatch, fake_semantic_router):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    import autonomy_mandate as mod

    importlib.reload(mod)
    st = mod.maybe_ingest_from_text(
        "Stop postponing this and execute the in-scope work now.",
        session_id="sid-semantic",
    )
    assert st is not None
    assert st.marker == "semantic-autonomy-mandate"
    assert fake_semantic_router[-1]["decision_kind"] == "autonomy_mandate"
    assert fake_semantic_router[-1]["labels"] == ("autonomy_mandate", "not_mandate")
    assert "execute the in-scope work" in fake_semantic_router[-1]["context"]


def test_guard_verbal_ack_routes_through_semantic_router(fake_semantic_router):
    import guard_verbal_ack as mod

    importlib.reload(mod)
    assert mod.detect_guard_verbal_ack(
        "vale, hazlo",
        task_type="edit",
        goal="Delete the legacy password file",
        file_path="/tmp/legacy-password.txt",
        guard_summary="BLOCKING RULES (#41)",
    ) is True
    assert fake_semantic_router[-1]["decision_kind"] == "guard_verbal_ack"
    assert fake_semantic_router[-1]["labels"] == ("explicit_ack", "not_ack")
    assert "Delete the legacy password file" in fake_semantic_router[-1]["context"]
    assert "/tmp/legacy-password.txt" in fake_semantic_router[-1]["context"]


def test_r20_constant_change_routes_through_semantic_router(fake_semantic_router):
    import r20_constant_change as mod

    importlib.reload(mod)
    assert mod.classify_edit_is_constant_change(
        "src/config.py",
        "API_BASE_URL = 'https://new.example.com'",
    ) is True
    assert fake_semantic_router[-1]["decision_kind"] == "r20_constant_change"
    assert fake_semantic_router[-1]["labels"] == (
        "shared_constant_change",
        "local_or_non_constant_change",
    )
    assert "API_BASE_URL" in fake_semantic_router[-1]["context"]


def test_t4_gate_routes_through_semantic_router(fake_semantic_router):
    fake_semantic_router.labels_by_kind["t4_r23e"] = "false_positive"
    import enforcement_engine as mod

    importlib.reload(mod)
    enforcer = mod.HeadlessEnforcer.__new__(mod.HeadlessEnforcer)
    enforcer._t4_gate_warned = set()
    enforcer._guardian_rule_event = lambda *args, **kwargs: None
    enforcer._guardian_rule_skip = lambda *args, **kwargs: None
    assert enforcer._t4_gate_says_no(
        "R23e",
        span="git push --force origin main",
        context="local test branch",
    ) is True
    assert fake_semantic_router[-1]["decision_kind"] == "t4_r23e"
    assert fake_semantic_router[-1]["labels"] == ("rule_applies", "false_positive")


def test_r34_engine_default_routes_through_semantic_router(fake_semantic_router):
    fake_semantic_router.labels_by_kind["r34_identity_coherence"] = "past_action_denial"
    import enforcement_engine as mod

    importlib.reload(mod)
    enforcer = mod.HeadlessEnforcer.__new__(mod.HeadlessEnforcer)
    enforcer.recent_tool_records = []
    enforcer.injection_queue = []
    enforcer.tools_called = set()
    enforcer.tool_timestamps = {}
    enforcer._guardian_mode_cache = {}
    enforcer._guardian_rule_mode = lambda _rule_id: "hard"
    enforcer.on_assistant_message("yo no he hecho eso")
    assert enforcer.injection_queue
    assert fake_semantic_router[-1]["decision_kind"] == "r34_identity_coherence"
    assert fake_semantic_router[-1]["labels"] == ("past_action_denial", "not_a_denial")


def test_followup_operator_attention_routes_through_semantic_router(fake_semantic_router):
    mod = _load_script_module(
        "nexo_followup_runner_semantic_test",
        "src/scripts/nexo-followup-runner.py",
    )

    assert mod._classifier_requires_operator_attention(
        "Francisco still needs to approve the production rollout.",
        operator_name="Francisco",
    ) is True
    assert fake_semantic_router[-1]["decision_kind"] == "followup_operator_attention"
    assert "Francisco" in fake_semantic_router[-1]["context"]


def test_drive_signal_and_area_route_through_semantic_router(fake_semantic_router):
    import tools_drive as mod

    importlib.reload(mod)
    signal = mod._local_classify_signal(
        "Revenue dropped 18 percent after yesterday's deploy and it looks unexpected."
    )
    assert signal["available"] is True
    assert signal["label"] == "anomaly"
    assert fake_semantic_router[-1]["decision_kind"] == "drive_signal_type"

    area = mod._local_classify_area("NEXO Guardian protocol map failed in headless mode.")
    assert area["available"] is True
    assert area["label"] == "shopify"
    assert fake_semantic_router[-1]["decision_kind"] == "drive_area"


def test_reply_event_routes_through_semantic_router(fake_semantic_router):
    mod = _load_script_module(
        "nexo_send_reply_semantic_test",
        "src/scripts/nexo-send-reply.py",
    )

    assert mod._classify_reply_event_semantically(
        "I received this and will send the full update tomorrow morning."
    ) == "ack"
    assert fake_semantic_router[-1]["decision_kind"] == "reply_event_type"


def test_query_intent_routes_through_semantic_router(fake_semantic_router):
    from cognitive import _search as mod

    importlib.reload(mod)
    result = mod._local_classify_query_intent("why did the deploy drift after update")
    assert result["available"] is True
    assert result["label"] == "howto"
    assert fake_semantic_router[-1]["decision_kind"] == "query_intent"


def test_sentiment_intent_routes_through_semantic_router(fake_semantic_router):
    from cognitive import _trust as mod

    importlib.reload(mod)
    result = mod._local_classify_sentiment_intent("this is wrong and needs fixing")
    assert result["available"] is True
    assert result["label"] == "correction"
    assert fake_semantic_router[-1]["decision_kind"] == "sentiment_intent"
