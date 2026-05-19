from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from automation_reconciler import (
    AutomationReconcileConfig,
    apply_reconciliation_plan,
    build_reconciliation_plan,
)


NOW = datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc)


def _write_manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "crons": [
                    {
                        "id": "followup-runner",
                        "interval_seconds": 3600,
                        "run_type": "scheduled",
                        "stuck_after_seconds": 900,
                        "recovery_policy": "catchup",
                        "idempotent": True,
                    },
                    {
                        "id": "manual-report",
                        "schedule": {"hour": 7, "minute": 0},
                        "run_type": "scheduled",
                        "stuck_after_seconds": 300,
                        "recovery_policy": "manual",
                        "idempotent": False,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def _create_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE cron_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cron_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            exit_code INTEGER,
            summary TEXT DEFAULT '',
            error TEXT DEFAULT '',
            duration_secs REAL
        )
        """
    )
    return conn


def _config(tmp_path: Path) -> AutomationReconcileConfig:
    return AutomationReconcileConfig(
        nexo_db_path=tmp_path / "nexo.db",
        manifest_path=tmp_path / "manifest.json",
        cron_spool_dir=tmp_path / "cron-spool",
        cron_spool_archive_dir=tmp_path / "cron-spool-archive",
        now=NOW,
        spool_stale_seconds=60,
    )


def test_reconciliation_plan_classifies_open_runs_and_spool_items(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    spool = tmp_path / "cron-spool"
    spool.mkdir()
    terminal = spool / "followup-runner-terminal.json"
    terminal.write_text(json.dumps({"cron_id": "followup-runner", "status": "completed"}), encoding="utf-8")
    orphan = spool / "unknown-job.json"
    orphan.write_text(json.dumps({"cron_id": "unknown-job"}), encoding="utf-8")
    old_mtime = NOW.timestamp() - 3600
    os.utime(terminal, (old_mtime, old_mtime))
    os.utime(orphan, (old_mtime, old_mtime))

    conn = _create_db(tmp_path / "nexo.db")
    conn.execute(
        "INSERT INTO cron_runs(cron_id, started_at, ended_at, exit_code) VALUES (?, ?, NULL, NULL)",
        ("followup-runner", "2026-05-19 07:00:00"),
    )
    conn.execute(
        "INSERT INTO cron_runs(cron_id, started_at, ended_at, exit_code) VALUES (?, ?, NULL, NULL)",
        ("manual-report", "2026-05-19 07:00:00"),
    )
    conn.commit()
    conn.close()

    plan = build_reconciliation_plan(_config(tmp_path))

    actions = {(item["action"], item.get("classification"), item.get("cron_id")) for item in plan["actions"]}
    assert ("close_cron_run", "retryable", "followup-runner") in actions
    assert ("manual_review_open_run", "stuck", "manual-report") in actions
    assert ("archive_spool_file", "terminal", "followup-runner") in actions
    assert ("manual_review_spool_file", "orphaned", "unknown-job") in actions
    assert plan["summary"]["safe_actions"] == 2


def test_orphaned_terminal_looking_spool_stays_manual(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    spool = tmp_path / "cron-spool"
    spool.mkdir()
    orphan_terminal = spool / "unknown-job.json"
    orphan_terminal.write_text(
        json.dumps({"cron_id": "unknown-job", "status": "completed", "terminal": True}),
        encoding="utf-8",
    )
    old_mtime = NOW.timestamp() - 3600
    os.utime(orphan_terminal, (old_mtime, old_mtime))
    conn = _create_db(tmp_path / "nexo.db")
    conn.close()

    plan = build_reconciliation_plan(_config(tmp_path))

    actions = plan["actions"]
    assert actions == [
        {
            "action": "manual_review_spool_file",
            "safe_apply": False,
            "cron_id": "unknown-job",
            "path": str(orphan_terminal),
            "classification": "orphaned",
            "reason": "spool item does not match a declared non-Evolution cron",
        }
    ]


def test_apply_reconciliation_plan_closes_retryable_runs_and_archives_terminal_spool(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    spool = tmp_path / "cron-spool"
    spool.mkdir()
    terminal = spool / "followup-runner-terminal.json"
    terminal.write_text(json.dumps({"cron_id": "followup-runner", "terminal": True}), encoding="utf-8")
    old_mtime = NOW.timestamp() - 3600
    os.utime(terminal, (old_mtime, old_mtime))

    conn = _create_db(tmp_path / "nexo.db")
    conn.execute(
        "INSERT INTO cron_runs(cron_id, started_at, ended_at, exit_code) VALUES (?, ?, NULL, NULL)",
        ("followup-runner", "2026-05-19 07:00:00"),
    )
    conn.commit()
    conn.close()

    cfg = _config(tmp_path)
    plan = build_reconciliation_plan(cfg)
    result = apply_reconciliation_plan(plan, cfg)

    assert result["ok"] is True
    assert result["summary"]["applied"] == 2
    assert not terminal.exists()
    archived = list((tmp_path / "cron-spool-archive").rglob("followup-runner-terminal.json"))
    assert len(archived) == 1

    conn = sqlite3.connect(str(tmp_path / "nexo.db"))
    ended_at, exit_code, summary = conn.execute(
        "SELECT ended_at, exit_code, summary FROM cron_runs WHERE cron_id = 'followup-runner'"
    ).fetchone()
    conn.close()

    assert ended_at is not None
    assert exit_code == 75
    assert summary == "closed by automation reconciler"


def test_apply_revalidates_cron_run_before_closing(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    (tmp_path / "cron-spool").mkdir()
    conn = _create_db(tmp_path / "nexo.db")
    followup_id = conn.execute(
        "INSERT INTO cron_runs(cron_id, started_at, ended_at, exit_code) VALUES (?, ?, NULL, NULL)",
        ("followup-runner", "2026-05-19 07:00:00"),
    ).lastrowid
    manual_id = conn.execute(
        "INSERT INTO cron_runs(cron_id, started_at, ended_at, exit_code) VALUES (?, ?, NULL, NULL)",
        ("manual-report", "2026-05-19 07:00:00"),
    ).lastrowid
    conn.commit()
    conn.close()

    cfg = _config(tmp_path)
    plan = build_reconciliation_plan(cfg)
    close_action = next(item for item in plan["actions"] if item.get("run_id") == followup_id)
    tampered_plan = {
        **plan,
        "actions": [{**close_action, "run_id": manual_id}],
    }
    result = apply_reconciliation_plan(tampered_plan, cfg)

    assert result["ok"] is False
    assert result["applied"][0]["error"] == "stale_plan_run_changed"
    conn = sqlite3.connect(str(tmp_path / "nexo.db"))
    manual = conn.execute(
        "SELECT ended_at, exit_code FROM cron_runs WHERE id = ?",
        (manual_id,),
    ).fetchone()
    conn.close()
    assert manual == (None, None)


def test_apply_rejects_close_action_without_started_at_evidence(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    (tmp_path / "cron-spool").mkdir()
    conn = _create_db(tmp_path / "nexo.db")
    run_id = conn.execute(
        "INSERT INTO cron_runs(cron_id, started_at, ended_at, exit_code) VALUES (?, ?, NULL, NULL)",
        ("followup-runner", "2026-05-19 07:00:00"),
    ).lastrowid
    conn.commit()
    conn.close()

    cfg = _config(tmp_path)
    action = {
        "action": "close_cron_run",
        "safe_apply": True,
        "run_id": run_id,
        "cron_id": "followup-runner",
        "classification": "retryable",
        "reason": "hand-built missing evidence",
    }
    result = apply_reconciliation_plan({"actions": [action]}, cfg)

    assert result["ok"] is False
    assert result["applied"][0]["error"] == "missing_plan_evidence"


def test_apply_revalidates_spool_file_before_archiving(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    spool = tmp_path / "cron-spool"
    spool.mkdir()
    terminal = spool / "followup-runner-terminal.json"
    terminal.write_text(json.dumps({"cron_id": "followup-runner", "terminal": True}), encoding="utf-8")
    old_mtime = NOW.timestamp() - 3600
    os.utime(terminal, (old_mtime, old_mtime))
    conn = _create_db(tmp_path / "nexo.db")
    conn.close()

    cfg = _config(tmp_path)
    plan = build_reconciliation_plan(cfg)
    archive_action = next(item for item in plan["actions"] if item["action"] == "archive_spool_file")
    terminal.write_text(json.dumps({"cron_id": "followup-runner", "status": "pending"}), encoding="utf-8")
    os.utime(terminal, (old_mtime, old_mtime))
    result = apply_reconciliation_plan({**plan, "actions": [archive_action]}, cfg)

    assert result["ok"] is False
    assert result["applied"][0]["error"] == "stale_plan_spool_changed"
    assert terminal.exists()
    assert not list((tmp_path / "cron-spool-archive").glob("**/*.json"))


def test_apply_rejects_archive_action_without_content_hash(tmp_path):
    _write_manifest(tmp_path / "manifest.json")
    spool = tmp_path / "cron-spool"
    spool.mkdir()
    terminal = spool / "followup-runner-terminal.json"
    terminal.write_text(json.dumps({"cron_id": "followup-runner", "terminal": True}), encoding="utf-8")
    old_mtime = NOW.timestamp() - 3600
    os.utime(terminal, (old_mtime, old_mtime))
    conn = _create_db(tmp_path / "nexo.db")
    conn.close()

    cfg = _config(tmp_path)
    action = {
        "action": "archive_spool_file",
        "safe_apply": True,
        "cron_id": "followup-runner",
        "path": str(terminal),
        "classification": "terminal",
        "reason": "hand-built missing evidence",
    }
    result = apply_reconciliation_plan({"actions": [action]}, cfg)

    assert result["ok"] is False
    assert result["applied"][0]["error"] == "missing_plan_evidence"
    assert terminal.exists()
