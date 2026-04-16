"""Tests for the db_guard module (v5.5.5 data-loss guard)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from db_guard import (
    CRITICAL_TABLES,
    EMPTY_DB_SIZE_BYTES,
    HOURLY_BACKUP_MAX_AGE,
    MIN_REFERENCE_ROWS,
    WIPE_THRESHOLD_PCT,
    TableDiff,
    WipeReport,
    db_looks_wiped,
    db_row_counts,
    diff_row_counts,
    find_latest_hourly_backup,
    safe_sqlite_backup,
    validate_backup_matches_source,
)


def _make_db(path: Path, rows_by_table: dict[str, int]) -> None:
    """Build a SQLite DB with CRITICAL_TABLES and populate the requested rows."""
    conn = sqlite3.connect(str(path))
    try:
        for table in CRITICAL_TABLES:
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, payload TEXT)")
        for table, count in rows_by_table.items():
            for i in range(count):
                conn.execute(
                    f"INSERT INTO {table} (payload) VALUES (?)",
                    (f"row-{i}",),
                )
        conn.commit()
    finally:
        conn.close()


def _make_empty_db(path: Path) -> None:
    """Schema-only SQLite file (~4 KB) — mimics the 4 KB files in the incident."""
    conn = sqlite3.connect(str(path))
    try:
        for table in CRITICAL_TABLES:
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, payload TEXT)")
        conn.commit()
    finally:
        conn.close()


# ── db_row_counts ──────────────────────────────────────────────────────

def test_db_row_counts_populated(tmp_path):
    db = tmp_path / "nexo.db"
    _make_db(db, {"protocol_tasks": 10, "followups": 5, "learnings": 3})
    counts = db_row_counts(db)
    assert counts["protocol_tasks"] == 10
    assert counts["followups"] == 5
    assert counts["learnings"] == 3
    assert counts["reminders"] == 0


def test_db_row_counts_missing_db(tmp_path):
    counts = db_row_counts(tmp_path / "nope.db")
    assert all(v is None for v in counts.values())


def test_db_row_counts_missing_tables(tmp_path):
    db = tmp_path / "schema_less.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE only_this (id INTEGER)")
    conn.commit()
    conn.close()
    counts = db_row_counts(db)
    assert all(v is None for v in counts.values())


# ── db_looks_wiped ─────────────────────────────────────────────────────

def test_db_looks_wiped_empty_schema(tmp_path):
    db = tmp_path / "empty.db"
    _make_empty_db(db)
    assert db_looks_wiped(db) is True


def test_db_looks_wiped_populated(tmp_path):
    db = tmp_path / "full.db"
    _make_db(db, {"protocol_tasks": 500, "followups": 200})
    assert db_looks_wiped(db) is False


def test_db_looks_wiped_missing_returns_false(tmp_path):
    # Missing DB is not "wiped" — it's nothing to protect.
    assert db_looks_wiped(tmp_path / "absent.db") is False


# ── find_latest_hourly_backup ──────────────────────────────────────────

def test_find_latest_hourly_backup_prefers_newest_nonempty(tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    # Empty (4 KB) backup — must be ignored.
    empty_backup = backups / "nexo-2026-04-16-1502.db"
    _make_empty_db(empty_backup)
    time.sleep(0.02)
    # Older but populated — should be chosen because the newer one is empty.
    populated = backups / "nexo-2026-04-16-1402.db"
    _make_db(populated, {"protocol_tasks": 600, "followups": 400})
    # Bump its mtime so it is the newest usable one.
    new_ts = time.time()
    import os
    os.utime(str(populated), (new_ts, new_ts))
    chosen = find_latest_hourly_backup(backups)
    assert chosen == populated


def test_find_latest_hourly_backup_none_when_empty_dir(tmp_path):
    (tmp_path / "backups").mkdir()
    assert find_latest_hourly_backup(tmp_path / "backups") is None


def test_find_latest_hourly_backup_respects_max_age(tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    ancient = backups / "nexo-2020-01-01-0000.db"
    _make_db(ancient, {"protocol_tasks": 600})
    import os
    # Pretend it is 3 days old.
    old_ts = time.time() - 3 * 24 * 3600
    os.utime(str(ancient), (old_ts, old_ts))
    assert find_latest_hourly_backup(backups, max_age_seconds=HOURLY_BACKUP_MAX_AGE) is None


# ── diff_row_counts + WipeReport ───────────────────────────────────────

def test_wipe_report_flags_incident(tmp_path):
    """Reproduces the v5.5.4 incident: 643 tasks -> 0, 442 followups -> 1."""
    current = tmp_path / "current.db"
    reference = tmp_path / "reference.db"
    _make_db(current, {"protocol_tasks": 0, "followups": 1, "reminders": 0, "learnings": 1})
    _make_db(reference, {
        "protocol_tasks": 643,
        "followups": 442,
        "reminders": 40,
        "learnings": 381,
    })
    report = diff_row_counts(current, reference)
    assert report.total_reference_rows == 643 + 442 + 40 + 381
    assert report.total_source_rows == 2
    assert report.overall_lost_pct > 99
    assert report.is_wipe() is True


def test_wipe_report_accepts_legitimate_churn(tmp_path):
    """Small drops should NOT be flagged as wipes."""
    current = tmp_path / "current.db"
    reference = tmp_path / "reference.db"
    _make_db(current, {"protocol_tasks": 550, "followups": 430, "learnings": 370})
    _make_db(reference, {"protocol_tasks": 600, "followups": 450, "learnings": 380})
    report = diff_row_counts(current, reference)
    assert report.overall_lost_pct < WIPE_THRESHOLD_PCT
    assert report.is_wipe() is False


def test_wipe_report_ignores_below_floor_reference(tmp_path):
    """A backup with near-zero rows cannot be used as proof of a wipe."""
    current = tmp_path / "current.db"
    reference = tmp_path / "reference.db"
    _make_db(current, {"protocol_tasks": 0})
    _make_db(reference, {"protocol_tasks": 5})
    report = diff_row_counts(current, reference)
    assert report.is_wipe() is False


def test_wipe_report_fires_on_two_table_regressions(tmp_path):
    """Two individual tables each dropping >80% is enough, even if overall loss is lower."""
    current = tmp_path / "current.db"
    reference = tmp_path / "reference.db"
    _make_db(current, {"protocol_tasks": 0, "followups": 0, "learnings": 1000})
    _make_db(reference, {"protocol_tasks": 100, "followups": 100, "learnings": 100})
    report = diff_row_counts(current, reference)
    # Overall source (1000) > reference total (300), so overall loss is 0%;
    # but two critical tables regressed >= threshold.
    assert report.is_wipe() is True


# ── safe_sqlite_backup + validate_backup_matches_source ────────────────

def test_safe_sqlite_backup_preserves_rows(tmp_path):
    src = tmp_path / "src.db"
    dst = tmp_path / "dst.db"
    _make_db(src, {"protocol_tasks": 500, "followups": 300})
    ok, err = safe_sqlite_backup(src, dst)
    assert ok and err is None
    assert db_row_counts(dst)["protocol_tasks"] == 500
    valid, verr = validate_backup_matches_source(src, dst)
    assert valid and verr is None


def test_validate_backup_detects_missing_rows(tmp_path):
    src = tmp_path / "src.db"
    dst = tmp_path / "dst.db"
    _make_db(src, {"protocol_tasks": 500})
    _make_empty_db(dst)  # Backup got corrupted.
    valid, verr = validate_backup_matches_source(src, dst)
    assert valid is False
    assert "protocol_tasks" in (verr or "")


def test_safe_sqlite_backup_missing_source(tmp_path):
    ok, err = safe_sqlite_backup(tmp_path / "nope.db", tmp_path / "dst.db")
    assert ok is False
    assert "source missing" in (err or "")
