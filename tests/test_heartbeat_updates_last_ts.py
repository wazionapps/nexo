"""v6.0.1 — nexo_heartbeat stamps sessions.last_heartbeat_ts.

Verifies that after a successful heartbeat the stored timestamp is
within one second of ``time.time()``.
"""
from __future__ import annotations

import sys
import time
import json
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


def test_handle_heartbeat_stamps_last_heartbeat_ts(isolated_home):
    import db as db_pkg
    from tools_sessions import handle_heartbeat

    sid = f"nexo-{int(time.time())}-7001"
    db_pkg.register_session(sid, "boot")

    before = time.time()
    handle_heartbeat(sid, "doing work")
    after = time.time()

    stamped = db_pkg.get_last_heartbeat_ts(sid)
    assert stamped is not None
    # Allow a generous one-second window to absorb test overhead.
    assert before - 1.0 <= stamped <= after + 1.0


def test_update_last_heartbeat_ts_noop_on_missing_session(isolated_home):
    import db as db_pkg

    # Must not raise, even for an unknown SID.
    db_pkg.update_last_heartbeat_ts(f"nexo-{int(time.time())}-9999")
    assert db_pkg.get_last_heartbeat_ts(f"nexo-{int(time.time())}-9999") is None


def test_session_compliance_state_reports_missing_and_satisfied_obligations(isolated_home):
    import db as db_pkg
    from tools_sessions import handle_session_compliance_state

    sid = f"nexo-{int(time.time())}-7010"
    db_pkg.register_session(sid, "compliance")
    missing = json.loads(handle_session_compliance_state(sid, diary_window_minutes=15))

    assert missing["ok"] is True
    assert missing["obligations"]["heartbeat_missing"] is True
    assert missing["obligations"]["clean_close_blocked"] is True

    db_pkg.update_last_heartbeat_ts(sid)
    db_pkg.write_session_diary(
        sid,
        decisions="[]",
        summary="Emergency close diary",
        discarded="",
        pending="",
        context_next="",
        mental_state="",
        source="desktop-lifecycle-fallback",
    )
    satisfied = json.loads(handle_session_compliance_state(sid, diary_window_minutes=15))

    assert satisfied["heartbeat"]["recorded"] is True
    assert satisfied["diary"]["close_ok"] is True
    assert satisfied["obligations"]["clean_close_blocked"] is False


def test_session_compliance_state_blocks_learning_when_correction_open(isolated_home):
    import db as db_pkg
    from tools_sessions import handle_session_compliance_state

    sid = f"nexo-{int(time.time())}-7011"
    db_pkg.register_session(sid, "correction")
    db_pkg.update_last_heartbeat_ts(sid)
    db_pkg.write_session_diary(
        sid,
        decisions="[]",
        summary="Diary exists",
        discarded="",
        pending="",
        context_next="",
        mental_state="",
    )
    db_pkg.record_session_correction_requirement(
        sid,
        "eso esta mal, hay que corregir la regla",
        source="test",
    )

    state = json.loads(handle_session_compliance_state(sid, diary_window_minutes=15))
    assert state["learning"]["open_correction_requirements"] == 1
    assert state["obligations"]["learning_required"] is True
    assert state["obligations"]["clean_close_blocked"] is True
