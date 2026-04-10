"""Regression tests for Outcome Tracker v1 and linked decision/followup/task state."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_SRC = Path(__file__).resolve().parents[1] / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_outcome_stack(monkeypatch, home: Path):
    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    monkeypatch.setenv("NEXO_TEST_DB", str(home / "data" / "nexo.db"))

    import db._core as db_core
    import db._fts as db_fts
    import db._schema as db_schema
    import db._episodic as db_episodic
    import db._reminders as db_reminders
    import db._protocol as db_protocol
    import db._outcomes as db_outcomes
    import db
    import plugins.episodic_memory as episodic_memory
    import tools_reminders_crud as reminders_tools

    db.close_db()
    importlib.reload(db_core)
    importlib.reload(db_fts)
    importlib.reload(db_schema)
    importlib.reload(db_episodic)
    importlib.reload(db_reminders)
    importlib.reload(db_protocol)
    importlib.reload(db_outcomes)
    importlib.reload(db)
    importlib.reload(episodic_memory)
    importlib.reload(reminders_tools)
    return db, episodic_memory, reminders_tools


@pytest.fixture
def outcome_env(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    (home / "data").mkdir(parents=True, exist_ok=True)
    return _reload_outcome_stack(monkeypatch, home)


def test_followup_completion_marks_linked_outcome_met(outcome_env):
    db, _, reminders_tools = outcome_env
    db.init_db()

    created = db.create_outcome(
        action_type="followup",
        action_id="NF-OUTCOME-1",
        description="Cerrar un followup de release.",
        expected_result="El followup queda completado.",
        metric_source="followup_status",
    )
    reminders_tools.handle_followup_create(
        id="NF-OUTCOME-1",
        description="Completar el followup que cierra el release.",
        verification="Release gate closed.",
    )

    message = reminders_tools.handle_followup_complete(
        id="NF-OUTCOME-1",
        result="Release gate closed cleanly.",
    )
    row = db.get_outcome(created["id"])

    assert "Linked outcomes met: 1" in message
    assert row["status"] == "met"
    assert row["actual_value"] == 1.0
    assert "Release gate closed cleanly." in row["actual_value_text"]


def test_decision_outcome_marks_linked_outcome_met(outcome_env):
    db, episodic_memory, _ = outcome_env
    db.init_db()

    episodic_memory.handle_decision_log(
        domain="nexo",
        decision="Cerrar v4.5 solo con release contract validado.",
        based_on="Hace falta evidencia antes de publicar.",
        confidence="high",
    )
    decision_id = db.get_db().execute(
        "SELECT id FROM decisions ORDER BY id DESC LIMIT 1"
    ).fetchone()["id"]
    created = db.create_outcome(
        action_type="decision",
        action_id=str(decision_id),
        description="Registrar outcome de la decision de release.",
        expected_result="La decision tiene outcome registrado.",
        metric_source="decision_outcome",
    )

    message = episodic_memory.handle_decision_outcome(
        decision_id,
        "Se mantuvo el bloqueo hasta validar todos los gates.",
    )
    row = db.get_outcome(created["id"])

    assert "Linked outcomes met: 1." in message
    assert row["status"] == "met"
    assert row["actual_value"] == 1.0
    assert "Se mantuvo el bloqueo" in row["actual_value_text"]


def test_protocol_task_status_can_be_checked_and_marked_met(outcome_env):
    db, _, _ = outcome_env
    db.init_db()

    task = db.create_protocol_task(
        session_id="nexo-test",
        goal="Cerrar un task de protocolo con outcome trazable.",
        task_type="execute",
    )
    outcome = db.create_outcome(
        action_type="protocol_task",
        action_id=task["task_id"],
        description="Verificar que el protocolo cierra el task.",
        expected_result="El protocol task termina como done.",
        metric_source="protocol_task_status",
    )

    db.close_protocol_task(
        task["task_id"],
        outcome="done",
        evidence="pytest green",
        outcome_notes="Task closed cleanly.",
    )
    checked = db.evaluate_outcome(outcome["id"])

    assert checked["status"] == "met"
    assert checked["actual_value"] == 1.0
    assert "Task closed cleanly." in checked["actual_value_text"]


def test_sqlite_outcome_below_target_after_deadline_becomes_missed_and_creates_learning(outcome_env):
    db, _, _ = outcome_env
    db.init_db()

    outcome = db.create_outcome(
        action_type="custom",
        description="Comprobar que el KPI sube.",
        expected_result="El KPI debe ser >= 10.",
        metric_source="nexo_sqlite",
        metric_query="SELECT 3",
        target_value=10,
        target_operator="gte",
        deadline="2000-01-01T00:00:00",
    )

    checked = db.evaluate_outcome(outcome["id"])
    learning_row = db.get_db().execute(
        "SELECT id, category FROM learnings WHERE id = ?",
        (checked["learning_id"],),
    ).fetchone()

    assert checked["status"] == "missed"
    assert checked["actual_value"] == 3.0
    assert checked["learning_id"] is not None
    assert learning_row["category"] == "outcomes"
