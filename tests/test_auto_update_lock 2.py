"""Tests for the auto_update concurrent-run lockfile.

Closes NF-AUDIT-2026-04-11-UPDATE-LOCK. Two NEXO terminals starting at the
same moment after a version bump used to race on auto_update_check(),
running run_migrations(), git pull, and file sync simultaneously and
occasionally tripping UNIQUE constraints on schema_migrations.

This file pins the lock contract:
  - Single holder at a time (POSIX flock LOCK_EX | LOCK_NB).
  - Second concurrent caller returns instantly with skipped_reason
    'locked_by_other_process'.
  - Stale locks (>10 minutes) are auto-stolen so a hard kill mid-update
    never wedges future runs forever.
"""

from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


@pytest.fixture
def lock_env(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    (home / "operations").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    sys.modules.pop("auto_update", None)
    import auto_update

    importlib.reload(auto_update)
    return home, auto_update


# ── Lock acquire / release primitives ─────────────────────────────────────


class TestAcquireReleaseAutoUpdateLock:
    def test_first_acquire_returns_lock_handle(self, lock_env):
        home, auto_update = lock_env
        acquired, fh, reason = auto_update._acquire_auto_update_lock()
        try:
            assert acquired is True
            assert fh is not None
            assert reason == ""
            assert auto_update._AUTO_UPDATE_LOCK_FILE.exists()
        finally:
            auto_update._release_auto_update_lock(fh)

    def test_second_acquire_while_held_fails_fast(self, lock_env):
        home, auto_update = lock_env
        first_acquired, first_fh, _ = auto_update._acquire_auto_update_lock()
        try:
            assert first_acquired is True
            second_acquired, second_fh, second_reason = auto_update._acquire_auto_update_lock()
            assert second_acquired is False
            assert second_fh is None
            assert second_reason == "locked_by_other_process"
        finally:
            auto_update._release_auto_update_lock(first_fh)

    def test_release_lets_next_caller_acquire(self, lock_env):
        home, auto_update = lock_env
        acquired, fh, _ = auto_update._acquire_auto_update_lock()
        assert acquired is True
        auto_update._release_auto_update_lock(fh)
        # After release, a fresh caller must succeed.
        acquired2, fh2, _ = auto_update._acquire_auto_update_lock()
        try:
            assert acquired2 is True
            assert fh2 is not None
        finally:
            auto_update._release_auto_update_lock(fh2)

    def test_lockfile_contains_pid_and_timestamp_after_acquire(self, lock_env):
        home, auto_update = lock_env
        acquired, fh, _ = auto_update._acquire_auto_update_lock()
        try:
            content = auto_update._AUTO_UPDATE_LOCK_FILE.read_text().strip()
            pid_str, ts_str = content.split(":", 1)
            assert int(pid_str) == os.getpid()
            assert float(ts_str) > 0
        finally:
            auto_update._release_auto_update_lock(fh)

    def test_stale_lock_older_than_10_minutes_is_stolen(self, lock_env):
        home, auto_update = lock_env

        # Manually create a stale lockfile and backdate its mtime by 15 minutes.
        auto_update._AUTO_UPDATE_LOCK_FILE.write_text("99999:0\n")
        old_mtime = time.time() - 900  # 15 minutes ago
        os.utime(auto_update._AUTO_UPDATE_LOCK_FILE, (old_mtime, old_mtime))

        acquired, fh, reason = auto_update._acquire_auto_update_lock()
        try:
            assert acquired is True
            assert reason == ""
            # The new content must be ours, not the dead 99999.
            content = auto_update._AUTO_UPDATE_LOCK_FILE.read_text().strip()
            pid_str, _ = content.split(":", 1)
            assert int(pid_str) == os.getpid()
        finally:
            auto_update._release_auto_update_lock(fh)

    def test_release_with_none_handle_is_noop(self, lock_env):
        home, auto_update = lock_env
        # Must not raise.
        auto_update._release_auto_update_lock(None)


# ── auto_update_check skips when locked ──────────────────────────────────


class TestAutoUpdateCheckHonorsLock:
    def test_returns_skipped_reason_when_lock_held_by_other(self, lock_env, monkeypatch):
        home, auto_update = lock_env
        # Hold the lock from a separate file handle so the function under
        # test sees the LOCK_EX state.
        acquired, fh, _ = auto_update._acquire_auto_update_lock()
        try:
            assert acquired is True
            result = auto_update.auto_update_check()
            assert result["checked"] is False
            assert result["skipped_reason"] == "locked_by_other_process"
            assert result["db_migrations"] == 0
            assert result["error"] is None
            assert result["migrations"] == []
        finally:
            auto_update._release_auto_update_lock(fh)

    def test_releases_lock_after_successful_run(self, lock_env, monkeypatch):
        home, auto_update = lock_env

        # Stub out everything the real run would do — we only care that the
        # lock is released, not that the migrations actually execute.
        monkeypatch.setattr(auto_update, "_auto_update_check_locked", lambda: {
            "checked": True,
            "git_update": None,
            "npm_notice": None,
            "claude_md_update": None,
            "client_bootstrap_updates": [],
            "migrations": [],
            "db_migrations": 0,
            "skipped_reason": None,
            "error": None,
        })

        result = auto_update.auto_update_check()
        assert result["checked"] is True

        # After completion, a fresh acquire must succeed (lock released).
        acquired, fh, _ = auto_update._acquire_auto_update_lock()
        try:
            assert acquired is True
        finally:
            auto_update._release_auto_update_lock(fh)

    def test_releases_lock_when_inner_raises(self, lock_env, monkeypatch):
        home, auto_update = lock_env

        def _boom():
            raise RuntimeError("simulated failure inside locked section")

        monkeypatch.setattr(auto_update, "_auto_update_check_locked", _boom)

        with pytest.raises(RuntimeError):
            auto_update.auto_update_check()

        # Lock must still be released so the next caller succeeds.
        acquired, fh, _ = auto_update._acquire_auto_update_lock()
        try:
            assert acquired is True
        finally:
            auto_update._release_auto_update_lock(fh)
