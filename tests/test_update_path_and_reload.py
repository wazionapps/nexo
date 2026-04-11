"""Tests for the update path + post-bump LaunchAgent reload — Bloque D.

Pins the contract that:

  D1. run_migrations() can take a database that is at any historical
      schema version between m0 (no schema_migrations table) and m38
      (the previous tip) and roll it forward to m39 in one call.
      Idempotent: calling run_migrations() a second time on the same
      database is a no-op.

  D2. _reload_launch_agents_after_bump() finds the com.nexo.*.plist
      files in ~/Library/LaunchAgents and runs unload + load on each.
      Best-effort — failures land in errors[] but never raise.
      No-op on Linux.
"""

from __future__ import annotations

import importlib
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


# ── D1: migration path from any historical version ──────────────────────


def _seed_partial_schema(conn: sqlite3.Connection, applied_versions: list[int]) -> None:
    """Pretend the DB only has a subset of migrations applied."""
    conn.execute("DELETE FROM schema_migrations WHERE version > ?",
                 (max(applied_versions) if applied_versions else 0,))
    conn.commit()


class TestRunMigrationsFromHistoricalState:
    def test_re_apply_from_m0_succeeds_idempotent(self, isolated_db):
        from db._core import get_db
        from db._schema import run_migrations

        conn = get_db()
        conn.execute("DELETE FROM schema_migrations")
        conn.commit()

        run_migrations(conn)
        max_version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        assert max_version is not None
        assert max_version >= 39

    def test_re_apply_from_m1_reaches_latest(self, isolated_db):
        from db._core import get_db
        from db._schema import run_migrations

        conn = get_db()
        _seed_partial_schema(conn, [1])

        run_migrations(conn)
        rows = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        applied = [r[0] for r in rows]
        assert 1 in applied
        assert 39 in applied

    def test_re_apply_from_m38_only_applies_m39(self, isolated_db):
        from db._core import get_db
        from db._schema import run_migrations

        conn = get_db()
        _seed_partial_schema(conn, list(range(1, 39)))

        run_migrations(conn)
        max_version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        assert max_version >= 39

    def test_double_run_is_idempotent(self, isolated_db):
        from db._core import get_db
        from db._schema import run_migrations

        conn = get_db()
        before = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        run_migrations(conn)
        after = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        assert before == after

    def test_critical_columns_present_after_migration(self, isolated_db):
        from db._core import get_db
        from db._schema import run_migrations

        conn = get_db()
        run_migrations(conn)

        evo_cols = {row["name"] for row in conn.execute("PRAGMA table_info(evolution_log)")}
        assert "proposal_payload" in evo_cols

        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "hook_runs" in tables


# ── D2: _reload_launch_agents_after_bump ─────────────────────────────────


class TestReloadLaunchAgentsAfterBump:
    def test_returns_noop_dict_on_linux(self, monkeypatch):
        import auto_update
        monkeypatch.setattr(auto_update.sys, "platform", "linux")
        result = auto_update._reload_launch_agents_after_bump()
        assert result["scanned"] == 0
        assert result["reloaded"] == 0
        assert result["platform"] == "linux"

    def test_returns_zero_when_launch_agents_dir_missing(self, monkeypatch, tmp_path):
        import auto_update
        monkeypatch.setattr(auto_update.sys, "platform", "darwin")
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(auto_update.Path, "home", lambda: fake_home)
        result = auto_update._reload_launch_agents_after_bump()
        assert result["scanned"] == 0
        assert result["reloaded"] == 0

    def test_calls_launchctl_unload_then_load_for_each_plist(self, monkeypatch, tmp_path):
        import auto_update
        monkeypatch.setattr(auto_update.sys, "platform", "darwin")
        fake_home = tmp_path / "home"
        la_dir = fake_home / "Library" / "LaunchAgents"
        la_dir.mkdir(parents=True)
        for name in ("com.nexo.evolution.plist", "com.nexo.watchdog.plist", "com.nexo.deep-sleep.plist"):
            (la_dir / name).write_text("<plist/>")
        (la_dir / "com.other.app.plist").write_text("<plist/>")
        monkeypatch.setattr(auto_update.Path, "home", lambda: fake_home)

        calls: list[list[str]] = []

        def _fake_run(args, *_a, **_k):
            calls.append(list(args))
            return mock.Mock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(auto_update.subprocess, "run", _fake_run)

        result = auto_update._reload_launch_agents_after_bump()
        assert result["scanned"] == 3
        assert result["reloaded"] == 3
        assert result["errors"] == []

        unload_targets = [c[2] for c in calls if c[:2] == ["launchctl", "unload"]]
        load_targets = [c[3] for c in calls if c[:3] == ["launchctl", "load", "-w"]]
        assert len(unload_targets) == 3
        assert len(load_targets) == 3
        assert all("com.other.app" not in t for t in unload_targets + load_targets)

    def test_records_errors_when_launchctl_load_fails(self, monkeypatch, tmp_path):
        import auto_update
        monkeypatch.setattr(auto_update.sys, "platform", "darwin")
        fake_home = tmp_path / "home"
        la_dir = fake_home / "Library" / "LaunchAgents"
        la_dir.mkdir(parents=True)
        (la_dir / "com.nexo.evolution.plist").write_text("<plist/>")
        monkeypatch.setattr(auto_update.Path, "home", lambda: fake_home)

        def _fake_run(args, *_a, **_k):
            if args[1] == "load":
                return mock.Mock(returncode=1, stdout="", stderr="bootstrap denied")
            return mock.Mock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(auto_update.subprocess, "run", _fake_run)

        result = auto_update._reload_launch_agents_after_bump()
        assert result["scanned"] == 1
        assert result["reloaded"] == 0
        assert len(result["errors"]) == 1
        assert "bootstrap denied" in result["errors"][0]["stderr"]

    def test_handles_subprocess_timeout_gracefully(self, monkeypatch, tmp_path):
        import auto_update
        monkeypatch.setattr(auto_update.sys, "platform", "darwin")
        fake_home = tmp_path / "home"
        la_dir = fake_home / "Library" / "LaunchAgents"
        la_dir.mkdir(parents=True)
        (la_dir / "com.nexo.test.plist").write_text("<plist/>")
        monkeypatch.setattr(auto_update.Path, "home", lambda: fake_home)

        def _fake_run(args, *_a, **_k):
            raise subprocess.TimeoutExpired(cmd=args, timeout=10)

        monkeypatch.setattr(auto_update.subprocess, "run", _fake_run)

        result = auto_update._reload_launch_agents_after_bump()
        assert result["scanned"] == 1
        assert result["reloaded"] == 0
        assert len(result["errors"]) == 1
        assert "timeout" in result["errors"][0]["stderr"]
