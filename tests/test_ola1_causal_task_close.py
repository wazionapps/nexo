"""Ola 1 — task_close materializes causal/provenance edges.

record_task_close_edges existed but had NO caller, so the causal graph stayed
empty (0 candidates) and could never feed connect-the-dots at answer time.
handle_task_close now wires it (best-effort).
"""
import json


def _register_session(sid: str) -> str:
    from db import register_session
    register_session(sid, "ola1 causal test")
    return sid


def test_task_close_invokes_causal_edge_recorder(monkeypatch):
    import causal_graph
    from plugins.protocol import handle_task_open, handle_task_close

    calls = []
    monkeypatch.setattr(causal_graph, "record_task_close_edges", lambda **kw: calls.append(kw) or [])

    sid = _register_session("nexo-9001-1001")
    opened = json.loads(handle_task_open(
        sid=sid, goal="Patch X for causal edges", task_type="edit", area="nexo-ops",
        files="/tmp/ola1/x.py", plan='["inspect","patch"]', verification_step="pytest",
    ))
    closed = json.loads(handle_task_close(
        sid=sid, task_id=opened["task_id"], outcome="done",
        evidence="pytest -q passed: 5 passed, 0 failed, 0 skipped in 0.10s; no regressions",
        change_summary="patched X", change_why="needed", change_verify="pytest -q",
    ))
    assert closed["ok"] is True
    assert closed["change_log_id"] is not None
    assert len(calls) == 1
    assert calls[0]["task_id"] == opened["task_id"]
    assert calls[0]["change_log_id"] == closed["change_log_id"]


def test_task_close_materializes_causal_edges_in_kg():
    import causal_graph
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-9002-1002")
    opened = json.loads(handle_task_open(
        sid=sid, goal="Patch Y for causal edges", task_type="edit", area="nexo-ops",
        files="/tmp/ola1/y.py", plan='["inspect","patch"]', verification_step="pytest",
    ))
    closed = json.loads(handle_task_close(
        sid=sid, task_id=opened["task_id"], outcome="done",
        evidence="pytest -q passed: 7 passed, 0 failed, 0 skipped in 0.20s; verified live",
        change_summary="patched Y", change_why="needed", change_verify="pytest -q",
    ))
    assert closed["ok"] is True
    assert closed["change_log_id"] is not None

    edges = causal_graph.query_edges(ref_type="protocol_task", ref=opened["task_id"])
    assert edges["has_evidence"] is True
    relations = {e.get("relation") for e in edges.get("edges", [])}
    assert "ops:produced" in relations
