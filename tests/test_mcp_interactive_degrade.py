from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _locked() -> sqlite3.OperationalError:
    return sqlite3.OperationalError("database is locked")


def test_startup_returns_sid_when_interactive_db_is_busy(monkeypatch, isolated_db):
    import tools_sessions

    monkeypatch.setattr(tools_sessions, "clean_stale_sessions", lambda: (_ for _ in ()).throw(_locked()))
    monkeypatch.setattr(tools_sessions, "register_session", lambda *args, **kwargs: (_ for _ in ()).throw(_locked()))
    monkeypatch.setattr(tools_sessions, "get_active_sessions", lambda: (_ for _ in ()).throw(_locked()))
    monkeypatch.setattr(tools_sessions, "get_inbox", lambda sid: (_ for _ in ()).throw(_locked()))

    started = time.monotonic()
    rendered = tools_sessions.handle_startup("probe")
    elapsed = time.monotonic() - started

    assert rendered.startswith("SID: nexo-")
    assert "STARTUP DEGRADED:" in rendered
    assert "database is busy" in rendered
    assert elapsed < 2.0


def test_heartbeat_degrades_when_session_update_is_busy(monkeypatch, isolated_db):
    import tools_sessions

    monkeypatch.setattr(tools_sessions, "update_session", lambda *args, **kwargs: (_ for _ in ()).throw(_locked()))

    rendered = tools_sessions.handle_heartbeat("nexo-1778880000-12345", "probe", "quick check")

    assert "OK: nexo-1778880000-12345 — probe" in rendered
    assert "HEARTBEAT DEGRADED:" in rendered
    assert "database is busy" in rendered


def test_heartbeat_does_not_inject_local_context_by_default(monkeypatch, isolated_db):
    import tools_sessions
    import db

    sid = "nexo-1778880000-54321"
    db.register_session(sid, "boot")
    monkeypatch.delenv("NEXO_HEARTBEAT_LOCAL_CONTEXT", raising=False)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("local context should not run during heartbeat by default")

    monkeypatch.setattr(tools_sessions, "append_local_context_evidence", fail_if_called)

    rendered = tools_sessions.handle_heartbeat(sid, "probe", "leebmann24 context")

    assert "OK: nexo-1778880000-54321 — probe" in rendered
    assert "LOCAL CONTEXT EVIDENCE" not in rendered
