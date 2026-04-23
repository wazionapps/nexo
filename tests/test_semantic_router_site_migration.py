from __future__ import annotations

import importlib
import os
import sys
from types import SimpleNamespace

import pytest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture
def fake_semantic_router(monkeypatch):
    calls = []

    def route(**kwargs):
        calls.append(kwargs)
        label = tuple(kwargs.get("labels") or ("yes",))[0]
        return SimpleNamespace(ok=True, label=label, verdict=label)

    fake = SimpleNamespace(route=route)
    monkeypatch.setitem(sys.modules, "semantic_router", fake)
    return calls


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
