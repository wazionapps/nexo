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

    news_catalog = build_preference_catalog(include_values=True, query="actualidad")
    news_ids = {entry["id"] for entry in news_catalog["preferences"]}
    assert "automation.morning-agent.news" in news_ids
    assert "automation.morning-agent.news_interests" in news_ids

    topics = set_preference("automation.morning-agent.news_interests", "tecnología, local", dry_run=True)
    assert topics == {
        "ok": True,
        "dry_run": True,
        "id": "automation.morning-agent.news_interests",
        "value": ["technology", "local"],
    }

    excluded = set_preference("automation.morning-agent.excluded_topics", "deportes, cripto", dry_run=True)
    assert excluded["value"] == ["sports", "crypto"]
