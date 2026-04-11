"""Tests for nexo-cortex-cycle.py — Fase 2 item 6.

Validates the pure detection logic, the manifest entry shape, and the
side-effect helpers (snapshot persistence + followup upsert).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"
SCRIPT_PATH = REPO_SRC / "scripts" / "nexo-cortex-cycle.py"
MANIFEST_PATH = REPO_SRC / "crons" / "manifest.json"


def _load_cycle_module(monkeypatch, nexo_home: Path):
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    if str(REPO_SRC) not in sys.path:
        sys.path.insert(0, str(REPO_SRC))
    sys.modules.pop("nexo_cortex_cycle_test", None)
    spec = importlib.util.spec_from_file_location("nexo_cortex_cycle_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cortex_env(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    for dirname in ["operations", "logs"]:
        (home / dirname).mkdir(parents=True, exist_ok=True)
    return home


# ── Pure detection logic ─────────────────────────────────────────────────


class TestDetectQualitySignals:
    def test_no_signals_when_sample_too_small(self, cortex_env, monkeypatch):
        cycle = _load_cycle_module(monkeypatch, cortex_env)
        summary = {
            "total_evaluations": 4,  # below ACCEPT_RATE_MIN_SAMPLE
            "recommendation_accept_rate": 25.0,  # would otherwise trip floor
            "linked_outcomes_total": 0,
            "linked_outcome_success_rate": 0.0,
            "recommended_success_rate": 0.0,
            "override_success_rate": 0.0,
        }
        assert cycle.detect_quality_signals(summary) == []

    def test_no_signals_when_metrics_healthy(self, cortex_env, monkeypatch):
        cycle = _load_cycle_module(monkeypatch, cortex_env)
        summary = {
            "total_evaluations": 50,
            "recommendation_accept_rate": 80.0,
            "linked_outcomes_total": 20,
            "linked_outcome_success_rate": 75.0,
            "recommended_success_rate": 78.0,
            "override_success_rate": 60.0,
        }
        assert cycle.detect_quality_signals(summary) == []

    def test_accept_rate_signal_when_under_floor_with_enough_sample(
        self, cortex_env, monkeypatch
    ):
        cycle = _load_cycle_module(monkeypatch, cortex_env)
        summary = {
            "total_evaluations": 30,
            "recommendation_accept_rate": 35.0,
            "linked_outcomes_total": 0,
            "linked_outcome_success_rate": 0.0,
            "recommended_success_rate": 0.0,
            "override_success_rate": 0.0,
        }
        signals = cycle.detect_quality_signals(summary)
        assert len(signals) == 1
        assert signals[0]["kind"] == "accept_rate"
        assert signals[0]["severity"] == "warn"
        assert signals[0]["metric_value"] == 35.0
        assert signals[0]["sample_size"] == 30

    def test_linked_success_signal_when_under_floor(self, cortex_env, monkeypatch):
        cycle = _load_cycle_module(monkeypatch, cortex_env)
        summary = {
            "total_evaluations": 40,
            "recommendation_accept_rate": 90.0,  # healthy
            "linked_outcomes_total": 8,
            "linked_outcome_success_rate": 25.0,  # below floor
            "recommended_success_rate": 25.0,
            "override_success_rate": 0.0,
        }
        signals = cycle.detect_quality_signals(summary)
        kinds = [s["kind"] for s in signals]
        assert "linked_success" in kinds
        assert "accept_rate" not in kinds  # accept rate is healthy

    def test_override_gap_signal_when_overrides_outperform(
        self, cortex_env, monkeypatch
    ):
        cycle = _load_cycle_module(monkeypatch, cortex_env)
        summary = {
            "total_evaluations": 30,
            "recommendation_accept_rate": 70.0,
            "linked_outcomes_total": 10,
            "linked_outcome_success_rate": 65.0,
            "recommended_success_rate": 50.0,
            "override_success_rate": 80.0,  # 30pp gap
        }
        signals = cycle.detect_quality_signals(summary)
        kinds = [s["kind"] for s in signals]
        assert "override_gap" in kinds
        gap_signal = next(s for s in signals if s["kind"] == "override_gap")
        assert gap_signal["severity"] == "error"
        assert gap_signal["metric_value"] == pytest.approx(30.0)

    def test_override_gap_ignored_when_linked_sample_too_small(
        self, cortex_env, monkeypatch
    ):
        cycle = _load_cycle_module(monkeypatch, cortex_env)
        summary = {
            "total_evaluations": 30,
            "recommendation_accept_rate": 70.0,
            "linked_outcomes_total": 3,  # below LINKED_MIN_SAMPLE
            "linked_outcome_success_rate": 50.0,
            "recommended_success_rate": 30.0,
            "override_success_rate": 80.0,
        }
        signals = cycle.detect_quality_signals(summary)
        assert all(s["kind"] != "override_gap" for s in signals)

    def test_handles_empty_summary_gracefully(self, cortex_env, monkeypatch):
        cycle = _load_cycle_module(monkeypatch, cortex_env)
        assert cycle.detect_quality_signals({}) == []
        assert cycle.detect_quality_signals(None) == []  # type: ignore[arg-type]


# ── Manifest entry shape ─────────────────────────────────────────────────


class TestManifestEntry:
    def test_cortex_cycle_entry_present(self):
        manifest = json.loads(MANIFEST_PATH.read_text())
        ids = [c["id"] for c in manifest["crons"]]
        assert "cortex-cycle" in ids

    def test_cortex_cycle_entry_uses_6h_interval_and_correct_script(self):
        manifest = json.loads(MANIFEST_PATH.read_text())
        entry = next(c for c in manifest["crons"] if c["id"] == "cortex-cycle")
        assert entry["interval_seconds"] == 21600
        assert entry["script"] == "scripts/nexo-cortex-cycle.py"
        assert entry["core"] is True
        assert entry["idempotent"] is True
        assert "schedule" not in entry  # interval_seconds is mutually exclusive
        assert (REPO_SRC / entry["script"]).exists()


# ── Snapshot + followup side effects ─────────────────────────────────────


class TestPersistAndUpsert:
    def test_persist_quality_snapshot_writes_json_file(self, cortex_env, monkeypatch):
        cycle = _load_cycle_module(monkeypatch, cortex_env)
        cycle._persist_quality_snapshot(
            window_7d={"total_evaluations": 12, "recommendation_accept_rate": 75.0},
            window_1d={"total_evaluations": 3},
            signals=[],
        )
        out = cortex_env / "operations" / "cortex-quality-latest.json"
        assert out.exists()
        payload = json.loads(out.read_text())
        assert payload["window_7d"]["total_evaluations"] == 12
        assert payload["window_1d"]["total_evaluations"] == 3
        assert payload["signals"] == []
        assert payload["schema"] == 1
        assert "captured_at" in payload

    def test_upsert_followup_skips_when_no_signals(self, cortex_env, monkeypatch):
        cycle = _load_cycle_module(monkeypatch, cortex_env)
        result = cycle._upsert_quality_followup(signals=[])
        assert result == "no_signal"

    def test_run_with_healthy_summary_logs_and_does_not_open_followup(
        self, cortex_env, monkeypatch
    ):
        cycle = _load_cycle_module(monkeypatch, cortex_env)

        healthy_7d = {
            "days": 7,
            "total_evaluations": 25,
            "recommendation_accept_rate": 88.0,
            "linked_outcomes_total": 10,
            "linked_outcome_success_rate": 80.0,
            "recommended_success_rate": 82.0,
            "override_success_rate": 70.0,
        }
        healthy_1d = {"days": 1, "total_evaluations": 4, "recommendation_accept_rate": 100.0}

        # Patch the db import inside the module to return our fake summaries.
        import db as nexo_db

        def fake_summary(days: int = 30):
            return healthy_7d if days >= 7 else healthy_1d

        monkeypatch.setattr(nexo_db, "cortex_evaluation_summary", fake_summary)

        rc = cycle.run()
        assert rc == 0

        snapshot = json.loads((cortex_env / "operations" / "cortex-quality-latest.json").read_text())
        assert snapshot["signals"] == []
        assert snapshot["window_7d"]["total_evaluations"] == 25

        log = (cortex_env / "logs" / "cortex-cycle.log").read_text()
        assert "Cortex cycle" in log
        assert "signals=0" in log

    def test_run_with_degraded_summary_opens_followup_idempotently(
        self, cortex_env, monkeypatch, isolated_db
    ):
        cycle = _load_cycle_module(monkeypatch, cortex_env)

        degraded = {
            "days": 7,
            "total_evaluations": 30,
            "recommendation_accept_rate": 30.0,
            "linked_outcomes_total": 8,
            "linked_outcome_success_rate": 25.0,
            "recommended_success_rate": 20.0,
            "override_success_rate": 80.0,
        }

        import db as nexo_db
        monkeypatch.setattr(nexo_db, "cortex_evaluation_summary", lambda days=30: degraded)

        rc1 = cycle.run()
        rc2 = cycle.run()  # second run must be idempotent
        assert rc1 == 0 and rc2 == 0

        # The followup is upserted via the runtime db helper, which goes
        # through the isolated_db fixture's redirected connection. Read it
        # back through the same wrapped connection so we hit the same DB.
        from db import get_followup
        existing = get_followup(cycle.FOLLOWUP_ID)
        assert existing is not None
        assert existing["id"] == cycle.FOLLOWUP_ID
        assert existing["status"] == "PENDING"
        assert existing["priority"] == "high"
        assert ("accept_rate" in existing["description"]
                or "override_gap" in existing["description"])

        # Also count: idempotent means exactly one row in the followups table.
        conn = sqlite3.connect(isolated_db["nexo_db"])
        rows = conn.execute(
            "SELECT COUNT(*) FROM followups WHERE id = ?", (cycle.FOLLOWUP_ID,)
        ).fetchone()
        conn.close()
        assert rows[0] == 1
