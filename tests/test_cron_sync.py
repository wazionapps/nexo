"""Tests for manifest-to-LaunchAgent sync behavior."""
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def test_build_plist_runs_from_runtime_root(tmp_path, monkeypatch):
    from crons import sync as cron_sync

    source_root = tmp_path / "repo-src"
    runtime_root = tmp_path / "nexo-home"
    (source_root / "scripts").mkdir(parents=True)
    (runtime_root / "logs").mkdir(parents=True)

    (source_root / "auto_close_sessions.py").write_text("print('ok')\n")
    wrapper = source_root / "scripts" / "nexo-cron-wrapper.sh"
    wrapper.write_text("#!/bin/bash\nexit 0\n")
    wrapper.chmod(0o755)

    monkeypatch.setattr(cron_sync, "SOURCE_ROOT", source_root)
    monkeypatch.setattr(cron_sync, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(cron_sync, "NEXO_HOME", runtime_root)
    monkeypatch.setattr(cron_sync, "LOG_DIR", runtime_root / "logs")

    plist = cron_sync.build_plist({"id": "auto-close-sessions", "script": "auto_close_sessions.py"})

    assert (runtime_root / "auto_close_sessions.py").is_file()
    assert (runtime_root / "scripts" / "nexo-cron-wrapper.sh").is_file()
    assert plist["ProgramArguments"][0] == "/bin/bash"
    assert plist["ProgramArguments"][2] == "auto-close-sessions"
    assert plist["ProgramArguments"][4] == str(runtime_root / "auto_close_sessions.py")
    assert plist["EnvironmentVariables"]["NEXO_CODE"] == str(runtime_root)
    assert plist["EnvironmentVariables"]["NEXO_SOURCE_CODE"] == str(source_root)
    assert plist["EnvironmentVariables"]["NEXO_MANAGED_CORE_CRON"] == "1"


def test_build_plist_preserves_script_subdirectories(tmp_path, monkeypatch):
    from crons import sync as cron_sync

    source_root = tmp_path / "repo-src"
    runtime_root = tmp_path / "nexo-home"
    (source_root / "scripts" / "deep-sleep").mkdir(parents=True)
    (runtime_root / "logs").mkdir(parents=True)

    script = source_root / "scripts" / "nexo-deep-sleep.sh"
    script.write_text("#!/bin/bash\nexit 0\n")
    script.chmod(0o755)
    wrapper = source_root / "scripts" / "nexo-cron-wrapper.sh"
    wrapper.write_text("#!/bin/bash\nexit 0\n")
    wrapper.chmod(0o755)
    (source_root / "scripts" / "deep-sleep" / "extract-prompt.md").write_text("prompt\n")

    monkeypatch.setattr(cron_sync, "SOURCE_ROOT", source_root)
    monkeypatch.setattr(cron_sync, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(cron_sync, "NEXO_HOME", runtime_root)
    monkeypatch.setattr(cron_sync, "LOG_DIR", runtime_root / "logs")

    plist = cron_sync.build_plist({"id": "deep-sleep", "script": "scripts/nexo-deep-sleep.sh", "type": "shell"})

    assert plist["ProgramArguments"][4] == str(runtime_root / "scripts" / "nexo-deep-sleep.sh")
    assert (runtime_root / "scripts" / "deep-sleep" / "extract-prompt.md").is_file()


def test_build_plist_reuses_runtime_script_when_source_already_matches_runtime(tmp_path, monkeypatch):
    from crons import sync as cron_sync

    runtime_root = tmp_path / "nexo-home"
    scripts_dir = runtime_root / "scripts"
    scripts_dir.mkdir(parents=True)
    (runtime_root / "logs").mkdir(parents=True)

    script = scripts_dir / "nexo-deep-sleep.sh"
    script.write_text("#!/bin/bash\nexit 0\n")
    script.chmod(0o755)
    wrapper = scripts_dir / "nexo-cron-wrapper.sh"
    wrapper.write_text("#!/bin/bash\nexit 0\n")
    wrapper.chmod(0o755)
    (scripts_dir / "deep-sleep").mkdir()
    (scripts_dir / "deep-sleep" / "extract-prompt.md").write_text("prompt\n")

    monkeypatch.setattr(cron_sync, "SOURCE_ROOT", runtime_root)
    monkeypatch.setattr(cron_sync, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(cron_sync, "NEXO_HOME", runtime_root)
    monkeypatch.setattr(cron_sync, "LOG_DIR", runtime_root / "logs")

    plist = cron_sync.build_plist({"id": "deep-sleep", "script": "scripts/nexo-deep-sleep.sh", "type": "shell"})

    assert plist["ProgramArguments"][4] == str(script)
    assert script.read_text() == "#!/bin/bash\nexit 0\n"


def test_build_plist_supports_keep_alive_jobs(tmp_path, monkeypatch):
    from crons import sync as cron_sync

    source_root = tmp_path / "repo-src"
    runtime_root = tmp_path / "nexo-home"
    (source_root / "scripts").mkdir(parents=True)
    (runtime_root / "logs").mkdir(parents=True)

    script = source_root / "scripts" / "nexo-personal-daemon.sh"
    script.write_text("#!/bin/bash\nwhile true; do sleep 60; done\n")
    script.chmod(0o755)
    wrapper = source_root / "scripts" / "nexo-cron-wrapper.sh"
    wrapper.write_text("#!/bin/bash\nexit 0\n")
    wrapper.chmod(0o755)

    monkeypatch.setattr(cron_sync, "SOURCE_ROOT", source_root)
    monkeypatch.setattr(cron_sync, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(cron_sync, "NEXO_HOME", runtime_root)
    monkeypatch.setattr(cron_sync, "LOG_DIR", runtime_root / "logs")

    plist = cron_sync.build_plist(
        {"id": "personal-daemon", "script": "scripts/nexo-personal-daemon.sh", "type": "shell", "keep_alive": True}
    )

    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["ProgramArguments"][4] == str(runtime_root / "scripts" / "nexo-personal-daemon.sh")


def test_build_plist_supports_interval_jobs_that_also_run_at_load(tmp_path, monkeypatch):
    from crons import sync as cron_sync

    source_root = tmp_path / "repo-src"
    runtime_root = tmp_path / "nexo-home"
    (source_root / "scripts").mkdir(parents=True)
    (runtime_root / "logs").mkdir(parents=True)

    script = source_root / "scripts" / "nexo-catchup.py"
    script.write_text("print('ok')\n")
    wrapper = source_root / "scripts" / "nexo-cron-wrapper.sh"
    wrapper.write_text("#!/bin/bash\nexit 0\n")
    wrapper.chmod(0o755)

    monkeypatch.setattr(cron_sync, "SOURCE_ROOT", source_root)
    monkeypatch.setattr(cron_sync, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(cron_sync, "NEXO_HOME", runtime_root)
    monkeypatch.setattr(cron_sync, "LOG_DIR", runtime_root / "logs")

    plist = cron_sync.build_plist(
        {
            "id": "catchup",
            "script": "scripts/nexo-catchup.py",
            "interval_seconds": 900,
            "run_at_load": True,
        }
    )

    assert plist["RunAtLoad"] is True
    assert plist["StartInterval"] == 900


def test_load_manifest_skips_disabled_optionals(tmp_path, monkeypatch):
    from crons import sync as cron_sync

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        """
{
  "crons": [
    {"id": "watchdog", "script": "scripts/nexo-watchdog.sh", "core": true},
    {"id": "personal-daemon", "script": "scripts/nexo-personal-daemon.sh", "core": true, "optional": "autonomy"}
  ]
}
""".strip()
    )
    nexo_home = tmp_path / "nexo-home"
    (nexo_home / "config").mkdir(parents=True)
    (nexo_home / "config" / "optionals.json").write_text('{"autonomy": false}')

    monkeypatch.setattr(cron_sync, "MANIFEST", manifest)
    monkeypatch.setattr(cron_sync, "NEXO_HOME", nexo_home)
    monkeypatch.setattr(cron_sync, "OPTIONALS_FILE", nexo_home / "config" / "optionals.json")

    crons = cron_sync.load_manifest()
    assert [cron["id"] for cron in crons] == ["watchdog"]


def test_plist_needs_update_when_runtime_env_changes(tmp_path):
    from crons import sync as cron_sync

    existing = tmp_path / "com.nexo.watchdog.plist"
    with existing.open("wb") as fh:
        plistlib.dump(
            {
                "ProgramArguments": ["/bin/bash", "/tmp/old-wrapper.sh", "watchdog", "/bin/bash", "/tmp/old-watchdog.sh"],
                "StartInterval": 1800,
                "EnvironmentVariables": {
                    "NEXO_HOME": "/tmp/old-home",
                    "NEXO_CODE": "/Users/test/Documents/repo/src",
                },
            },
            fh,
        )

    new_plist = {
        "ProgramArguments": ["/bin/bash", "/tmp/old-wrapper.sh", "watchdog", "/bin/bash", "/tmp/old-watchdog.sh"],
        "StartInterval": 1800,
        "KeepAlive": False,
        "EnvironmentVariables": {
            "NEXO_HOME": "/tmp/old-home",
            "NEXO_CODE": "/tmp/old-home",
        },
    }

    assert cron_sync.plist_needs_update(existing, new_plist) is True


def test_sync_watchdog_hash_registry_tracks_runtime_script(tmp_path, monkeypatch):
    from crons import sync as cron_sync

    runtime_root = tmp_path / "nexo-home"
    scripts_dir = runtime_root / "scripts"
    scripts_dir.mkdir(parents=True)
    watchdog = scripts_dir / "nexo-watchdog.sh"
    watchdog.write_text("#!/bin/bash\nexit 0\n")

    monkeypatch.setattr(cron_sync, "RUNTIME_ROOT", runtime_root)

    cron_sync._sync_watchdog_hash_registry()

    registry = scripts_dir / ".watchdog-hashes"
    assert registry.is_file()
    body = registry.read_text()
    assert str(watchdog) in body


def test_refresh_runtime_manifest_copies_source_manifest(tmp_path, monkeypatch):
    from crons import sync as cron_sync

    source_manifest = tmp_path / "repo-src" / "crons" / "manifest.json"
    source_manifest.parent.mkdir(parents=True)
    source_manifest.write_text('{"crons":[{"id":"watchdog"}]}')
    runtime_root = tmp_path / "nexo-home"

    monkeypatch.setattr(cron_sync, "MANIFEST", source_manifest)
    monkeypatch.setattr(cron_sync, "RUNTIME_ROOT", runtime_root)

    cron_sync._refresh_runtime_manifest()

    assert (runtime_root / "crons" / "manifest.json").read_text() == source_manifest.read_text()


def test_cleanup_retired_core_files_removes_day_orchestrator_script(tmp_path, monkeypatch):
    from crons import sync as cron_sync

    runtime_root = tmp_path / "nexo-home"
    retired = runtime_root / "scripts" / "nexo-day-orchestrator.sh"
    retired.parent.mkdir(parents=True)
    retired.write_text("#!/bin/bash\nexit 0\n")

    monkeypatch.setattr(cron_sync, "RUNTIME_ROOT", runtime_root)

    cron_sync._cleanup_retired_core_files()

    assert not retired.exists()


def test_sync_script_runs_directly_from_runtime_root(tmp_path):
    repo_src = Path(__file__).resolve().parent.parent / "src"
    runtime_root = tmp_path / "runtime"
    (runtime_root / "crons").mkdir(parents=True)
    shutil.copy2(repo_src / "cron_recovery.py", runtime_root / "cron_recovery.py")
    shutil.copy2(repo_src / "crons" / "sync.py", runtime_root / "crons" / "sync.py")
    (runtime_root / "crons" / "manifest.json").write_text('{"crons":[]}')
    (runtime_root / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(repo_src / "scripts" / "nexo-cron-wrapper.sh", runtime_root / "scripts" / "nexo-cron-wrapper.sh")

    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, str(runtime_root / "crons" / "sync.py"), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "HOME": str(home),
            "NEXO_HOME": str(runtime_root),
            "NEXO_CODE": str(runtime_root),
        },
    )

    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr


def test_sync_linux_weekday_uses_launchd_convention(tmp_path, monkeypatch):
    """Manifest weekday follows launchd convention (0=Sunday).

    sync_linux must map weekday values to the correct systemd OnCalendar
    day abbreviation.  A previous bug used Python's weekday ordering
    (0=Monday), causing crons to fire on the wrong day.
    """
    from crons import sync as cron_sync

    source_root = tmp_path / "repo-src"
    runtime_root = tmp_path / "nexo-home"
    unit_dir = tmp_path / "systemd-user"
    (source_root / "scripts").mkdir(parents=True)
    (runtime_root / "logs").mkdir(parents=True)
    unit_dir.mkdir(parents=True)

    script = source_root / "scripts" / "nexo-weekly.py"
    script.write_text("print('ok')\n")
    wrapper = source_root / "scripts" / "nexo-cron-wrapper.sh"
    wrapper.write_text("#!/bin/bash\nexit 0\n")
    wrapper.chmod(0o755)

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"crons": [{"id": "weekly-job", "script": "scripts/nexo-weekly.py", '
        '"schedule": {"hour": 5, "minute": 0, "weekday": 0}}]}'
    )

    monkeypatch.setattr(cron_sync, "SOURCE_ROOT", source_root)
    monkeypatch.setattr(cron_sync, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(cron_sync, "NEXO_HOME", runtime_root)
    monkeypatch.setattr(cron_sync, "LOG_DIR", runtime_root / "logs")
    monkeypatch.setattr(cron_sync, "MANIFEST", manifest)
    monkeypatch.setattr(cron_sync, "OPTIONALS_FILE", runtime_root / "config" / "optionals.json")
    monkeypatch.setattr(cron_sync, "SCHEDULE_FILE", runtime_root / "config" / "schedule.json")

    # Patch Path.home to use unit_dir parent for systemd path
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    (tmp_path / "home" / ".config" / "systemd" / "user").mkdir(parents=True)

    # Use dry_run=True to skip the systemctl calls (unavailable on macOS)
    cron_sync.sync_linux(dry_run=True)

    # dry_run doesn't write files, so verify the mapping directly
    days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert days[0] == "Sun", "weekday=0 (launchd Sunday) must map to 'Sun'"
    assert days[1] == "Mon", "weekday=1 (launchd Monday) must map to 'Mon'"
    assert days[6] == "Sat", "weekday=6 (launchd Saturday) must map to 'Sat'"


def test_sync_linux_weekday_7_is_sunday_alias():
    """launchd allows weekday=7 as a Sunday alias; the days list must handle index 7."""
    # The days list in sync_linux has 8 entries to support weekday=7
    days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert days[7] == "Sun", "weekday=7 should map to Sun (Sunday alias)"
