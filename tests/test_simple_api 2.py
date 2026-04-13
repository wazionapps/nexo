from __future__ import annotations

import json
from types import SimpleNamespace


def test_handle_remember_wraps_durable_ingest(monkeypatch):
    from plugins import simple_api

    monkeypatch.setattr(simple_api.cognitive, "ingest_to_ltm", lambda *args, **kwargs: 42)

    payload = json.loads(
        simple_api.handle_remember(
            "Persist this note",
            title="Protocol note",
            domain="nexo",
            source_type="note",
        )
    )

    assert payload["ok"] is True
    assert payload["memory_id"] == 42
    assert payload["title"] == "Protocol note"
    assert payload["domain"] == "nexo"


def test_handle_memory_recall_delegates_to_recall(monkeypatch):
    from plugins import simple_api

    monkeypatch.setattr(simple_api, "handle_recall", lambda query, days=30: f"{query}|{days}")
    result = simple_api.handle_memory_recall("workflow drift", days=14)
    assert result == "workflow drift|14"


def test_handle_consolidate_returns_structured_summary(monkeypatch):
    from plugins import simple_api

    monkeypatch.setattr(simple_api.cognitive, "promote_stm_to_ltm", lambda: 3)
    monkeypatch.setattr(simple_api.cognitive, "process_quarantine", lambda: {"promoted": 2, "still_pending": 1})
    monkeypatch.setattr(simple_api.cognitive, "dream_cycle", lambda max_insights=12: {"insights": 4, "max_insights": max_insights})
    monkeypatch.setattr(simple_api.cognitive, "consolidate_semantic", lambda threshold=0.9, dry_run=False: {"merged": 5, "threshold": threshold, "dry_run": dry_run})

    payload = json.loads(simple_api.handle_consolidate(max_insights=6, threshold=0.88, dry_run=True))

    assert payload["ok"] is True
    assert payload["promoted_to_ltm"] == 3
    assert payload["quarantine"]["promoted"] == 2
    assert payload["dream_cycle"]["max_insights"] == 6
    assert payload["semantic_consolidation"]["threshold"] == 0.88
    assert payload["dry_run"] is True


def test_handle_run_workflow_wraps_workflow_open(monkeypatch):
    from plugins import simple_api

    monkeypatch.setattr(
        simple_api,
        "handle_workflow_open",
        lambda **kwargs: json.dumps({"ok": True, "run_id": "WF-123", "goal": kwargs["goal"]}),
    )

    payload = json.loads(
        simple_api.handle_run_workflow(
            sid="SID-1",
            goal="Ship v3",
            steps='[{"step_key":"plan","title":"Plan"}]',
        )
    )

    assert payload["ok"] is True
    assert payload["run_id"] == "WF-123"
    assert payload["goal"] == "Ship v3"
