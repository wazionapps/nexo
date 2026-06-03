from __future__ import annotations

import sys
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_preference_catalog_exposes_morning_agent_options(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo-home"))

    from preference_catalog import build_preference_catalog, explain_preference, set_preference

    catalog = build_preference_catalog(include_values=True, query="tiempo")
    ids = {entry["id"] for entry in catalog["preferences"]}

    assert "automation.morning-agent.weather" in ids
    assert "automation.morning-agent.audience" not in ids

    explanation = explain_preference("automation.morning-agent.weather")
    assert explanation["ok"] is True
    assert explanation["preference"]["writable"] is True
    assert "ubicación" in explanation["preference"]["help"].lower()

    dry_run = set_preference("automation.morning-agent.schedule", "07:00 Tue,Sat", dry_run=True)
    assert dry_run == {
        "ok": True,
        "dry_run": True,
        "id": "automation.morning-agent.schedule",
        "daily_at": "07:00",
        "weekdays": "Tue,Sat",
    }
