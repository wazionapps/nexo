"""Tests for Plan Consolidado 0.25 — guardian metrics aggregator."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "guardian_metrics_aggregate.py"


@pytest.fixture
def mod(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("guardian_metrics_aggregate", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(m)
    return m


def _seed_telemetry(home: Path, events: list[dict]) -> None:
    d = home / "logs"
    d.mkdir(parents=True, exist_ok=True)
    with (d / "guardian-telemetry.ndjson").open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def test_empty_telemetry_gives_zero_capture(mod, tmp_path):
    out = mod.aggregate(home=tmp_path)
    assert out["events_read"] == 0
    assert out["capture_rate"] == 0.0
    assert out["core_rule_violations_per_session"] == 0.0


def test_capture_rate_counts_injections(mod, tmp_path):
    _seed_telemetry(tmp_path, [
        {"ts": 1, "rule_id": "R13_pre_edit_guard", "event": "trigger", "session_id": "s1"},
        {"ts": 2, "rule_id": "R13_pre_edit_guard", "event": "enqueue", "mode": "hard", "session_id": "s1"},
        {"ts": 3, "rule_id": "R14_correction_learning", "event": "trigger", "session_id": "s1"},
    ])
    out = mod.aggregate(home=tmp_path)
    assert out["events_read"] == 3
    assert out["sessions_seen"] == 1
    assert 0 < out["capture_rate"] <= 1.0
    assert out["per_rule"]["R13_pre_edit_guard"]["injected"] == 1


def test_core_rule_violations_per_session(mod, tmp_path):
    _seed_telemetry(tmp_path, [
        {"ts": 1, "rule_id": "R13_pre_edit_guard", "event": "enqueue", "mode": "hard", "session_id": "s1"},
        {"ts": 2, "rule_id": "R25_nora_maria_read_only", "event": "enqueue", "mode": "hard", "session_id": "s1"},
        {"ts": 3, "rule_id": "R13_pre_edit_guard", "event": "enqueue", "mode": "hard", "session_id": "s2"},
    ])
    out = mod.aggregate(home=tmp_path)
    assert out["sessions_seen"] == 2
    assert out["core_rule_violations_per_session"] == 1.5  # 3 violations / 2 sessions


def test_r16_declared_done_hard_ratio(mod, tmp_path):
    _seed_telemetry(tmp_path, [
        {"ts": 1, "rule_id": "R16_declared_done", "event": "enqueue", "mode": "hard", "session_id": "s1"},
        {"ts": 2, "rule_id": "R16_declared_done", "event": "enqueue", "mode": "shadow", "session_id": "s1"},
    ])
    out = mod.aggregate(home=tmp_path)
    assert out["declared_done_without_evidence_ratio"] == 1.0


def test_fp_count_and_rate(mod, tmp_path):
    _seed_telemetry(tmp_path, [
        {"ts": 1, "rule_id": "R17_promise_debt", "event": "enqueue", "session_id": "s1", "fp": True},
        {"ts": 2, "rule_id": "R17_promise_debt", "event": "enqueue", "session_id": "s1"},
    ])
    out = mod.aggregate(home=tmp_path)
    assert out["false_positive_correction_rate"] > 0


def test_write_metrics_appends_ndjson(mod, tmp_path):
    _seed_telemetry(tmp_path, [{"ts": 1, "rule_id": "R13_pre_edit_guard", "event": "enqueue", "session_id": "s1"}])
    result = mod.aggregate(home=tmp_path)
    path = mod.write_metrics(result, home=tmp_path)
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["events_read"] == 1


def test_malformed_ndjson_line_is_skipped(mod, tmp_path):
    d = tmp_path / "logs"; d.mkdir(parents=True, exist_ok=True)
    (d / "guardian-telemetry.ndjson").write_text('{"rule_id":"R13","event":"enqueue","session_id":"s"}\nNOT-JSON\n')
    out = mod.aggregate(home=tmp_path)
    assert out["events_read"] == 1


def test_drift_baseline_merges_into_per_rule(mod, tmp_path):
    _seed_telemetry(tmp_path, [{"rule_id": "R13_pre_edit_guard", "event": "enqueue", "session_id": "s1"}])
    reports = tmp_path / "reports"; reports.mkdir(parents=True, exist_ok=True)
    (reports / "drift-baseline-2026-01-01.json").write_text(json.dumps({
        "rule_counts": {"R13_pre_edit_guard": 42}
    }))
    out = mod.aggregate(home=tmp_path)
    assert out["per_rule"]["R13_pre_edit_guard"]["baseline_hits"] == 42
    assert out["drift_baseline_source"] is not None
