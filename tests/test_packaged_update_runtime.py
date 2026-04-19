from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def test_backup_code_tree_includes_skills_runtime_and_templates(tmp_path, monkeypatch):
    from plugins import update

    runtime_home = tmp_path / "runtime"
    backup_base = tmp_path / "backups"
    runtime_home.mkdir()
    backup_base.mkdir()

    (runtime_home / "skills" / "personal-skill").mkdir(parents=True)
    (runtime_home / "skills-core" / "core-skill").mkdir(parents=True)
    (runtime_home / "skills-runtime" / "sk-runner").mkdir(parents=True)
    (runtime_home / "templates").mkdir()
    (runtime_home / "bin").mkdir()

    (runtime_home / "skills" / "personal-skill" / "skill.json").write_text("{}\n")
    (runtime_home / "skills-core" / "core-skill" / "skill.json").write_text("{}\n")
    (runtime_home / "skills-runtime" / "sk-runner" / "script.py").write_text("print('ok')\n")
    (runtime_home / "templates" / "skill-template.md").write_text("# template\n")
    (runtime_home / "bin" / "nexo").write_text("#!/bin/bash\n")

    monkeypatch.setenv("NEXO_HOME", str(runtime_home))


    monkeypatch.setenv("NEXO_HOME", str(runtime_home))


    monkeypatch.setenv("NEXO_HOME", str(runtime_home))


    monkeypatch.setenv("NEXO_HOME", str(runtime_home))


    monkeypatch.setenv("NEXO_HOME", str(runtime_home))


    monkeypatch.setenv("NEXO_HOME", str(runtime_home))


    monkeypatch.setenv("NEXO_HOME", str(runtime_home))


    monkeypatch.setenv("NEXO_HOME", str(runtime_home))
    monkeypatch.setattr(update, "NEXO_HOME", runtime_home)
    monkeypatch.setattr(update, "BACKUP_BASE", backup_base)

    backup_dir, err = update._backup_code_tree()

    assert err is None
    backup_path = Path(backup_dir)
    assert (backup_path / "skills" / "personal-skill" / "skill.json").is_file()
    assert (backup_path / "skills-core" / "core-skill" / "skill.json").is_file()
    assert (backup_path / "skills-runtime" / "sk-runner" / "script.py").is_file()
    assert (backup_path / "templates" / "skill-template.md").is_file()
    assert (backup_path / "bin" / "nexo").is_file()


def test_sync_packaged_clients_normalizes_preferences_and_targets_runtime_home(tmp_path, monkeypatch):
    from plugins import update
    import client_sync

    runtime_home = tmp_path / "runtime"
    config_dir = runtime_home / "config"
    config_dir.mkdir(parents=True)
    schedule_path = config_dir / "schedule.json"
    schedule_path.write_text(json.dumps({"default_terminal_client": "codex"}))

    captured = {}

    def fake_sync_all_clients(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "clients": {}}

    monkeypatch.setenv("NEXO_HOME", str(runtime_home))
    monkeypatch.setattr(update, "NEXO_HOME", runtime_home)
    monkeypatch.setattr(client_sync, "sync_all_clients", fake_sync_all_clients)

    ok, err = update._sync_packaged_clients()

    assert ok is True
    assert err is None
    assert captured["nexo_home"] == runtime_home
    assert captured["runtime_root"] == runtime_home
    assert captured["auto_install_missing_claude"] is True
    updated_schedule = json.loads(schedule_path.read_text())
    assert "interactive_clients" in updated_schedule
    assert "automation_enabled" in updated_schedule


def test_refresh_installed_manifest_writes_runtime_core_artifacts(tmp_path, monkeypatch):
    from plugins import update

    runtime_home = tmp_path / "runtime"
    src_dir = tmp_path / "src"
    (src_dir / "crons").mkdir(parents=True)
    (src_dir / "scripts").mkdir()
    (src_dir / "hooks").mkdir()
    (src_dir / "crons" / "manifest.json").write_text('{"crons":[]}\n')
    (src_dir / "scripts" / "nexo-catchup.py").write_text("print('ok')\n")
    (src_dir / "scripts" / "nexo-catchup 2.py").write_text("print('old')\n")
    (src_dir / "hooks" / "capture-tool-logs.sh").write_text("#!/bin/bash\necho ok\n")
    (src_dir / "hooks" / "capture-tool-logs 2.sh").write_text("#!/bin/bash\necho old\n")

    monkeypatch.setenv("NEXO_HOME", str(runtime_home))
    monkeypatch.setattr(update, "NEXO_HOME", runtime_home)
    monkeypatch.setattr(update, "SRC_DIR", src_dir)

    update._refresh_installed_manifest()

    manifest = json.loads((runtime_home / "config" / "runtime-core-artifacts.json").read_text())
    assert manifest["script_names"] == ["nexo-catchup.py"]
    assert manifest["hook_names"] == ["capture-tool-logs.sh"]
    _crons_old = runtime_home / "crons" / "manifest.json"
    _crons_new = runtime_home / "runtime" / "crons" / "manifest.json"
    assert _crons_old.is_file() or _crons_new.is_file(), f"manifest at neither {_crons_old} nor {_crons_new}"


def test_refresh_installed_manifest_packaged_mode_uses_npm_src_for_core_artifacts(tmp_path, monkeypatch):
    from plugins import update

    runtime_home = tmp_path / "runtime"
    npm_src = tmp_path / "npm-src"
    (runtime_home / "scripts").mkdir(parents=True)
    (npm_src / "crons").mkdir(parents=True)
    (npm_src / "scripts").mkdir()
    (npm_src / "hooks").mkdir()

    (runtime_home / "scripts" / "daily-report.py").write_text("print('personal')\n")
    (npm_src / "crons" / "manifest.json").write_text('{"crons":[]}\n')
    (npm_src / "scripts" / "nexo-catchup.py").write_text("print('core')\n")
    (npm_src / "hooks" / "capture-tool-logs.sh").write_text("#!/bin/bash\necho ok\n")

    monkeypatch.setenv("NEXO_HOME", str(runtime_home))
    monkeypatch.setattr(update, "NEXO_HOME", runtime_home)
    monkeypatch.setattr(update, "SRC_DIR", runtime_home)
    monkeypatch.setattr(update, "_PACKAGED_INSTALL", True)
    monkeypatch.setattr(update, "_find_npm_pkg_src", lambda: npm_src)

    update._refresh_installed_manifest()

    manifest = json.loads((runtime_home / "config" / "runtime-core-artifacts.json").read_text())
    assert manifest["script_names"] == ["nexo-catchup.py"]
    assert manifest["hook_names"] == ["capture-tool-logs.sh"]
    assert "daily-report.py" not in manifest["script_names"]


def test_cleanup_retired_runtime_files_removes_legacy_heartbeat_files(tmp_path, monkeypatch):
    from plugins import update

    runtime_home = tmp_path / "runtime"
    (runtime_home / "scripts").mkdir(parents=True)
    (runtime_home / "hooks").mkdir()
    legacy_files = [
        runtime_home / "scripts" / "heartbeat-enforcement.py",
        runtime_home / "scripts" / "heartbeat-posttool.sh",
        runtime_home / "scripts" / "heartbeat-user-msg.sh",
        runtime_home / "hooks" / "heartbeat-guard.sh",
    ]
    for path in legacy_files:
        path.write_text("legacy\n")

    monkeypatch.setenv("NEXO_HOME", str(runtime_home))
    monkeypatch.setattr(update, "NEXO_HOME", runtime_home)

    removed = update._cleanup_retired_runtime_files()

    assert len(removed) == 4
    assert all(not path.exists() for path in legacy_files)


def test_sync_hooks_to_home_skips_samefile_when_packaged_runtime_is_source(tmp_path, monkeypatch):
    from plugins import update

    runtime_home = tmp_path / "runtime"
    hooks_dir = runtime_home / "hooks"
    hooks_dir.mkdir(parents=True)
    hook = hooks_dir / "session-stop.sh"
    hook.write_text("#!/bin/bash\nexit 0\n")

    monkeypatch.setenv("NEXO_HOME", str(runtime_home))
    monkeypatch.setattr(update, "NEXO_HOME", runtime_home)
    monkeypatch.setattr(update, "SRC_DIR", runtime_home)

    update._sync_hooks_to_home()

    assert hook.read_text() == "#!/bin/bash\nexit 0\n"


def test_sync_packaged_crons_runs_runtime_sync_script(tmp_path, monkeypatch):
    from plugins import update

    runtime_home = tmp_path / "runtime"
    sync_path = runtime_home / "crons" / "sync.py"
    sync_path.parent.mkdir(parents=True)
    sync_path.write_text("print('ok')\n")

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env", {})
        return mock.Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("NEXO_HOME", str(runtime_home))
    monkeypatch.setattr(update, "NEXO_HOME", runtime_home)
    monkeypatch.setattr(update, "SRC_DIR", runtime_home)
    monkeypatch.setattr(update.subprocess, "run", fake_run)

    ok, err = update._sync_packaged_crons()

    assert ok is True
    assert err is None
    assert captured["args"] == [sys.executable, str(sync_path)]
    assert captured["cwd"] == str(runtime_home)
    assert captured["env"]["NEXO_HOME"] == str(runtime_home)
    assert captured["env"]["NEXO_CODE"] == str(runtime_home)


def test_handle_packaged_update_reloads_launchagents_after_successful_bump(monkeypatch):
    from plugins import update

    versions = iter(["5.3.6", "5.3.7"])

    monkeypatch.setattr(update, "_read_version", lambda: next(versions))
    monkeypatch.setattr(update, "_backup_databases", lambda: ("backup-dir", None))
    monkeypatch.setattr(update, "_backup_code_tree", lambda: ("code-backup", None))
    monkeypatch.setattr(update, "_reinstall_pip_deps", lambda: None)
    monkeypatch.setattr(update, "_run_migrations", lambda: None)
    monkeypatch.setattr(update, "_verify_import", lambda: None)
    monkeypatch.setattr(update, "_sync_packaged_crons", lambda progress_fn=None: (True, None))
    monkeypatch.setattr(update, "_sync_hooks_to_home", lambda: None)
    monkeypatch.setattr(update, "_cleanup_retired_runtime_files", lambda: [])
    monkeypatch.setattr(update, "_sync_packaged_clients", lambda: (True, None))
    monkeypatch.setattr(
        update,
        "_reload_launch_agents_after_bump",
        lambda: {"scanned": 3, "reloaded": 3, "errors": []},
    )

    def fake_run(args, **kwargs):
        if args == ["npm", "update", "-g", "nexo-brain"]:
            return mock.Mock(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess call: {args}")

    monkeypatch.setattr(update.subprocess, "run", fake_run)

    result = update._handle_packaged_update()

    assert "UPDATE SUCCESSFUL (packaged install)" in result
    assert "Version: 5.3.6 -> 5.3.7" in result
    assert "Crons: synced with manifest" in result
    assert "Hooks: synced to NEXO_HOME" in result
    assert "Clients: configured client targets synced" in result
    assert "LaunchAgents: reloaded 3/3" in result


def test_packaged_installer_discovers_root_python_modules_for_migration():
    installer = REPO_ROOT / "bin" / "nexo-brain.js"
    text = installer.read_text(encoding="utf-8")

    assert 'function getCoreRuntimeFlatFiles(srcDir = path.join(__dirname, "..", "src"))' in text
    assert 'name.endsWith(".py")' in text
    assert 'new Set([...staticFiles, ...discoveredRootModules])' in text


def test_packaged_installer_syncs_runtime_package_metadata():
    installer = REPO_ROOT / "bin" / "nexo-brain.js"
    text = installer.read_text(encoding="utf-8")

    assert 'function syncRuntimePackageMetadata(repoRoot = path.join(__dirname, ".."), runtimeHome = NEXO_HOME)' in text
    assert 'fs.copyFileSync(pkgSrc, path.join(runtimeHome, "package.json"));' in text
    assert text.count('syncRuntimePackageMetadata(path.join(__dirname, ".."), NEXO_HOME);') >= 3
