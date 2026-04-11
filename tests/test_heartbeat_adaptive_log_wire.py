"""Tests for the heartbeat → adaptive_log auto-wire.

Audit Bloque B follow-up: before this commit, adaptive_log was a
"feature inactiva" — _log_to_db existed but only fired when an agent
called nexo_adaptive_mode explicitly with signals. The result was that
learn_weights() (Fase 2 item 4) had zero training data and the
shadow→active graduation pipeline never activated.

These tests pin the new contract: every heartbeat with non-empty
context_hint writes exactly one adaptive_log row, and the row contains
sane values for all 6 signals derived from context_hint length and
sentiment.
"""

from __future__ import annotations

import importlib
import sys
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_session_modules():
    import db._core as db_core
    import db._schema as db_schema
    import db
    import plugins.adaptive_mode as adaptive_mode
    import tools_sessions as ts

    importlib.reload(db_core)
    importlib.reload(db_schema)
    importlib.reload(db)
    importlib.reload(adaptive_mode)
    importlib.reload(ts)
    return db, ts, adaptive_mode


def _adaptive_log_count() -> int:
    from db import get_db
    return get_db().execute("SELECT COUNT(*) FROM adaptive_log").fetchone()[0]


def _register_session(sid: str):
    from db import register_session
    register_session(sid, "adaptive log wire test")
    return sid


# ── Heartbeat fires _log_to_db ──────────────────────────────────────────


class TestHeartbeatAutoFiresAdaptiveLog:
    def test_heartbeat_with_context_hint_writes_one_row(self, isolated_db):
        _db, ts, _ = _reload_session_modules()
        sid = _register_session("nexo-9001-1001")
        before = _adaptive_log_count()

        ts.handle_heartbeat(
            sid=sid,
            task="audit follow-up",
            context_hint="seguir el plan de release sin pisar nada en producción",
        )

        after = _adaptive_log_count()
        assert after == before + 1

    def test_heartbeat_with_empty_context_hint_does_not_write(self, isolated_db):
        _db, ts, _ = _reload_session_modules()
        sid = _register_session("nexo-9002-1002")
        before = _adaptive_log_count()

        ts.handle_heartbeat(sid=sid, task="bare heartbeat", context_hint="")

        after = _adaptive_log_count()
        assert after == before  # no row when context is empty

    def test_heartbeat_with_short_context_hint_does_not_write(self, isolated_db):
        _db, ts, _ = _reload_session_modules()
        sid = _register_session("nexo-9003-1003")
        before = _adaptive_log_count()

        ts.handle_heartbeat(sid=sid, task="ok", context_hint="ok")  # < 5 chars

        after = _adaptive_log_count()
        assert after == before

    def test_consecutive_heartbeats_accumulate_rows(self, isolated_db):
        _db, ts, _ = _reload_session_modules()
        sid = _register_session("nexo-9004-1004")
        before = _adaptive_log_count()

        for _ in range(3):
            ts.handle_heartbeat(
                sid=sid,
                task="step",
                context_hint="continuando trabajo en la auditoria",
            )

        after = _adaptive_log_count()
        assert after == before + 3

    def test_row_contains_signal_columns(self, isolated_db):
        _db, ts, _ = _reload_session_modules()
        sid = _register_session("nexo-9005-1005")

        ts.handle_heartbeat(
            sid=sid,
            task="signal sniff",
            context_hint="this should produce all 6 signal fields populated",
        )

        from db import get_db
        row = get_db().execute(
            "SELECT mode, tension_score, sig_vibe, sig_corrections, sig_brevity, "
            "sig_topic, sig_tool_errors, sig_git_diff, context_hint "
            "FROM adaptive_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        # mode is one of the 3 valid values
        assert row[0] in {"FLOW", "NORMAL", "TENSION"}
        # tension_score is a float
        assert isinstance(row[1], (int, float))
        # All 6 signal columns are floats (default 0 if not active)
        for col in row[2:8]:
            assert isinstance(col, (int, float))
        # context_hint is the truncated message
        assert "signal fields" in (row[8] or "")

    def test_failure_in_compute_mode_does_not_break_heartbeat(self, isolated_db, monkeypatch):
        _db, ts, adaptive_mode = _reload_session_modules()

        def _boom(**kwargs):
            raise RuntimeError("simulated compute_mode failure")

        monkeypatch.setattr(adaptive_mode, "compute_mode", _boom)
        sid = _register_session("nexo-9006-1006")

        # Heartbeat must still return its normal payload, not raise.
        result = ts.handle_heartbeat(
            sid=sid,
            task="resilience test",
            context_hint="this hint will trigger the boom mock",
        )
        assert isinstance(result, str)
        assert sid in result
