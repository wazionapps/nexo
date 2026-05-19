from __future__ import annotations

import json
import sys
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_macos_recovery_registry_runs_calendar_mail_icloud_and_common_apps():
    from disk_recovery.registry import run_sweep

    commands: list[list[str]] = []

    def runner(command: list[str]) -> dict:
        commands.append(command)
        return {"ok": True, "command": command}

    report = run_sweep(
        platform="darwin",
        processes={"dropbox", "bird", "cloudd"},
        runner=runner,
    )

    assert report["ok"] is True
    assert "macos_calendar_mail" in report["touched_apps"]
    assert "icloud_drive" in report["touched_apps"]
    assert "Dropbox" in report["touched_apps"]
    assert ["killall", "CalendarAgent"] in commands
    assert ["osascript", "-e", 'tell application "Mail" to check for new mail'] in commands
    assert ["killall", "bird"] in commands
    assert ["open", "-a", "Dropbox"] in commands


def test_windows_recovery_registry_runs_onesync_outlook_onedrive_and_common_apps():
    from disk_recovery.registry import run_sweep

    commands: list[list[str]] = []

    def runner(command: list[str]) -> dict:
        commands.append(command)
        return {"ok": True, "command": command}

    report = run_sweep(
        platform="windows",
        processes={"OneDrive.exe", "Slack.exe"},
        runner=runner,
        env={"LOCALAPPDATA": r"C:\Users\test\AppData\Local"},
    )

    assert report["ok"] is True
    assert "windows_onesync" in report["touched_apps"]
    assert "outlook_send_receive" in report["touched_apps"]
    assert "onedrive" in report["touched_apps"]
    assert "Slack" in report["touched_apps"]
    assert ["powershell", "-NoProfile", "-Command", "Get-Service OneSyncSvc* | Restart-Service -Force"] in commands
    assert any("SendAndReceive" in " ".join(command) for command in commands)
    assert [r"C:\Users\test\AppData\Local\Microsoft\OneDrive\OneDrive.exe", "/shutdown"] in commands
    assert ["taskkill", "/IM", "Slack.exe", "/F"] in commands


def test_windows_default_processes_parse_tasklist_table(monkeypatch):
    from disk_recovery import registry

    class Result:
        stdout = """Image Name                     PID Session Name        Session#    Mem Usage
========================= ======== ================ =========== ============
OneDrive.exe                  1234 Console                    1     20,000 K
Slack.exe                     2345 Console                    1     30,000 K
GoogleDriveFS.exe             3456 Console                    1     40,000 K
"""

    monkeypatch.setattr(
        registry.subprocess,
        "run",
        lambda *args, **kwargs: Result(),
    )

    processes = registry.default_processes("windows")

    assert {"onedrive.exe", "slack.exe", "googledrivefs.exe"}.issubset(processes)
    assert "image" not in processes


def test_post_disk_recovery_sweep_cli_logs_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo-home"))
    from scripts import post_disk_recovery_sweep

    rc = post_disk_recovery_sweep.main([
        "--platform",
        "darwin",
        "--dry-run",
        "--json",
        "--network-window-seconds",
        "0",
        "--reason",
        "test",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["reason"] == "test"
    assert payload["dry_run"] is True
    assert "macos_calendar_mail" in payload["touched_apps"]
    log_path = tmp_path / "nexo-home" / "runtime" / "operations" / "post-disk-recovery-sweep.jsonl"
    assert log_path.is_file()
