"""Ola 1 — atomic flock lock for the followup runner (fixes PID-file TOCTOU race
that let two concurrent runners both acquire and both spend LLM budget)."""

from __future__ import annotations

import fcntl
import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "src" / "scripts" / "nexo-followup-runner.py"


def _load_runner(monkeypatch, tmp_path: Path):
    nexo_home = tmp_path / "nexo-home"
    for sub in ("runtime/coordination", "runtime/operations", "runtime/memory", "personal/brain"):
        (nexo_home / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setenv("NEXO_CODE", str(ROOT / "src"))
    for name in ("followup_runner_test_module", "paths"):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location("followup_runner_test_module", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "LOCK_FILE", tmp_path / "followup-runner.lock")
    module._LOCK_FH = None
    module._LOCK_RELEASED = False
    return module


def test_first_acquire_creates_lock_and_stamps_pid(monkeypatch, tmp_path):
    mod = _load_runner(monkeypatch, tmp_path)
    assert mod.acquire_lock() is True
    assert mod.LOCK_FILE.exists()
    assert mod._LOCK_FH is not None
    assert mod.LOCK_FILE.read_text().split(":", 1)[0] == str(os.getpid())
    mod.release_lock()


def test_module_flock_blocks_a_second_open(monkeypatch, tmp_path):
    """Proves the lock is a real kernel flock: a second open of the same path
    cannot acquire while the module holds it (the TOCTOU race is gone)."""
    mod = _load_runner(monkeypatch, tmp_path)
    assert mod.acquire_lock() is True
    other = open(mod.LOCK_FILE, "a+")
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        other.close()
        mod.release_lock()


def test_live_external_holder_blocks_module_acquire(monkeypatch, tmp_path):
    mod = _load_runner(monkeypatch, tmp_path)
    holder = open(mod.LOCK_FILE, "a+")
    holder.write(f"{os.getpid()}:{time.time()}\n")  # live pid → not stale
    holder.flush()
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        t0 = time.time()
        assert mod.acquire_lock() is False
        assert time.time() - t0 < 0.5  # LOCK_NB: returns instantly, never blocks
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()
    assert mod.acquire_lock() is True
    mod.release_lock()


def test_release_lets_next_acquire_and_is_idempotent(monkeypatch, tmp_path):
    mod = _load_runner(monkeypatch, tmp_path)
    assert mod.acquire_lock() is True
    mod.release_lock()
    mod.release_lock()  # idempotent (main() calls it at 1145 + finally 1152)
    assert mod.acquire_lock() is True
    mod.release_lock()


def test_stale_dead_pid_lock_is_reclaimed(monkeypatch, tmp_path):
    mod = _load_runner(monkeypatch, tmp_path)
    mod.LOCK_FILE.write_text("2147483646:0\n")  # pid that os.kill → ProcessLookupError
    assert mod.acquire_lock() is True
    assert mod.LOCK_FILE.read_text().split(":", 1)[0] == str(os.getpid())
    mod.release_lock()


def test_legacy_bare_int_dead_lock_is_reclaimed(monkeypatch, tmp_path):
    mod = _load_runner(monkeypatch, tmp_path)
    mod.LOCK_FILE.write_text("2147483646")  # old bare-int format, dead pid
    assert mod.acquire_lock() is True
    mod.release_lock()


def test_stale_by_old_mtime_is_reclaimed(monkeypatch, tmp_path):
    mod = _load_runner(monkeypatch, tmp_path)
    mod.LOCK_FILE.write_text(f"{os.getpid()}:0\n")  # our pid (alive) but...
    old = time.time() - mod.FOLLOWUP_LOCK_STALE_SECONDS - 60
    os.utime(mod.LOCK_FILE, (old, old))  # ...mtime older than the stale window
    assert mod.acquire_lock() is True
    mod.release_lock()
