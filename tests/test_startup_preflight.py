"""Tests for startup preflight orchestration."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def test_startup_preflight_defers_sync_update_when_runtime_busy(tmp_path, monkeypatch):
    import auto_update
    import runtime_power
    import db
    import script_registry

    nexo_home = tmp_path / "nexo"
    user_home = tmp_path / "home"
    (nexo_home / "config").mkdir(parents=True)
    (nexo_home / "logs").mkdir(parents=True)
    user_home.mkdir(parents=True)
    (nexo_home / "config" / "schedule.json").write_text(json.dumps({
        "timezone": "UTC",
        "auto_update": True,
        "power_policy": "disabled",
        "processes": {},
    }))

    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setattr(auto_update, "NEXO_HOME", nexo_home)
    monkeypatch.setattr(auto_update, "UPDATE_SUMMARY_FILE", nexo_home / "logs" / "update-last-summary.json")
    monkeypatch.setattr(auto_update, "UPDATE_HISTORY_FILE", nexo_home / "logs" / "update-history.jsonl")
    monkeypatch.setattr(auto_update, "CHECK_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(auto_update, "_resolve_sync_source", lambda: (tmp_path / "repo" / "src", tmp_path / "repo"))
    monkeypatch.setattr(auto_update, "_run_db_migrations", lambda: True)
    monkeypatch.setattr(auto_update, "run_file_migrations", lambda: [])
    monkeypatch.setattr(auto_update, "_migrate_claude_md", lambda: None)
    monkeypatch.setattr(auto_update, "_sync_watchdog_hash_registry", lambda: None)
    monkeypatch.setattr(auto_update, "_warn_protected_runtime_location", lambda: None)
    monkeypatch.setattr(auto_update, "_ensure_runtime_cli_wrapper", lambda: None)
    monkeypatch.setattr(auto_update, "_ensure_runtime_cli_in_shell", lambda: None)
    monkeypatch.setattr(auto_update, "_runtime_busy_reason", lambda: "active sessions: 1")
    monkeypatch.setattr(auto_update, "_source_repo_status", lambda repo: {
        "is_git": True,
        "dirty": False,
        "diverged": False,
        "behind": True,
    })
    monkeypatch.setattr(auto_update, "_read_last_check", lambda: {})
    monkeypatch.setattr(auto_update, "_write_last_check", lambda data: None)
    monkeypatch.setattr(auto_update, "manual_sync_update", lambda **kwargs: {"ok": True, "updated": True, "actions": ["unexpected"]})
    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(script_registry, "reconcile_personal_scripts", lambda dry_run=False: {"ok": True})
    monkeypatch.setattr(runtime_power, "ensure_power_policy_choice", lambda **kwargs: {"policy": "disabled", "prompted": False})
    monkeypatch.setattr(runtime_power, "apply_power_policy", lambda policy=None: {"ok": True, "action": "disabled"})
    monkeypatch.setattr(runtime_power, "get_power_policy", lambda schedule=None: "disabled")
    monkeypatch.setattr(runtime_power, "ensure_full_disk_access_choice", lambda **kwargs: {"status": "unset", "prompted": False, "message": ""})
    monkeypatch.setattr(runtime_power, "get_full_disk_access_status", lambda schedule=None: "unset")

    result = auto_update.startup_preflight(entrypoint="chat", interactive=False)

    assert result["deferred_reason"] == "active sessions: 1"
    assert result["updated"] is False


def test_runtime_cli_wrapper_text_probes_fastmcp_and_supports_override():
    import auto_update

    text = auto_update._runtime_cli_wrapper_text()

    assert "NEXO_RUNTIME_PYTHON" in text
    assert 'import fastmcp' in text
    assert 'resolve_python()' in text
    assert 'DEFAULT_NEXO_HOME=' in text
    assert '${HOME}/claude' in text
    assert 'pwd -P' in text
    assert '$NEXO_HOME/claude/cli.py' not in text


def test_resolve_sync_source_supports_hybrid_runtime_code_dir(tmp_path, monkeypatch):
    import auto_update

    runtime_home = tmp_path / "runtime"
    runtime_code = runtime_home / "claude"
    runtime_code.mkdir(parents=True)
    (runtime_code / "db").mkdir()
    (runtime_code / "package.json").write_text("{}")

    monkeypatch.setattr(auto_update, "NEXO_HOME", runtime_home)
    monkeypatch.setattr(auto_update, "NEXO_CODE", runtime_code)

    src_dir, repo_dir = auto_update._resolve_sync_source()

    assert src_dir == runtime_code
    assert repo_dir == runtime_code


def test_copy_runtime_from_source_creates_skill_scaffold_dirs(tmp_path, monkeypatch):
    import auto_update

    src_dir = tmp_path / "src"
    repo_dir = tmp_path / "repo"
    dest = tmp_path / "runtime"

    src_dir.mkdir()
    repo_dir.mkdir()
    (src_dir / "db").mkdir()
    (src_dir / "scripts").mkdir()
    (src_dir / "skills" / "demo-skill").mkdir(parents=True)
    (repo_dir / "templates").mkdir()

    (src_dir / "server.py").write_text("print('server')\n")
    (src_dir / "cli.py").write_text("print('cli')\n")
    (src_dir / "requirements.txt").write_text("fastmcp\n")
    (src_dir / "skills" / "demo-skill" / "skill.json").write_text("{}\n")
    (repo_dir / "templates" / "skill-template.md").write_text("# template\n")
    (repo_dir / "package.json").write_text("{}\n")

    monkeypatch.setattr(auto_update, "_installed_scripts_classification", lambda _dest: {})

    result = auto_update._copy_runtime_from_source(src_dir, repo_dir, dest)

    assert result["source"] == str(src_dir)
    assert (dest / "bin").is_dir()
    assert (dest / "skills").is_dir()
    assert (dest / "skills-runtime").is_dir()
    assert (dest / "skills-core" / "demo-skill" / "skill.json").is_file()


def test_reinstall_runtime_pip_deps_creates_venv_when_missing(tmp_path, monkeypatch):
    import auto_update

    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(parents=True)
    (runtime_home / "requirements.txt").write_text("fastmcp>=2.9.0\n")
    calls = []

    def fake_run(cmd, capture_output=True, text=True, timeout=120, env=None):
        calls.append(cmd)
        if cmd[:3] == [sys.executable, "-m", "venv"]:
            venv_bin = runtime_home / ".venv" / "bin"
            venv_bin.mkdir(parents=True, exist_ok=True)
            (venv_bin / "python3").write_text("")
            pip = venv_bin / "pip"
            pip.write_text("#!/bin/sh\nexit 0\n")
            pip.chmod(0o755)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(auto_update.subprocess, "run", fake_run)

    ok = auto_update._reinstall_runtime_pip_deps(runtime_home)

    assert ok is True
    assert any(cmd[:3] == [sys.executable, "-m", "venv"] for cmd in calls)


def test_run_runtime_post_sync_uses_reconcile_personal_scripts(tmp_path, monkeypatch):
    import auto_update
    import client_sync

    runtime_home = tmp_path / "runtime"
    (runtime_home / "logs").mkdir(parents=True)
    (runtime_home / "crons").mkdir(parents=True)
    (runtime_home / "crons" / "sync.py").write_text("print('ok')\n")

    calls = []
    captured = {}

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(auto_update, "NEXO_HOME", runtime_home)
    monkeypatch.setattr(auto_update, "_reinstall_runtime_pip_deps", lambda dest: True)
    monkeypatch.setattr(auto_update.subprocess, "run", fake_run)
    def fake_sync_all_clients(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "clients": {}}

    monkeypatch.setattr(client_sync, "sync_all_clients", fake_sync_all_clients)

    import runtime_power

    monkeypatch.setattr(runtime_power, "apply_power_policy", lambda policy=None: {"ok": True, "action": "enabled"})
    monkeypatch.setattr(runtime_power, "ensure_full_disk_access_choice", lambda **kwargs: {"status": "unset", "prompted": False, "message": ""})
    monkeypatch.setattr(runtime_power, "get_full_disk_access_status", lambda schedule=None: "unset")

    ok, actions = auto_update._run_runtime_post_sync(runtime_home)

    assert ok is True
    assert "db+personal-sync" in actions
    assert "client-sync" in actions
    init_call = next(cmd for cmd in calls if isinstance(cmd, list) and len(cmd) >= 3 and cmd[1] == "-c")
    assert "reconcile_personal_scripts" in init_call[2]
    assert captured["auto_install_missing_claude"] is True


def test_run_runtime_post_sync_reports_personal_schedule_heal(tmp_path, monkeypatch):
    import auto_update
    import client_sync

    runtime_home = tmp_path / "runtime"
    (runtime_home / "logs").mkdir(parents=True)
    (runtime_home / "crons").mkdir(parents=True)
    (runtime_home / "crons" / "sync.py").write_text("print('ok')\n")

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and len(cmd) >= 3 and cmd[1] == "-c":
            return subprocess.CompletedProcess(
                cmd,
                0,
                json.dumps({
                    "ensure_schedules": {
                        "created": [{"cron_id": "email-monitor"}],
                        "repaired": [],
                        "invalid": [],
                    }
                }),
                "",
            )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(auto_update, "NEXO_HOME", runtime_home)
    monkeypatch.setattr(auto_update, "_reinstall_runtime_pip_deps", lambda dest: True)
    monkeypatch.setattr(auto_update.subprocess, "run", fake_run)
    monkeypatch.setattr(client_sync, "sync_all_clients", lambda **kwargs: {"ok": True, "clients": {}})

    import runtime_power

    monkeypatch.setattr(runtime_power, "apply_power_policy", lambda policy=None: {"ok": True, "action": "enabled"})
    monkeypatch.setattr(runtime_power, "ensure_full_disk_access_choice", lambda **kwargs: {"status": "unset", "prompted": False, "message": ""})
    monkeypatch.setattr(runtime_power, "get_full_disk_access_status", lambda schedule=None: "unset")

    ok, actions = auto_update._run_runtime_post_sync(runtime_home)

    assert ok is True
    assert "personal-schedules-healed:1" in actions


def test_startup_preflight_reports_personal_schedule_heal(tmp_path, monkeypatch):
    import auto_update
    import runtime_power
    import db
    import script_registry

    nexo_home = tmp_path / "nexo"
    user_home = tmp_path / "home"
    (nexo_home / "config").mkdir(parents=True)
    (nexo_home / "logs").mkdir(parents=True)
    user_home.mkdir(parents=True)
    (nexo_home / "config" / "schedule.json").write_text(json.dumps({
        "timezone": "UTC",
        "auto_update": True,
        "power_policy": "disabled",
        "processes": {},
    }))

    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setattr(auto_update, "NEXO_HOME", nexo_home)
    monkeypatch.setattr(auto_update, "UPDATE_SUMMARY_FILE", nexo_home / "logs" / "update-last-summary.json")
    monkeypatch.setattr(auto_update, "UPDATE_HISTORY_FILE", nexo_home / "logs" / "update-history.jsonl")
    monkeypatch.setattr(auto_update, "CHECK_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(auto_update, "_resolve_sync_source", lambda: (tmp_path / "repo" / "src", tmp_path / "repo"))
    monkeypatch.setattr(auto_update, "_run_db_migrations", lambda: True)
    monkeypatch.setattr(auto_update, "run_file_migrations", lambda: [])
    monkeypatch.setattr(auto_update, "_migrate_claude_md", lambda: None)
    monkeypatch.setattr(auto_update, "_sync_watchdog_hash_registry", lambda: None)
    monkeypatch.setattr(auto_update, "_warn_protected_runtime_location", lambda: None)
    monkeypatch.setattr(auto_update, "_ensure_runtime_cli_wrapper", lambda: None)
    monkeypatch.setattr(auto_update, "_ensure_runtime_cli_in_shell", lambda: None)
    monkeypatch.setattr(auto_update, "_runtime_busy_reason", lambda: None)
    monkeypatch.setattr(auto_update, "_source_repo_status", lambda repo: {
        "is_git": True,
        "dirty": False,
        "diverged": False,
        "behind": False,
    })
    monkeypatch.setattr(auto_update, "_read_last_check", lambda: {})
    monkeypatch.setattr(auto_update, "_write_last_check", lambda data: None)
    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(script_registry, "reconcile_personal_scripts", lambda dry_run=False: {
        "ensure_schedules": {
            "created": [{"cron_id": "email-monitor"}],
            "repaired": [],
            "invalid": [],
        }
    })
    monkeypatch.setattr(runtime_power, "ensure_power_policy_choice", lambda **kwargs: {"policy": "disabled", "prompted": False})
    monkeypatch.setattr(runtime_power, "apply_power_policy", lambda policy=None: {"ok": True, "action": "disabled"})
    monkeypatch.setattr(runtime_power, "get_power_policy", lambda schedule=None: "disabled")
    monkeypatch.setattr(runtime_power, "ensure_full_disk_access_choice", lambda **kwargs: {"status": "unset", "prompted": False, "message": ""})
    monkeypatch.setattr(runtime_power, "get_full_disk_access_status", lambda schedule=None: "unset")

    result = auto_update.startup_preflight(entrypoint="chat", interactive=False)

    assert "db+personal-sync" in result["actions"]
    assert "personal-schedules-healed:1" in result["actions"]


def test_copy_runtime_from_source_preserves_personal_script_collision(tmp_path, monkeypatch):
    import auto_update

    runtime_home = tmp_path / "runtime"
    src_dir = tmp_path / "repo" / "src"
    repo_dir = tmp_path / "repo"
    (runtime_home / "scripts").mkdir(parents=True)
    (src_dir / "scripts").mkdir(parents=True)
    (src_dir / "hooks").mkdir(parents=True)
    (src_dir / "plugins").mkdir(parents=True)
    (repo_dir / "templates").mkdir(parents=True)

    personal_script = runtime_home / "scripts" / "email-triage-agent.py"
    personal_script.write_text("# personal\n")
    (runtime_home / "scripts" / "nexo-watchdog.sh").write_text("#!/bin/bash\necho ok\n")
    (src_dir / "scripts" / "email-triage-agent.py").write_text("# core candidate\n")
    (src_dir / "scripts" / "nexo-watchdog.sh").write_text("#!/bin/bash\necho core\n")

    monkeypatch.setattr(auto_update, "NEXO_HOME", runtime_home)
    monkeypatch.setattr(
        auto_update,
        "_installed_scripts_classification",
        lambda dest: {
            "email-triage-agent.py": "personal",
            "nexo-watchdog.sh": "ignored",
        },
    )

    stats = auto_update._copy_runtime_from_source(src_dir, repo_dir, runtime_home)

    assert personal_script.read_text() == "# personal\n"
    assert stats["scripts"] == 1
    assert len(stats["script_conflicts"]) == 1
    assert stats["script_conflicts"][0]["name"] == "email-triage-agent.py"
