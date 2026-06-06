import json
import sqlite3
import time

import db
from closure_plane import (
    closure_close_item,
    closure_item_get,
    closure_next,
    closure_status,
    closure_verify_item,
    refresh_closure_items,
)
from db._schema import _m78_operational_closure_plane


def test_closure_migration_is_idempotent():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    _m78_operational_closure_plane(conn)
    _m78_operational_closure_plane(conn)

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {
        "closure_items",
        "closure_item_sources",
        "closure_item_events",
        "closure_daily_snapshots",
    } <= tables


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
