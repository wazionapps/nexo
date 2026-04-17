"""Tests for auto_update._heal_deep_sleep_runtime.

Runtimes that ran the pre-5.8.1 deep-sleep loop (watchdog kickstart -k over
in-flight workers) accumulated three flavors of junk:

  - Poisoned extraction checkpoints containing overloaded_error responses.
  - Stale sleep/synthesis locks that the new 04:30 run cannot acquire.
  - Dangling cron_runs rows with ended_at=NULL and no recent heartbeat.

The heal routine runs silently on every auto_update post-sync and is an
idempotent no-op on clean runtimes.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_auto_update(monkeypatch, home: Path):
    import importlib
    monkeypatch.setenv("NEXO_HOME", str(home))
    import auto_update as au
    importlib.reload(au)
    return au


def _init_cron_runs(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE cron_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cron_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            exit_code INTEGER,
            summary TEXT,
            error TEXT,
            duration_secs REAL
        )
        """
    )
    conn.commit()
    conn.close()


def test_heal_is_noop_on_clean_runtime(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    home.mkdir()
    au = _reload_auto_update(monkeypatch, home)
    assert au._heal_deep_sleep_runtime(home) == []


def test_heal_purges_poisoned_checkpoints(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    checkpoint_dir = home / "operations" / "deep-sleep" / "2026-04-16" / "checkpoints"
    checkpoint_dir.mkdir(parents=True)
    poisoned = checkpoint_dir / "session-abcd.json"
    poisoned.write_text(
        json.dumps({
            "type": "error",
            "error": {"type": "overloaded_error", "message": "Overloaded"},
            "request_id": "req_x",
        })
    )
    good = checkpoint_dir / "session-good.json"
    good.write_text(json.dumps({"session_id": "s", "findings": [{"title": "t"}]}))

    au = _reload_auto_update(monkeypatch, home)
    actions = au._heal_deep_sleep_runtime(home)

    assert any(a.startswith("checkpoints-purged:") for a in actions)
    assert not poisoned.exists()
    assert good.exists()


def test_heal_releases_stale_locks(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    coord = home / "coordination"
    coord.mkdir(parents=True)
    stale = coord / "sleep.lock"
    stale.write_text("stale")
    old = time.time() - 12 * 3600
    import os
    os.utime(stale, (old, old))

    fresh = coord / "synthesis.lock"
    fresh.write_text("fresh")

    au = _reload_auto_update(monkeypatch, home)
    actions = au._heal_deep_sleep_runtime(home)

    assert any(a.startswith("stale-locks-released:") for a in actions)
    assert not stale.exists()
    assert fresh.exists(), "Locks younger than 6h must survive the heal"


def test_heal_closes_dangling_cron_runs(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    db = home / "data" / "nexo.db"
    _init_cron_runs(db)

    conn = sqlite3.connect(db)
    # Row older than 6h with no ended_at — classic wedged row from the old bug.
    conn.execute(
        "INSERT INTO cron_runs (cron_id, started_at, ended_at) "
        "VALUES ('deep-sleep', datetime('now','-12 hours'), NULL)"
    )
    # Row younger than 6h — legitimate in-flight, must survive the heal.
    conn.execute(
        "INSERT INTO cron_runs (cron_id, started_at, ended_at) "
        "VALUES ('deep-sleep', datetime('now','-10 minutes'), NULL)"
    )
    conn.commit()
    conn.close()

    au = _reload_auto_update(monkeypatch, home)
    actions = au._heal_deep_sleep_runtime(home)
    assert any(a.startswith("cron_runs-closed-dangling:1") for a in actions)

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT started_at, ended_at, exit_code, error FROM cron_runs ORDER BY id"
    ).fetchall()
    conn.close()
    assert rows[0][1] is not None  # old row was closed
    assert rows[0][2] == 143
    assert "pre-5.8.1" in (rows[0][3] or "")
    assert rows[1][1] is None  # fresh in-flight row untouched


def test_heal_resets_old_watchdog_fails_registry(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    scripts = home / "scripts"
    scripts.mkdir(parents=True)
    fails_file = scripts / ".watchdog-fails"
    fails_file.write_text("deep-sleep=9")
    import os
    old = time.time() - 48 * 3600
    os.utime(fails_file, (old, old))

    au = _reload_auto_update(monkeypatch, home)
    actions = au._heal_deep_sleep_runtime(home)
    assert "watchdog-fails-reset" in actions
    assert not fails_file.exists()


def test_heal_swallows_db_errors(tmp_path, monkeypatch):
    """A missing cron_runs table must not crash the heal — other users of
    auto_update may not have migrations applied yet."""
    home = tmp_path / "nexo"
    (home / "data").mkdir(parents=True)
    # Empty db file (no tables)
    sqlite3.connect(home / "data" / "nexo.db").close()

    au = _reload_auto_update(monkeypatch, home)
    actions = au._heal_deep_sleep_runtime(home)
    # Either a clean no-op or a benign warning — never a raise.
    assert isinstance(actions, list)
