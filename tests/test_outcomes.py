"""Regression tests for Outcome Tracker v1 and linked decision/followup/task state."""

from __future__ import annotations

import importlib
import json
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


def _seed_pattern(
    db,
    *,
    selected_choice: str,
    status: str,
    count: int,
    area: str = "release",
    task_type: str = "execute",
    goal_profile_id: str = "release_safety",
):
    for idx in range(count):
        deadline = "2099-01-01T00:00:00" if status == "met" else "2000-01-01T00:00:00"
        outcome = db.create_outcome(
            action_type="manual_review",
            description=f"Seed {status} for {selected_choice} #{idx}",
            expected_result=f"{selected_choice} should succeed",
            metric_source="manual",
            target_value=1,
            target_operator="gte",
            deadline=deadline,
        )
        db.create_cortex_evaluation(
            goal="Seed structured outcome pattern",
            task_type=task_type,
            area=area,
            impact_level="high",
            alternatives=[{"name": selected_choice, "description": selected_choice}],
            scores=[{"name": selected_choice, "total_score": 1.0}],
            recommended_choice=selected_choice,
            recommended_reasoning="seed",
            linked_outcome_id=outcome["id"],
            goal_profile_id=goal_profile_id,
            goal_profile_labels=[],
            goal_profile_weights={},
            selected_choice=selected_choice,
            selection_reason="seed",
            selection_source="recommended",
        )
        db.evaluate_outcome(outcome["id"], actual_value=1.0 if status == "met" else 0.0)


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


def test_outcome_pattern_candidates_surface_positive_and_negative_groups(outcome_env):
    db, _, _ = outcome_env
    db.init_db()

    import plugins.outcomes as outcomes_plugin

    importlib.reload(outcomes_plugin)

    _seed_pattern(db, selected_choice="staged_validation", status="met", count=3)
    _seed_pattern(db, selected_choice="direct_push", status="missed", count=3)

    payload = json.loads(outcomes_plugin.handle_outcome_pattern_candidates(min_resolved=3, limit=10))

    assert payload["ok"] is True
    candidates = payload["candidates"]
    positive = next(item for item in candidates if item["selected_choice"] == "staged_validation")
    negative = next(item for item in candidates if item["selected_choice"] == "direct_push")

    assert positive["candidate_type"] == "reinforce_strategy"
    assert positive["resolved_outcomes"] == 3
    assert positive["success_rate"] == 1.0
    assert negative["candidate_type"] == "avoid_strategy"
    assert negative["resolved_outcomes"] == 3
    assert negative["success_rate"] == 0.0
    assert len(positive["evidence"]) >= 1


def test_outcome_pattern_capture_creates_learning_once(outcome_env):
    db, _, _ = outcome_env
    db.init_db()

    import plugins.outcomes as outcomes_plugin

    importlib.reload(outcomes_plugin)

    _seed_pattern(db, selected_choice="staged_validation", status="met", count=3)
    payload = json.loads(outcomes_plugin.handle_outcome_pattern_candidates(min_resolved=3, limit=10))
    candidate = next(item for item in payload["candidates"] if item["selected_choice"] == "staged_validation")

    first = json.loads(outcomes_plugin.handle_outcome_pattern_capture(candidate["pattern_key"]))
    second = json.loads(outcomes_plugin.handle_outcome_pattern_capture(candidate["pattern_key"]))
    row = db.get_db().execute(
        "SELECT COUNT(*) AS total FROM learnings WHERE applies_to = ?",
        (f"outcome-pattern:{candidate['pattern_key']}",),
    ).fetchone()

    assert first["ok"] is True
    assert first["created"] is True
    assert first["learning"]["category"] == "outcomes"
    assert "Prefer staged_validation" in first["learning"]["title"]
    assert second["ok"] is True
    assert second["created"] is False
    assert second["learning"]["id"] == first["learning"]["id"]
    assert row["total"] == 1


def test_outcome_pattern_candidates_skip_contradictory_groups(outcome_env):
    db, _, _ = outcome_env
    db.init_db()

    import plugins.outcomes as outcomes_plugin

    importlib.reload(outcomes_plugin)

    _seed_pattern(db, selected_choice="mixed_strategy", status="met", count=2)
    _seed_pattern(db, selected_choice="mixed_strategy", status="missed", count=2)

    payload = json.loads(outcomes_plugin.handle_outcome_pattern_candidates(min_resolved=4, limit=10))

    assert payload["ok"] is True
    assert not any(item["selected_choice"] == "mixed_strategy" for item in payload["candidates"])
