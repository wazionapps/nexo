"""Regression tests for launchagent auto-repair warnings in tools_sessions."""

from __future__ import annotations

import os
import plistlib
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def _write_launchagent_plist(plist_path):
    with plist_path.open("wb") as fh:
        plistlib.dump(
            {"ProgramArguments": ["/Users/tester/.nexo/scripts/watchdog.py"]},
            fh,
        )


def test_check_launchagents_reports_verified_stale_path_repair(tmp_path, monkeypatch):
    import glob
    import platform
    import subprocess
    import tools_sessions

    plist_path = tmp_path / "com.nexo.watchdog.plist"
    _write_launchagent_plist(plist_path)

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1] == "list":
            return SimpleNamespace(
                returncode=0,
                stdout='"ProgramArguments" = (\n"/tmp/stale/watchdog.py"\n);\n',
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(glob, "glob", lambda pattern: [str(plist_path)])
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(os, "getuid", lambda: 501)

    warnings = tools_sessions._check_launchagents()
    assert warnings == [
        "com.nexo.watchdog: AUTO-REPAIRED (was pointing to stale/tmp path, reloaded from disk)"
    ]
    assert [args[1] for args in calls] == ["list", "bootout", "bootstrap"]


def test_check_launchagents_refuses_false_auto_repair_on_bootstrap_failure(tmp_path, monkeypatch):
    import glob
    import platform
    import subprocess
    import tools_sessions

    plist_path = tmp_path / "com.nexo.watchdog.plist"
    _write_launchagent_plist(plist_path)

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1] == "list":
            return SimpleNamespace(
                returncode=0,
                stdout='"ProgramArguments" = (\n"/tmp/stale/watchdog.py"\n);\n',
                stderr="",
            )
        if args[1] == "bootstrap":
            return SimpleNamespace(returncode=1, stdout="", stderr="bootstrap failed")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(glob, "glob", lambda pattern: [str(plist_path)])
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(os, "getuid", lambda: 501)

    warnings = tools_sessions._check_launchagents()
    assert warnings == ["com.nexo.watchdog: REPAIR FAILED — bootstrap failed"]
    assert not any("AUTO-REPAIRED" in warning for warning in warnings)
    assert [args[1] for args in calls] == ["list", "bootout", "bootstrap"]
