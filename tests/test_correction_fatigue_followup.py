"""Tests for the correction fatigue followup surfacing — Fase 3 item 4.

The check_correction_fatigue helper itself already auto-decays affected
memories. This file pins the new follow-up surfacing in
nexo-cognitive-decay.py so the operator sees fatigued memories at the
next morning briefing instead of having to read the cron log.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "src" / "scripts" / "nexo-cognitive-decay.py"


def _load_module(monkeypatch, home: Path):
    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_ROOT / "src"))
    sys.modules.pop("nexo_cognitive_decay_test", None)
    spec = importlib.util.spec_from_file_location("nexo_cognitive_decay_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _seed_followups_table(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS followups (
            id TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            date TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            verification TEXT DEFAULT '',
            recurrence TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            reasoning TEXT,
            priority TEXT DEFAULT 'medium'
        )"""
    )
    conn.commit()
    conn.close()


@pytest.fixture
def fatigue_env(tmp_path, monkeypatch, isolated_db):
    home = tmp_path / "nexo"
    (home / "data").mkdir(parents=True, exist_ok=True)
    db_path = Path(isolated_db["nexo_db"])
    _seed_followups_table(db_path)
    return home, db_path


def test_no_fatigued_memories_returns_no_signal(fatigue_env, monkeypatch):
    home, _ = fatigue_env
    mod = _load_module(monkeypatch, home)
    result = mod._open_correction_fatigue_followup([])
    assert result == "no_signal"


def test_fatigued_memories_create_followup_with_high_priority(fatigue_env, monkeypatch):
    home, db_path = fatigue_env
    mod = _load_module(monkeypatch, home)

    fatigued = [
        {
            "memory_id": 101,
            "corrections_7d": 4,
            "types": "explicit,implicit,explicit,implicit",
            "content": "El servidor de Recambios BMW se llama mundiserver",
            "strength": 0.2,
            "source_type": "session",
            "domain": "ecommerce",
        },
        {
            "memory_id": 202,
            "corrections_7d": 3,
            "types": "explicit",
            "content": "WAzion soporta sesiones múltiples por número",
            "strength": 0.2,
            "source_type": "session",
            "domain": "wazion",
        },
    ]

    result = mod._open_correction_fatigue_followup(fatigued)
    assert result == "opened_or_refreshed"

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT id, status, priority, description, verification FROM followups WHERE id = ?",
        (mod.CORRECTION_FATIGUE_FOLLOWUP_ID,),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "NF-CORRECTION-FATIGUE"
    assert row[1] == "PENDING"
    assert row[2] == "high"
    assert "2 memory" in row[3] or "2 memories" in row[3]
    assert "LTM #101" in row[3]
    assert "LTM #202" in row[3]
    assert "under_review" in row[3]
    assert "cognitive.db" in row[4]


def test_idempotent_across_runs(fatigue_env, monkeypatch):
    home, db_path = fatigue_env
    mod = _load_module(monkeypatch, home)

    fatigued = [
        {
            "memory_id": 101,
            "corrections_7d": 3,
            "types": "explicit",
            "content": "Test memory",
            "strength": 0.2,
            "source_type": "session",
            "domain": "test",
        }
    ]

    mod._open_correction_fatigue_followup(fatigued)
    mod._open_correction_fatigue_followup(fatigued)

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT id FROM followups WHERE id = ?",
        (mod.CORRECTION_FATIGUE_FOLLOWUP_ID,),
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_truncates_long_lists_to_10_with_remainder(fatigue_env, monkeypatch):
    home, db_path = fatigue_env
    mod = _load_module(monkeypatch, home)

    fatigued = [
        {
            "memory_id": i,
            "corrections_7d": 3,
            "types": "explicit",
            "content": f"Memory #{i}",
            "strength": 0.2,
            "source_type": "session",
            "domain": "test",
        }
        for i in range(15)
    ]

    mod._open_correction_fatigue_followup(fatigued)

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT description FROM followups WHERE id = ?",
        (mod.CORRECTION_FATIGUE_FOLLOWUP_ID,),
    ).fetchone()
    conn.close()
    assert row is not None
    assert "and 5 more" in row[0]
