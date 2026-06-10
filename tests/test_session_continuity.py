"""Phase 2.1 — session continuity (SPEC-FIABILIDAD-FASES-2026-06 §2.1).

Incident evidence (10-jun): a working session that spends >15 minutes in
code tools without touching a nexo_* tool was PHYSICALLY DELETED by the next
session/cron that started (SESSION_STALE_SECONDS=900 + hard DELETE), so its
next nexo_track failed with "Session not found. Register first." and its open
protocol tasks were orphaned. Meanwhile update_session (heartbeat) already
revived missing sessions — the layer was internally inconsistent.

Contract pinned here:
- a syntactically valid SID is NEVER told "not found": it revives.
- stale cleanup only purges sessions older than the PURGE horizon (24h),
  while the visible "active sessions" semantics keep the short TTL.
"""

import importlib

import pytest


@pytest.fixture()
def sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    import db._core as core
    import db._sessions as mod
    importlib.reload(core)
    importlib.reload(mod)
    yield mod, core
    importlib.reload(core)
    importlib.reload(mod)


def test_track_files_revives_a_missing_valid_sid(sessions):
    mod, _core = sessions
    sid = "nexo-1781050796-99621"

    result = mod.track_files(sid, ["/tmp/some/file.py"])

    assert "error" not in result, f"valid SID must revive, got: {result}"
    assert result.get("revived") is True
    assert result["tracked"] == ["/tmp/some/file.py"]
    active = {row["sid"] for row in mod.get_active_sessions()}
    assert sid in active, "revived session must be alive again"


def test_track_files_still_rejects_garbage_sids(sessions):
    mod, _core = sessions
    with pytest.raises(ValueError):
        mod.track_files("definitely-not-a-sid'; DROP TABLE sessions;--", ["/tmp/x"])


def test_clean_stale_sessions_keeps_recent_but_inactive_sessions(sessions):
    mod, core = sessions
    sid = "nexo-1781000000-11111"
    mod.register_session(sid, "long build, no nexo tools for a while")

    # Simulate 1 hour of tool silence: stale for the ACTIVE listing (15 min)
    # but far from the 24h purge horizon.
    conn = core.get_db()
    one_hour_ago = core.now_epoch() - 3600
    conn.execute("UPDATE sessions SET last_update_epoch = ? WHERE sid = ?", (one_hour_ago, sid))
    conn.commit()

    removed = mod.clean_stale_sessions()

    survivors = conn.execute("SELECT sid FROM sessions WHERE sid = ?", (sid,)).fetchone()
    assert survivors is not None, f"1h-quiet session must survive cleanup (removed={removed})"
    active = {row["sid"] for row in mod.get_active_sessions()}
    assert sid not in active, "but it must NOT be listed as active (visible TTL unchanged)"


def test_clean_stale_sessions_purges_beyond_horizon(sessions):
    mod, core = sessions
    sid = "nexo-1780000000-22222"
    mod.register_session(sid, "ancient session")
    conn = core.get_db()
    two_days_ago = core.now_epoch() - 2 * 24 * 3600
    conn.execute("UPDATE sessions SET last_update_epoch = ? WHERE sid = ?", (two_days_ago, sid))
    conn.commit()

    mod.clean_stale_sessions()

    row = conn.execute("SELECT sid FROM sessions WHERE sid = ?", (sid,)).fetchone()
    assert row is None, "sessions beyond the purge horizon are still removed"


def test_heartbeat_revival_contract_still_holds(sessions):
    mod, _core = sessions
    sid = "nexo-1781999999-33333"
    result = mod.update_session(sid, "revived by heartbeat")
    assert result["sid"] == sid
    active = {row["sid"] for row in mod.get_active_sessions()}
    assert sid in active
