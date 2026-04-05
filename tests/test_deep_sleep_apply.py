"""Tests for Deep Sleep applied summaries and project weighting."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APPLY_PATH = REPO_ROOT / "src" / "scripts" / "deep-sleep" / "apply_findings.py"


def _load_apply_module(monkeypatch, home: Path):
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NEXO_HOME", str(home / "nexo-home"))
    monkeypatch.setenv("NEXO_CODE", str(REPO_ROOT / "src"))
    sys.modules.pop("deep_sleep_apply_test", None)
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
        "INSERT INTO followups VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Review dashboard deploy", "2026-04-05", "PENDING", "high", "2026-04-04 11:00:00", "2026-04-04 11:30:00", "dashboard remains critical"),
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

    current_synthesis = {
        "date": "2026-04-05",
        "cross_session_patterns": [{"pattern": "deploy drift", "severity": "high"}],
        "morning_agenda": [{"title": "Review dashboard deploy"}],
        "emotional_day": {"mood_score": 0.7},
        "trust_calibration": {"score": 78},
        "productivity_day": {"total_corrections": 3},
        "summary": "Current synthesis summary",
    }

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

    weekly_markdown = Path(outputs["weekly_markdown"]).read_text()
    assert "Top Projects" in weekly_markdown
    assert "wazion" in weekly_markdown
