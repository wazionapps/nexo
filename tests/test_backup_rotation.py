"""Tests for auto-update backup rotation.

Covers NEXO-AUDIT-2026-04-11 post-fase1 finding: both `pre-autoupdate-*/`
and `runtime-tree-*/` backup directories were accumulating forever under
`$NEXO_HOME/backups/` with no rotation. The fix keeps the N most recent
entries per prefix and silently swallows housekeeping failures so the
auto-update flow can never be interrupted by cleanup problems.
"""

from __future__ import annotations

import importlib
import os
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

    # Create 15 pre-autoupdate dirs with increasing (more recent) mtimes
    created = []
    for i in range(15):
        # Offset ranges from -1400 to 0 seconds so index 14 is the newest
        d = _make_backup_dir(backups_dir, f"pre-autoupdate-{i:02d}", mtime_offset=-1400 + i * 100)
        created.append(d)

    removed = auto_update._rotate_auto_update_backups("pre-autoupdate-", keep=10)
    assert removed == 5

    remaining = sorted(
        p.name for p in backups_dir.iterdir()
        if p.is_dir() and p.name.startswith("pre-autoupdate-")
    )
    # Oldest 5 (00..04) should be gone, newest 10 (05..14) should remain
    assert remaining == [f"pre-autoupdate-{i:02d}" for i in range(5, 15)]


def test_rotate_keeps_newest_n_runtime_tree(isolated_nexo_home):
    auto_update, backups_dir = isolated_nexo_home

    for i in range(12):
        _make_backup_dir(backups_dir, f"runtime-tree-{i:02d}", mtime_offset=-1200 + i * 100)

    removed = auto_update._rotate_auto_update_backups("runtime-tree-", keep=10)
    assert removed == 2

    remaining = sorted(
        p.name for p in backups_dir.iterdir() if p.name.startswith("runtime-tree-")
    )
    assert remaining == [f"runtime-tree-{i:02d}" for i in range(2, 12)]


def test_rotate_noop_when_fewer_than_keep(isolated_nexo_home):
    auto_update, backups_dir = isolated_nexo_home

    for i in range(3):
        _make_backup_dir(backups_dir, f"pre-autoupdate-{i:02d}")

    removed = auto_update._rotate_auto_update_backups("pre-autoupdate-", keep=10)
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

    removed = auto_update._rotate_auto_update_backups("pre-autoupdate-", keep=10)
    assert removed == 2

    # runtime-tree-* and something-else-* must be untouched
    assert sum(1 for p in backups_dir.iterdir() if p.name.startswith("runtime-tree-")) == 5
    assert sum(1 for p in backups_dir.iterdir() if p.name.startswith("something-else-")) == 3


def test_rotate_is_silent_when_base_missing(isolated_nexo_home, tmp_path):
    auto_update, backups_dir = isolated_nexo_home

    # Wipe backups dir entirely and verify no exception surfaces
    import shutil
    shutil.rmtree(str(backups_dir))
    assert not backups_dir.exists()

    removed = auto_update._rotate_auto_update_backups("pre-autoupdate-", keep=10)
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
