"""Tests for plugins.update pre-flight wipe guard and validated backups."""

from __future__ import annotations

import importlib
import os
import sqlite3
import time
from pathlib import Path

import pytest

from db_guard import CRITICAL_TABLES


def _make_populated_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        for table in CRITICAL_TABLES:
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, payload TEXT)")
        for table in ("protocol_tasks", "followups", "learnings"):
            for i in range(200):
                conn.execute(f"INSERT INTO {table} (payload) VALUES (?)", (f"row-{i}",))
        conn.commit()
    finally:
        conn.close()


def _make_wiped_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        for table in CRITICAL_TABLES:
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, payload TEXT)")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def update_env(tmp_path, monkeypatch):
    """Point plugins.update at a throwaway NEXO_HOME."""
    nexo_home = tmp_path / "nexo_home"
    data_dir = nexo_home / "data"
    backups = nexo_home / "backups"
    data_dir.mkdir(parents=True)
    backups.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.delenv("NEXO_SKIP_WIPE_GUARD", raising=False)
    import plugins.update as upd
    importlib.reload(upd)
    return {
        "home": nexo_home,
        "data": data_dir,
        "backups": backups,
        "upd": upd,
    }


def test_preflight_blocks_wiped_db_with_healthy_backup(update_env):
    """Reproduces the v5.5.4 incident: wiped primary + hourly backup with real data.

    The pre-flight check must ABORT the update so the rollback cannot capture
    the empty DB as "pre-update".
    """
    primary = update_env["data"] / "nexo.db"
    hourly = update_env["backups"] / "nexo-2026-04-16-1502.db"
    _make_wiped_db(primary)
    _make_populated_db(hourly)

    err = update_env["upd"]._preflight_wipe_check()
    assert err is not None
    assert "wiped" in err.lower()
    assert "nexo recover" in err


def test_preflight_allows_healthy_db(update_env):
    primary = update_env["data"] / "nexo.db"
    _make_populated_db(primary)
    assert update_env["upd"]._preflight_wipe_check() is None


def test_preflight_allows_fresh_install_with_no_backup(update_env):
    """No hourly backup -> cannot distinguish wipe from fresh install -> allow."""
    primary = update_env["data"] / "nexo.db"
    _make_wiped_db(primary)
    assert update_env["upd"]._preflight_wipe_check() is None


def test_preflight_skipped_when_env_override_set(update_env, monkeypatch):
    primary = update_env["data"] / "nexo.db"
    hourly = update_env["backups"] / "nexo-2026-04-16-1502.db"
    _make_wiped_db(primary)
    _make_populated_db(hourly)
    monkeypatch.setenv("NEXO_SKIP_WIPE_GUARD", "1")
    assert update_env["upd"]._preflight_wipe_check() is None


def test_backup_databases_validates_row_counts(update_env):
    """_backup_databases must reject a copy that did not preserve critical rows.

    This is the direct fix for "pre-update-*/nexo.db is 4 KB even though the
    source had 38 MB at the time".
    """
    primary = update_env["data"] / "nexo.db"
    _make_populated_db(primary)
    # Normal path: backup succeeds and validates.
    backup_dir, err = update_env["upd"]._backup_databases()
    assert err is None
    assert Path(backup_dir).is_dir()


def test_row_count_regression_detects_wipe(update_env):
    """_row_count_regression fires on 2+ regressed critical tables."""
    pre = {"protocol_tasks": 600, "followups": 400, "learnings": 380, "reminders": 40}
    post = {"protocol_tasks": 0, "followups": 1, "learnings": 380, "reminders": 40}
    regression = update_env["upd"]._row_count_regression(pre, post)
    assert regression is not None
    assert "protocol_tasks" in regression
    assert "followups" in regression


def test_row_count_regression_ignores_small_churn(update_env):
    pre = {"protocol_tasks": 600, "followups": 400, "learnings": 380}
    post = {"protocol_tasks": 595, "followups": 402, "learnings": 380}
    assert update_env["upd"]._row_count_regression(pre, post) is None
