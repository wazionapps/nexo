"""Tests for hook lifecycle observability — Fase 3 item 7.

Pins the m39 schema, the record/list/summary helpers, and the CLI shim
that bash hooks invoke as the last step of their lifecycle.
"""

from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_hook_observability():
    import db._core as db_core
    import db._schema as db_schema
    import db
    import hook_observability

    importlib.reload(db_core)
    importlib.reload(db_schema)
    importlib.reload(db)
    importlib.reload(hook_observability)
    return db, hook_observability


# ── Migration m39 ─────────────────────────────────────────────────────────


class TestM39HookRunsMigration:
    def test_hook_runs_table_exists_after_migrations(self, isolated_db):
        from db import get_db
        conn = get_db()
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(hook_runs)").fetchall()}
        assert "hook_name" in cols
        assert "started_at" in cols
        assert "duration_ms" in cols
        assert "exit_code" in cols
        assert "status" in cols
        assert "session_id" in cols
        assert "summary" in cols
        assert "metadata" in cols

    def test_hook_runs_indexes_exist(self, isolated_db):
        from db import get_db
        conn = get_db()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='hook_runs'"
        ).fetchall()
        names = {r["name"] for r in rows}
        assert "idx_hook_runs_hook_name" in names
        assert "idx_hook_runs_started_at" in names
        assert "idx_hook_runs_status" in names


# ── record_hook_run ───────────────────────────────────────────────────────


class TestRecordHookRun:
    def test_returns_zero_on_empty_hook_name(self, isolated_db):
        _db, hook_observability = _reload_hook_observability()
        rid = hook_observability.record_hook_run("")
        assert rid == 0

    def test_inserts_row_with_default_status_ok(self, isolated_db):
        _db, hook_observability = _reload_hook_observability()
        rid = hook_observability.record_hook_run(
            "session-start",
            duration_ms=142,
            exit_code=0,
            session_id="claude-1234",
            summary="Briefing generated",
        )
        assert rid > 0
        from db import get_db
        row = dict(get_db().execute("SELECT * FROM hook_runs WHERE id = ?", (rid,)).fetchone())
        assert row["hook_name"] == "session-start"
        assert row["duration_ms"] == 142
        assert row["exit_code"] == 0
        assert row["status"] == "ok"
        assert row["session_id"] == "claude-1234"
        assert row["summary"] == "Briefing generated"

    def test_derives_error_status_from_nonzero_exit(self, isolated_db):
        _db, hook_observability = _reload_hook_observability()
        rid = hook_observability.record_hook_run(
            "post-compact",
            duration_ms=200,
            exit_code=1,
        )
        from db import get_db
        row = dict(get_db().execute("SELECT * FROM hook_runs WHERE id = ?", (rid,)).fetchone())
        assert row["status"] == "error"

    def test_explicit_status_overrides_exit_code_derivation(self, isolated_db):
        _db, hook_observability = _reload_hook_observability()
        rid = hook_observability.record_hook_run(
            "inbox-hook",
            duration_ms=50,
            exit_code=0,
            status="skipped",
        )
        from db import get_db
        row = dict(get_db().execute("SELECT * FROM hook_runs WHERE id = ?", (rid,)).fetchone())
        assert row["status"] == "skipped"

    def test_truncates_long_summary_and_metadata(self, isolated_db):
        _db, hook_observability = _reload_hook_observability()
        long_summary = "x" * 5000
        big_metadata = {"key": "y" * 10000}
        rid = hook_observability.record_hook_run(
            "session-start",
            summary=long_summary,
            metadata=big_metadata,
        )
        from db import get_db
        row = dict(get_db().execute("SELECT * FROM hook_runs WHERE id = ?", (rid,)).fetchone())
        assert len(row["summary"]) == 500
        assert len(row["metadata"]) <= 4096


# ── list_recent_hook_runs ─────────────────────────────────────────────────


class TestListRecentHookRuns:
    def test_returns_empty_list_when_no_rows(self, isolated_db):
        _db, hook_observability = _reload_hook_observability()
        rows = hook_observability.list_recent_hook_runs(hours=24)
        assert rows == []

    def test_filters_by_time_window(self, isolated_db):
        _db, hook_observability = _reload_hook_observability()
        # Old row outside the window
        from db import get_db
        old_ts = time.time() - 86400 * 5
        get_db().execute(
            "INSERT INTO hook_runs (hook_name, started_at, duration_ms, exit_code, "
            "status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("session-start", old_ts, 100, 0, "ok", old_ts),
        )
        get_db().commit()
        # Fresh row inside the window
        hook_observability.record_hook_run("session-start", duration_ms=120)

        rows = hook_observability.list_recent_hook_runs(hours=24)
        assert len(rows) == 1
        assert rows[0]["duration_ms"] == 120

    def test_filters_by_hook_name_substring(self, isolated_db):
        _db, hook_observability = _reload_hook_observability()
        hook_observability.record_hook_run("session-start", duration_ms=100)
        hook_observability.record_hook_run("post-compact", duration_ms=120)

        rows = hook_observability.list_recent_hook_runs(hook_name="session")
        assert len(rows) == 1
        assert rows[0]["hook_name"] == "session-start"

    def test_filters_by_status(self, isolated_db):
        _db, hook_observability = _reload_hook_observability()
        hook_observability.record_hook_run("session-start", exit_code=0)
        hook_observability.record_hook_run("session-start", exit_code=1)

        ok_rows = hook_observability.list_recent_hook_runs(status="ok")
        err_rows = hook_observability.list_recent_hook_runs(status="error")
        assert len(ok_rows) == 1
        assert len(err_rows) == 1


# ── hook_health_summary ───────────────────────────────────────────────────


class TestHookHealthSummary:
    def test_summary_with_no_runs_returns_empty_state(self, isolated_db):
        _db, hook_observability = _reload_hook_observability()
        summary = hook_observability.hook_health_summary(hours=24)
        assert summary["window_hours"] == 24
        assert summary["total_runs"] == 0
        assert summary["by_hook"] == []
        assert summary["unhealthy_hooks"] == []

    def test_summary_aggregates_per_hook(self, isolated_db):
        _db, hook_observability = _reload_hook_observability()
        # session-start: 4 runs, all ok
        for _ in range(4):
            hook_observability.record_hook_run("session-start", duration_ms=100, exit_code=0)
        # post-compact: 5 runs, 2 errors -> success rate 0.6 -> unhealthy
        for _ in range(3):
            hook_observability.record_hook_run("post-compact", duration_ms=200, exit_code=0)
        for _ in range(2):
            hook_observability.record_hook_run("post-compact", duration_ms=500, exit_code=1)

        summary = hook_observability.hook_health_summary(hours=24)
        assert summary["total_runs"] == 9
        by_name = {b["hook_name"]: b for b in summary["by_hook"]}
        assert by_name["session-start"]["runs"] == 4
        assert by_name["session-start"]["ok"] == 4
        assert by_name["session-start"]["errors"] == 0
        assert by_name["session-start"]["success_rate"] == 1.0
        assert by_name["post-compact"]["runs"] == 5
        assert by_name["post-compact"]["errors"] == 2
        assert by_name["post-compact"]["success_rate"] == 0.6
        assert "post-compact" in summary["unhealthy_hooks"]
        assert "session-start" not in summary["unhealthy_hooks"]

    def test_unhealthy_threshold_requires_minimum_runs(self, isolated_db):
        _db, hook_observability = _reload_hook_observability()
        # 2 errors out of 2 runs but below the 3-run minimum -> not flagged
        hook_observability.record_hook_run("rare-hook", exit_code=1)
        hook_observability.record_hook_run("rare-hook", exit_code=1)

        summary = hook_observability.hook_health_summary(hours=24)
        assert "rare-hook" not in summary["unhealthy_hooks"]


# ── CLI shim ──────────────────────────────────────────────────────────────


class TestMainCli:
    def test_cli_records_via_subprocess_argv(self, isolated_db):
        _db, hook_observability = _reload_hook_observability()
        rc = hook_observability.main_cli([
            "record",
            "--hook", "session-start",
            "--duration-ms", "210",
            "--exit", "0",
            "--session", "abc123",
            "--summary", "Briefing OK",
        ])
        assert rc == 0
        rows = hook_observability.list_recent_hook_runs(hook_name="session-start")
        assert len(rows) == 1
        assert rows[0]["duration_ms"] == 210
        assert rows[0]["session_id"] == "abc123"
        assert rows[0]["summary"] == "Briefing OK"

    def test_cli_unknown_verb_returns_zero_without_inserting(self, isolated_db):
        _db, hook_observability = _reload_hook_observability()
        rc = hook_observability.main_cli(["bogus", "--hook", "x"])
        assert rc == 0
        rows = hook_observability.list_recent_hook_runs(hours=24)
        assert rows == []
