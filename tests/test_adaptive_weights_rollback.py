"""Tests for adaptive_mode learn_weights graduation + rollback + followup surfacing.

Fase 2 item 4 of NEXO-AUDIT-2026-04-11. The shadow→active transition and the
correction-rate guard already existed; this test file pins the contract and
covers the new NF-ADAPTIVE-WEIGHTS-ROLLBACK followup surfacing so a future
edit cannot silently drop the visibility on rollback events.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_adaptive_stack(monkeypatch, nexo_home: Path):
    """Reload db + adaptive_mode against a fresh NEXO_HOME."""
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    (nexo_home / "brain").mkdir(parents=True, exist_ok=True)

    import db._core as db_core
    import db._schema as db_schema
    import db
    import plugins.adaptive_mode as adaptive_mode

    importlib.reload(db_core)
    importlib.reload(db_schema)
    importlib.reload(db)
    importlib.reload(adaptive_mode)
    return db, adaptive_mode


@pytest.fixture
def adaptive_env(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _seed_adaptive_log_with_feedback(
    db,
    *,
    rows: int = 35,
    base_offset_days: int = 1,
    sig_corrections_value: float = 0.6,
    feedback_delta: float = -1,
):
    """Insert N adaptive_log rows with feedback annotations spread across time."""
    conn = db.get_db()
    base = datetime.utcnow() - timedelta(days=base_offset_days)
    for i in range(rows):
        ts = (base - timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO adaptive_log "
            "(timestamp, mode, tension_score, sig_vibe, sig_corrections, sig_brevity, "
            "sig_topic, sig_tool_errors, sig_git_diff, context_hint, feedback_event, feedback_delta) "
            "VALUES (?, 'NORMAL', 0.0, 0.1, ?, 0.0, 0.0, 0.0, 0.0, '', 'correction', ?)",
            (ts, sig_corrections_value, feedback_delta),
        )
    try:
        conn.commit()
    except Exception:
        pass


def _seed_adaptive_log_clean(db, *, rows: int = 12, base_offset_days: int = 14):
    """Insert N adaptive_log rows WITHOUT correction events for the pre-window."""
    conn = db.get_db()
    base = datetime.utcnow() - timedelta(days=base_offset_days)
    for i in range(rows):
        ts = (base - timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO adaptive_log "
            "(timestamp, mode, tension_score, sig_vibe, sig_corrections, sig_brevity, "
            "sig_topic, sig_tool_errors, sig_git_diff, context_hint) "
            "VALUES (?, 'NORMAL', 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, '')",
            (ts,),
        )
    try:
        conn.commit()
    except Exception:
        pass


def _seed_recent_corrections(db, *, rows: int):
    """Insert N adaptive_log entries in the last 7 days, all flagged as corrections."""
    conn = db.get_db()
    now = datetime.utcnow()
    for i in range(rows):
        ts = (now - timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO adaptive_log "
            "(timestamp, mode, tension_score, sig_vibe, sig_corrections, sig_brevity, "
            "sig_topic, sig_tool_errors, sig_git_diff, context_hint, feedback_event, feedback_delta) "
            "VALUES (?, 'NORMAL', 0.0, 0.1, 0.6, 0.0, 0.0, 0.0, 0.0, '', 'correction', -1)",
            (ts,),
        )
    try:
        conn.commit()
    except Exception:
        pass


# ── Shadow / graduation logic ─────────────────────────────────────────────


class TestLearnWeightsShadowAndGraduation:
    def test_returns_insufficient_data_below_min_samples(self, adaptive_env, monkeypatch):
        db, adaptive_mode = _reload_adaptive_stack(monkeypatch, adaptive_env)
        db.init_db()

        _seed_adaptive_log_with_feedback(db, rows=5)

        result = adaptive_mode.learn_weights(min_samples=30, lookback_days=30)

        assert result["status"] == "insufficient_data"
        assert result["samples"] == 5

    def test_first_learning_lands_in_shadow_mode(self, adaptive_env, monkeypatch):
        db, adaptive_mode = _reload_adaptive_stack(monkeypatch, adaptive_env)
        db.init_db()

        _seed_adaptive_log_with_feedback(db, rows=35)

        result = adaptive_mode.learn_weights(min_samples=30)

        assert result["status"] == "shadow"
        assert result["mode"] == "shadow"
        assert result["days_in_shadow"] == 0
        assert "weights" in result
        assert sum(result["weights"].values()) == pytest.approx(1.0, abs=0.01)
        # learned_weights must NOT be activated yet
        state = adaptive_mode._load_state()
        assert "shadow_weights" in state
        assert state.get("learned_weights") is None or "learned_weights" not in state

    def test_graduates_to_active_after_14_days_in_shadow(self, adaptive_env, monkeypatch):
        db, adaptive_mode = _reload_adaptive_stack(monkeypatch, adaptive_env)
        db.init_db()

        _seed_adaptive_log_with_feedback(db, rows=35)

        # Pretend learning started 15 days ago
        state = adaptive_mode._load_state()
        state["learned_weights_first_date"] = (
            datetime.utcnow() - timedelta(days=15)
        ).strftime("%Y-%m-%dT%H:%M:%S")
        adaptive_mode._save_state(state)

        result = adaptive_mode.learn_weights(min_samples=30)

        assert result["status"] == "active"
        assert result["mode"] == "active"
        state = adaptive_mode._load_state()
        assert state.get("learned_weights")
        assert len(state["learned_weights"]) == 6


# ── Rollback safety ────────────────────────────────────────────────────────


class TestCheckWeightRollback:
    def test_no_learned_weights_returns_no_op(self, adaptive_env, monkeypatch):
        db, adaptive_mode = _reload_adaptive_stack(monkeypatch, adaptive_env)
        db.init_db()

        result = adaptive_mode.check_weight_rollback()
        assert result["status"] == "no_learned_weights"

    def test_too_early_when_activated_under_7_days_ago(self, adaptive_env, monkeypatch):
        db, adaptive_mode = _reload_adaptive_stack(monkeypatch, adaptive_env)
        db.init_db()

        state = adaptive_mode._load_state()
        state["learned_weights_date"] = (
            datetime.utcnow() - timedelta(days=3)
        ).strftime("%Y-%m-%dT%H:%M:%S")
        state["learned_weights"] = dict(adaptive_mode.WEIGHTS)
        adaptive_mode._save_state(state)

        result = adaptive_mode.check_weight_rollback()
        assert result["status"] == "too_early"
        assert result["days_since_activation"] == 3

    def test_low_volume_guard_skips_rollback(self, adaptive_env, monkeypatch):
        db, adaptive_mode = _reload_adaptive_stack(monkeypatch, adaptive_env)
        db.init_db()

        state = adaptive_mode._load_state()
        state["learned_weights_date"] = (
            datetime.utcnow() - timedelta(days=10)
        ).strftime("%Y-%m-%dT%H:%M:%S")
        state["learned_weights"] = dict(adaptive_mode.WEIGHTS)
        adaptive_mode._save_state(state)

        # Only 5 events in pre-window, way below the 10-event guard
        _seed_adaptive_log_clean(db, rows=5, base_offset_days=12)

        result = adaptive_mode.check_weight_rollback()
        assert result["status"] == "low_volume"

    def test_rollback_fires_when_correction_rate_doubles_and_opens_followup(
        self, adaptive_env, monkeypatch
    ):
        db, adaptive_mode = _reload_adaptive_stack(monkeypatch, adaptive_env)
        db.init_db()

        # Activation 10 days ago
        state = adaptive_mode._load_state()
        state["learned_weights_date"] = (
            datetime.utcnow() - timedelta(days=10)
        ).strftime("%Y-%m-%dT%H:%M:%S")
        state["learned_weights"] = dict(adaptive_mode.WEIGHTS)
        adaptive_mode._save_state(state)

        # Pre-window (days -17 to -10): 14 entries, 1 correction → ~0.14/day rate
        # Inserting one correction + 13 clean entries pre activation.
        _seed_adaptive_log_clean(db, rows=13, base_offset_days=14)
        conn = db.get_db()
        ts_pre = (datetime.utcnow() - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO adaptive_log "
            "(timestamp, mode, tension_score, feedback_event, feedback_delta) "
            "VALUES (?, 'NORMAL', 0.0, 'correction', -1)",
            (ts_pre,),
        )
        try:
            conn.commit()
        except Exception:
            pass

        # Post-window (last 7 days): 14 entries, all corrections → 2.0/day,
        # which is far above 2x the pre rate.
        _seed_recent_corrections(db, rows=14)

        result = adaptive_mode.check_weight_rollback()
        assert result["status"] == "rolled_back"
        assert result["post_rate"] >= 2 * result["pre_rate"]

        # State must reflect rollback
        state = adaptive_mode._load_state()
        assert "learned_weights" not in state
        assert "learned_weights_rollback" in state

        # And the followup must exist with the fixed id, PENDING, priority high
        followup = db.get_followup("NF-ADAPTIVE-WEIGHTS-ROLLBACK")
        assert followup is not None
        assert followup["status"] == "PENDING"
        assert followup["priority"] == "high"
        assert "rolled back" in (followup["description"] or "").lower()
        assert "pre-activation" in (followup["description"] or "").lower()

    def test_rollback_followup_is_idempotent_across_runs(self, adaptive_env, monkeypatch):
        db, adaptive_mode = _reload_adaptive_stack(monkeypatch, adaptive_env)
        db.init_db()

        # Manually trigger the helper twice with the same payload
        adaptive_mode._open_rollback_followup(reason="x", pre_rate=0.5, post_rate=2.0)
        adaptive_mode._open_rollback_followup(reason="x", pre_rate=0.5, post_rate=2.0)

        conn = db.get_db()
        count = conn.execute(
            "SELECT COUNT(*) FROM followups WHERE id = ?",
            ("NF-ADAPTIVE-WEIGHTS-ROLLBACK",),
        ).fetchone()[0]
        assert count == 1
