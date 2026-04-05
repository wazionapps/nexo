import json
from pathlib import Path

from dashboard.app import _latest_periodic_summary, _summarize_engineering_loop


def test_latest_periodic_summary_reads_latest_label(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    deep_sleep = tmp_path / "operations" / "deep-sleep"
    deep_sleep.mkdir(parents=True)

    older = {
        "label": "2026-W13",
        "project_pulse": [{"project": "alpha", "score": 3.2}],
    }
    newer = {
        "label": "2026-W14",
        "project_pulse": [{"project": "beta", "score": 5.1}],
    }

    (deep_sleep / "2026-W13-weekly-summary.json").write_text(json.dumps(older), encoding="utf-8")
    (deep_sleep / "2026-W14-weekly-summary.json").write_text(json.dumps(newer), encoding="utf-8")

    summary = _latest_periodic_summary("weekly")
    assert summary["label"] == "2026-W14"
    assert summary["project_pulse"][0]["project"] == "beta"


def test_summarize_engineering_loop_surfaces_matters_drift_and_improvement():
    weekly = {
        "project_pulse": [
            {"project": "wazion", "score": 9.4, "status": "critical", "reasons": ["open followups", "blocked decisions"]},
            {"project": "nexo", "score": 6.2, "status": "watch", "reasons": ["recent diary activity"]},
        ],
        "protocol_summary": {
            "guard_check": {"compliance_pct": 62.5},
            "heartbeat": {"compliance_pct": 41.0},
            "change_log": {"compliance_pct": 88.0},
        },
        "top_patterns": [
            {"pattern": "release validation skipped", "count": 3},
        ],
        "trend": {
            "avg_trust_delta": 1.2,
            "avg_mood_delta": 0.08,
            "total_corrections_delta": -2,
            "protocol_compliance_delta": 9.5,
        },
        "delivery_metrics": {
            "engineering_followups": 4,
        },
    }

    summary = _summarize_engineering_loop(weekly, {})

    assert summary["matters_now"][0]["title"] == "wazion"
    assert summary["drifting"][0]["title"] == "guard_check"
    assert summary["drifting"][1]["title"] == "heartbeat"
    assert summary["drifting"][2]["title"] == "release validation skipped"
    assert summary["improving"][0]["title"] == "Trust"
    assert any(item["title"] == "Engineering followups" for item in summary["improving"])
