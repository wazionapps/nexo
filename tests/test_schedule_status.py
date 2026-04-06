import os
import sys

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
