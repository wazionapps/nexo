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

    token = client.get(f"/api/reminders/{rid}").json()["reminder"]["read_token"]
    deleted = client.delete(f"/api/reminders/{rid}?read_token={token}")
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

    token = client.get(f"/api/reminders/{rid}").json()["reminder"]["read_token"]
    moved = client.post("/api/ops/move", json={"id": rid, "direction": "to_followup", "read_token": token})
    assert moved.status_code == 200
    fid = moved.json()["new_id"]

    old_row = db.get_reminder(rid, include_history=True)
    new_row = db.get_followup(fid, include_history=True)

    assert old_row["status"] == "DELETED"
    assert any(event["event_type"] == "deleted" for event in old_row["history"])
    assert any("dashboard move" in (event.get("note") or "") for event in new_row["history"])


def test_dashboard_status_filters_distinguish_all_deleted_and_history(isolated_db):
    client = TestClient(app)

    active = client.post(
        "/api/reminders",
        json={"description": "Active reminder", "date": "2026-04-09", "category": "tasks"},
    ).json()["reminder"]["id"]
    completed = client.post(
        "/api/reminders",
        json={"description": "Completed reminder", "date": "2026-04-10", "category": "tasks"},
    ).json()["reminder"]["id"]
    deleted = client.post(
        "/api/reminders",
        json={"description": "Deleted reminder", "date": "2026-04-11", "category": "tasks"},
    ).json()["reminder"]["id"]

    completed_token = client.get(f"/api/reminders/{completed}").json()["reminder"]["read_token"]
    deleted_token = client.get(f"/api/reminders/{deleted}").json()["reminder"]["read_token"]
    client.put(f"/api/reminders/{completed}", json={"status": "COMPLETED", "read_token": completed_token})
    client.delete(f"/api/reminders/{deleted}?read_token={deleted_token}")

    all_payload = client.get("/api/reminders?status=all").json()["reminders"]
    deleted_payload = client.get("/api/reminders?status=deleted").json()["reminders"]
    history_payload = client.get("/api/reminders?status=history").json()["reminders"]

    all_ids = {item["id"] for item in all_payload}
    deleted_ids = {item["id"] for item in deleted_payload}
    history_ids = {item["id"] for item in history_payload}

    assert active in all_ids
    assert completed in all_ids
    assert deleted not in all_ids
    assert deleted_ids == {deleted}
    assert {active, completed, deleted}.issubset(history_ids)


def test_operations_page_exposes_deleted_and_history_filters():
    client = TestClient(app)
    page = client.get("/ops")
    assert page.status_code == 200
    assert 'value="deleted"' in page.text
    assert 'value="history"' in page.text


def test_dashboard_recent_context_api_surfaces_hot_context(isolated_db):
    client = TestClient(app)

    db.capture_context_event(
        event_type="context_capture",
        title="Review DNS with Maria",
        summary="Talked about registrar ownership and next step.",
        body="Francisco said the domain is not his and Maria should handle it.",
        topic="dns maria registrar",
        context_key="topic:dns-maria-registrar",
        context_title="Review DNS with Maria",
        context_summary="Waiting Maria action on registrar side.",
        context_type="topic",
        state="waiting_third_party",
        owner="maria",
        actor="test",
        source_type="email",
        source_id="thread-1",
        session_id="nexo-1-1",
        ttl_hours=24,
    )

    res = client.get("/api/recent-context?query=dns maria")
    assert res.status_code == 200
    payload = res.json()
    assert payload["has_matches"] is True
    assert payload["counts"]["contexts"] >= 1
    assert payload["contexts"][0]["context_key"] == "topic:dns-maria-registrar"
    assert any(event["event_type"] == "context_capture" for event in payload["events"])


def test_dashboard_cortex_api_parses_goal_profile_trace(isolated_db):
    client = TestClient(app)

    from plugins.cortex import handle_cortex_decide

    handle_cortex_decide(
        goal="Ship the public release package",
        task_type="execute",
        impact_level="critical",
        area="release",
        alternatives=json.dumps([
            {"name": "staged_validation", "description": "Validate in staging with smoke tests and rollback ready."},
            {"name": "direct_growth_push", "description": "Deploy release directly to production and skip manual review."},
        ]),
        goal_profile_id="release_safety",
    )

    res = client.get("/api/cortex")
    assert res.status_code == 200
    payload = res.json()
    assert payload["evaluations"]
    latest = payload["evaluations"][0]
    assert latest["goal_profile_id"] == "release_safety"
    assert isinstance(latest["goal_profile_labels"], list)
    assert "preserve_trust" in latest["goal_profile_labels"]
    assert isinstance(latest["goal_profile_weights"], dict)
    assert latest["goal_profile_weights"]["risk"] > 0


def test_dashboard_reminder_update_requires_read_token(isolated_db):
    client = TestClient(app)

    rid = client.post(
        "/api/reminders",
        json={"description": "Need history first", "date": "2026-04-09", "category": "tasks"},
    ).json()["reminder"]["id"]

    denied = client.put(f"/api/reminders/{rid}", json={"status": "COMPLETED"})
    assert denied.status_code == 409
    assert "read_token" in denied.json()["error"].lower()

    token = client.get(f"/api/reminders/{rid}").json()["reminder"]["read_token"]
    allowed = client.put(f"/api/reminders/{rid}", json={"status": "COMPLETED", "read_token": token})
    assert allowed.status_code == 200
    assert allowed.json()["reminder"]["status"] == "COMPLETED"


def test_dashboard_followup_move_requires_read_token(isolated_db):
    client = TestClient(app)

    fid = client.post(
        "/api/followups",
        json={"description": "Move only after reading", "date": "2026-04-10", "verification": "done"},
    ).json()["followup"]["id"]

    denied = client.post("/api/ops/move", json={"id": fid, "direction": "to_reminder"})
    assert denied.status_code == 409
    assert "read_token" in denied.json()["error"].lower()

    token = client.get(f"/api/followups/{fid}").json()["followup"]["read_token"]
    allowed = client.post("/api/ops/move", json={"id": fid, "direction": "to_reminder", "read_token": token})
    assert allowed.status_code == 200
    new_id = allowed.json()["new_id"]
    moved = db.get_reminder(new_id, include_history=True)
    assert moved["description"] == "Move only after reading"
