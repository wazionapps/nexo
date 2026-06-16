from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

from deep_sleep_retention import prune_deep_sleep_runtime


def _write(path: Path, size: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((b"x" * size) or b"x")


def test_deep_sleep_retention_keeps_recent_db_backups_and_analyzed_contexts(tmp_path):
    home = tmp_path / "nexo"
    root = home / "runtime" / "operations" / "deep-sleep"

    for day in range(1, 7):
        run_id = f"2026-05-{day:02d}-050000"
        _write(root / f"{run_id}-backup-nexo.db", 100)
        _write(root / f"{run_id}-backup-cognitive.db", 80)

    for day in range(1, 11):
        run_id = f"2026-05-{day:02d}"
        _write(root / f"{run_id}-context.txt", 50)
        _write(root / run_id / "shared-context.txt", 40)
        if day != 1:
            _write(root / f"{run_id}-synthesis.json", 5)

    dry_run = prune_deep_sleep_runtime(nexo_home=home, apply=False, keep_db_backups=3, keep_contexts=7)
    assert dry_run["deleted_count"] > 0
    assert (root / "2026-05-01-context.txt").exists(), "dry-run must not delete"

    report = prune_deep_sleep_runtime(nexo_home=home, apply=True, keep_db_backups=3, keep_contexts=7)
    assert report["deleted_count"] > 0

    assert len(list(root.glob("*-backup-nexo.db"))) == 3
    assert len(list(root.glob("*-backup-cognitive.db"))) == 3
    assert not (root / "2026-05-02-context.txt").exists()
    assert not (root / "2026-05-02").exists()
    assert (root / "2026-05-01-context.txt").exists(), "unanalyzed context is preserved"
    assert (root / "2026-05-10-context.txt").exists()


def test_deep_sleep_retention_sweeps_orphan_sidecars(tmp_path):
    home = tmp_path / "nexo"
    root = home / "runtime" / "operations" / "deep-sleep"

    # A live backup with its own sidecars (base .db exists) — must be kept.
    _write(root / "2026-05-06-050000-backup-nexo.db", 100)
    _write(root / "2026-05-06-050000-backup-nexo.db-wal", 0)
    _write(root / "2026-05-06-050000-backup-nexo.db-shm", 32)
    # Orphan sidecars whose base .db is gone (interrupted process) — must go.
    _write(root / "2026-04-18-050000-backup-nexo.db-wal", 0)
    _write(root / "2026-04-18-050000-backup-cognitive.db-shm", 32)

    report = prune_deep_sleep_runtime(nexo_home=home, apply=True, keep_db_backups=3, keep_contexts=7)

    assert not (root / "2026-04-18-050000-backup-nexo.db-wal").exists()
    assert not (root / "2026-04-18-050000-backup-cognitive.db-shm").exists()
    # The base .db (within keep) and its sidecars are untouched.
    assert (root / "2026-05-06-050000-backup-nexo.db").exists()
    assert (root / "2026-05-06-050000-backup-nexo.db-wal").exists()
    assert (root / "2026-05-06-050000-backup-nexo.db-shm").exists()
    assert any(d.get("reason") == "orphan-db-sidecar" for d in report.get("deleted", []))


def test_deep_sleep_retention_rotates_deep_sleep_logs(tmp_path):
    home = tmp_path / "nexo"
    log_file = home / "runtime" / "logs" / "deep-sleep.log"
    log_file.parent.mkdir(parents=True)
    log_file.write_text("old line\n" * 2000, encoding="utf-8")
    original = log_file.stat().st_size

    report = prune_deep_sleep_runtime(
        nexo_home=home,
        apply=True,
        max_log_bytes=1024,
        retained_log_bytes=512,
    )

    assert report["logs_rotated"] == 1
    assert report["log_bytes_trimmed"] > 0
    assert log_file.stat().st_size < original
    assert log_file.read_text(encoding="utf-8").startswith("[rotated by NEXO Deep Sleep retention;")


def test_deep_sleep_retention_handles_legacy_operations_layout(tmp_path):
    home = tmp_path / "nexo"
    root = home / "operations" / "deep-sleep"
    for idx in range(5):
        _write(root / f"2026-05-0{idx + 1}-050000-backup-cognitive.db", 10)

    report = prune_deep_sleep_runtime(nexo_home=home, apply=True, keep_db_backups=3)

    assert report["deleted_count"] == 2
    assert len(list(root.glob("*-backup-cognitive.db"))) == 3
