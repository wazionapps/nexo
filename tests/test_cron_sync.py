"""Tests for manifest-to-LaunchAgent sync behavior."""
import os
import plistlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


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
