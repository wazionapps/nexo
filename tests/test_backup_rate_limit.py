"""Tests for the v5.5.6 rate-limit on plugins.backup and user_data_portability.

These guard the v5.5.4 incident surface from the tool side: a runaway MCP
client calling nexo_backup_now / nexo_backup_restore / export_user_bundle in
a loop can no longer hammer sqlite3.Connection.backup() hundreds of times per
minute. The first call goes through; subsequent calls within the window
return a clear, client-actionable error.
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import time
from pathlib import Path

import pytest


def _seed_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS sample (id INTEGER PRIMARY KEY, body TEXT)")
        for i in range(20):
            conn.execute("INSERT INTO sample (body) VALUES (?)", (f"row-{i}",))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def backup_env(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo_home"
    (nexo_home / "data").mkdir(parents=True)
    (nexo_home / "backups").mkdir(parents=True)
    _seed_db(nexo_home / "data" / "nexo.db")

    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    # Keep the window short enough for deterministic tests but long enough to
    # still exercise the "second call too soon" branch before time advances.
    monkeypatch.setenv("NEXO_BACKUP_MIN_INTERVAL_SECS", "2")
    monkeypatch.setenv("NEXO_BACKUP_RESTORE_MIN_INTERVAL_SECS", "2")

    import plugins.backup as backup_mod
    importlib.reload(backup_mod)
    backup_mod._reset_rate_limit_state_for_tests()
    return {
        "home": nexo_home,
        "backups": nexo_home / "backups",
        "mod": backup_mod,
    }


def test_backup_now_first_call_succeeds(backup_env):
    result = backup_env["mod"].handle_backup_now()
    assert result.startswith("Backup created:"), result


def test_backup_now_second_call_is_rate_limited(backup_env):
    first = backup_env["mod"].handle_backup_now()
    assert first.startswith("Backup created:"), first
    second = backup_env["mod"].handle_backup_now()
    assert "Rate-limited" in second, second
    assert "backup_now" in second
    assert "stuck in a" in second  # explicit loop guidance


def test_backup_now_rate_limit_clears_after_interval(backup_env):
    backup_env["mod"].handle_backup_now()
    # interval is 2s in the fixture
    time.sleep(2.1)
    again = backup_env["mod"].handle_backup_now()
    assert again.startswith("Backup created:"), again


def test_backup_restore_first_call_succeeds(backup_env):
    # Create a source backup file to restore from.
    src_backup = backup_env["backups"] / "nexo-2026-01-01-0000.db"
    _seed_db(src_backup)
    result = backup_env["mod"].handle_backup_restore("nexo-2026-01-01-0000.db")
    assert result.startswith("DB restaurada desde"), result


def test_backup_restore_second_call_is_rate_limited(backup_env):
    src_backup = backup_env["backups"] / "nexo-2026-01-01-0000.db"
    _seed_db(src_backup)
    first = backup_env["mod"].handle_backup_restore("nexo-2026-01-01-0000.db")
    assert first.startswith("DB restaurada desde"), first
    second = backup_env["mod"].handle_backup_restore("nexo-2026-01-01-0000.db")
    assert "Rate-limited" in second
    assert "backup_restore" in second


def test_backup_list_is_never_rate_limited(backup_env):
    """backup_list is pure read; should not be rate-limited under any circumstance."""
    for _ in range(50):
        result = backup_env["mod"].handle_backup_list()
        assert "Rate-limited" not in result


def test_rate_limit_state_is_tool_local(backup_env):
    """Hitting backup_now must not consume the quota of backup_restore."""
    src_backup = backup_env["backups"] / "nexo-2026-01-01-0000.db"
    _seed_db(src_backup)
    first = backup_env["mod"].handle_backup_now()
    assert first.startswith("Backup created:")
    # restore should still be allowed — separate counter
    restore = backup_env["mod"].handle_backup_restore("nexo-2026-01-01-0000.db")
    assert restore.startswith("DB restaurada desde")


# ── Export bundle rate limit ────────────────────────────────────────

@pytest.fixture
def export_env(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo_home"
    (nexo_home / "data").mkdir(parents=True)
    (nexo_home / "exports").mkdir(parents=True)
    _seed_db(nexo_home / "data" / "nexo.db")
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setenv("NEXO_EXPORT_MIN_INTERVAL_SECS", "2")
    import user_data_portability as portability
    importlib.reload(portability)
    portability._reset_export_rate_limit_state_for_tests()
    return {"home": nexo_home, "mod": portability}


def test_export_first_call_succeeds(export_env):
    result = export_env["mod"].export_user_bundle()
    # Export may succeed or partially succeed; what matters here is it is not
    # flagged as rate-limited.
    assert not result.get("rate_limited"), result


def test_export_second_call_is_rate_limited(export_env):
    first = export_env["mod"].export_user_bundle()
    assert not first.get("rate_limited"), first
    second = export_env["mod"].export_user_bundle()
    assert second.get("rate_limited") is True
    assert second["ok"] is False
    assert "Rate-limited" in second["error"]


def test_export_rate_limit_clears_after_interval(export_env):
    export_env["mod"].export_user_bundle()
    time.sleep(2.1)
    again = export_env["mod"].export_user_bundle()
    assert not again.get("rate_limited"), again
