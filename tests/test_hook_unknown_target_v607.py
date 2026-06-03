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
import json
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
        "SELECT claude_session_id, external_session_id, session_client, session_provider FROM sessions WHERE sid = ?",
        (sid,),
    ).fetchone()
    assert row is not None
    assert row["claude_session_id"] == uuid
    assert row["external_session_id"] == uuid
    assert row["session_client"] == "claude_code"
    assert row["session_provider"] == "anthropic"


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


def test_startup_records_openai_provider_for_codex_sessions(hook_hotfix_runtime):
    from tools_sessions import handle_startup
    from db import get_db

    out = handle_startup(task="tests", session_token="codex-session-1", session_client="codex")
    sid = out.split("SID:")[1].strip().split()[0]
    row = get_db().execute(
        "SELECT external_session_id, session_client, session_provider FROM sessions WHERE sid = ?",
        (sid,),
    ).fetchone()

    assert row["external_session_id"] == "codex-session-1"
    assert row["session_client"] == "codex"
    assert row["session_provider"] == "openai"


def test_startup_no_coordination_no_crash(hook_hotfix_runtime):
    """Coordination file missing (non-Claude client, fresh install) still works."""
    from tools_sessions import handle_startup
    from db import get_db
    out = handle_startup(task="tests")
    sid = out.split("SID:")[1].strip().split()[0]
    row = get_db().execute("SELECT claude_session_id FROM sessions WHERE sid = ?", (sid,)).fetchone()
    assert row is not None
    assert row["claude_session_id"] == ""


def test_startup_includes_session_briefing_excerpt_when_present(hook_hotfix_runtime):
    from tools_sessions import handle_startup

    briefing = hook_hotfix_runtime / "coordination" / "session-briefing.txt"
    briefing.write_text(
        "Top priority: reconcile pending release notes\n"
        "Check the stale launchagent warnings\n"
        "Avoid touching runtime core directly\n",
        encoding="utf-8",
    )

    out = handle_startup(task="tests")

    assert "SESSION BRIEFING:" in out
    assert "Top priority: reconcile pending release notes" in out
    assert "Full briefing:" in out


def test_startup_includes_sleep_health_warning_when_failed(hook_hotfix_runtime):
    from tools_sessions import handle_startup

    health = hook_hotfix_runtime / "coordination" / "sleep-health.json"
    health.write_text(
        json.dumps(
            {
                "date": "2026-06-03",
                "status": "failed",
                "error": "coverage only saw 41/100 learnings",
                "coverage": {
                    "learnings_visible_count": 41,
                    "learnings_total_declared": 100,
                    "coverage_pct": 41.0,
                },
            }
        ),
        encoding="utf-8",
    )

    out = handle_startup(task="tests")

    assert "SLEEP HEALTH:" in out
    assert "status=failed date=2026-06-03" in out
    assert "coverage=41/100 (41.0%)" in out
    assert "Full health:" in out


def test_startup_includes_latest_deep_sleep_context(hook_hotfix_runtime):
    from tools_sessions import handle_startup

    deep_sleep = hook_hotfix_runtime / "runtime" / "operations" / "deep-sleep"
    deep_sleep.mkdir(parents=True, exist_ok=True)
    (deep_sleep / "2026-06-03-synthesis.json").write_text(
        json.dumps(
            {
                "date": "2026-06-03",
                "summary": "NEXO should continue release validation before touching runtime installs.",
                "morning_agenda": [
                    {
                        "priority": "P0",
                        "title": "Validate release gates",
                        "description": "Run doctor, parity, and packaging checks.",
                    }
                ],
                "context_packets": [
                    {
                        "topic": "Desktop release",
                        "last_state": "Packaging passed; Windows smoke still pending.",
                        "key_files": ["src/auto_update.py", "tests/test_runtime_update_contract.py"],
                    }
                ],
                "actions": [
                    {
                        "action_class": "draft_for_morning",
                        "content": {"title": "Decide whether to cut the next release."},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    out = handle_startup(task="tests")

    assert "DEEP SLEEP CONTEXT:" in out
    assert "date=2026-06-03" in out
    assert "summary=NEXO should continue release validation" in out
    assert "agenda[P0]=Validate release gates" in out
    assert "context=Desktop release: Packaging passed" in out
    assert "files=src/auto_update.py, tests/test_runtime_update_contract.py" in out
    assert "review=Decide whether to cut the next release." in out
    assert "Full synthesis:" in out


def test_startup_prefers_deep_sleep_agent_start_packet(hook_hotfix_runtime):
    from tools_sessions import handle_startup

    deep_sleep = hook_hotfix_runtime / "runtime" / "operations" / "deep-sleep"
    deep_sleep.mkdir(parents=True, exist_ok=True)
    (deep_sleep / "2026-06-03-synthesis.json").write_text(
        json.dumps({"date": "2026-06-03", "summary": "stale synthesis"}),
        encoding="utf-8",
    )
    (deep_sleep / "2026-06-03-agent-start-packet.json").write_text(
        json.dumps(
            {
                "date": "2026-06-03",
                "summary": "packet summary",
                "agenda": [{"priority": "P1", "title": "Use packet", "description": "Prefer compact handoff."}],
                "context_packets": [{"topic": "Packet topic", "last_state": "Ready."}],
                "review_items": [{"title": "Review packet item"}],
            }
        ),
        encoding="utf-8",
    )

    out = handle_startup(task="tests")

    assert "DEEP SLEEP CONTEXT:" in out
    assert "summary=packet summary" in out
    assert "agenda[P1]=Use packet" in out
    assert "context=Packet topic: Ready." in out
    assert "review=Review packet item" in out
    assert "stale synthesis" not in out


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
