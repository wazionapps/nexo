"""Regression tests for Impact Scoring v1 over real followup queues."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_SRC = Path(__file__).resolve().parents[1] / "src"
REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_impact_stack(monkeypatch, home: Path):
    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    monkeypatch.setenv("NEXO_TEST_DB", str(home / "data" / "nexo.db"))

    import db._core as db_core
    import db._fts as db_fts
    import db._schema as db_schema
    import db._reminders as db_reminders
    import db
    import tools_reminders as reminders_reader

    db.close_db()
    importlib.reload(db_core)
    importlib.reload(db_fts)
    importlib.reload(db_schema)
    importlib.reload(db_reminders)
    importlib.reload(db)
    importlib.reload(reminders_reader)
    return db, reminders_reader


@pytest.fixture
def impact_env(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    (home / "data").mkdir(parents=True, exist_ok=True)
    return _reload_impact_stack(monkeypatch, home)


def _load_script(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_score_followup_prefers_high_priority_due_work(impact_env):
    db, _ = impact_env
    db.init_db()

    db.create_followup(
        id="NF-HIGH",
        description="Cerrar gate de release principal.",
        date="2000-01-01",
        verification="Todos los checks verdes.",
        reasoning="Bloquea el release.",
        priority="high",
    )
    db.create_followup(
        id="NF-LOW",
        description="Revisar una idea secundaria sin fecha urgente.",
        date="2099-12-31",
        priority="low",
    )

    high = db.score_followup("NF-HIGH")
    low = db.score_followup("NF-LOW")

    assert high["impact_score"] > low["impact_score"]
    assert high["impact_factors"]["business_impact"] == 8.0
    assert high["impact_factors"]["temporal_urgency"] == 10.0


def test_get_followups_orders_by_impact_score_then_falls_back_to_date(impact_env):
    db, reminders_reader = impact_env
    db.init_db()

    db.create_followup(
        id="NF-1",
        description="Item de alto impacto.",
        date="2099-12-31",
        verification="Tiene criterio claro.",
        reasoning="Importa mucho.",
        priority="critical",
    )
    db.create_followup(
        id="NF-2",
        description="Item de menor impacto pero antes en fecha.",
        date="2000-01-01",
        priority="low",
    )
    db.score_active_followups()

    ordered = db.get_followups("all")
    assert ordered[0]["id"] == "NF-1"

    conn = db.get_db()
    conn.execute("UPDATE followups SET impact_score = 0, impact_factors = '{}', last_scored_at = NULL")
    conn.commit()
    fallback = db.get_followups("all")
    assert fallback[0]["id"] == "NF-2"

    rendered = reminders_reader.handle_reminders("followups")
    assert "impact" not in rendered or "NF-2" in rendered


def test_score_active_followups_persists_factors_json(impact_env):
    db, _ = impact_env
    db.init_db()

    db.create_followup(
        id="NF-VERIFY",
        description="Comprobar integración importante.",
        date="2000-01-01",
        verification="pytest green",
        reasoning="Afecta a un flujo principal.",
        priority="medium",
    )

    scored = db.score_active_followups()
    row = db.get_followup("NF-VERIFY")

    assert scored
    assert float(row["impact_score"]) > 0
    assert "business_impact" in row["impact_factors"]
    assert row["last_scored_at"] is not None


def test_impact_scorer_writes_reasoned_summary(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    (home / "data").mkdir(parents=True, exist_ok=True)
    db, _ = _reload_impact_stack(monkeypatch, home)
    db.init_db()

    db.create_followup(
        id="NF-SUMMARY",
        description="Cerrar trabajo crítico de revenue.",
        date="2000-01-01",
        verification="pytest green",
        reasoning="Bloquea ingresos",
        priority="critical",
    )
    db.create_followup(
        id="NF-LATER",
        description="Revisar idea secundaria.",
        date="2099-12-31",
        priority="low",
    )

    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    monkeypatch.setenv("NEXO_TEST_DB", str(home / "data" / "nexo.db"))
    script = _load_script("impact_scorer_test", REPO_ROOT / "src" / "scripts" / "nexo-impact-scorer.py")

    assert script.main() == 0

    summary = json.loads((home / "runtime" / "coordination" / "impact-scorer-summary.json").read_text(encoding="utf-8"))
    assert summary["scored_count"] >= 2
    assert summary["top_followups"][0]["id"] == "NF-SUMMARY"
    assert "reasoning" in summary["top_followups"][0]["impact_factors"]
    assert summary["top_changes"][0]["id"] == "NF-SUMMARY"
