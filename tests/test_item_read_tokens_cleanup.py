"""Tests for the opportunistic cleanup of expired item_read_tokens.

Covers the NEXO-AUDIT-2026-04-11 item 9 fix: token issuance drives an
in-band purge of expired rows throttled to once per hour, with failures
swallowed so cleanup never blocks the issue path.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def reminders_mod():
    """Re-import db._reminders for every test so module-level throttle state
    does not leak between tests (the conftest reset_repo_import_state hook
    recycles modules, but any prior call from other suites may have bumped
    _last_read_token_purge to now())."""
    import db._reminders as mod
    mod = importlib.reload(mod)
    mod._last_read_token_purge = 0.0
    return mod


@pytest.fixture
def db_handles():
    """Return fresh (get_db, now_epoch) bindings from db._core."""
    import db._core as core
    return core.get_db, core.now_epoch


def _insert_token(get_db, now_epoch, token: str, item_type: str, item_id: str, expires_offset: float) -> None:
    conn = get_db()
    now = now_epoch()
    conn.execute(
        "INSERT INTO item_read_tokens (token, item_type, item_id, history_seq, issued_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (token, item_type, item_id, 0, now, now + expires_offset),
    )
    conn.commit()


def _count_tokens(get_db) -> int:
    conn = get_db()
    return conn.execute("SELECT COUNT(*) FROM item_read_tokens").fetchone()[0]


def test_purge_expired_read_tokens_removes_only_past_expiration(reminders_mod, db_handles):
    """Expired tokens are deleted; future ones survive."""
    get_db, now_epoch = db_handles

    # Two expired (expires in the past), one alive (expires in the future)
    _insert_token(get_db, now_epoch, "IRT-expired-1", "reminder", "R-1", -10.0)
    _insert_token(get_db, now_epoch, "IRT-expired-2", "followup", "F-1", -3600.0)
    _insert_token(get_db, now_epoch, "IRT-alive-1", "reminder", "R-2", +1800.0)

    assert _count_tokens(get_db) == 3

    conn = get_db()
    reminders_mod._purge_expired_read_tokens_if_due(conn, now=now_epoch())

    remaining = [
        r["token"]
        for r in conn.execute("SELECT token FROM item_read_tokens ORDER BY token").fetchall()
    ]
    assert remaining == ["IRT-alive-1"]


def test_purge_is_throttled_to_once_per_interval(reminders_mod, db_handles):
    """A second call within the throttle window must not re-run the DELETE."""
    get_db, now_epoch = db_handles

    _insert_token(get_db, now_epoch, "IRT-expired-A", "reminder", "R-A", -10.0)
    conn = get_db()
    t0 = now_epoch()

    # First call purges (sets _last_read_token_purge to now)
    reminders_mod._purge_expired_read_tokens_if_due(conn, now=t0)
    assert _count_tokens(get_db) == 0

    # Add a new expired token and call again immediately — the throttle
    # must skip the DELETE so the new token stays.
    _insert_token(get_db, now_epoch, "IRT-expired-B", "reminder", "R-B", -10.0)
    reminders_mod._purge_expired_read_tokens_if_due(conn, now=t0 + 10)
    assert _count_tokens(get_db) == 1

    # After the throttle interval passes (simulated by passing a far-future now),
    # the purge runs again.
    reminders_mod._purge_expired_read_tokens_if_due(
        conn, now=t0 + reminders_mod._READ_TOKEN_PURGE_INTERVAL + 5
    )
    assert _count_tokens(get_db) == 0


def test_issue_token_triggers_cleanup_at_first_call(reminders_mod, db_handles):
    """Issuing a token after startup performs an initial purge of stale rows."""
    get_db, now_epoch = db_handles

    _insert_token(get_db, now_epoch, "IRT-stale-1", "reminder", "R-STALE", -60.0)
    _insert_token(get_db, now_epoch, "IRT-stale-2", "followup", "F-STALE", -60.0)
    assert _count_tokens(get_db) == 2

    # Any issue path triggers the cleanup
    token = reminders_mod._issue_item_read_token("reminder", "R-FRESH")
    assert token.startswith("IRT-")

    conn = get_db()
    rows = conn.execute("SELECT token FROM item_read_tokens ORDER BY token").fetchall()
    remaining = [r["token"] for r in rows]
    # Only the freshly issued token should remain (the 2 stale ones are gone).
    assert remaining == [token]


def test_cleanup_failure_does_not_block_issue(reminders_mod, db_handles):
    """If the DELETE raises, token issuance must still succeed."""
    # db_handles not used here but the fixture still ensures isolated_db ran

    # Monkey-patch the helper to raise — simulating a transient DB error
    original = reminders_mod._purge_expired_read_tokens_if_due

    def boom(_conn, _now):  # noqa: ANN001
        raise RuntimeError("simulated DB glitch")

    reminders_mod._purge_expired_read_tokens_if_due = boom  # type: ignore[assignment]
    try:
        # Should NOT raise — the issue path is resilient
        try:
            token = reminders_mod._issue_item_read_token("reminder", "R-RESILIENT")
        except Exception:
            raise AssertionError("issue path must not propagate cleanup errors")
        assert token.startswith("IRT-")
    finally:
        reminders_mod._purge_expired_read_tokens_if_due = original  # type: ignore[assignment]
