"""Tests for evolution objective normalization and managed execution mode."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"
EVOLUTION_RUN = REPO_SRC / "scripts" / "nexo-evolution-run.py"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _load_runner_module(monkeypatch, nexo_home: Path):
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))

    import evolution_cycle

    importlib.reload(evolution_cycle)
    sys.modules.pop("nexo_evolution_run_test", None)
    spec = importlib.util.spec_from_file_location("nexo_evolution_run_test", EVOLUTION_RUN)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return evolution_cycle, module


@pytest.fixture
def evolution_env(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    for dirname in [
        "brain",
        "data",
        "logs",
        "scripts",
        "plugins",
        "coordination",
        "snapshots",
        "sandbox" / Path("workspace"),
    ]:
        (home / dirname).mkdir(parents=True, exist_ok=True)

    restore_script = home / "scripts" / "nexo-snapshot-restore.sh"
    restore_script.write_text("#!/usr/bin/env bash\nexit 0\n")
    restore_script.chmod(0o755)

    db_path = home / "data" / "nexo.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE learnings (category TEXT, title TEXT, content TEXT, created_at REAL)")
    conn.execute(
        "CREATE TABLE decisions (domain TEXT, decision TEXT, alternatives TEXT, based_on TEXT, confidence REAL, outcome TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE change_log (files TEXT, what_changed TEXT, why TEXT, affects TEXT, risks TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE session_diary (summary TEXT, decisions TEXT, pending TEXT, mental_state TEXT, domain TEXT, user_signals TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE evolution_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT DEFAULT (datetime('now')), "
        "cycle_number INTEGER NOT NULL, dimension TEXT NOT NULL, proposal TEXT NOT NULL, "
        "classification TEXT NOT NULL DEFAULT 'auto', status TEXT DEFAULT 'pending', "
        "files_changed TEXT, snapshot_ref TEXT, test_result TEXT, impact INTEGER DEFAULT 0, reasoning TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE evolution_metrics ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, dimension TEXT NOT NULL, score INTEGER NOT NULL, "
        "measured_at TEXT DEFAULT (datetime('now')), evidence TEXT NOT NULL, delta INTEGER DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE followups ("
        "id TEXT PRIMARY KEY, date TEXT, description TEXT NOT NULL, verification TEXT DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'PENDING', recurrence TEXT DEFAULT NULL, "
        "created_at REAL NOT NULL, updated_at REAL NOT NULL, reasoning TEXT, priority TEXT DEFAULT 'medium')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    return home


class TestEvolutionObjective:
    def test_load_objective_normalizes_legacy_schema(self, evolution_env, monkeypatch):
        legacy = {
            "objective": "Improve operational excellence and reduce repeated errors",
            "focus_areas": ["error_prevention"],
            "evolution_enabled": True,
            "review_mode": "manual",
            "dimension_targets": {
                "episodic_memory": 80,
                "agi_readiness": 50,
            },
            "cycles_completed": 3,
            "last_cycle": "2026-04-01",
        }
        objective_file = evolution_env / "brain" / "evolution-objective.json"
        objective_file.write_text(json.dumps(legacy, indent=2))

        evolution_cycle, _ = _load_runner_module(monkeypatch, evolution_env)
        objective = evolution_cycle.load_objective()

        assert objective["evolution_mode"] == "review"
        assert objective["total_evolutions"] == 3
        assert objective["last_evolution"] == "2026-04-01"
        assert objective["dimensions"]["episodic_memory"]["target"] == 80
        assert objective["dimensions"]["agi"]["target"] == 50
        assert "review_mode" not in objective
        assert "cycles_completed" not in objective


class TestEvolutionModes:
    def test_managed_mode_allows_core_paths_but_auto_does_not(self, evolution_env, monkeypatch):
        _, runner = _load_runner_module(monkeypatch, evolution_env)
        core_target = REPO_SRC / "plugins" / "guard.py"

        assert runner.is_safe_path(str(core_target), mode="managed") is True
        assert runner.is_safe_path(str(core_target), mode="auto") is False

    def test_managed_mode_unlocks_tools_modules_but_keeps_kernel_guarded(self, evolution_env, monkeypatch):
        _, runner = _load_runner_module(monkeypatch, evolution_env)

        assert runner.is_safe_path(str(REPO_SRC / "tools_learnings.py"), mode="managed") is True
        assert runner.is_safe_path(str(REPO_SRC / "tools_learnings.py"), mode="review") is False
        assert runner.is_safe_path(str(REPO_SRC / "tools_learnings.py"), mode="auto") is False
        assert runner.is_safe_path(str(REPO_SRC / "server.py"), mode="managed") is False


class TestManagedExecution:
    def test_failed_auto_proposal_rolls_back_and_creates_followup(self, evolution_env, monkeypatch):
        objective_file = evolution_env / "brain" / "evolution-objective.json"
        objective_file.write_text(json.dumps({
            "objective": "Improve operational excellence and reduce repeated errors",
            "focus_areas": ["error_prevention"],
            "evolution_enabled": True,
            "evolution_mode": "managed",
            "dimensions": {"autonomy": {"current": 0, "target": 80}},
            "total_evolutions": 0,
            "consecutive_failures": 0,
        }, indent=2))

        target_file = evolution_env / "scripts" / "repair.py"
        target_file.write_text("print('original')\n")

        _, runner = _load_runner_module(monkeypatch, evolution_env)
        runner.verify_claude_cli = lambda: True
        runner.dry_run_restore_test = lambda: True
        runner.call_claude_cli = lambda prompt: json.dumps({
            "analysis": "Managed mode found a deterministic but invalid auto edit.",
            "patterns": [],
            "proposals": [
                {
                    "classification": "auto",
                    "dimension": "safety",
                    "action": "Patch repair.py",
                    "reasoning": "Test rollback path",
                    "scope": "local",
                    "changes": [
                        {
                            "file": str(target_file),
                            "operation": "replace",
                            "search": "missing-marker",
                            "content": "print('patched')\n",
                        }
                    ],
                }
            ],
        })

        runner.run()

        conn = sqlite3.connect(str(evolution_env / "data" / "nexo.db"))
        status, test_result = conn.execute(
            "SELECT status, test_result FROM evolution_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        followup = conn.execute(
            "SELECT id, description FROM followups ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        conn.close()

        assert status == "rolled_back"
        assert "ROLLBACK" in test_result
        assert followup[0].startswith("NF-EVO-L")
        assert "Patch repair.py" in followup[1]
        assert target_file.read_text() == "print('original')\n"
