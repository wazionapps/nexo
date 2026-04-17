"""v6.0.1 — smoke E2E: session B on autopilot receives an inbox reminder.

Mirrors the scenario the hotfix exists to solve:
  1. Session A sends B a message via ``nexo_send``.
  2. Session B goes 65s without a heartbeat while still running tools.
  3. The PostToolUse hook on B must surface the reminder.
"""
from __future__ import annotations

import json
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


def test_session_b_gets_reminder_after_65s_without_heartbeat(isolated_home):
    import db as db_pkg
    from hooks.post_tool_use import check_inbox_and_emit_reminder

    sid_a = f"nexo-{int(time.time())}-8001"
    sid_b = f"nexo-{int(time.time())}-8002"

    db_pkg.register_session(sid_a, "sender")
    db_pkg.register_session(sid_b, "receiver")

    # B just heartbeated — baseline.
    now = time.time()
    db_pkg.update_last_heartbeat_ts(sid_b, now - 65)

    # A sends a message to B.
    db_pkg.send_message(sid_a, sid_b, "please take over")

    reminder = check_inbox_and_emit_reminder(sid_b, now=now)
    assert reminder is not None

    # The hook prints the reminder as a JSON line so Claude Code
    # surfaces it as a systemMessage. Simulate that shape here.
    payload = json.dumps({"systemMessage": reminder})
    decoded = json.loads(payload)
    assert "systemMessage" in decoded
    assert "1 unread" in decoded["systemMessage"]
