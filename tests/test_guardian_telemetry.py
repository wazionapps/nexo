"""Tests for guardian_telemetry.py (Fase F F.1/F.2)."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

import pytest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture
def tel(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo_home"))
    import importlib
    import guardian_telemetry
    importlib.reload(guardian_telemetry)
    return guardian_telemetry


def _read_lines(p: pathlib.Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_log_event_writes_ndjson(tel, tmp_path):
    ok = tel.log_event("R13_pre_edit_guard", "trigger", mode="hard", tool="Edit", details={"x": 1})
    assert ok is True
    entries = _read_lines(tel._telemetry_path())
    assert len(entries) == 1
    e = entries[0]
    assert e["rule_id"] == "R13_pre_edit_guard"
    assert e["event"] == "trigger"
    assert e["mode"] == "hard"
    assert e["tool"] == "Edit"
    assert e["details"]["x"] == 1
    assert isinstance(e["ts"], (int, float))


def test_log_event_rejects_empty(tel):
    assert tel.log_event("", "trigger") is False
    assert tel.log_event("R13", "") is False


def test_log_event_fail_closed_on_bad_dir(tel, monkeypatch, tmp_path):
    # Force the parent path to a file so mkdir fails.
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    bad_path = blocker / "nested" / "telemetry.ndjson"
    assert tel.log_event("R13", "trigger", path=bad_path) is False


def test_summarize_rule_counts_events(tel):
    for event in ["trigger", "trigger", "injection", "compliance", "false_positive", "trigger", "skipped", "classifier_unavailable"]:
        tel.log_event("R16_declared_done", event, mode="hard")
    counts = tel.summarize_rule("R16_declared_done")
    assert counts["trigger"] == 3
    assert counts["injection"] == 1
    assert counts["compliance"] == 1
    assert counts["false_positive"] == 1
    assert counts["skipped"] == 1
    assert counts["classifier_unavailable"] == 1


def test_summarize_filters_by_rule(tel):
    tel.log_event("R13", "trigger")
    tel.log_event("R14", "trigger")
    tel.log_event("R14", "compliance")
    counts = tel.summarize_rule("R14")
    assert counts["trigger"] == 1
    assert counts["compliance"] == 1
    # R13 entries not counted under R14.
    counts_r13 = tel.summarize_rule("R13")
    assert counts_r13["trigger"] == 1


def test_efficacy_returns_ratio(tel):
    for _ in range(10):
        tel.log_event("R16", "trigger")
    for _ in range(6):
        tel.log_event("R16", "compliance")
    assert tel.efficacy("R16") == pytest.approx(0.6)


def test_efficacy_returns_none_without_triggers(tel):
    assert tel.efficacy("R99") is None


def test_rotation_on_size_threshold(tel, tmp_path):
    target = tmp_path / "rotate.ndjson"
    # Write many entries with a tiny max_bytes to force rotation.
    for i in range(50):
        tel.log_event("R13", "trigger", details={"i": i}, max_bytes=200, path=target)
    rotated = list(tmp_path.glob("rotate.ndjson.*"))
    assert rotated, "expected at least one rotated file"
    # Remaining active file has fewer than 50 entries.
    active = _read_lines(target)
    assert len(active) < 50
