"""Tests for v7.11.2 enforcer restart-required gate.

The Guardian/Enforcer (`HeadlessEnforcer` in `enforcement_engine.py`)
periodically injects `<system-reminder>` blocks asking the agent to call
`nexo_*` tools (heartbeat, diary, smart_startup, guard_check, ...). When
the MCP server has a `mcp-restart-required.json` marker on disk (written
by `plugins/update.py` after a `nexo update` that actually changes
runtime `.py` bytes — see v7.11.0 fingerprint gating), every one of
those reminders triggers a tool call that immediately fails with
`mcp_restart_required`. The agent burns cycles on guaranteed no-ops
until the operator restarts the client.

v7.11.2 adds a gate at the top of `_enqueue()`: if the prompt mentions
`nexo_` and the marker file exists, skip + log. Reminders that don't
ask for `nexo_*` tools (R23 deploy guards, R25 nora/maria read-only,
etc) still fire — they don't depend on the MCP being live.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def fresh_enforcer(tmp_path, monkeypatch):
    """A HeadlessEnforcer pointing at a tmp NEXO_HOME with no marker file.

    Tests that need a marker create it explicitly under runtime/operations/.
    """
    home = tmp_path / "nexo"
    (home / "runtime" / "operations").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    import importlib
    import enforcement_engine
    importlib.reload(enforcement_engine)
    enforcer = enforcement_engine.HeadlessEnforcer()
    return home, enforcer


def test_enqueue_passes_nexo_prompt_when_marker_absent(fresh_enforcer):
    """Sanity: with no marker on disk, a nexo_* prompt enqueues normally.
    Otherwise the gate is over-eager."""
    home, enforcer = fresh_enforcer
    assert not (home / "runtime" / "operations" / "mcp-restart-required.json").exists()
    enforcer._enqueue(
        "Execute nexo_session_diary_write with a summary.",
        tag="periodic_msgs:nexo_session_diary_write",
        rule_id="periodic_diary",
    )
    assert len(enforcer.injection_queue) == 1
    assert enforcer.injection_queue[0]["tag"] == "periodic_msgs:nexo_session_diary_write"


def test_enqueue_skips_nexo_prompt_when_marker_present(fresh_enforcer):
    """The actual fix: with the marker on disk, a nexo_* prompt is skipped
    and the queue stays empty."""
    home, enforcer = fresh_enforcer
    marker = home / "runtime" / "operations" / "mcp-restart-required.json"
    marker.write_text('{"reason": "test"}')
    enforcer._enqueue(
        "Execute nexo_smart_startup to pre-load context.",
        tag="start:nexo_smart_startup",
        rule_id="periodic_smart_startup",
    )
    assert enforcer.injection_queue == [], (
        "_enqueue must skip nexo_* prompts when mcp-restart-required marker exists"
    )


def test_enqueue_passes_non_nexo_prompt_even_with_marker(fresh_enforcer):
    """A reminder that doesn't ask for nexo_* tools (R23 deploy guards,
    R25 nora/maria read-only, etc) must still fire even when the MCP is
    in restart_required mode — those rules don't depend on the MCP."""
    home, enforcer = fresh_enforcer
    marker = home / "runtime" / "operations" / "mcp-restart-required.json"
    marker.write_text('{"reason": "test"}')
    enforcer._enqueue(
        "[NEXO Protocol Enforcer] R25 gate: do not write to nora's inbox without explicit permit token.",
        tag="R25_nora_maria_read_only",
        rule_id="R25_nora_maria_read_only",
    )
    assert len(enforcer.injection_queue) == 1, (
        "non-nexo_* reminders must still fire even with marker present"
    )


def test_marker_pending_cache_ttl_30s(fresh_enforcer):
    """The pending check is cached per-instance with a 30s TTL so we don't
    stat the marker on every _enqueue call. Verify both the warm cache
    path and that the cache eventually re-reads after expiry."""
    home, enforcer = fresh_enforcer
    # No marker → first call returns False and primes the cache.
    assert enforcer._mcp_restart_pending() is False
    cached_at = enforcer._mcp_restart_pending_cache_at
    assert cached_at > 0
    # Create the marker AFTER the cache was primed; within the TTL the
    # cache still returns False (this is the documented trade-off).
    marker = home / "runtime" / "operations" / "mcp-restart-required.json"
    marker.write_text('{"reason": "test"}')
    assert enforcer._mcp_restart_pending() is False, "warm cache should still return False inside TTL"
    # Force the cache to look stale (older than 30s) and verify it
    # re-reads and now sees the marker.
    enforcer._mcp_restart_pending_cache_at = time.time() - 31.0
    assert enforcer._mcp_restart_pending() is True


def test_marker_pending_handles_legacy_path_layout(tmp_path, monkeypatch):
    """Pre-F0.6 installs may have the marker under operations/ instead of
    runtime/operations/. The resolver must find both."""
    home = tmp_path / "nexo"
    (home / "operations").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    import importlib
    import enforcement_engine
    importlib.reload(enforcement_engine)
    enforcer = enforcement_engine.HeadlessEnforcer()
    legacy_marker = home / "operations" / "mcp-restart-required.json"
    legacy_marker.write_text('{"reason": "legacy test"}')
    assert enforcer._mcp_restart_pending() is True


def test_marker_pending_safe_when_nexo_home_missing(tmp_path, monkeypatch):
    """If NEXO_HOME points at a directory that doesn't exist, the gate
    must return False (no marker) instead of raising — the enforcer
    should never block on path errors."""
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "does-not-exist"))
    import importlib
    import enforcement_engine
    importlib.reload(enforcement_engine)
    enforcer = enforcement_engine.HeadlessEnforcer()
    assert enforcer._mcp_restart_pending() is False
