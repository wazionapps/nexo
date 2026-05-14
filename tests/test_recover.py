"""Tests for plugins.recover — the CLI/MCP entry point that restores nexo.db."""

from __future__ import annotations

import importlib
import json
import sqlite3
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
def recover_env(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo_home"
    (nexo_home / "data").mkdir(parents=True)
    (nexo_home / "backups").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    import plugins.recover as recover
    importlib.reload(recover)
    return {
        "home": nexo_home,
        "data": nexo_home / "data",
        "backups": nexo_home / "backups",
        "recover": recover,
    }


def test_recover_restores_wiped_db_from_hourly_backup(recover_env):
    primary = recover_env["data"] / "nexo.db"
    hourly = recover_env["backups"] / "nexo-2026-04-16-1402.db"
    _make_wiped_db(primary)
    _make_populated_db(hourly, rows_per_table=250)

    report = recover_env["recover"].recover(skip_kill=True)
    assert report["ok"] is True, report
    assert report["source"] == str(hourly)
    assert report["final_row_counts"]["protocol_tasks"] == 250
    # pre-heal / pre-recover snapshot was made
    assert "pre_recover_dir" in report


def test_recover_prefers_richest_local_memory_backup_over_newest_regressed_backup(recover_env):
    primary = recover_env["data"] / "nexo.db"
    rich = recover_env["backups"] / "nexo-2026-05-13-2350.db"
    regressed = recover_env["backups"] / "nexo-2026-05-14-0046.db"
    _make_wiped_db(primary)
    _make_populated_db(rich, rows_per_table=250)
    _add_local_rows(rich, {"local_assets": 2000, "local_chunks": 4000, "local_embeddings": 4000})
    _make_populated_db(regressed, rows_per_table=500)
    import os
    older = 1000.0
    newer = 2000.0
    os.utime(str(rich), (older, older))
    os.utime(str(regressed), (newer, newer))

    report = recover_env["recover"].recover(skip_kill=True)

    assert report["ok"] is True, report
    assert report["source"] == str(rich)
    assert db_row_counts(primary)["local_assets"] == 2000


def test_recover_quiesces_and_resumes_db_writers(recover_env, monkeypatch):
    primary = recover_env["data"] / "nexo.db"
    hourly = recover_env["backups"] / "nexo-2026-04-16-1402.db"
    _make_wiped_db(primary)
    _make_populated_db(hourly, rows_per_table=250)

    calls = {"quiesce": 0, "resume": 0}

    def fake_quiesce(dry_run=False):
        calls["quiesce"] += 1
        return {
            "terminated": 2,
            "errors": [],
            "mcp": {"terminated": 1, "scanned": 1, "errors": []},
            "launchagents": {"stopped": ["com.nexo.local-index"], "errors": [], "unsupported": False},
            "processes": {"terminated": 1, "scanned": 1, "errors": [], "pids": []},
            "dry_run": dry_run,
        }

    def fake_resume(labels=None, dry_run=False):
        calls["resume"] += 1
        return {"started": list(labels or []), "errors": [], "dry_run": dry_run}

    monkeypatch.setattr(recover_env["recover"], "quiesce_nexo_db_writers", fake_quiesce)
    monkeypatch.setattr(recover_env["recover"], "resume_nexo_launchagents", fake_resume)

    report = recover_env["recover"].recover()
    assert report["ok"] is True, report
    assert calls == {"quiesce": 1, "resume": 1}
    assert report["quiesce"]["terminated"] == 2
    assert report["resume"]["started"] == ["com.nexo.local-index"]


def test_recover_refuses_healthy_db_without_force(recover_env):
    primary = recover_env["data"] / "nexo.db"
    hourly = recover_env["backups"] / "nexo-2026-04-16-1402.db"
    _make_populated_db(primary, rows_per_table=300)
    _make_populated_db(hourly, rows_per_table=100)

    report = recover_env["recover"].recover(skip_kill=True)
    assert report["ok"] is False
    assert any("does not look wiped" in e for e in report["errors"])


def test_recover_force_overrides_healthy_guard(recover_env):
    primary = recover_env["data"] / "nexo.db"
    hourly = recover_env["backups"] / "nexo-2026-04-16-1402.db"
    _make_populated_db(primary, rows_per_table=300)
    _make_populated_db(hourly, rows_per_table=100)

    report = recover_env["recover"].recover(skip_kill=True, force=True)
    assert report["ok"] is True
    assert report["final_row_counts"]["protocol_tasks"] == 100


def test_recover_dry_run_does_not_touch_db(recover_env):
    primary = recover_env["data"] / "nexo.db"
    hourly = recover_env["backups"] / "nexo-2026-04-16-1402.db"
    _make_wiped_db(primary)
    _make_populated_db(hourly, rows_per_table=200)

    report = recover_env["recover"].recover(skip_kill=True, dry_run=True)
    assert report["ok"] is True
    assert report["dry_run"] is True
    # Primary DB still wiped.
    from db_guard import db_row_counts
    assert db_row_counts(primary)["protocol_tasks"] == 0


def test_recover_rejects_below_floor_backup(recover_env):
    primary = recover_env["data"] / "nexo.db"
    hourly = recover_env["backups"] / "nexo-2026-04-16-1402.db"
    _make_wiped_db(primary)
    _make_populated_db(hourly, rows_per_table=5)  # Below MIN_REFERENCE_ROWS

    report = recover_env["recover"].recover(skip_kill=True)
    assert report["ok"] is False
    assert any("minimum" in e or "no usable" in e for e in report["errors"])


def test_recover_lists_backups(recover_env):
    _make_populated_db(recover_env["backups"] / "nexo-2026-04-16-1402.db", 200)
    _make_populated_db(recover_env["backups"] / "nexo-2026-04-16-1502.db", 300)

    entries = recover_env["recover"].list_available_backups()
    assert len(entries) == 2
    # Newest first.
    assert entries[0]["path"].endswith("1502.db")
    assert all(e["is_usable"] for e in entries)


def test_recover_mcp_tool_returns_json(recover_env):
    _make_wiped_db(recover_env["data"] / "nexo.db")
    _make_populated_db(recover_env["backups"] / "nexo-2026-04-16-1502.db", 200)

    # nexo_recover is the MCP adapter — should succeed via dry_run path.
    raw = recover_env["recover"].nexo_recover(dry_run=True)
    report = json.loads(raw)
    assert report["ok"] is True
    assert report["dry_run"] is True
