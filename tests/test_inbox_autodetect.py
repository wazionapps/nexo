"""v6.0.1 — PostToolUse inbox autodetect.

The hook must emit a reminder when ALL three conditions hold:
  - there is at least one unread message addressed to the session,
  - the last heartbeat is more than the configured threshold (default 60s) old,
  - no reminder has been surfaced for the session inside that window.

Otherwise it returns ``None`` so the hook pipeline stays silent.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Scoped DB redirect without touching sys.modules.

    Points ``db._core.DB_PATH`` at a scratch SQLite file for the
    duration of the test and forces ``get_db()`` to open a fresh
    connection. ``monkeypatch`` reverts both at teardown so the next
    test sees the process-default DB.
    """
    import db._core as _core

    tmp_db = str(tmp_path / "nexo.db")
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    monkeypatch.setenv("NEXO_DB", tmp_db)
    monkeypatch.setenv("NEXO_TEST_DB", tmp_db)
    monkeypatch.setattr(_core, "DB_PATH", tmp_db, raising=False)
    monkeypatch.setattr(_core, "_shared_conn", None, raising=False)

    import db as db_pkg
    db_pkg.init_db()
    try:
        yield tmp_path
    finally:
        # Release the scratch connection so monkeypatch's setattr
        # revert lands on a clean slot; the next test opens its own.
        try:
            _core.close_db()
        except Exception:
            pass


def _register_session(db_pkg, sid: str, last_hb: float | None = None) -> None:
    db_pkg.register_session(sid, "test task")
    if last_hb is not None:
        db_pkg.update_last_heartbeat_ts(sid, last_hb)


def _seed_message(db_pkg, from_sid: str, to_sid: str, text: str) -> str:
    return db_pkg.send_message(from_sid, to_sid, text)


def test_emits_reminder_when_pending_and_stale_heartbeat(isolated_home):
    import db as db_pkg
    from hooks.post_tool_use import check_inbox_and_emit_reminder

    sid = f"nexo-{int(time.time())}-1111"
    other = f"nexo-{int(time.time())}-2222"
    db_pkg.register_session(other, "sender")
    now = time.time()
    _register_session(db_pkg, sid, last_hb=now - 120)

    for i in range(3):
        _seed_message(db_pkg, other, sid, f"hello-{i}")

    reminder = check_inbox_and_emit_reminder(sid, now=now)
    assert reminder is not None
    assert "3 unread" in reminder
    assert "nexo_heartbeat" in reminder


def test_silent_when_heartbeat_is_recent(isolated_home):
    import db as db_pkg
    from hooks.post_tool_use import check_inbox_and_emit_reminder

    sid = f"nexo-{int(time.time())}-3333"
    other = f"nexo-{int(time.time())}-4444"
    db_pkg.register_session(other, "sender")
    now = time.time()
    _register_session(db_pkg, sid, last_hb=now - 30)
    _seed_message(db_pkg, other, sid, "hello")

    assert check_inbox_and_emit_reminder(sid, now=now) is None


def test_silent_when_no_pending_messages(isolated_home):
    import db as db_pkg
    from hooks.post_tool_use import check_inbox_and_emit_reminder

    sid = f"nexo-{int(time.time())}-5555"
    now = time.time()
    _register_session(db_pkg, sid, last_hb=now - 300)

    assert check_inbox_and_emit_reminder(sid, now=now) is None


def test_silent_when_heartbeat_never_recorded(isolated_home):
    import db as db_pkg
    from hooks.post_tool_use import check_inbox_and_emit_reminder

    sid = f"nexo-{int(time.time())}-6666"
    other = f"nexo-{int(time.time())}-7777"
    db_pkg.register_session(other, "sender")
    # Register without stamping last_heartbeat_ts — simulates brand-new session.
    db_pkg.register_session(sid, "task")
    _seed_message(db_pkg, other, sid, "hello")
    now = time.time()

    assert check_inbox_and_emit_reminder(sid, now=now) is None
