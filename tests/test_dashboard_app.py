import json
from pathlib import Path

from fastapi.testclient import TestClient

import db
from dashboard.app import app, _latest_periodic_summary, _protocol_explainability_snapshot, _summarize_engineering_loop


def test_latest_periodic_summary_reads_latest_label(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    deep_sleep = tmp_path / "operations" / "deep-sleep"
    deep_sleep.mkdir(parents=True)

    older = {
        "label": "2026-W13",
        "project_pulse": [{"project": "alpha", "score": 3.2}],
    }
    newer = {
        "label": "2026-W14",
        "project_pulse": [{"project": "beta", "score": 5.1}],
    }

    (deep_sleep / "2026-W13-weekly-summary.json").write_text(json.dumps(older), encoding="utf-8")
    (deep_sleep / "2026-W14-weekly-summary.json").write_text(json.dumps(newer), encoding="utf-8")

    summary = _latest_periodic_summary("weekly")
    assert summary["label"] == "2026-W14"
    assert summary["project_pulse"][0]["project"] == "beta"


def test_summarize_engineering_loop_surfaces_matters_drift_and_improvement():
    weekly = {
        "project_pulse": [
            {"project": "wazion", "score": 9.4, "status": "critical", "reasons": ["open followups", "blocked decisions"]},
            {"project": "nexo", "score": 6.2, "status": "watch", "reasons": ["recent diary activity"]},
        ],
        "protocol_summary": {
            "guard_check": {"compliance_pct": 62.5},
            "heartbeat": {"compliance_pct": 41.0},
            "change_log": {"compliance_pct": 88.0},
        },
        "top_patterns": [
            {"pattern": "release validation skipped", "count": 3},
        ],
        "trend": {
            "avg_trust_delta": 1.2,
            "avg_mood_delta": 0.08,
            "total_corrections_delta": -2,
            "protocol_compliance_delta": 9.5,
        },
        "delivery_metrics": {
            "engineering_followups": 4,
        },
    }

    summary = _summarize_engineering_loop(weekly, {})

    assert summary["matters_now"][0]["title"] == "wazion"
    assert summary["drifting"][0]["title"] == "guard_check"
    assert summary["drifting"][1]["title"] == "heartbeat"
    assert summary["drifting"][2]["title"] == "release validation skipped"
    assert summary["improving"][0]["title"] == "Trust"
    assert any(item["title"] == "Engineering followups" for item in summary["improving"])


def test_protocol_explainability_snapshot_surfaces_runtime_state(isolated_db):
    task = db.create_protocol_task(
        "sid-1",
        "Ship explainability dashboard",
        task_type="edit",
        must_verify=True,
        must_change_log=True,
        opened_with_guard=True,
        opened_with_rules=True,
    )
    db.close_protocol_task(
        task["task_id"],
        outcome="done",
        evidence="Dashboard route and template validated",
        files_changed=["src/dashboard/app.py"],
    )
    db.create_protocol_debt(
        "sid-1",
        "missing_change_log",
        severity="warn",
        task_id=task["task_id"],
        evidence="Change log not written yet",
    )
    goal = db.create_workflow_goal(
        "sid-1",
        "Close explainability UI",
        next_action="Render protocol dashboard",
    )
    db.create_workflow_run(
        "sid-1",
        "Render protocol dashboard",
        goal_id=goal["goal_id"],
        workflow_kind="dashboard",
        steps=[{"step_key": "render", "title": "Render dashboard page"}],
    )
    db.create_learning(
        "code",
        "Read conditioned learnings first",
        "Always review file-conditioned learnings before touching guarded files.",
        applies_to="/repo/guarded.py",
    )
    conn = db.get_db()
    conn.execute(
        """INSERT INTO guard_checks (
               session_id, files, area, learnings_returned, blocking_rules_returned, created_at
           ) VALUES (?, ?, ?, ?, ?, datetime('now'))""",
        ("sid-1", "/repo/guarded.py", "nexo", 2, 1),
    )
    conn.commit()

    snapshot = _protocol_explainability_snapshot(limit=10)

    assert snapshot["debt_summary"]["open_total"] == 1
    assert snapshot["workflow_summary"]["open_runs"] == 1
    assert snapshot["goal_summary"]["active"] == 1
    assert snapshot["guard_summary"]["blocking_hits"] == 1
    assert snapshot["conditioned_learnings"][0]["applies_to"] == "/repo/guarded.py"
    assert snapshot["recent_tasks"][0]["has_evidence"] is True


def test_protocol_routes_render_and_return_snapshot():
    client = TestClient(app)

    page = client.get("/protocol")
    assert page.status_code == 200
    assert "Protocol Explainability" in page.text

    api = client.get("/api/protocol")
    assert api.status_code == 200
    payload = api.json()
    assert "protocol_summary" in payload
    assert "recent_tasks" in payload


def test_dashboard_reminder_delete_is_soft_and_history_visible(isolated_db):
    client = TestClient(app)

    created = client.post(
        "/api/reminders",
        json={"description": "Dashboard soft delete", "date": "2026-04-09", "category": "tasks"},
    )
    assert created.status_code == 200
    rid = created.json()["reminder"]["id"]

    deleted = client.delete(f"/api/reminders/{rid}")
    assert deleted.status_code == 200

    listing = client.get("/api/reminders")
    assert listing.status_code == 200
    assert all(item["id"] != rid for item in listing.json()["reminders"])

    detail = client.get(f"/api/reminders/{rid}")
    assert detail.status_code == 200
    payload = detail.json()["reminder"]
    assert payload["status"] == "DELETED"
    assert any(event["event_type"] == "deleted" for event in payload["history"])


def test_dashboard_move_preserves_source_as_deleted(isolated_db):
    client = TestClient(app)

    created = client.post(
        "/api/reminders",
        json={"description": "Move me to followup", "date": "2026-04-10", "category": "tasks"},
    )
    rid = created.json()["reminder"]["id"]

    moved = client.post("/api/ops/move", json={"id": rid, "direction": "to_followup"})
    assert moved.status_code == 200
    fid = moved.json()["new_id"]

    old_row = db.get_reminder(rid, include_history=True)
    new_row = db.get_followup(fid, include_history=True)

    assert old_row["status"] == "DELETED"
    assert any(event["event_type"] == "deleted" for event in old_row["history"])
    assert any("dashboard move" in (event.get("note") or "") for event in new_row["history"])
