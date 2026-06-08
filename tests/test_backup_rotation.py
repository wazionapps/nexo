"""Tests for auto-update backup rotation.

Covers technical rollback snapshots that can grow quickly under
`$NEXO_HOME/runtime/backups/`. The fix keeps the N most recent entries per
prefix and silently swallows housekeeping failures so update/backfill flows
can never be interrupted by cleanup problems.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture
def isolated_nexo_home(tmp_path, monkeypatch):
    """Point NEXO_HOME at an empty temp directory for the duration of the test.

    The auto_update module reads NEXO_HOME at import time, so we re-import
    the module under the patched environment."""
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()

    monkeypatch.setenv("NEXO_HOME", str(tmp_path))

    import auto_update
    mod = importlib.reload(auto_update)
    return mod, backups_dir


def _make_backup_dir(base: Path, name: str, mtime_offset: int = 0) -> Path:
    d = base / name
    d.mkdir()
    (d / "marker").write_text("ok")
    if mtime_offset:
        t = time.time() + mtime_offset
        os.utime(str(d), (t, t))
    return d


def test_rotate_keeps_newest_n_pre_autoupdate(isolated_nexo_home):
    auto_update, backups_dir = isolated_nexo_home

    # Create 15 pre-autoupdate dirs with increasing (more recent) mtimes.
    for i in range(15):
        # Offset ranges from -1400 to 0 seconds so index 14 is the newest
        _make_backup_dir(backups_dir, f"pre-autoupdate-{i:02d}", mtime_offset=-1400 + i * 100)

    removed = auto_update._rotate_auto_update_backups("pre-autoupdate-", keep=5)
    assert removed == 10

    remaining = sorted(
        p.name for p in backups_dir.iterdir()
        if p.is_dir() and p.name.startswith("pre-autoupdate-")
    )
    # Oldest 10 (00..09) should be gone, newest 5 (10..14) should remain.
    assert remaining == [f"pre-autoupdate-{i:02d}" for i in range(10, 15)]


def test_rotate_keeps_newest_n_runtime_tree(isolated_nexo_home):
    auto_update, backups_dir = isolated_nexo_home

    for i in range(12):
        _make_backup_dir(backups_dir, f"runtime-tree-{i:02d}", mtime_offset=-1200 + i * 100)

    removed = auto_update._rotate_auto_update_backups("runtime-tree-", keep=5)
    assert removed == 7

    remaining = sorted(
        p.name for p in backups_dir.iterdir() if p.name.startswith("runtime-tree-")
    )
    assert remaining == [f"runtime-tree-{i:02d}" for i in range(7, 12)]


def test_rotate_noop_when_fewer_than_keep(isolated_nexo_home):
    auto_update, backups_dir = isolated_nexo_home

    for i in range(3):
        _make_backup_dir(backups_dir, f"pre-autoupdate-{i:02d}")

    removed = auto_update._rotate_auto_update_backups("pre-autoupdate-", keep=5)
    assert removed == 0

    remaining = sorted(p.name for p in backups_dir.iterdir())
    assert len(remaining) == 3


def test_rotate_only_touches_matching_prefix(isolated_nexo_home):
    auto_update, backups_dir = isolated_nexo_home

    # Mix prefixes: 12 pre-autoupdate + 5 runtime-tree + 3 other
    for i in range(12):
        _make_backup_dir(backups_dir, f"pre-autoupdate-{i:02d}", mtime_offset=-1200 + i * 100)
    for i in range(5):
        _make_backup_dir(backups_dir, f"runtime-tree-{i:02d}")
    for i in range(3):
        _make_backup_dir(backups_dir, f"something-else-{i:02d}")

    removed = auto_update._rotate_auto_update_backups("pre-autoupdate-", keep=5)
    assert removed == 7

    # runtime-tree-* and something-else-* must be untouched
    assert sum(1 for p in backups_dir.iterdir() if p.name.startswith("runtime-tree-")) == 5
    assert sum(1 for p in backups_dir.iterdir() if p.name.startswith("something-else-")) == 3


def test_rotate_is_silent_when_base_missing(isolated_nexo_home, tmp_path):
    auto_update, backups_dir = isolated_nexo_home

    # Wipe backups dir entirely and verify no exception surfaces
    import shutil
    shutil.rmtree(str(backups_dir))
    assert not backups_dir.exists()

    removed = auto_update._rotate_auto_update_backups("pre-autoupdate-", keep=5)
    assert removed == 0


def test_rotate_zero_keep_is_noop(isolated_nexo_home):
    """Safety: keep<=0 should not wipe all backups — it's a defensive no-op
    so a buggy caller can never nuke the entire backup history."""
    auto_update, backups_dir = isolated_nexo_home

    for i in range(5):
        _make_backup_dir(backups_dir, f"pre-autoupdate-{i:02d}")

    removed = auto_update._rotate_auto_update_backups("pre-autoupdate-", keep=0)
    assert removed == 0
    assert len(list(backups_dir.iterdir())) == 5


def test_manual_update_rotates_pre_update_backups(tmp_path, monkeypatch):
    from plugins import update

    backup_base = tmp_path / "backups"
    backup_base.mkdir()
    monkeypatch.setattr(update, "BACKUP_BASE", backup_base)

    for i in range(9):
        _make_backup_dir(backup_base, f"pre-update-{i:02d}", mtime_offset=-900 + i * 100)
    _make_backup_dir(backup_base, "shopify-backups")

    removed = update._rotate_backup_family("pre-update-")
    assert removed == 4
    remaining = sorted(p.name for p in backup_base.iterdir() if p.name.startswith("pre-update-"))
    assert remaining == [f"pre-update-{i:02d}" for i in range(4, 9)]
    assert (backup_base / "shopify-backups").is_dir()


def test_manual_update_rotates_code_tree_backups(tmp_path, monkeypatch):
    from plugins import update

    backup_base = tmp_path / "backups"
    backup_base.mkdir()
    monkeypatch.setattr(update, "BACKUP_BASE", backup_base)

    for i in range(8):
        _make_backup_dir(backup_base, f"code-tree-{i:02d}", mtime_offset=-800 + i * 100)

    removed = update._rotate_backup_family("code-tree-")
    assert removed == 3
    remaining = sorted(p.name for p in backup_base.iterdir() if p.name.startswith("code-tree-"))
    assert remaining == [f"code-tree-{i:02d}" for i in range(3, 8)]


def test_backfill_owner_rotates_own_backup_family(tmp_path):
    from scripts import backfill_task_owner

    backup_base = tmp_path / "backups"
    backup_base.mkdir()
    for i in range(11):
        _make_backup_dir(backup_base, f"pre-backfill-owner-{i:02d}", mtime_offset=-1100 + i * 100)
    _make_backup_dir(backup_base, "pre-update-untouched")

    removed = backfill_task_owner._rotate_backup_family(backup_base)
    assert removed == 6
    remaining = sorted(p.name for p in backup_base.iterdir() if p.name.startswith("pre-backfill-owner-"))
    assert remaining == [f"pre-backfill-owner-{i:02d}" for i in range(6, 11)]
    assert (backup_base / "pre-update-untouched").is_dir()


def test_pruner_self_heals_local_context_artifacts_under_hard_cap(tmp_path):
    backup_base = tmp_path / "backups"
    backup_base.mkdir()
    script = Path(__file__).resolve().parent.parent / "src" / "scripts" / "prune_runtime_backups.py"

    protected = backup_base / "shopify-backups"
    protected.mkdir()
    (protected / "keep.txt").write_text("business")

    old_local = backup_base / "local-context-2026-05-18-1622.db"
    old_local.write_bytes(b"x" * 90)
    new_local = backup_base / "local-context-2026-05-19-1829.db"
    new_local.write_bytes(b"x" * 90)
    orphan_tmp = backup_base / "local-context-2026-05-19-1829.db.tmp.1310"
    orphan_tmp.write_bytes(b"x" * 90)
    orphan_wal = backup_base / "nexo-2026-05-19-1829.db-wal"
    orphan_wal.write_bytes(b"x" * 90)

    now = time.time()
    os.utime(old_local, (now - 3000, now - 3000))
    os.utime(new_local, (now - 100, now - 100))
    os.utime(orphan_tmp, (now - 3000, now - 3000))
    os.utime(orphan_wal, (now - 3000, now - 3000))

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            str(backup_base),
            "--apply",
            "--max-bytes",
            "150",
            "--local-context-keep",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert protected.is_dir()
    assert new_local.is_file()
    assert not old_local.exists()
    assert not orphan_tmp.exists()
    assert not orphan_wal.exists()


def test_pruner_rotates_hourly_db_artifacts_under_hard_cap(tmp_path):
    backup_base = tmp_path / "backups"
    backup_base.mkdir()
    script = Path(__file__).resolve().parent.parent / "src" / "scripts" / "prune_runtime_backups.py"

    protected = backup_base / "shopify-backups"
    protected.mkdir()
    (protected / "keep.txt").write_text("business")
    weekly = backup_base / "weekly"
    weekly.mkdir()
    (weekly / "weekly-2026-W23.db").write_bytes(b"w" * 1024)

    for hour in range(24):
        backup = backup_base / f"nexo-2026-06-07-{hour:02d}00.db"
        backup.write_bytes(b"x" * 1024 * 1024)

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            str(backup_base),
            "--apply",
            "--max-bytes",
            "5M",
            "--hourly-keep",
            "3",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert protected.is_dir()
    assert weekly.is_dir()
    remaining = sorted(path.name for path in backup_base.glob("nexo-*.db"))
    assert remaining == [
        "nexo-2026-06-07-2100.db",
        "nexo-2026-06-07-2200.db",
        "nexo-2026-06-07-2300.db",
    ]
