from __future__ import annotations

import sqlite3
import sys
import types
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def test_allow_fresh_db_on_corruption_defaults_false(monkeypatch, isolated_db):
    import server

    monkeypatch.delenv("NEXO_ALLOW_FRESH_DB_ON_CORRUPTION", raising=False)

    assert server._allow_fresh_db_on_corruption() is False


def test_init_db_or_exit_refuses_fresh_db_without_override(monkeypatch, isolated_db):
    import server

    calls: list[str] = []
    quarantined: list[str] = []

    def fake_init_db():
        calls.append("init")
        raise sqlite3.DatabaseError("corrupt")

    monkeypatch.setattr(server, "init_db", fake_init_db)
    monkeypatch.setattr(server, "_restore_valid_db_backup", lambda: False)
    monkeypatch.setattr(server, "_quarantine_corrupt_db_file", lambda db_path: quarantined.append(db_path))
    monkeypatch.setattr(server, "_allow_fresh_db_on_corruption", lambda: False)

    with pytest.raises(SystemExit):
        server._init_db_or_exit()

    assert calls == ["init"]
    assert quarantined


def test_init_db_or_exit_allows_override_for_fresh_db(monkeypatch, isolated_db):
    import server

    calls = {"count": 0}
    quarantined: list[str] = []

    def fake_init_db():
        calls["count"] += 1
        if calls["count"] == 1:
            raise sqlite3.DatabaseError("corrupt")

    monkeypatch.setattr(server, "init_db", fake_init_db)
    monkeypatch.setattr(server, "_restore_valid_db_backup", lambda: False)
    monkeypatch.setattr(server, "_quarantine_corrupt_db_file", lambda db_path: quarantined.append(db_path))
    monkeypatch.setattr(server, "_allow_fresh_db_on_corruption", lambda: True)

    server._init_db_or_exit()

    assert calls["count"] == 2
    assert quarantined


def test_run_startup_preflight_sync_emits_messages(monkeypatch, capsys, isolated_db):
    import server

    fake_auto_update = types.SimpleNamespace(
        startup_preflight=lambda **kwargs: {
            "updated": True,
            "git_update": "Auto-updated: 5.3.27 -> 5.3.28",
            "client_bootstrap_updates": ["Codex bootstrap refreshed"],
            "migrations": [{"file": "m41.py", "status": "failed", "message": "boom"}],
        }
    )
    monkeypatch.setitem(sys.modules, "auto_update", fake_auto_update)

    server._run_startup_preflight_sync()

    stderr = capsys.readouterr().err
    assert "Startup update applied." in stderr
    assert "Auto-updated: 5.3.27 -> 5.3.28" in stderr
    assert "Codex bootstrap refreshed" in stderr
    assert "Migration m41.py FAILED: boom" in stderr
