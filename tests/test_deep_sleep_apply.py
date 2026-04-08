"""Tests for Deep Sleep applied summaries and project weighting."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

import importlib


REPO_ROOT = Path(__file__).resolve().parents[1]
APPLY_PATH = REPO_ROOT / "src" / "scripts" / "deep-sleep" / "apply_findings.py"


def _load_apply_module(monkeypatch, home: Path):
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NEXO_HOME", str(home / "nexo-home"))
    monkeypatch.setenv("NEXO_CODE", str(REPO_ROOT / "src"))
    monkeypatch.setenv("NEXO_DB", str(home / "nexo-home" / "data" / "nexo.db"))
    monkeypatch.setenv("NEXO_TEST_DB", str(home / "nexo-home" / "data" / "nexo.db"))
    sys.modules.pop("deep_sleep_apply_test", None)
    for name in ("db", "db._core", "db._schema", "db._reminders", "db._fts"):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location("deep_sleep_apply_test", APPLY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_write_periodic_summaries_creates_weekly_and_monthly_outputs(monkeypatch, tmp_path):
    apply_mod = _load_apply_module(monkeypatch, tmp_path)
    nexo_home = Path(os.environ["NEXO_HOME"])
    data_dir = nexo_home / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    deep_sleep_dir = nexo_home / "operations" / "deep-sleep"
    deep_sleep_dir.mkdir(parents=True, exist_ok=True)
    brain_dir = nexo_home / "brain"
    brain_dir.mkdir(parents=True, exist_ok=True)

    (brain_dir / "project-atlas.json").write_text(json.dumps({
        "_meta": {"purpose": "test"},
        "wazion": {"aliases": ["dashboard", "extension"]},
        "nexo": {"aliases": ["memory", "shared brain"]},
    }))

    conn = sqlite3.connect(str(data_dir / "nexo.db"))
    conn.execute(
        "CREATE TABLE session_diary (created_at TEXT, summary TEXT, self_critique TEXT, domain TEXT)"
    )
    conn.execute(
        "CREATE TABLE learnings (title TEXT, content TEXT, applies_to TEXT, priority TEXT, weight REAL, updated_at TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE followups (description TEXT, date TEXT, status TEXT, priority TEXT, created_at TEXT, updated_at TEXT, reasoning TEXT)"
    )
    conn.execute(
        "CREATE TABLE decisions (domain TEXT, outcome TEXT, status TEXT, reasoning TEXT, created_at TEXT, review_due_at TEXT)"
    )
    conn.execute(
        "INSERT INTO session_diary VALUES (?, ?, ?, ?)",
        ("2026-04-04 08:00:00", "Worked on dashboard deploy", "Need tighter checks", "wazion"),
    )
    conn.execute(
        "INSERT INTO learnings VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Fix shared brain drift", "The dashboard deploy drifted again", "wazion", "high", 0.8, "2026-04-04 10:00:00", "2026-04-04 09:00:00"),
    )
    conn.execute(
        "INSERT INTO learnings VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Fix shared brain deploy drift", "Dashboard deploy drifted again and again", "wazion", "high", 0.7, "2026-04-04 10:30:00", "2026-04-04 09:30:00"),
    )
    conn.execute(
        "INSERT INTO followups VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Review dashboard deploy", "2026-04-05", "PENDING", "high", "2026-04-04 11:00:00", "2026-04-04 11:30:00", "dashboard remains critical"),
    )
    conn.execute(
        "INSERT INTO followups VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Review dashboard deployment", "2026-04-05", "PENDING", "high", "2026-04-04 11:10:00", "2026-04-04 11:35:00", "dashboard remains critical"),
    )
    conn.execute(
        "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?)",
        ("wazion", "deploy regression", "blocked", "dashboard deploy is blocked", "2026-04-04 12:00:00", "2026-04-05"),
    )
    conn.commit()
    conn.close()

    for date_str, mood, trust, corrections in (
        ("2026-04-03", 0.6, 70, 2),
        ("2026-04-04", 0.8, 82, 1),
    ):
        (deep_sleep_dir / f"{date_str}-synthesis.json").write_text(json.dumps({
            "date": date_str,
            "cross_session_patterns": [{"pattern": "deploy drift", "severity": "high"}],
            "morning_agenda": [{"title": "Review dashboard deploy"}],
            "emotional_day": {"mood_score": mood},
            "trust_calibration": {"score": trust},
            "productivity_day": {"total_corrections": corrections},
            "summary": "summary",
        }))
        (deep_sleep_dir / f"{date_str}-extractions.json").write_text(json.dumps({
            "date": date_str,
            "extractions": [
                {
                    "session_id": f"{date_str}-a",
                    "findings": [],
                    "protocol_summary": {
                        "guard_check": {"required": 2, "executed": 1},
                        "heartbeat": {"total": 3, "with_context": 2},
                        "change_log": {"edits": 2, "logged": 1},
                    },
                }
            ],
        }))
        (deep_sleep_dir / f"{date_str}-applied.json").write_text(json.dumps({
            "date": date_str,
            "stats": {
                "applied": 2,
                "deferred": 1,
                "skipped_dedupe": 1,
                "errors": 0,
            },
            "applied_actions": [
                {
                    "action_type": "followup_create",
                    "details": {
                        "description": "Engineering guardrail for deploy drift",
                        "reasoning": "Engineering fix",
                    },
                },
                {
                    "action_type": "followup_create",
                    "details": {
                        "description": "Review dashboard deploy",
                        "reasoning": "Matched duplicate",
                        "outcome": "matched_existing_followup",
                    },
                },
                {
                    "action_type": "learning_add",
                    "details": {
                        "outcome": "reinforced_learning",
                    },
                }
            ],
        }))

    current_synthesis = {
        "date": "2026-04-05",
        "cross_session_patterns": [{"pattern": "deploy drift", "severity": "high"}],
        "morning_agenda": [{"title": "Review dashboard deploy"}],
        "emotional_day": {"mood_score": 0.7},
        "trust_calibration": {"score": 78},
        "productivity_day": {"total_corrections": 3},
        "summary": "Current synthesis summary",
    }
    (deep_sleep_dir / "2026-04-05-extractions.json").write_text(json.dumps({
        "date": "2026-04-05",
        "extractions": [
            {
                "session_id": "2026-04-05-a",
                "findings": [],
                "protocol_summary": {
                    "guard_check": {"required": 4, "executed": 3},
                    "heartbeat": {"total": 5, "with_context": 4},
                    "change_log": {"edits": 3, "logged": 2},
                },
            }
        ],
    }))
    (deep_sleep_dir / "2026-04-05-applied.json").write_text(json.dumps({
        "date": "2026-04-05",
        "stats": {
            "applied": 3,
            "deferred": 1,
            "skipped_dedupe": 2,
            "errors": 0,
        },
            "applied_actions": [
                {
                    "action_type": "followup_create",
                    "details": {
                        "description": "Engineering fix for recurring deploy drift",
                        "reasoning": "Engineering guardrail",
                    },
                },
                {
                    "action_type": "learning_add",
                    "details": {
                        "outcome": "duplicate_learning",
                    },
                }
            ],
    }))

    (deep_sleep_dir / "2026-W13-weekly-summary.json").write_text(json.dumps({
        "label": "2026-W13",
        "followup_deduplication": {
            "open_followups": 5,
            "duplicate_open_followups": 3,
            "duplicate_rate_pct": 60.0,
        },
        "learning_consolidation": {
            "active_learnings": 5,
            "noise_pressure": 4,
            "noise_rate_pct": 80.0,
        },
        "protocol_summary": {"overall_compliance_pct": 52.0},
        "avg_mood_score": 0.65,
        "avg_trust_score": 75.0,
        "total_corrections": 4,
    }))

    outputs = apply_mod.write_periodic_summaries("2026-04-05", current_synthesis)

    weekly_json = Path(outputs["weekly_json"])
    monthly_json = Path(outputs["monthly_json"])
    assert weekly_json.is_file()
    assert monthly_json.is_file()

    weekly_payload = json.loads(weekly_json.read_text())
    assert weekly_payload["kind"] == "weekly"
    assert weekly_payload["daily_syntheses"] == 3
    assert weekly_payload["top_projects"][0]["project"] == "wazion"
    assert weekly_payload["top_patterns"][0]["pattern"] == "deploy drift"
    assert weekly_payload["protocol_summary"]["overall_compliance_pct"] == 64.1
    assert weekly_payload["delivery_metrics"]["engineering_followups"] == 3
    assert weekly_payload["delivery_metrics"]["followup_dedupe_matches"] == 2
    assert weekly_payload["delivery_metrics"]["learning_reinforcements"] == 2
    assert weekly_payload["delivery_metrics"]["learning_duplicate_skips"] == 1
    assert weekly_payload["followup_deduplication"]["duplicate_open_followups"] == 1
    assert weekly_payload["learning_consolidation"]["noise_pressure"] >= 2
    assert weekly_payload["trend"]["followup_duplicate_open_delta"] == -2
    assert weekly_payload["trend"]["learning_noise_delta"] <= 0
    assert weekly_payload["project_pulse"][0]["project"] == "wazion"

    weekly_markdown = Path(outputs["weekly_markdown"]).read_text()
    assert "Top Projects" in weekly_markdown
    assert "Protocol Compliance" in weekly_markdown
    assert "Loop Output" in weekly_markdown
    assert "Prevention Quality" in weekly_markdown
    assert "Duplicate followups delta" in weekly_markdown
    assert "wazion" in weekly_markdown


def test_create_followup_consolidates_semantic_duplicates(monkeypatch, tmp_path):
    apply_mod = _load_apply_module(monkeypatch, tmp_path)
    nexo_home = Path(os.environ["NEXO_HOME"])
    data_dir = nexo_home / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    import db

    db.init_db()
    db.create_followup(
        id="NF-EXISTING",
        description="Release reliability issue",
        date="2026-04-08",
        verification="",
        reasoning="",
    )

    result = apply_mod.create_followup(
        "Add a release reliability validation checklist and script before publishing.",
        date="2026-04-06",
        reasoning_note="Recurring hotfix pattern detected overnight.",
    )

    assert result["success"] is True
    assert result["id"] == "NF-EXISTING"
    assert result["outcome"] == "matched_existing_followup"

    conn = sqlite3.connect(str(data_dir / "nexo.db"))
    row = conn.execute("SELECT description, date, reasoning FROM followups WHERE id = 'NF-EXISTING'").fetchone()
    history = conn.execute(
        """SELECT event_type, note, actor
           FROM item_history
           WHERE item_type = 'followup' AND item_id = 'NF-EXISTING'
           ORDER BY id DESC"""
    ).fetchall()
    conn.close()
    assert "validation checklist" in row[0].lower()
    assert row[1] == "2026-04-06"
    assert row[2] == ""
    assert history[0][0] == "updated"
    assert "Recurring hotfix pattern detected overnight." in history[0][1]
    assert history[0][2] == "deep-sleep"


def test_add_learning_reinforces_instead_of_duplication(monkeypatch, tmp_path):
    apply_mod = _load_apply_module(monkeypatch, tmp_path)
    nexo_home = Path(os.environ["NEXO_HOME"])
    data_dir = nexo_home / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(data_dir / "nexo.db"))
    conn.execute(
        "CREATE TABLE learnings (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, title TEXT, content TEXT, reasoning TEXT, weight REAL, created_at REAL, updated_at REAL)"
    )
    conn.execute(
        "INSERT INTO learnings (category, title, content, reasoning, weight, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "release",
            "Validate before publishing",
            "Run nexo doctor and parity checks before publishing a release.",
            "",
            1.0,
            1.0,
            1.0,
        ),
    )
    conn.commit()
    conn.close()

    result = apply_mod.add_learning(
        "release",
        "Always validate releases before publish",
        "Run nexo doctor plus parity and packaging checks before publishing any release.",
    )

    assert result["success"] is True
    assert result["outcome"] == "reinforced_learning"

    conn = sqlite3.connect(str(data_dir / "nexo.db"))
    count = conn.execute("SELECT COUNT(*) FROM learnings").fetchone()[0]
    row = conn.execute("SELECT weight, reasoning FROM learnings LIMIT 1").fetchone()
    conn.close()
    assert count == 1
    assert row[0] > 1.0
    assert "Deep Sleep reinforcement" in row[1]


def test_add_learning_flags_contradictions_for_review(monkeypatch, tmp_path):
    apply_mod = _load_apply_module(monkeypatch, tmp_path)
    nexo_home = Path(os.environ["NEXO_HOME"])
    data_dir = nexo_home / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(data_dir / "nexo.db"))
    conn.execute(
        "CREATE TABLE learnings (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, title TEXT, content TEXT, reasoning TEXT, created_at REAL, updated_at REAL)"
    )
    conn.execute(
        "CREATE TABLE followups (id TEXT PRIMARY KEY, description TEXT, date TEXT, status TEXT, verification TEXT, reasoning TEXT, created_at REAL, updated_at REAL)"
    )
    conn.execute(
        "INSERT INTO learnings (category, title, content, reasoning, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "release",
            "Never skip validation",
            "Never skip release validation before publishing.",
            "",
            1.0,
            1.0,
        ),
    )
    conn.commit()
    conn.close()

    result = apply_mod.add_learning(
        "release",
        "Skip validation on tiny releases",
        "Skip release validation when the patch looks small.",
    )

    assert result["success"] is True
    assert result["outcome"] == "contradiction_review"
    assert result["review_followup_id"]

    conn = sqlite3.connect(str(data_dir / "nexo.db"))
    learning_count = conn.execute("SELECT COUNT(*) FROM learnings").fetchone()[0]
    followup_count = conn.execute("SELECT COUNT(*) FROM followups").fetchone()[0]
    followup_desc = conn.execute("SELECT description FROM followups LIMIT 1").fetchone()[0]
    conn.close()
    assert learning_count == 1
    assert followup_count == 1
    assert "Reconcile contradictory learning" in followup_desc
