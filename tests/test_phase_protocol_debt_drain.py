"""Deep Sleep phase: auto-drain stale protocol_debt rows (Block K G2)."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PHASE_PATH = REPO / "src" / "scripts" / "deep-sleep" / "phase_protocol_debt_drain.py"


def _load_phase():
    spec = importlib.util.spec_from_file_location("phase_protocol_debt_drain", PHASE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


PHASE = _load_phase()


@pytest.fixture
def seeded_db(tmp_path):
    db = tmp_path / "nexo.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE protocol_tasks (
            task_id TEXT PRIMARY KEY,
            status TEXT NOT NULL
        );
        CREATE TABLE protocol_debt (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT DEFAULT '',
            task_id TEXT DEFAULT '',
            debt_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'warn',
            status TEXT NOT NULL DEFAULT 'open',
            evidence TEXT DEFAULT '',
            resolution TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            resolved_at TEXT DEFAULT NULL
        );
        """
    )
    conn.commit()
    conn.close()
    return db


def _seed_debt(db: Path, *, debt_type: str, task_id: str, created_at: str, resolved: bool = False) -> int:
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO protocol_debt (task_id, debt_type, severity, created_at, resolved_at) "
        "VALUES (?, ?, 'error', ?, ?)",
        (task_id, debt_type, created_at, "2020-01-01 00:00:00" if resolved else None),
    )
    conn.commit()
    debt_id = int(cur.lastrowid or 0)
    conn.close()
    return debt_id


def _seed_task(db: Path, task_id: str, status: str) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO protocol_tasks (task_id, status) VALUES (?, ?)",
        (task_id, status),
    )
    conn.commit()
    conn.close()


# --- Pure classifier --------------------------------------------------------

def test_classify_open_task_is_still_valid():
    now = datetime(2026, 4, 22, 12, 0, 0)
    assert (
        PHASE.classify_debt(
            created_at="2026-04-01 00:00:00",
            task_id="PT-x",
            now=now,
            task_open=True,
            stale_age_days=7,
        )
        == "still_valid"
    )


def test_classify_old_closed_task_is_stale():
    now = datetime(2026, 4, 22, 12, 0, 0)
    # 14 days old + task closed ⇒ stale
    assert (
        PHASE.classify_debt(
            created_at="2026-04-08 00:00:00",
            task_id="PT-x",
            now=now,
            task_open=False,
            stale_age_days=7,
        )
        == "stale"
    )


def test_classify_recent_requires_user():
    now = datetime(2026, 4, 22, 12, 0, 0)
    # 2 days old ⇒ requires_user even if task is unknown
    assert (
        PHASE.classify_debt(
            created_at="2026-04-20 00:00:00",
            task_id="",
            now=now,
            task_open=None,
            stale_age_days=7,
        )
        == "requires_user"
    )


def test_classify_unparseable_timestamp_surfaces_for_user():
    now = datetime(2026, 4, 22, 12, 0, 0)
    assert (
        PHASE.classify_debt(
            created_at="not-a-date",
            task_id="",
            now=now,
            task_open=None,
            stale_age_days=7,
        )
        == "requires_user"
    )


# --- run() end-to-end -------------------------------------------------------

def test_run_drains_stale_without_touching_open_or_recent(seeded_db, tmp_path):
    now = datetime(2026, 4, 22, 12, 0, 0)
    # open task → debt must stay
    _seed_task(seeded_db, "PT-open", "open")
    debt_open = _seed_debt(
        seeded_db, debt_type="unacknowledged_guard_blocking",
        task_id="PT-open", created_at="2026-04-01 00:00:00",
    )
    # closed task + old → drain
    _seed_task(seeded_db, "PT-closed", "closed")
    debt_stale = _seed_debt(
        seeded_db, debt_type="missing_cortex_evaluation",
        task_id="PT-closed", created_at="2026-04-05 00:00:00",
    )
    # recent debt, unknown task → requires_user
    debt_recent = _seed_debt(
        seeded_db, debt_type="unacknowledged_guard_blocking",
        task_id="", created_at="2026-04-20 00:00:00",
    )

    report = PHASE.run(
        db_path=seeded_db,
        ops_dir=tmp_path / "ops",
        stale_age_days=7,
        dry_run=False,
        now=now,
    )

    assert report["totals"]["stale"] == 1
    assert report["totals"]["still_valid"] == 1
    assert report["totals"]["requires_user"] == 1
    assert report["drained_ids"] == [debt_stale]

    # Mutation landed on the stale row only.
    with sqlite3.connect(seeded_db) as conn:
        rows = {
            r[0]: (r[1], r[2], r[3])
            for r in conn.execute("SELECT id, resolved_at, resolution, status FROM protocol_debt").fetchall()
        }
    assert rows[debt_stale][0] is not None
    assert rows[debt_stale][1] == PHASE.AUTO_DRAIN_NOTE
    assert rows[debt_stale][2] == "resolved"
    assert rows[debt_open][0] is None
    assert rows[debt_recent][0] is None

    # Audit JSON lands on disk.
    audit_path = Path(report["audit_path"])
    assert audit_path.exists()
    audit = json.loads(audit_path.read_text())
    assert audit["totals"]["stale"] == 1


def test_run_dry_run_leaves_db_intact(seeded_db, tmp_path):
    now = datetime(2026, 4, 22, 12, 0, 0)
    _seed_task(seeded_db, "PT-closed", "closed")
    debt_stale = _seed_debt(
        seeded_db, debt_type="missing_cortex_evaluation",
        task_id="PT-closed", created_at="2026-04-05 00:00:00",
    )

    report = PHASE.run(
        db_path=seeded_db,
        ops_dir=tmp_path / "ops",
        stale_age_days=7,
        dry_run=True,
        now=now,
    )
    assert report["totals"]["stale"] == 1
    with sqlite3.connect(seeded_db) as conn:
        row = conn.execute(
            "SELECT resolved_at FROM protocol_debt WHERE id = ?", (debt_stale,)
        ).fetchone()
    assert row[0] is None  # dry_run left it untouched


def test_run_is_idempotent(seeded_db, tmp_path):
    now = datetime(2026, 4, 22, 12, 0, 0)
    _seed_task(seeded_db, "PT-closed", "closed")
    _seed_debt(
        seeded_db, debt_type="missing_cortex_evaluation",
        task_id="PT-closed", created_at="2026-04-05 00:00:00",
    )
    first = PHASE.run(db_path=seeded_db, ops_dir=tmp_path / "ops",
                      stale_age_days=7, dry_run=False, now=now)
    second = PHASE.run(db_path=seeded_db, ops_dir=tmp_path / "ops",
                       stale_age_days=7, dry_run=False,
                       now=now + timedelta(hours=1))
    assert first["totals"]["stale"] == 1
    assert second["totals"]["stale"] == 0  # already drained


def test_run_missing_db_reports_error(tmp_path):
    report = PHASE.run(
        db_path=tmp_path / "missing.db",
        ops_dir=tmp_path / "ops",
        stale_age_days=7,
        dry_run=False,
        now=datetime(2026, 4, 22),
    )
    assert report["error"] == "db_path_missing"
