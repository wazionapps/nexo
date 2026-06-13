from __future__ import annotations


def test_durable_write_queue_commits_heartbeat_update(isolated_db):
    import db
    import mcp_write_queue

    sid = "nexo-4100-5100"
    accepted = mcp_write_queue.enqueue_write(
        "heartbeat_update",
        {"sid": sid, "task": "queued heartbeat", "heartbeat_ts": 1779000000.0},
        priority="high",
    )

    assert accepted["accepted"] is True
    assert accepted["status"] == "queued"

    drained = mcp_write_queue.drain_write_queue(limit=10)
    assert drained["committed"] == 1

    row = db.get_db().execute(
        "SELECT task, last_heartbeat_ts FROM sessions WHERE sid = ?",
        (sid,),
    ).fetchone()
    assert row["task"] == "queued heartbeat"
    assert float(row["last_heartbeat_ts"]) == 1779000000.0

    status = mcp_write_queue.write_status(accepted["writeId"])
    assert status["status"] == "committed"
    assert status["ok"] is True


def test_durable_write_queue_dead_letters_unknown_kind(isolated_db, monkeypatch):
    import mcp_write_queue

    monkeypatch.setattr(mcp_write_queue, "MAX_ATTEMPTS", 1)
    accepted = mcp_write_queue.enqueue_write("unknown_kind", {"x": 1}, priority="low")

    drained = mcp_write_queue.drain_write_queue(limit=10)
    assert drained["dead_letter"] == 1

    status = mcp_write_queue.write_status(accepted["writeId"])
    assert status["status"] == "dead_letter"
    assert "unsupported write kind" in status["last_error"]


def test_durable_write_queue_commits_followup_create(isolated_db):
    import db
    import mcp_write_queue

    accepted = mcp_write_queue.enqueue_write(
        "followup_create",
        {
            "id": "NF-QUEUE-FOLLOWUP",
            "description": "Verificar cola durable de followups",
            "date": "2026-06-12",
            "priority": "high",
            "owner": "agent",
            "internal": "1",
            "force": "true",
        },
        priority="high",
    )

    drained = mcp_write_queue.drain_write_queue(limit=10)
    assert drained["committed"] == 1
    row = db.get_db().execute("SELECT id, status FROM followups WHERE id = 'NF-QUEUE-FOLLOWUP'").fetchone()
    assert row["status"] == "PENDING"


def test_durable_write_queue_commits_change_log(isolated_db):
    import db
    import mcp_write_queue

    accepted = mcp_write_queue.enqueue_write(
        "change_log",
        {
            "session_id": "nexo-queue-change",
            "files": "src/server.py",
            "what_changed": "Queued change log",
            "why": "MCP fallback test",
            "verify": "pytest",
        },
        priority="high",
    )

    assert accepted["accepted"] is True
    drained = mcp_write_queue.drain_write_queue(limit=10)
    assert drained["committed"] == 1
    row = db.get_db().execute("SELECT files, what_changed FROM change_log WHERE session_id = 'nexo-queue-change'").fetchone()
    assert row["files"] == "src/server.py"


def test_durable_write_queue_commits_learning_add(isolated_db):
    import db
    import mcp_write_queue

    accepted = mcp_write_queue.enqueue_write(
        "learning_add",
        {
            "category": "protocol",
            "title": "Queued learning fallback",
            "content": "La cola durable puede reconciliar learning_add cuando MCP vuelve.",
            "reasoning": "Regression test",
            "priority": "high",
        },
        priority="high",
    )

    assert accepted["accepted"] is True
    drained = mcp_write_queue.drain_write_queue(limit=10)
    assert drained["committed"] == 1
    row = db.get_db().execute("SELECT title FROM learnings WHERE title = 'Queued learning fallback'").fetchone()
    assert row["title"] == "Queued learning fallback"
