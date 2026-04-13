"""Regression tests for decision review dates, outcomes, and auto-reconciliation."""

from __future__ import annotations

import importlib
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO_SRC = Path(__file__).resolve().parents[1] / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_decision_stack(monkeypatch, home: Path):
    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    monkeypatch.setenv("NEXO_TEST_DB", str(home / "data" / "nexo.db"))

    import importlib.util
    import db._core as db_core
    import db._fts as db_fts
    import db._schema as db_schema
    import db._episodic as db_episodic
    import db._reminders as db_reminders
    import db
    import plugins.episodic_memory as episodic_memory
    import tools_reminders_crud as reminders_tools
    db.close_db()
    importlib.reload(db_core)
    importlib.reload(db_fts)
    importlib.reload(db_schema)
    importlib.reload(db_episodic)
    importlib.reload(db_reminders)
    importlib.reload(db)
    importlib.reload(episodic_memory)
    importlib.reload(reminders_tools)
    housekeep_path = REPO_SRC / "scripts" / "nexo-learning-housekeep.py"
    spec = importlib.util.spec_from_file_location("nexo_learning_housekeep", housekeep_path)
    housekeep = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(housekeep)
    return db, episodic_memory, reminders_tools, housekeep


@pytest.fixture
def decision_env(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    (home / "data").mkdir(parents=True, exist_ok=True)
    return _reload_decision_stack(monkeypatch, home)


def test_decision_log_sets_pending_review_with_due_date(decision_env):
    db, episodic_memory, _, _ = decision_env
    db.init_db()

    message = episodic_memory.handle_decision_log(
        domain="nexo",
        decision="Use a single public v3.0 release instead of many micro releases.",
        based_on="Need tighter release discipline and one clear public cut.",
        confidence="high",
        review_days=5,
    )

    row = db.get_db().execute(
        "SELECT status, review_due_at FROM decisions ORDER BY id DESC LIMIT 1"
    ).fetchone()

    assert "review_due=" in message
    assert row["status"] == "pending_review"
    assert row["review_due_at"]


def test_memory_review_queue_and_followup_completion_close_decision(decision_env):
    db, episodic_memory, reminders_tools, _ = decision_env
    db.init_db()

    episodic_memory.handle_decision_log(
        domain="nexo",
        decision="Keep the public release blocked until v3.0 is integrated.",
        based_on="Roadmap still has open runtime work.",
        confidence="medium",
        context_ref="NF-V3-RELEASE",
        review_days=1,
    )
    conn = db.get_db()
    conn.execute(
        "UPDATE decisions SET review_due_at = datetime('now', '-1 day') WHERE context_ref = ?",
        ("NF-V3-RELEASE",),
    )
    conn.commit()

    queue = episodic_memory.handle_memory_review_queue(days=0)
    result = reminders_tools.handle_followup_create(
        id="NF-V3-RELEASE",
        description="Launch public v3.0 release when all gates are green.",
        verification="Doctor green and release checklist complete.",
    )
    completed = reminders_tools.handle_followup_complete(
        id="NF-V3-RELEASE",
        result="Release kept blocked because runtime gates are still open.",
    )
    row = conn.execute(
        "SELECT outcome, status, review_due_at, last_reviewed_at FROM decisions WHERE context_ref = ?",
        ("NF-V3-RELEASE",),
    ).fetchone()

    assert "Keep the public release blocked" in queue
    assert "Followup created." in result
    assert "Decision(s)" in completed
    assert "Release kept blocked" in row["outcome"]
    assert row["status"] == "reviewed"
    assert row["review_due_at"] is None
    assert row["last_reviewed_at"] is not None


def test_overdue_decision_auto_reconciles_from_change_log(decision_env):
    db, episodic_memory, _, housekeep = decision_env
    db.init_db()

    episodic_memory.handle_decision_log(
        domain="nexo",
        decision="Adopt a durable workflow runtime for long-running v3 work.",
        based_on="Current workflow state is too fragile across sessions.",
        confidence="high",
        review_days=1,
    )
    conn = db.get_db()
    conn.execute(
        "UPDATE decisions SET created_at = ?, review_due_at = ?, status = 'pending_review' WHERE id = 1",
        (
            (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds"),
            (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds"),
        ),
    )
    conn.execute(
        "INSERT INTO change_log (session_id, files, what_changed, why, triggered_by, affects, risks, verify, commit_ref) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "nexo-1",
            "src/plugins/workflow.py",
            "Adopt durable workflow runtime with checkpoints and resume support",
            "Needed to preserve execution state across long v3 work",
            "decision review",
            "runtime",
            "",
            "pytest tests/test_workflow.py",
            "abc1234",
        ),
    )
    conn.commit()

    processed = housekeep.process_overdue_reviews(conn)
    row = conn.execute("SELECT status, outcome FROM decisions WHERE id = 1").fetchone()

    assert processed >= 1
    assert row["status"] == "resolved"
    assert "[auto-reconciled from change_log]" in row["outcome"]
