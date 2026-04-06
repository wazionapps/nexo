from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


@pytest.fixture
def watcher_env(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    for rel in ("data", "operations", "crons"):
        (home / rel).mkdir(parents=True, exist_ok=True)

    db_path = home / "data" / "nexo.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS state_watchers (
            watcher_id TEXT PRIMARY KEY,
            watcher_type TEXT NOT NULL,
            title TEXT NOT NULL,
            target TEXT DEFAULT '',
            severity TEXT NOT NULL DEFAULT 'warn',
            status TEXT NOT NULL DEFAULT 'active',
            config TEXT DEFAULT '{}',
            last_health TEXT NOT NULL DEFAULT 'unknown',
            last_result TEXT DEFAULT '{}',
            last_checked_at TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cron_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cron_id TEXT NOT NULL,
            started_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    conn.close()

    (home / "crons" / "manifest.json").write_text(json.dumps({"crons": [{"id": "watchdog", "interval_seconds": 1800}]}))

    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_DB", str(db_path))
    for name in ["db._core", "db._watchers", "db", "state_watchers_runtime"]:
        sys.modules.pop(name, None)

    import db
    import state_watchers_runtime

    importlib.reload(db)
    importlib.reload(state_watchers_runtime)
    return home, db, state_watchers_runtime


def test_expiry_watcher_persists_summary(watcher_env):
    home, db, runtime = watcher_env
    db.create_state_watcher(
        "expiry",
        "SSL cert",
        config={"due_at": (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d"), "warn_days": 14, "critical_days": 5},
    )
    summary = runtime.run_state_watchers()

    assert summary["counts"]["critical"] == 1
    payload = json.loads((home / "operations" / "state-watchers-status.json").read_text())
    assert payload["watcher_count"] == 1
    assert payload["watchers"][0]["watcher_type"] == "expiry"


def test_repo_drift_watcher_detects_dirty_repo(watcher_env):
    _home, db, runtime = watcher_env
    repo = Path(_home) / "repo"
    repo.mkdir()
    os.system(f"git -C {repo} init -q")
    (repo / "README.md").write_text("dirty\n")

    db.create_state_watcher("repo_drift", "Repo drift", target=str(repo))
    summary = runtime.run_state_watchers()
    assert summary["counts"]["degraded"] == 1
    assert "uncommitted drift" in summary["watchers"][0]["summary"]


def test_api_health_watcher_marks_bad_status_critical(watcher_env, monkeypatch):
    _home, db, runtime = watcher_env

    class _Resp:
        status = 503

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(runtime.request, "urlopen", lambda req, timeout=0: _Resp())
    db.create_state_watcher("api_health", "API", target="https://example.com/health")
    summary = runtime.run_state_watchers()
    assert summary["counts"]["critical"] == 1
    assert summary["watchers"][0]["watcher_type"] == "api_health"
