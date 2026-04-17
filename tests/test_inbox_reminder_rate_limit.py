"""v6.0.1 — PostToolUse inbox reminder rate limit (1/min/sid).

The first emission writes to ``hook_inbox_reminders``; a second call
inside the threshold window must return None; a third call past the
window must emit and update the row in place.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
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
        try:
            _core.close_db()
        except Exception:
            pass


def test_rate_limit_first_emit_second_silent_third_emit(isolated_home):
    import db as db_pkg
    from hooks.post_tool_use import check_inbox_and_emit_reminder

    sid = f"nexo-{int(time.time())}-1000"
    other = f"nexo-{int(time.time())}-2000"
    db_pkg.register_session(other, "sender")
    db_pkg.register_session(sid, "receiver")

    base = time.time()
    db_pkg.update_last_heartbeat_ts(sid, base - 120)  # stale heartbeat
    db_pkg.send_message(other, sid, "first message")

    first = check_inbox_and_emit_reminder(sid, now=base)
    assert first is not None, "first call must emit"
    assert db_pkg.get_last_reminder_ts(sid) is not None

    # 10s later with same payload → rate-limited.
    second = check_inbox_and_emit_reminder(sid, now=base + 10)
    assert second is None, "rate limit must suppress second emit"

    # 70s later — past the 60s threshold. Inject a fresh message (the
    # previous one has been marked read by get_inbox via other paths
    # in the real runtime). Also move the heartbeat backwards so it
    # is still stale relative to the new ``now``.
    db_pkg.update_last_heartbeat_ts(sid, base - 50)
    db_pkg.send_message(other, sid, "second message")

    third = check_inbox_and_emit_reminder(sid, now=base + 70)
    assert third is not None, "post-threshold call must emit again"

    # Row updated in place (still one row per sid).
    count = db_pkg.get_db().execute(
        "SELECT COUNT(*) FROM hook_inbox_reminders WHERE sid = ?", (sid,)
    ).fetchone()[0]
    assert count == 1
