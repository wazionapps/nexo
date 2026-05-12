"""Tests for the 7.19.0 release: bundle-managed updates kill-switch and drift autoexit."""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path

import pytest


@pytest.fixture
def brain_env(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo_home"
    (nexo_home / "data").mkdir(parents=True)
    (nexo_home / "backups").mkdir(parents=True)
    (nexo_home / "operations").mkdir(parents=True)
    (nexo_home / "runtime" / "config").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.delenv("NEXO_DISABLE_AUTO_HEAL", raising=False)
    return nexo_home


def test_auto_update_env_var_disables_check(brain_env, monkeypatch):
    monkeypatch.setenv("NEXO_BRAIN_AUTO_UPDATE", "false")
    import auto_update as au
    importlib.reload(au)
    monkeypatch.setattr(
        au,
        "_resolve_sync_source",
        lambda: (Path(brain_env), Path(brain_env)),
    )
    monkeypatch.setattr(au, "_run_db_migrations", lambda: None)
    monkeypatch.setattr(au, "run_file_migrations", lambda: [])
    monkeypatch.setattr(au, "_sync_client_bootstraps", lambda: [])
    monkeypatch.setattr(au, "_sync_watchdog_hash_registry", lambda: None)
    monkeypatch.setattr(au, "_warn_protected_runtime_location", lambda: None)
    monkeypatch.setattr(au, "_ensure_runtime_cli_wrapper", lambda: None)
    monkeypatch.setattr(au, "_ensure_runtime_cli_in_shell", lambda: None)
    from runtime_power import (
        apply_power_policy,
        ensure_power_policy_choice,
        ensure_full_disk_access_choice,
    )
    monkeypatch.setattr(
        au, "ensure_power_policy_choice",
        lambda interactive=False, reason="": {"policy": "balanced"},
        raising=False,
    )

    def _fake_init_db(*args, **kwargs):
        return None

    monkeypatch.setitem(sys.modules, "db", type(sys)("db"))
    sys.modules["db"].init_db = _fake_init_db

    fake_script_registry = type(sys)("script_registry")
    fake_script_registry.reconcile_personal_scripts = lambda dry_run=False: {}
    monkeypatch.setitem(sys.modules, "script_registry", fake_script_registry)

    monkeypatch.setattr(
        au,
        "_personal_schedule_reconcile_summary",
        lambda result: ([], ""),
        raising=False,
    )
    monkeypatch.setattr(au, "_write_update_summary", lambda *a, **k: None)

    result = au.startup_preflight(entrypoint="test", interactive=False)
    assert result["skipped_reason"] == (
        "auto_update disabled via NEXO_BRAIN_AUTO_UPDATE env var"
    )
    assert result["updated"] is False
    assert result["checked"] is False


def test_auto_update_env_var_accepts_off_zero_no(monkeypatch, brain_env):
    for value in ("0", "off", "no", "FALSE", "False"):
        monkeypatch.setenv("NEXO_BRAIN_AUTO_UPDATE", value)
        env_value = os.environ.get("NEXO_BRAIN_AUTO_UPDATE", "").strip().lower()
        assert env_value in ("0", "false", "off", "no")


def test_drift_autoexit_idempotent_schedule(monkeypatch):
    import runtime_versioning as rv
    importlib.reload(rv)

    called = []

    def fake_exit(code=0):
        called.append(code)

    monkeypatch.setattr(rv.os, "_exit", fake_exit)

    rv._DRIFT_AUTOEXIT_SCHEDULED = False

    async def run_schedule_in_loop():
        rv._schedule_drift_autoexit()
        rv._schedule_drift_autoexit()
        await asyncio.sleep(rv._DRIFT_EXIT_DELAY_SECONDS + 0.2)

    asyncio.run(run_schedule_in_loop())

    assert called == [rv._DRIFT_EXIT_CODE], (
        f"Expected exactly one os._exit({rv._DRIFT_EXIT_CODE}) call; got {called}"
    )


def test_drift_autoexit_immediate_when_no_loop(monkeypatch):
    import runtime_versioning as rv
    importlib.reload(rv)

    called = []

    def fake_exit(code=0):
        called.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(rv.os, "_exit", fake_exit)

    rv._DRIFT_AUTOEXIT_SCHEDULED = False

    with pytest.raises(SystemExit) as excinfo:
        rv._schedule_drift_autoexit()
    assert excinfo.value.code == rv._DRIFT_EXIT_CODE
    assert called and called[0] == rv._DRIFT_EXIT_CODE
