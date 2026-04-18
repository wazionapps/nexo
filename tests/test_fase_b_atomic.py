"""Tests for Fase B atomic rules R03 + R10 (Protocol Enforcer).

R03 — nexo_task_close evidence validator rejects trivial evidence
R10 — nexo_workflow_open requires a parent protocol task in the session

Both tests rely on the shared `isolated_db` fixture from conftest.py so
they never touch the live runtime (Regla dura 2: zero pytest on live
runtime, learning #437).
"""
from __future__ import annotations

import importlib
import json
import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")),
)


@pytest.fixture(autouse=True)
def faseb_runtime(isolated_db, monkeypatch):
    import db._core as db_core
    import db._protocol as db_protocol
    import db
    import plugins.protocol as protocol
    import plugins.workflow as workflow

    importlib.reload(db_core)
    importlib.reload(db_protocol)
    importlib.reload(db)
    importlib.reload(protocol)
    importlib.reload(workflow)
    # Force strict mode unconditionally for these tests — Fase 2 R03/R10
    # enforcement happens only in strict mode. Patch the function in both
    # modules that already imported it.
    monkeypatch.setattr("plugins.protocol.get_protocol_strictness", lambda: "strict")
    monkeypatch.setattr("plugins.workflow.get_protocol_strictness", lambda: "strict")
    yield


def _register_session(sid: str) -> str:
    from db import register_session
    register_session(sid, "fase b test")
    return sid


def _open_task(sid: str, goal: str = "fase b test task") -> str:
    """Open a strict-mode edit task that triggers must_verify."""
    from plugins.protocol import handle_task_open

    response = handle_task_open(
        sid=sid,
        goal=goal,
        task_type="edit",
        area="nexo-core",
        files=f"/tmp/fase-b-test/{sid}.py",
    )
    payload = json.loads(response)
    assert payload.get("ok") is True, f"task_open failed: {payload}"
    return payload["task_id"]


# ──────────────────────────────────────────────────────────────────────
# R03 — trivial evidence rejection
# ──────────────────────────────────────────────────────────────────────


def test_r03_rejects_empty_evidence():
    from plugins.protocol import handle_task_close

    sid = _register_session("nexo-99000001-1")
    tid = _open_task(sid)
    raw = handle_task_close(
        sid=sid,
        task_id=tid,
        outcome="done",
        evidence="",
    )
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "without evidence" in payload["error"].lower() or "without proof" in payload["error"].lower()


def test_r03_rejects_short_evidence():
    from plugins.protocol import handle_task_close

    sid = _register_session("nexo-99000001-2")
    tid = _open_task(sid)
    raw = handle_task_close(
        sid=sid,
        task_id=tid,
        outcome="done",
        evidence="ran tests",  # 9 chars < 50
    )
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload.get("evidence_quality_reason", "").startswith("too_short")
    assert "trivial" in payload["error"].lower()


def test_r03_rejects_single_word_done():
    from plugins.protocol import handle_task_close

    sid = _register_session("nexo-99000001-3")
    tid = _open_task(sid)
    raw = handle_task_close(
        sid=sid,
        task_id=tid,
        outcome="done",
        evidence="done.",
    )
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload.get("evidence_quality_reason") == "single_filler_word"


@pytest.mark.parametrize("trivial", [
    "listo",
    "LISTO",
    "ok",
    "ok!",
    "fixed",
    "completado",
    "resuelto",
    "hecho.",
])
def test_r03_rejects_all_filler_variants(trivial):
    from plugins.protocol import handle_task_close

    # Convert hash to positive digits only to match nexo-\d+-\d+
    sid = _register_session(f"nexo-99000002-{abs(hash(trivial)) % 1000000}")
    tid = _open_task(sid)
    raw = handle_task_close(
        sid=sid,
        task_id=tid,
        outcome="done",
        evidence=trivial,
    )
    payload = json.loads(raw)
    assert payload["ok"] is False, f"Expected rejection for {trivial!r}, got {payload}"
    assert payload.get("evidence_quality_reason") == "single_filler_word"


def test_r03_accepts_substantive_evidence():
    from plugins.protocol import handle_task_close

    sid = _register_session("nexo-99000001-4")
    tid = _open_task(sid)
    good = (
        "pytest tests/test_fake.py: 42 passed in 1.3s; "
        "diff check: no regressions in enforcement_engine.py; "
        "verified via NEXO_HOME=/tmp/nexo-test-r03 isolated run."
    )
    assert len(good) >= 50
    raw = handle_task_close(
        sid=sid,
        task_id=tid,
        outcome="done",
        evidence=good,
    )
    payload = json.loads(raw)
    assert payload["ok"] is True, f"Expected acceptance, got: {payload}"


def test_r03_does_not_affect_partial_outcome():
    """Partial/failed outcomes are not subject to the done-evidence gate."""
    from plugins.protocol import handle_task_close

    sid = _register_session("nexo-99000001-5")
    tid = _open_task(sid)
    raw = handle_task_close(
        sid=sid,
        task_id=tid,
        outcome="partial",
        evidence="",  # empty is OK for partial
        outcome_notes="work in progress, continuing next session",
    )
    payload = json.loads(raw)
    assert payload["ok"] is True, f"partial outcome rejected unexpectedly: {payload}"


# ──────────────────────────────────────────────────────────────────────
# R10 — workflow_open requires parent protocol task
# ──────────────────────────────────────────────────────────────────────


def test_r10_rejects_workflow_without_any_task():
    from plugins.workflow import handle_workflow_open

    sid = _register_session("nexo-99000001-6")
    raw = handle_workflow_open(sid=sid, goal="orphan workflow")
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload.get("rule_id") == "R10_workflow_open_without_task"
    assert "without a parent protocol task" in payload["error"]


def test_r10_accepts_workflow_with_explicit_task():
    from plugins.workflow import handle_workflow_open

    sid = _register_session("nexo-99000001-7")
    tid = _open_task(sid, "task for explicit workflow")
    raw = handle_workflow_open(
        sid=sid,
        goal="workflow anchored to task",
        protocol_task_id=tid,
    )
    payload = json.loads(raw)
    assert payload["ok"] is True, f"workflow_open rejected: {payload}"
    assert payload.get("protocol_task_id") == tid


def test_r10_accepts_workflow_with_any_open_task_in_session():
    """If the session has any open task, workflow_open succeeds without explicit id."""
    from plugins.workflow import handle_workflow_open

    sid = _register_session("nexo-99000001-8")
    _open_task(sid, "some other task in same session")
    raw = handle_workflow_open(sid=sid, goal="workflow without explicit id")
    payload = json.loads(raw)
    assert payload["ok"] is True, f"workflow_open rejected when session had open task: {payload}"


def test_r10_rejects_workflow_with_bogus_explicit_task():
    """Even with protocol_task_id, an unknown id still fails — unchanged behaviour."""
    from plugins.workflow import handle_workflow_open

    sid = _register_session("nexo-99000001-9")
    raw = handle_workflow_open(
        sid=sid,
        goal="workflow with bogus id",
        protocol_task_id="PT-does-not-exist-123",
    )
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "Unknown protocol_task_id" in payload["error"]
