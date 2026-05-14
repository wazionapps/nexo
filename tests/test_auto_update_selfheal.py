"""Tests for auto_update._self_heal_if_wiped — automatic recovery at startup."""

from __future__ import annotations

import importlib
import sqlite3
import time
from pathlib import Path

import pytest

from db_guard import CRITICAL_TABLES, LOCAL_CONTEXT_TABLES, db_row_counts


def _make_populated_db(path: Path, rows_per_table: int = 200) -> None:
    conn = sqlite3.connect(str(path))
    try:
        for table in CRITICAL_TABLES:
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, payload TEXT)")
        for table in ("protocol_tasks", "followups", "learnings"):
            for i in range(rows_per_table):
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


def _add_local_rows(path: Path, rows_by_table: dict[str, int]) -> None:
    conn = sqlite3.connect(str(path))
    try:
        for table in LOCAL_CONTEXT_TABLES:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, payload TEXT)")
        for table, count in rows_by_table.items():
            for i in range(count):
                conn.execute(f"INSERT INTO {table} (payload) VALUES (?)", (f"local-{i}",))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def auto_update_env(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo_home"
    (nexo_home / "data").mkdir(parents=True)
    (nexo_home / "backups").mkdir(parents=True)
    (nexo_home / "operations").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.delenv("NEXO_DISABLE_AUTO_HEAL", raising=False)
    import auto_update as au
    importlib.reload(au)
    # Neutralise DB-writer quiescence so tests never touch real processes.
    import db_guard
    monkeypatch.setattr(
        db_guard,
        "quiesce_nexo_db_writers",
        lambda dry_run=False: {
            "terminated": 0,
            "errors": [],
            "mcp": {"scanned": 0, "terminated": 0, "errors": [], "pids": [], "dry_run": dry_run},
            "launchagents": {"stopped": [], "errors": [], "unsupported": False},
            "processes": {"scanned": 0, "terminated": 0, "errors": [], "pids": [], "dry_run": dry_run},
            "dry_run": dry_run,
        },
    )
    monkeypatch.setattr(
        db_guard,
        "resume_nexo_launchagents",
        lambda labels=None, dry_run=False: {"started": list(labels or []), "errors": [], "dry_run": dry_run},
    )
    return {
        "home": nexo_home,
        "data": nexo_home / "data",
        "backups": nexo_home / "backups",
        "au": au,
    }


def test_self_heal_restores_wiped_db(auto_update_env):
    """The full incident scenario: boot with wiped DB + healthy hourly backup."""
    primary = auto_update_env["data"] / "nexo.db"
    hourly = auto_update_env["backups"] / "nexo-2026-04-16-1402.db"
    _make_wiped_db(primary)
    _make_populated_db(hourly, rows_per_table=250)

    report = auto_update_env["au"]._self_heal_if_wiped()
    assert report is not None, "self-heal must fire when wiped DB + good backup"
    assert report["action"] == "restored"
    assert report["restored_rows"] >= 250
    assert db_row_counts(primary)["protocol_tasks"] == 250


def test_self_heal_uses_richest_backup_not_newest_regressed_backup(auto_update_env):
    primary = auto_update_env["data"] / "nexo.db"
    rich = auto_update_env["backups"] / "nexo-2026-05-13-2350.db"
    regressed = auto_update_env["backups"] / "nexo-2026-05-14-0046.db"
    _make_wiped_db(primary)
    _make_populated_db(rich, rows_per_table=250)
    _add_local_rows(rich, {"local_assets": 2000, "local_chunks": 4000, "local_embeddings": 4000})
    _make_populated_db(regressed, rows_per_table=500)
    import os
    older = time.time() - 100
    newer = time.time()
    os.utime(str(rich), (older, older))
    os.utime(str(regressed), (newer, newer))

    report = auto_update_env["au"]._self_heal_if_wiped()

    assert report["action"] == "restored"
    assert report["reference"] == str(rich)
    assert db_row_counts(primary)["local_assets"] == 2000


def test_self_heal_noop_on_healthy_db(auto_update_env):
    primary = auto_update_env["data"] / "nexo.db"
    hourly = auto_update_env["backups"] / "nexo-2026-04-16-1402.db"
    _make_populated_db(primary, rows_per_table=300)
    _make_populated_db(hourly, rows_per_table=100)

    assert auto_update_env["au"]._self_heal_if_wiped() is None


def test_self_heal_skipped_when_disabled(auto_update_env, monkeypatch):
    primary = auto_update_env["data"] / "nexo.db"
    hourly = auto_update_env["backups"] / "nexo-2026-04-16-1402.db"
    _make_wiped_db(primary)
    _make_populated_db(hourly, rows_per_table=250)
    monkeypatch.setenv("NEXO_DISABLE_AUTO_HEAL", "1")

    assert auto_update_env["au"]._self_heal_if_wiped() is None


def test_self_heal_respects_cooldown(auto_update_env):
    primary = auto_update_env["data"] / "nexo.db"
    hourly = auto_update_env["backups"] / "nexo-2026-04-16-1402.db"
    _make_wiped_db(primary)
    _make_populated_db(hourly, rows_per_table=250)

    first = auto_update_env["au"]._self_heal_if_wiped()
    assert first["action"] == "restored"

    # Wipe again to simulate a pathological loop; cooldown must block re-heal.
    primary.unlink()
    for sidecar in ("-wal", "-shm"):
        extra = primary.parent / f"{primary.name}{sidecar}"
        if extra.exists():
            extra.unlink()
    _make_wiped_db(primary)
    second = auto_update_env["au"]._self_heal_if_wiped()
    assert second is not None
    assert second["action"] == "skipped"
    assert second["reason"] == "cooldown"


def test_self_heal_skips_when_no_reference_available(auto_update_env):
    primary = auto_update_env["data"] / "nexo.db"
    _make_wiped_db(primary)
    # No hourly backup at all.
    report = auto_update_env["au"]._self_heal_if_wiped()
    assert report is not None
    assert report["action"] == "skipped"
    assert report["reason"] == "no_usable_hourly_backup"


def test_self_heal_refuses_fresh_install_scenario(auto_update_env):
    """Both primary and backup are near-empty -> must not heal."""
    primary = auto_update_env["data"] / "nexo.db"
    hourly = auto_update_env["backups"] / "nexo-2026-04-16-1402.db"
    _make_wiped_db(primary)
    _make_populated_db(hourly, rows_per_table=2)  # Below MIN_REFERENCE_ROWS

    report = auto_update_env["au"]._self_heal_if_wiped()
    assert report is not None
    assert report["action"] == "skipped"
    assert report["reason"] in ("reference_below_floor", "no_usable_hourly_backup")
