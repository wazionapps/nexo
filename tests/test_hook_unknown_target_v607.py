"""Regression tests for v6.0.7 hotfix — strict hook 'unknown target' edge case.

Covers two sides of the correlation:

1. handle_startup without an explicit UUID reads the SessionStart
   coordination file and stamps the row, so later correlation from the
   PreToolUse hook succeeds on claude_session_id alone.

2. _resolve_nexo_sid falls back to the single-active-session when the
   UUID from the payload (or coordination file) does not match any
   session — covering the case where Claude Code rotated its internal
   session_id mid-session without rewriting the coordination file.
"""
from __future__ import annotations

import importlib
import os
import sys
import time

import pytest

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")),
)


@pytest.fixture(autouse=True)
def hook_hotfix_runtime(isolated_db, tmp_path, monkeypatch):
    import db._core as db_core
    import db._sessions as db_sessions
    import db
    import tools_sessions
    import hook_guardrails

    importlib.reload(db_core)
    importlib.reload(db_sessions)
    importlib.reload(db)
    importlib.reload(tools_sessions)
    importlib.reload(hook_guardrails)

    # Fake NEXO_HOME so coordination file is isolated per test
    fake_home = tmp_path / "nexo_home"
    (fake_home / "coordination").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))
    yield fake_home


# ──────────────────────────────────────────────────────────────────────
# Primary fix — handle_startup auto-detect from coordination file
# ──────────────────────────────────────────────────────────────────────


def test_startup_autodetects_uuid_when_caller_forgets(hook_hotfix_runtime):
    from tools_sessions import handle_startup
    from db import get_db
    uuid = "abcdef12-3456-7890-abcd-ef1234567890"
    (hook_hotfix_runtime / "coordination" / ".claude-session-id").write_text(uuid)
    out = handle_startup(task="tests")
    assert "SID:" in out
    sid = out.split("SID:")[1].strip().split()[0]
    row = get_db().execute(
        "SELECT claude_session_id, external_session_id, session_client FROM sessions WHERE sid = ?",
        (sid,),
    ).fetchone()
    assert row is not None
    assert row["claude_session_id"] == uuid
    assert row["external_session_id"] == uuid
    assert row["session_client"] == "claude_code"


def test_startup_explicit_token_takes_precedence(hook_hotfix_runtime):
    from tools_sessions import handle_startup
    from db import get_db
    coord_uuid = "cccc1111-2222-3333-4444-555555555555"
    explicit_uuid = "eeee9999-8888-7777-6666-444444444444"
    (hook_hotfix_runtime / "coordination" / ".claude-session-id").write_text(coord_uuid)
    out = handle_startup(task="tests", session_token=explicit_uuid)
    sid = out.split("SID:")[1].strip().split()[0]
    row = get_db().execute(
        "SELECT claude_session_id FROM sessions WHERE sid = ?", (sid,)
    ).fetchone()
    assert row["claude_session_id"] == explicit_uuid


def test_startup_no_coordination_no_crash(hook_hotfix_runtime):
    """Coordination file missing (non-Claude client, fresh install) still works."""
    from tools_sessions import handle_startup
    from db import get_db
    out = handle_startup(task="tests")
    sid = out.split("SID:")[1].strip().split()[0]
    row = get_db().execute("SELECT claude_session_id FROM sessions WHERE sid = ?", (sid,)).fetchone()
    assert row is not None
    assert row["claude_session_id"] == ""


# ──────────────────────────────────────────────────────────────────────
# Secondary fix — _resolve_nexo_sid single-session fallback
# ──────────────────────────────────────────────────────────────────────


def test_resolve_matches_exact_uuid(hook_hotfix_runtime):
    from hook_guardrails import _resolve_nexo_sid
    from tools_sessions import handle_startup
    from db import get_db
    uuid = "11111111-2222-3333-4444-555555555555"
    (hook_hotfix_runtime / "coordination" / ".claude-session-id").write_text(uuid)
    out = handle_startup(task="tests")
    sid = out.split("SID:")[1].strip().split()[0]
    resolved = _resolve_nexo_sid(get_db(), uuid)
    assert resolved == sid


def test_resolve_falls_back_when_single_recent_session(hook_hotfix_runtime):
    from hook_guardrails import _resolve_nexo_sid
    from tools_sessions import handle_startup
    from db import get_db
    # Start a session without a coordination UUID
    out = handle_startup(task="tests")
    sid = out.split("SID:")[1].strip().split()[0]
    # UUID the payload passes does NOT match anything in the table.
    resolved = _resolve_nexo_sid(get_db(), "totally-unknown-uuid-99999")
    # Single session heartbeated < 5 min ago → fallback attributes to it.
    assert resolved == sid


def test_resolve_blocks_when_multiple_active_sessions(hook_hotfix_runtime):
    """With >1 active session we must NOT guess — fall back to "" (block)."""
    from hook_guardrails import _resolve_nexo_sid
    from tools_sessions import handle_startup
    from db import get_db
    handle_startup(task="session A")
    handle_startup(task="session B")
    resolved = _resolve_nexo_sid(get_db(), "unknown-uuid")
    assert resolved == ""


def test_resolve_blocks_when_session_is_stale(hook_hotfix_runtime):
    """Even with a single session, if it's older than 5 min, do not fall back."""
    from hook_guardrails import _resolve_nexo_sid
    from tools_sessions import handle_startup
    from db import get_db
    out = handle_startup(task="stale")
    sid = out.split("SID:")[1].strip().split()[0]
    # Force last_update_epoch 10 min in the past
    get_db().execute(
        "UPDATE sessions SET last_update_epoch = ? WHERE sid = ?",
        (time.time() - 600, sid),
    )
    get_db().commit()
    resolved = _resolve_nexo_sid(get_db(), "another-unknown-uuid")
    assert resolved == ""


def test_resolve_empty_input_without_sessions(hook_hotfix_runtime):
    """No sessions at all and empty input → blocks."""
    from hook_guardrails import _resolve_nexo_sid
    from db import get_db
    assert _resolve_nexo_sid(get_db(), "") == ""
