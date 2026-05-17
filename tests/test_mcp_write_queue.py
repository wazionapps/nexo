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

