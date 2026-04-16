"""Regression tests for auto_update._purge_zero_byte_db_files.

Interrupted installs leave 0-byte nexo.db orphans in NEXO_HOME or its data/
subdir. These orphans break backup validation: they look like an empty
source to _validate_db_backup and mask the real DB in
_find_primary_db_path. The purge helper must remove them before the
backup runs, and must never touch non-empty .db files.
"""

from __future__ import annotations

import sys
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_auto_update(monkeypatch, home: Path):
    """Reload auto_update against a tmp NEXO_HOME so module-level paths pick
    up the fixture directory instead of Francisco's real ~/.nexo."""
    import importlib
    monkeypatch.setenv("NEXO_HOME", str(home))
    import auto_update as au
    importlib.reload(au)
    return au


def test_purge_removes_zero_byte_db_in_nexo_home(tmp_path, monkeypatch):
    home = tmp_path / "nexo_home"
    (home / "data").mkdir(parents=True)
    orphan = home / "nexo.db"
    orphan.touch()  # 0 bytes
    assert orphan.stat().st_size == 0

    au = _reload_auto_update(monkeypatch, home)
    removed = au._purge_zero_byte_db_files()

    assert orphan in removed
    assert not orphan.exists()


def test_purge_removes_zero_byte_db_in_data_subdir(tmp_path, monkeypatch):
    home = tmp_path / "nexo_home"
    data = home / "data"
    data.mkdir(parents=True)
    orphan = data / "nexo.db"
    orphan.touch()

    au = _reload_auto_update(monkeypatch, home)
    removed = au._purge_zero_byte_db_files()

    assert orphan in removed
    assert not orphan.exists()


def test_purge_keeps_non_empty_db(tmp_path, monkeypatch):
    home = tmp_path / "nexo_home"
    data = home / "data"
    data.mkdir(parents=True)
    good = data / "nexo.db"
    conn = sqlite3.connect(str(good))
    try:
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
    finally:
        conn.close()
    assert good.stat().st_size > 0
    size_before = good.stat().st_size

    au = _reload_auto_update(monkeypatch, home)
    removed = au._purge_zero_byte_db_files()

    assert removed == []
    assert good.exists()
    assert good.stat().st_size == size_before


def test_purge_is_noop_when_no_db_files(tmp_path, monkeypatch):
    home = tmp_path / "nexo_home"
    (home / "data").mkdir(parents=True)

    au = _reload_auto_update(monkeypatch, home)
    removed = au._purge_zero_byte_db_files()

    assert removed == []


def test_backup_dbs_skips_zero_byte_orphan(tmp_path, monkeypatch):
    """End-to-end: _backup_dbs must not copy a 0-byte orphan into the backup."""
    home = tmp_path / "nexo_home"
    data = home / "data"
    data.mkdir(parents=True)
    (home / "backups").mkdir()

    orphan = home / "nexo.db"
    orphan.touch()
    real = data / "nexo.db"
    conn = sqlite3.connect(str(real))
    try:
        conn.execute("CREATE TABLE learnings (id INTEGER)")
        conn.commit()
    finally:
        conn.close()

    au = _reload_auto_update(monkeypatch, home)
    backup_dir = au._backup_dbs()

    assert backup_dir is not None
    backup_path = Path(backup_dir)
    # The orphan has been purged from disk and must NOT appear in the backup.
    assert not orphan.exists()
    # The real DB IS in the backup.
    assert (backup_path / "nexo.db").is_file()
    assert (backup_path / "nexo.db").stat().st_size > 0
