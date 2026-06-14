"""Ola 1 — auto_close reaps abandoned durable workflow_runs / workflow_goals.

Previously only protocol_tasks were reaped; a session that opened a durable
workflow_run/goal and never closed it left a zombie 'running'/'active' row
forever, polluting the resume surface.
"""


def test_reaper_cancels_abandoned_runs_and_goals():
    from db import get_db
    import auto_close_sessions as acs

    conn = get_db()
    sid = "nexo-9100-1100"
    conn.execute(
        "INSERT INTO workflow_runs (run_id, session_id, goal, status, opened_at, updated_at) "
        "VALUES (?,?,?,?,datetime('now'),datetime('now'))",
        ("WF-reap-1", sid, "abandoned goal", "running"),
    )
    conn.execute(
        "INSERT INTO workflow_goals (goal_id, session_id, title, status, opened_at, updated_at) "
        "VALUES (?,?,?,?,datetime('now'),datetime('now'))",
        ("WG-reap-1", sid, "abandoned goal", "active"),
    )
    conn.commit()

    res = acs.auto_close_abandoned_workflow_runs(conn, sid)
    conn.commit()

    assert res == {"runs": 1, "goals": 1}
    assert conn.execute("SELECT status FROM workflow_runs WHERE run_id='WF-reap-1'").fetchone()[0] == "cancelled"
    assert conn.execute("SELECT status FROM workflow_goals WHERE goal_id='WG-reap-1'").fetchone()[0] == "abandoned"
    assert conn.execute("SELECT closed_at FROM workflow_runs WHERE run_id='WF-reap-1'").fetchone()[0] is not None


def test_reaper_leaves_terminal_and_other_session_rows_untouched():
    from db import get_db
    import auto_close_sessions as acs

    conn = get_db()
    sid = "nexo-9101-1101"
    other = "nexo-9102-1102"
    conn.execute(
        "INSERT INTO workflow_runs (run_id, session_id, goal, status, opened_at, updated_at) "
        "VALUES (?,?,?,?,datetime('now'),datetime('now'))",
        ("WF-done", sid, "done", "completed"),
    )
    conn.execute(
        "INSERT INTO workflow_runs (run_id, session_id, goal, status, opened_at, updated_at) "
        "VALUES (?,?,?,?,datetime('now'),datetime('now'))",
        ("WF-other", other, "other session", "running"),
    )
    conn.commit()

    res = acs.auto_close_abandoned_workflow_runs(conn, sid)
    conn.commit()

    assert res == {"runs": 0, "goals": 0}
    assert conn.execute("SELECT status FROM workflow_runs WHERE run_id='WF-done'").fetchone()[0] == "completed"
    assert conn.execute("SELECT status FROM workflow_runs WHERE run_id='WF-other'").fetchone()[0] == "running"
