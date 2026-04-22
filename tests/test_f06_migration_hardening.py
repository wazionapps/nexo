"""F0.6 migration hardening tests.

Covers three master checklist items (Brain B2):
1. v6.x → F0.6 full migration with rollback from ``~/.nexo-pre-f06-snapshot``.
2. LaunchAgent rewriter preserves ``StartCalendarInterval`` +
   ``EnvironmentVariables`` in every shape while it patches paths.
3. Half-migration detector (``check_f06_migration_consistency``) rejects
   both flavours of half-migration instead of accepting "new OR legacy"
   indistinctly.

These are hardening tests: they encode *what the contract must guarantee*
so a future refactor of the migrator cannot silently break any of the
three surfaces without a red suite.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import sqlite3
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ---------------------------------------------------------------------------
# 1. v6.x → F0.6 rollback
# ---------------------------------------------------------------------------


def _run_rollback_cli(nexo_home: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["NEXO_HOME"] = str(nexo_home)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "cli", "rollback", "f06", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
    )


def test_v6_to_f06_full_migration_rollback_restores_snapshot(tmp_path):
    # Simulate the pre-F0.6 (v6.x) flat layout as it looked right before the
    # migration landed — scripts/, data/, logs/, operations/, brain/ at the
    # root with real content, and a marker-less install.
    nexo_home = tmp_path / "nexo"
    nexo_home.mkdir()
    pre_f06 = {
        "scripts/nexo-watchdog.sh": "#!/bin/bash\necho v6 watchdog\n",
        "data/nexo.db": "",
        "data/config.json": '{"operator_name": "Francisco"}',
        "logs/watchdog.log": "2026-04-10 boot\n",
        "operations/watchdog-status.json": '{"status": "ok"}',
        "brain/calibration.json": '{"user": {"name": "Francisco"}}',
    }
    for rel, payload in pre_f06.items():
        p = nexo_home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(payload)

    # Simulate the migrator: snapshot first, then lay down the F0.6 skeleton.
    snapshot = Path(str(nexo_home) + "-pre-f06-snapshot")
    shutil.copytree(nexo_home, snapshot)

    # The migrator moves content into core/, personal/, runtime/ and writes
    # the F0.6 marker. We don't need to exercise the real migrator here —
    # the rollback contract only requires that the snapshot is a faithful
    # copy of the pre-migration tree.
    for name in list(pre_f06):
        (nexo_home / name).unlink()
    for canonical in ("core/scripts", "personal/brain", "runtime/data", "runtime/logs", "runtime/operations"):
        (nexo_home / canonical).mkdir(parents=True, exist_ok=True)
    (nexo_home / "core" / "scripts" / "nexo-watchdog.sh").write_text(
        "#!/bin/bash\necho F0.6 watchdog\n"
    )
    (nexo_home / ".structure-version").write_text("F0.6\n")

    # Rollback: must restore every file byte-for-byte from the snapshot.
    proc = _run_rollback_cli(nexo_home, "--yes", "--keep-agents-running", "--json")
    assert proc.returncode == 0, proc.stderr

    # Verify: snapshot is gone (it was renamed into NEXO_HOME), the old
    # pre-F0.6 files are back, and the F0.6 marker is gone.
    assert not snapshot.exists()
    for rel, payload in pre_f06.items():
        restored = nexo_home / rel
        assert restored.is_file(), f"v6 file missing after rollback: {rel}"
        assert restored.read_text() == payload, f"v6 file content drifted: {rel}"
    assert not (nexo_home / ".structure-version").exists()
    # The F0.6 watchdog copy must NOT leak back — it lived in the current
    # tree that got renamed into the rollback-backup, not the snapshot.
    assert not (nexo_home / "core" / "scripts" / "nexo-watchdog.sh").exists()


# ---------------------------------------------------------------------------
# 2. LaunchAgent rewriter preserves StartCalendarInterval + EnvironmentVariables
# ---------------------------------------------------------------------------


def _write_plist(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        plistlib.dump(payload, fh)


@pytest.fixture
def f06_agents_env(tmp_path, monkeypatch):
    """Isolate the home + LaunchAgents dir so the rewriter works on a clean stage."""
    fake_home = tmp_path / "home"
    (fake_home / "Library" / "LaunchAgents").mkdir(parents=True)
    nexo_home = fake_home / ".nexo"
    nexo_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    # Reload auto_update so its module-level NEXO_HOME binds to this fake tree.
    import importlib
    import auto_update
    importlib.reload(auto_update)
    return fake_home, nexo_home, auto_update


def test_rewriter_preserves_start_calendar_interval_dict(f06_agents_env):
    fake_home, nexo_home, auto_update = f06_agents_env
    plist_path = fake_home / "Library" / "LaunchAgents" / "com.nexo.morning-agent.plist"
    _write_plist(plist_path, {
        "Label": "com.nexo.morning-agent",
        "ProgramArguments": [
            "/usr/bin/python3",
            str(nexo_home / "scripts" / "nexo-morning-agent.py"),
        ],
        "StartCalendarInterval": {"Hour": 8, "Minute": 30},
        "EnvironmentVariables": {"NEXO_HOME": str(nexo_home), "PATH": "/usr/bin:/bin"},
        "RunAtLoad": True,
    })

    rewrites = auto_update._rewrite_f06_launch_agents()
    assert rewrites == 1

    with plist_path.open("rb") as fh:
        after = plistlib.load(fh)

    # Path migrated to F0.6 canonical location.
    assert after["ProgramArguments"][1] == str(nexo_home / "core" / "scripts" / "nexo-morning-agent.py")
    # Structure fields preserved verbatim.
    assert after["StartCalendarInterval"] == {"Hour": 8, "Minute": 30}
    assert after["EnvironmentVariables"] == {"NEXO_HOME": str(nexo_home), "PATH": "/usr/bin:/bin"}
    assert after["RunAtLoad"] is True
    assert after["Label"] == "com.nexo.morning-agent"


def test_rewriter_preserves_start_calendar_interval_array(f06_agents_env):
    fake_home, nexo_home, auto_update = f06_agents_env
    plist_path = fake_home / "Library" / "LaunchAgents" / "com.nexo.deep-sleep.plist"
    _write_plist(plist_path, {
        "Label": "com.nexo.deep-sleep",
        "ProgramArguments": [
            "/bin/bash",
            str(nexo_home / "scripts" / "nexo-deep-sleep.sh"),
        ],
        # The array shape (list of dicts) must survive too — real installs
        # use it for crons that fire multiple times per day.
        "StartCalendarInterval": [
            {"Hour": 4, "Minute": 0},
            {"Hour": 16, "Minute": 0},
        ],
        "EnvironmentVariables": {"NEXO_HOME": str(nexo_home)},
        "StandardOutPath": str(nexo_home / "logs" / "deep-sleep.log"),
        "StandardErrorPath": str(nexo_home / "logs" / "deep-sleep.err"),
    })

    rewrites = auto_update._rewrite_f06_launch_agents()
    assert rewrites == 1

    with plist_path.open("rb") as fh:
        after = plistlib.load(fh)

    # Both paths migrated — scripts/ → core/scripts/, logs/ → runtime/logs/.
    assert after["ProgramArguments"][1] == str(nexo_home / "core" / "scripts" / "nexo-deep-sleep.sh")
    assert after["StandardOutPath"] == str(nexo_home / "runtime" / "logs" / "deep-sleep.log")
    assert after["StandardErrorPath"] == str(nexo_home / "runtime" / "logs" / "deep-sleep.err")
    # Array of intervals preserved verbatim.
    assert after["StartCalendarInterval"] == [
        {"Hour": 4, "Minute": 0},
        {"Hour": 16, "Minute": 0},
    ]
    assert after["EnvironmentVariables"] == {"NEXO_HOME": str(nexo_home)}


def test_rewriter_is_noop_when_already_f06(f06_agents_env):
    fake_home, nexo_home, auto_update = f06_agents_env
    plist_path = fake_home / "Library" / "LaunchAgents" / "com.nexo.watchdog.plist"
    # Already using the canonical F0.6 paths; rewriter must not touch it
    # (preserves mtime-sensitive consumers like launchctl that re-read on
    # change).
    _write_plist(plist_path, {
        "Label": "com.nexo.watchdog",
        "ProgramArguments": [
            "/bin/bash",
            str(nexo_home / "core" / "scripts" / "nexo-watchdog.sh"),
        ],
        "StartInterval": 1800,
        "EnvironmentVariables": {"NEXO_HOME": str(nexo_home)},
    })
    before_bytes = plist_path.read_bytes()

    rewrites = auto_update._rewrite_f06_launch_agents()

    assert rewrites == 0
    assert plist_path.read_bytes() == before_bytes


# ---------------------------------------------------------------------------
# 3. check_f06_migration_consistency — half-migration detection
# ---------------------------------------------------------------------------


@pytest.fixture
def nexo_home(tmp_path, monkeypatch):
    """Minimal NEXO_HOME for doctor boot-tier invocation."""
    import json
    home = tmp_path / "nexo"
    for d in ["data", "scripts", "plugins", "crons", "hooks", "coordination", "operations", "logs"]:
        (home / d).mkdir(parents=True)
    (home / "crons" / "manifest.json").write_text(json.dumps({"crons": []}))
    db_path = home / "data" / "nexo.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA user_version = 42")
    conn.close()
    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))

    repo_src = str(SRC)
    if repo_src in sys.path:
        sys.path.remove(repo_src)
    sys.path.insert(0, repo_src)
    for name in list(sys.modules):
        if name == "doctor" or name.startswith("doctor."):
            sys.modules.pop(name, None)

    from doctor.providers import boot
    monkeypatch.setattr(boot, "NEXO_HOME", home)
    return home


def test_f06_consistency_pure_legacy_is_healthy(nexo_home):
    # Pre-F0.6 install: no marker, no core/, legacy runtime dirs populated.
    (nexo_home / "coordination" / "sessions.json").write_text("{}")
    (nexo_home / "data" / "nexo.db").touch()
    from doctor.providers.boot import check_f06_migration_consistency
    check = check_f06_migration_consistency()
    assert check.status == "healthy", check.evidence


def test_f06_consistency_clean_f06_is_healthy(nexo_home):
    # Post-migration install: F0.6 marker, core/ populated, legacy runtime
    # dirs empty or absent (already moved into runtime/).
    (nexo_home / ".structure-version").write_text("F0.6\n")
    (nexo_home / "core").mkdir(parents=True, exist_ok=True)
    (nexo_home / "core" / "scripts").mkdir(parents=True, exist_ok=True)
    (nexo_home / "core" / "scripts" / "marker").write_text("post-f06")
    for legacy in ("coordination", "data", "logs", "operations"):
        shutil.rmtree(nexo_home / legacy, ignore_errors=True)
    from doctor.providers.boot import check_f06_migration_consistency
    check = check_f06_migration_consistency()
    assert check.status == "healthy", (check.summary, check.evidence)


def test_f06_consistency_half_migration_marker_with_legacy_is_critical(nexo_home):
    # Half-migration A: F0.6 marker written but legacy coordination/ still
    # has content. Operators hit this when the migrator aborts mid-move.
    (nexo_home / ".structure-version").write_text("F0.6\n")
    (nexo_home / "core").mkdir(parents=True, exist_ok=True)
    (nexo_home / "coordination" / "sessions.json").write_text("{}")
    (nexo_home / "data" / "nexo.db").touch()
    from doctor.providers.boot import check_f06_migration_consistency
    check = check_f06_migration_consistency()
    assert check.status == "critical"
    assert check.severity == "error"
    assert any("Legacy with content" in ev for ev in check.evidence)
    assert any("nexo update" in plan for plan in check.repair_plan)


def test_f06_consistency_half_migration_core_without_marker_is_critical(nexo_home):
    # Half-migration B: migrator wrote ``core/`` but the final marker
    # write failed. Without the marker, transition-aware helpers assume
    # pre-F0.6 and route operator state into legacy paths.
    (nexo_home / "core").mkdir(parents=True, exist_ok=True)
    (nexo_home / "core" / "scripts").mkdir(parents=True, exist_ok=True)
    (nexo_home / "core" / "scripts" / "marker").write_text("post-migration core")
    # No .structure-version file.
    from doctor.providers.boot import check_f06_migration_consistency
    check = check_f06_migration_consistency()
    assert check.status == "critical"
    assert check.severity == "error"
    assert any("marker absent" in ev or "(absent)" in ev for ev in check.evidence)


def test_f06_consistency_symlink_compat_counts_as_clean(nexo_home):
    # F0.6 marker + legacy NAMES still resolve via compat symlinks into
    # runtime/<name>. Those symlinks MUST be considered healthy, not
    # flagged as half-migration.
    (nexo_home / ".structure-version").write_text("F0.6\n")
    (nexo_home / "core").mkdir(parents=True, exist_ok=True)
    for legacy in ("coordination", "data", "logs", "operations"):
        shutil.rmtree(nexo_home / legacy, ignore_errors=True)
        target = nexo_home / "runtime" / legacy
        target.mkdir(parents=True, exist_ok=True)
        os.symlink(target, nexo_home / legacy)
    from doctor.providers.boot import check_f06_migration_consistency
    check = check_f06_migration_consistency()
    assert check.status == "healthy", (check.summary, check.evidence)
