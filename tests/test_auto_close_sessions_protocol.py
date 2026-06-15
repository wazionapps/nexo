from __future__ import annotations

import importlib
import sys
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_auto_close_marks_open_protocol_tasks_partial(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo-home"
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    # Reload the db stack IN PLACE (never pop the 'db' package object): a
    # pop+reimport creates a NEW module object that orphans the collection-time
    # ``import db`` / ``import db._reminders`` of other test modules
    # (test_resolution_cache, test_semantic_similarity_hybrid), leaking stale
    # connections / monkeypatch targets into them. ``db/__init__`` reloads its
    # submodules in place, so reloading the package re-points DB_PATH coherently
    # while preserving module identity.
    sys.modules.pop("auto_close_sessions", None)

    import db
    importlib.reload(db)
    import auto_close_sessions
    importlib.reload(auto_close_sessions)

    db.init_db()
    conn = db.get_db()
    sid = "nexo-1777969000-12345"
    db.register_session(sid, "stale release task", session_client="codex")
    task = db.create_protocol_task(
        sid,
        "Publish release safely",
        task_type="edit",
        files=["/repo/src/example.py"],
    )

    closed = auto_close_sessions.auto_close_open_protocol_tasks(
        conn,
        sid,
        task="stale release task",
    )
    row = conn.execute(
        "SELECT status, close_evidence, outcome_notes FROM protocol_tasks WHERE task_id = ?",
        (task["task_id"],),
    ).fetchone()

    assert closed == [task["task_id"]]
    assert row["status"] == "partial"
    assert "stale" in row["close_evidence"]
    assert "without explicit task_close" in row["outcome_notes"]
