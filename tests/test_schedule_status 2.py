import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def test_schedule_status_marks_keep_alive_daemon_as_active(monkeypatch):
    from plugins import schedule

    monkeypatch.setattr(
        schedule,
        "cron_runs_summary",
        lambda hours: [{
            "cron_id": "wake-recovery",
            "succeeded": 0,
            "total_runs": 1,
            "avg_duration": None,
            "last_summary": "",
            "last_exit_code": None,
        }],
    )
    monkeypatch.setattr(
        schedule,
        "get_personal_script_schedule",
        lambda cron_id: {"schedule_type": "keep_alive"} if cron_id == "wake-recovery" else {},
    )

    output = schedule.handle_schedule_status(hours=24)

    assert "🟢 wake-recovery" in output
    assert "daemon active" in output


def test_schedule_status_marks_exit_zero_with_warnings_as_warning(monkeypatch):
    from plugins import schedule

    monkeypatch.setattr(
        schedule,
        "cron_runs_summary",
        lambda hours: [{
            "cron_id": "orchestrator-v2",
            "succeeded": 3,
            "total_runs": 3,
            "avg_duration": 2.0,
            "last_summary": "Cron sync warning: missing optional file",
            "last_exit_code": 0,
        }],
    )
    monkeypatch.setattr(schedule, "get_personal_script_schedule", lambda cron_id: {})

    output = schedule.handle_schedule_status(hours=24)

    assert "⚠ orchestrator-v2" in output
    assert "exit 0 with warnings" in output


def test_schedule_status_marks_self_audit_findings_as_warning(monkeypatch):
    from plugins import schedule

    monkeypatch.setattr(
        schedule,
        "cron_runs_summary",
        lambda hours: [{
            "cron_id": "self-audit",
            "succeeded": 1,
            "total_runs": 1,
            "avg_duration": 12.0,
            "last_summary": "Self-audit completed with findings: 2 errors, 4 warnings, 3 info. Summary written to /tmp/self-audit-summary.json.",
            "last_exit_code": 0,
        }],
    )
    monkeypatch.setattr(schedule, "get_personal_script_schedule", lambda cron_id: {})

    output = schedule.handle_schedule_status(hours=24)

    assert "⚠ self-audit" in output
    assert "exit 0 with warnings" in output


def test_schedule_status_marks_recent_open_run_as_running(monkeypatch):
    from plugins import schedule

    monkeypatch.setattr(
        schedule,
        "cron_runs_summary",
        lambda hours: [{
            "cron_id": "orchestrator-v2",
            "succeeded": 3,
            "completed_runs": 3,
            "total_runs": 4,
            "avg_duration": 137.0,
            "last_summary": "2026-04-12 16:10:18 Cycle #111 finished (exit 0)",
            "last_exit_code": None,
            "last_run": "2026-04-12 14:25:19",
            "last_ended_at": None,
        }],
    )
    monkeypatch.setattr(
        schedule,
        "get_personal_script_schedule",
        lambda cron_id: {"schedule_type": "interval", "interval_seconds": 900} if cron_id == "orchestrator-v2" else {},
    )
    monkeypatch.setattr(
        schedule,
        "_now_utc",
        lambda: datetime(2026, 4, 12, 14, 27, 0, tzinfo=timezone.utc),
    )

    output = schedule.handle_schedule_status(hours=24)

    assert "⏳ orchestrator-v2" in output
    assert "3/3" in output
    assert "running 2m" in output


def test_schedule_status_marks_old_open_run_as_warning(monkeypatch):
    from plugins import schedule

    monkeypatch.setattr(
        schedule,
        "cron_runs_summary",
        lambda hours: [{
            "cron_id": "shopify-backup",
            "succeeded": 1,
            "completed_runs": 1,
            "total_runs": 2,
            "avg_duration": 47.0,
            "last_summary": "[2026-04-12 03:00:47] All done.",
            "last_exit_code": None,
            "last_run": "2026-04-12 11:20:34",
            "last_ended_at": None,
        }],
    )
    monkeypatch.setattr(
        schedule,
        "get_personal_script_schedule",
        lambda cron_id: {"schedule_type": "calendar"} if cron_id == "shopify-backup" else {},
    )
    monkeypatch.setattr(
        schedule,
        "_now_utc",
        lambda: datetime(2026, 4, 12, 16, 27, 0, tzinfo=timezone.utc),
    )

    output = schedule.handle_schedule_status(hours=24)

    assert "⚠ shopify-backup" in output
    assert "1/1" in output
    assert "open run 5.1h" in output


def test_schedule_status_prefers_legacy_backup_runs_when_newer_than_cron_runs(monkeypatch):
    from plugins import schedule

    monkeypatch.setattr(
        schedule,
        "cron_runs_recent",
        lambda hours, cron_id: [{
            "started_at": "2026-04-12 16:43:09",
            "ended_at": "2026-04-12 16:43:10",
            "exit_code": 0,
            "summary": "",
            "error": "",
            "duration_secs": 1.0,
        }] if cron_id == "backup" else [],
    )
    monkeypatch.setattr(
        schedule,
        "_legacy_backup_runs",
        lambda hours: [{
            "started_at": "2026-04-13 06:22:00",
            "ended_at": "2026-04-13 06:22:00",
            "exit_code": 0,
            "summary": "legacy backup file evidence",
            "error": "",
            "duration_secs": 1.0,
        }],
    )
    monkeypatch.setattr(schedule, "get_personal_script_schedule", lambda cron_id: {})

    output = schedule.handle_schedule_status(hours=24, cron_id="backup")

    assert "2026-04-13 06:22:00" in output
    assert "legacy backup file evidence" in output
    assert "2026-04-12 16:43:09" not in output


def test_schedule_status_summary_prefers_legacy_backup_when_db_row_is_stale(monkeypatch):
    from plugins import schedule

    monkeypatch.setattr(
        schedule,
        "cron_runs_summary",
        lambda hours: [{
            "cron_id": "backup",
            "succeeded": 5,
            "completed_runs": 5,
            "total_runs": 5,
            "avg_duration": 1.0,
            "last_summary": "",
            "last_exit_code": 0,
            "last_run": "2026-04-12 16:43:09",
            "last_ended_at": "2026-04-12 16:43:10",
        }],
    )
    monkeypatch.setattr(
        schedule,
        "_legacy_backup_runs",
        lambda hours: [
            {
                "started_at": "2026-04-13 06:22:00",
                "ended_at": "2026-04-13 06:22:00",
                "exit_code": 0,
                "summary": "legacy backup file evidence",
                "error": "",
                "duration_secs": 1.0,
            },
            {
                "started_at": "2026-04-13 05:22:00",
                "ended_at": "2026-04-13 05:22:00",
                "exit_code": 0,
                "summary": "legacy backup file evidence",
                "error": "",
                "duration_secs": 1.0,
            },
        ],
    )
    monkeypatch.setattr(schedule, "get_personal_script_schedule", lambda cron_id: {})

    output = schedule.handle_schedule_status(hours=24)

    assert "✅ backup: 2/2" in output
    assert "legacy backup file evidence" in output
