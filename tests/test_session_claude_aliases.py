"""Tests for the multi-claude-sid-per-sid alias table (migration v43).

NEXO Desktop spawns one `claude` CLI subprocess per conversation; each
spawn fires a SessionStart hook with a fresh UUID. The legacy schema
held only one `claude_session_id` per sid, so the PreToolUse hook
blocked edits in every conversation after the first. This suite pins
the fix: the alias table is 1-to-N, every registered UUID resolves to
the same NEXO sid, and re-registration is idempotent.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time

import pytest


sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")),
)


@pytest.fixture(autouse=True)
def alias_runtime(isolated_db, tmp_path, monkeypatch):
    fake_home = tmp_path / "nexo_home"
    (fake_home / "coordination").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))
    for mod in ["hook_guardrails", "tools_sessions"]:
        importlib.reload(importlib.import_module(mod))
    yield


def _new_sid(task="alias test"):
    from tools_sessions import handle_startup
    out = handle_startup(task=task)
    sid_line = [l for l in out.splitlines() if l.startswith("SID: ")][0]
    return sid_line.split("SID: ", 1)[1].strip()


def _register(sid, uuid):
    from hook_guardrails import register_claude_session_alias
    from db import get_db
    return register_claude_session_alias(get_db(), sid, uuid)


def _resolve(uuid):
    from hook_guardrails import _resolve_nexo_sid
    from db import get_db
    return _resolve_nexo_sid(get_db(), uuid)


def test_one_sid_to_many_claude_sids():
    """Three distinct claude UUIDs registered against the same sid all
    resolve back to that sid."""
    sid = _new_sid()
    uuids = [
        "uuid-alpha-1111-2222-3333",
        "uuid-beta-1111-2222-3333",
        "uuid-gamma-1111-2222-3333",
    ]
    for u in uuids:
        assert _register(sid, u) is True
    for u in uuids:
        assert _resolve(u) == sid, f"expected {sid}, got {_resolve(u)}"


def test_register_is_idempotent():
    sid = _new_sid()
    uuid = "uuid-idem-test"
    assert _register(sid, uuid) is True
    ts_before = time.time()
    time.sleep(0.01)
    assert _register(sid, uuid) is True
    from db import get_db
    row = get_db().execute(
        "SELECT first_seen, last_seen FROM session_claude_aliases WHERE sid=? AND claude_session_id=?",
        (sid, uuid),
    ).fetchone()
    assert row is not None
    assert row["first_seen"] < row["last_seen"]  # last_seen bumped
    # Re-registering must NOT create a second row.
    count = get_db().execute(
        "SELECT COUNT(*) FROM session_claude_aliases WHERE sid=? AND claude_session_id=?",
        (sid, uuid),
    ).fetchone()[0]
    assert count == 1


def test_register_rejects_empty_args():
    assert _register("", "uuid-x") is False
    assert _register("sid-y", "") is False
    assert _register("  ", "  ") is False


def test_resolve_falls_back_to_legacy_sessions_column():
    """Rows created before the migration (no alias row) still resolve
    via the legacy sessions.claude_session_id column."""
    from db import get_db, register_session
    sid = "nexo-8888888888-1"
    legacy_uuid = "legacy-uuid-no-alias-row"
    register_session(
        sid, "legacy task",
        claude_session_id=legacy_uuid,
        external_session_id=legacy_uuid,
    )
    # No call to register_claude_session_alias.
    assert _resolve(legacy_uuid) == sid


def test_resolve_prefers_alias_over_legacy_when_both_exist():
    """If a UUID exists in BOTH the alias table (for sid A) and the
    legacy column (for sid B), the alias table wins — it represents
    the operator's explicit multi-sid intent."""
    sid_a = _new_sid(task="alias owner")
    sid_b = "nexo-9999999999-1"
    conflict_uuid = "uuid-conflict-alias-vs-legacy"
    _register(sid_a, conflict_uuid)
    from db import register_session
    register_session(
        sid_b, "legacy claimant",
        claude_session_id=conflict_uuid,
        external_session_id=conflict_uuid,
    )
    assert _resolve(conflict_uuid) == sid_a


def test_handle_startup_registers_alias_automatically():
    """nexo_startup with an explicit session_token writes the alias row
    in addition to updating sessions.claude_session_id — so the first
    PreToolUse hook from that spawn resolves immediately."""
    from tools_sessions import handle_startup
    uuid = "uuid-from-startup-test"
    out = handle_startup(task="startup binds alias", session_token=uuid)
    sid = [l for l in out.splitlines() if l.startswith("SID: ")][0].split("SID: ", 1)[1].strip()
    assert _resolve(uuid) == sid


def test_multi_conversation_scenario_end_to_end():
    """Reproduces the NEXO Desktop bug: three conversations, each with
    its own claude UUID. All three are registered against the same
    NEXO sid via explicit register_claude_session_alias calls. Every
    PreToolUse hook from any of the three conversations must succeed."""
    sid = _new_sid(task="multi-convo desktop")
    claude_uuids = [f"desktop-convo-{i}-uuid" for i in range(3)]
    for u in claude_uuids:
        _register(sid, u)
    # Simulate each conversation's hook: run the resolver with its UUID.
    for u in claude_uuids:
        assert _resolve(u) == sid


def test_resolve_single_active_session_fallback(nexo_runtime=None):
    """v6.0.7 fallback: when the UUID is unknown and exactly one session
    is fresh (heartbeated in the last 5 min), R23e the hook should
    resolve to that session to close the compaction-rotated-UUID gap."""
    # Fresh session via handle_startup — it heartbeats on creation.
    sid = _new_sid(task="single-active fallback")
    # Completely unknown UUID → alias lookup misses, legacy misses,
    # single-active fallback hits.
    unknown = "uuid-completely-unknown-abc123"
    assert _resolve(unknown) == sid


def test_resolve_two_active_sessions_does_not_fallback():
    """With 2+ fresh sessions the single-active fallback is ambiguous
    and MUST fail closed (empty string), not silently pick one."""
    sid_a = _new_sid(task="session A")
    sid_b = _new_sid(task="session B")
    unknown = "uuid-unknown-ambiguous-xyz789"
    assert _resolve(unknown) == ""
