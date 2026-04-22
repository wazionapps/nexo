"""Tests for ``nexo rollback f06``.

Contract:
- refuses if ``~/.nexo-pre-f06-snapshot`` is absent (exit 1, status=error_no_snapshot).
- dry-run does not mutate the filesystem.
- real swap moves current home to a dated rollback-backup and the snapshot
  into place, never overwriting data in a single move.
- LaunchAgents bootout/load are skipped when ``--keep-agents-running``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _run_cli(nexo_home: Path, *args: str) -> subprocess.CompletedProcess:
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


def test_rollback_f06_errors_when_snapshot_missing(tmp_path):
    nexo_home = tmp_path / "nexo"
    nexo_home.mkdir()
    proc = _run_cli(nexo_home, "--dry-run", "--json")
    assert proc.returncode == 1, proc.stderr
    report = json.loads(proc.stdout)
    assert report["status"] == "error_no_snapshot"
    assert report["snapshot_exists"] is False


def test_rollback_f06_dry_run_does_not_mutate(tmp_path):
    nexo_home = tmp_path / "nexo"
    nexo_home.mkdir()
    (nexo_home / "marker").write_text("active")
    snapshot = Path(str(nexo_home) + "-pre-f06-snapshot")
    snapshot.mkdir()
    (snapshot / "marker").write_text("snapshot")

    proc = _run_cli(nexo_home, "--dry-run", "--json", "--keep-agents-running")
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["status"] == "dry_run"
    # Neither filesystem side was touched.
    assert (nexo_home / "marker").read_text() == "active"
    assert (snapshot / "marker").read_text() == "snapshot"
    # Plan should include the two atomic renames.
    plan_steps = {step["step"] for step in report["steps"]}
    assert {"move_current_nexo_home_to_backup", "move_snapshot_to_nexo_home"}.issubset(plan_steps)


def test_rollback_f06_real_swap_preserves_prior_home(tmp_path):
    nexo_home = tmp_path / "nexo"
    nexo_home.mkdir()
    (nexo_home / "marker").write_text("active")
    snapshot = Path(str(nexo_home) + "-pre-f06-snapshot")
    snapshot.mkdir()
    (snapshot / "marker").write_text("snapshot")

    proc = _run_cli(nexo_home, "--yes", "--keep-agents-running", "--json")
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["status"] == "done"

    # Restored content comes from the snapshot.
    assert (nexo_home / "marker").read_text() == "snapshot"

    # Prior home is preserved in a dated rollback-backup directory, not dropped.
    backup_target = Path(report["backup_target"])
    assert backup_target.is_dir()
    assert (backup_target / "marker").read_text() == "active"
    # The snapshot path itself no longer exists because it was renamed.
    assert not snapshot.exists()


def test_rollback_f06_non_interactive_requires_yes(tmp_path):
    nexo_home = tmp_path / "nexo"
    nexo_home.mkdir()
    snapshot = Path(str(nexo_home) + "-pre-f06-snapshot")
    snapshot.mkdir()

    # No --yes, no TTY → should refuse instead of silently prompting.
    proc = _run_cli(nexo_home, "--json", "--keep-agents-running")
    assert proc.returncode == 1, proc.stdout
    assert "interactive confirmation required" in proc.stderr

    # The snapshot and home remain untouched.
    assert nexo_home.is_dir()
    assert snapshot.is_dir()
