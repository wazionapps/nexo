import json
import sqlite3
import time

import db
import closure_plane
from closure_plane import (
    closure_close_item,
    closure_item_get,
    closure_link_item,
    closure_next,
    closure_set_capability_readiness,
    closure_snapshot,
    closure_status,
    closure_triage_item,
    closure_verify_item,
    refresh_closure_items,
)
from db._schema import _m78_operational_closure_plane, _m79_operational_closure_links_readiness, run_migrations


def test_closure_migration_is_idempotent():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    _m78_operational_closure_plane(conn)
    _m79_operational_closure_links_readiness(conn)
    _m79_operational_closure_links_readiness(conn)

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {
        "closure_items",
        "closure_item_sources",
        "closure_item_events",
        "closure_daily_snapshots",
        "closure_item_links",
        "closure_capability_readiness",
    } <= tables


def test_closure_m79_upgrades_existing_m78_install():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _m78_operational_closure_plane(conn)
    conn.execute(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    for version in range(1, 79):
        conn.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
            (version, f"already_applied_{version}"),
        )
    conn.commit()

    run_migrations(conn)

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"closure_item_links", "closure_capability_readiness"} <= tables
    assert conn.execute("SELECT version FROM schema_migrations WHERE version = 79").fetchone()


def test_closure_plane_backfills_from_all_mvp_adapters(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo-home"))
    conn = db.get_db()
    now = time.time()

    conn.execute(
        """
        INSERT INTO protocol_tasks (
            task_id, session_id, goal, task_type, status, opened_at, verification_step
        ) VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
        """,
        ("PT-test", "SID-test", "Ship release", "edit", "open", "pytest evidence"),
    )
    conn.execute(
        """
        INSERT INTO followups (id, date, description, verification, status, created_at, updated_at)
        VALUES (?, date('now'), ?, ?, ?, ?, ?)
        """,
        ("FUP-test", "Check release smoke", "smoke evidence", "PENDING", now, now),
    )
    conn.execute(
        """
        INSERT INTO protocol_debt (session_id, task_id, debt_type, severity, status, evidence)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("SID-test", "PT-test", "missing_verification", "warn", "open", "needs evidence"),
    )
    conn.execute(
        """
        INSERT INTO outcomes (
            action_type, action_id, session_id, description, expected_result, status, deadline
        ) VALUES (?, ?, ?, ?, ?, ?, date('now'))
        """,
        ("release", "REL-test", "SID-test", "Release outcome", "tests pass", "pending"),
    )
    queue_dir = tmp_path / "nexo-home" / "runtime" / "operations" / "mcp-write-queue" / "failed"
    queue_dir.mkdir(parents=True)
    (queue_dir / "1-write-test.json").write_text(json.dumps({
        "writeId": "write-test",
        "kind": "learning_add",
        "status": "failed",
        "attempts": 2,
        "created_at": now,
        "last_error": "boom",
    }))

    result = refresh_closure_items(conn)
    status = closure_status(conn, refresh=False, limit=10)
    items = closure_next(conn, limit=10, include_waiting=True)
    readiness = closure_set_capability_readiness(
        "desktop_control",
        status="needs_user_permission",
        reason="pytest permission",
        evidence="unit",
        conn=conn,
    )
    status_with_readiness = closure_status(conn, refresh=False, limit=10)

    assert result["ok"] is True
    assert result["adapters"] == {
        "protocol_tasks": 1,
        "followups": 1,
        "protocol_debt": 1,
        "outcomes": 1,
        "mcp_write_queue": 1,
    }
    assert status["open_total"] >= 5
    assert len(items) >= 5
    assert {item["source_primary"] for item in items} >= {
        "protocol_tasks",
        "followups",
        "protocol_debt",
        "outcomes",
        "mcp_write_queue",
    }
    assert readiness["ok"] is True
    assert status_with_readiness["capability_readiness"]["needs_user_permission"] == 1


def test_closure_verify_then_close_requires_evidence():
    conn = db.get_db()
    conn.execute(
        """
        INSERT INTO protocol_tasks (
            task_id, session_id, goal, task_type, status, opened_at, verification_step
        ) VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
        """,
        ("PT-close", "SID-test", "Close with evidence", "edit", "open", "test output"),
    )
    refresh_closure_items(conn)
    item = closure_next(conn, limit=1, include_waiting=True)[0]

    rejected_close = closure_close_item(item["id"], conn=conn)
    verified = closure_verify_item(item["id"], "pytest passed", conn)
    closed = closure_close_item(item["id"], conn=conn)
    fetched = closure_item_get(item["id"], conn)

    assert rejected_close["ok"] is False
    assert verified == {"ok": True, "id": item["id"], "state": "verified"}
    assert closed["ok"] is True
    assert closed["state"] == "closed"
    assert fetched["state"] == "closed"
    assert fetched["events"]


def test_closure_link_triage_and_snapshot_are_idempotent():
    conn = db.get_db()
    conn.execute(
        """
        INSERT INTO protocol_tasks (
            task_id, session_id, goal, task_type, status, opened_at, verification_step
        ) VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
        """,
        ("PT-link", "SID-test", "Link closure item", "edit", "open", "test output"),
    )
    refresh_closure_items(conn)
    item = closure_next(conn, limit=1, include_waiting=True)[0]

    linked = closure_link_item(
        item["id"],
        link_type="workflow_run",
        link_id="WF-test",
        relation="implements",
        conn=conn,
    )
    linked_again = closure_link_item(
        item["id"],
        link_type="workflow_run",
        link_id="WF-test",
        relation="implements",
        conn=conn,
    )
    triaged = closure_triage_item(
        item["id"],
        state="waiting",
        blocker_reason="waiting for smoke",
        next_action="Run smoke matrix",
        evidence_required="smoke output",
        conn=conn,
    )
    fetched = closure_item_get(item["id"], conn)
    snapshot = closure_snapshot(conn, refresh=False, snapshot_date="2026-06-07")

    assert linked["ok"] is True
    assert linked_again["ok"] is True
    assert linked_again["id"] == linked["id"]
    assert triaged["ok"] is True
    assert triaged["state"] == "waiting"
    assert fetched["links"][0]["link_type"] == "workflow_run"
    assert fetched["blocker_reason"] == "waiting for smoke"
    assert snapshot["ok"] is True
    assert snapshot["snapshot"]["snapshot_date"] == "2026-06-07"


def test_closure_next_filters_state_area_and_risk_aliases():
    conn = db.get_db()
    now = time.time()
    conn.execute(
        """
        INSERT INTO protocol_tasks (
            task_id, session_id, goal, task_type, area, status, opened_at, verification_step
        ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?)
        """,
        ("PT-area", "SID-test", "Release area item", "edit", "release", "open", "test output"),
    )
    conn.execute(
        """
        INSERT INTO followups (id, date, description, verification, status, created_at, updated_at)
        VALUES (?, date('now'), ?, ?, ?, ?, ?)
        """,
        ("FUP-low-risk", "Low risk release followup", "smoke evidence", "PENDING", now, now),
    )
    refresh_closure_items(conn)
    item = closure_next(conn, limit=1, include_waiting=True, area="release")[0]

    triaged = closure_triage_item(item["id"], state="waiting_user", blocker_reason="operator", conn=conn)
    waiting = closure_next(conn, limit=5, state="waiting_user")
    ready = closure_next(conn, limit=5, state="ready")
    low_risk = closure_next(conn, limit=10, include_waiting=True, max_risk=0.12)

    assert triaged["ok"] is True
    assert triaged["state"] == "waiting"
    assert triaged["requested_state"] == "waiting_user"
    assert any(row["id"] == item["id"] for row in waiting)
    assert all(row["state"] == "open" for row in ready)
    assert low_risk
    assert all(float(row["risk_score"]) <= 0.12 for row in low_risk)


def test_closure_refresh_reports_adapter_errors(monkeypatch):
    conn = db.get_db()

    def fail_adapter(_conn, _limit):
        raise RuntimeError("adapter exploded")

    monkeypatch.setattr(closure_plane, "_protocol_task_candidates", fail_adapter)
    result = refresh_closure_items(conn)

    assert result["ok"] is True
    assert result["adapters"]["protocol_tasks"] == 0
    assert "protocol_tasks" in result["adapter_errors"]
    assert "adapter exploded" in result["adapter_errors"]["protocol_tasks"]
