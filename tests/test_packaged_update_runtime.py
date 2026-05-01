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
    (runtime_home / "core").mkdir(parents=True)
    (runtime_home / "core" / "server.py").write_text("print('server')\n")
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
    assert captured["runtime_root"] == runtime_home / "core"
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

    manifest_path = runtime_home / "personal" / "config" / "runtime-core-artifacts.json"
    if not manifest_path.is_file():
        manifest_path = runtime_home / "config" / "runtime-core-artifacts.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["script_names"] == ["nexo-catchup.py"]
    assert manifest["hook_names"] == ["capture-tool-logs.sh"]
    _crons_old = runtime_home / "crons" / "manifest.json"
    _crons_new = runtime_home / "runtime" / "crons" / "manifest.json"
    assert _crons_old.is_file() or _crons_new.is_file(), f"manifest at neither {_crons_old} nor {_crons_new}"


def test_resolve_sync_source_reads_recorded_source_from_core_version_metadata(tmp_path, monkeypatch):
    import auto_update

    runtime_home = tmp_path / "runtime"
    repo_root = tmp_path / "repo"
    (runtime_home / "core").mkdir(parents=True)
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "package.json").write_text('{"name":"nexo-brain"}\n')
    (runtime_home / "core" / "version.json").write_text(
        json.dumps({"version": "7.9.6", "source": str(repo_root)})
    )

    monkeypatch.setenv("NEXO_HOME", str(runtime_home))
    monkeypatch.setattr(auto_update, "NEXO_HOME", runtime_home)
    monkeypatch.setattr(auto_update, "NEXO_CODE", runtime_home / "core")

    src_dir, resolved_repo = auto_update._resolve_sync_source()

    assert src_dir == repo_root / "src"
    assert resolved_repo == repo_root


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
    # B10 mini-refactor: callsites moved from the _PACKAGED_INSTALL constant
    # to the _is_packaged_install() helper, so monkey-patch the helper too.
    monkeypatch.setattr(update, "_PACKAGED_INSTALL", True)
    monkeypatch.setattr(update, "_is_packaged_install", lambda: True)
    monkeypatch.setattr(update, "_find_npm_pkg_src", lambda: npm_src)

    update._refresh_installed_manifest()

    manifest_path = runtime_home / "personal" / "config" / "runtime-core-artifacts.json"
    if not manifest_path.is_file():
        manifest_path = runtime_home / "config" / "runtime-core-artifacts.json"
    manifest = json.loads(manifest_path.read_text())
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
    (runtime_home / "core").mkdir(parents=True)
    (runtime_home / "core" / "cli.py").write_text("print('cli')\n")
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
    assert captured["env"]["NEXO_CODE"] == str(runtime_home / "core")


def test_handle_packaged_update_reloads_launchagents_after_successful_bump(monkeypatch):
    from plugins import update

    versions = iter(["5.3.6", "5.3.7"])

    monkeypatch.setattr(update, "_read_version", lambda: next(versions))
    monkeypatch.setattr(update, "_backup_databases", lambda: ("backup-dir", None))
    monkeypatch.setattr(update, "_backup_code_tree", lambda: ("code-backup", None))
    monkeypatch.setattr(update, "_reinstall_pip_deps", lambda: None)
    monkeypatch.setattr(update, "_run_migrations", lambda: None)
    monkeypatch.setattr(update, "_verify_import", lambda: None)
    monkeypatch.setattr(update, "_finalize_packaged_runtime_layout", lambda: (True, None))
    monkeypatch.setattr(update, "_sync_packaged_crons", lambda progress_fn=None: (True, None))
    monkeypatch.setattr(update, "_sync_hooks_to_home", lambda: None)
    monkeypatch.setattr(update, "_cleanup_retired_runtime_files", lambda: [])
    monkeypatch.setattr(update, "_update_runtime_dependencies", lambda progress_fn=None: [])
    monkeypatch.setattr(update, "_sync_packaged_clients", lambda: (True, None))
    monkeypatch.setattr(
        update,
        "_reload_launch_agents_after_bump",
        lambda: {"scanned": 3, "reloaded": 3, "errors": []},
    )
    monkeypatch.setattr(
        update,
        "activate_versioned_runtime_snapshot",
        lambda source_root, version: {"ok": True, "version": version},
    )
    monkeypatch.setattr(
        update,
        "write_restart_required_marker",
        lambda from_version, to_version, **_kw: {"path": f"/tmp/mcp-restart-required-{to_version}.json"},
    )

    def fake_run(args, **kwargs):
        if args == ["npm", "update", "-g", "nexo-brain"]:
            return mock.Mock(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess call: {args}")

    monkeypatch.setattr(update.subprocess, "run", fake_run)

    result = update._handle_packaged_update(include_clis=False)

    assert "UPDATE SUCCESSFUL (packaged install)" in result
    assert "Version: 5.3.6 -> 5.3.7" in result
    assert "Crons: synced with manifest" in result
    assert "Hooks: synced to NEXO_HOME" in result
    assert "Clients: configured client targets synced" in result
    assert "LaunchAgents: reloaded 3/3" in result


def test_handle_packaged_update_uses_desktop_managed_npm_runtime(monkeypatch):
    from plugins import update

    versions = iter(["7.1.1", "7.1.2"])
    desktop_node = "/Applications/NEXO Desktop.app/Contents/MacOS/NEXO Desktop"
    bundled_npm_cli = "/Applications/NEXO Desktop.app/Contents/Resources/app.asar.unpacked/node_modules/npm/bin/npm-cli.js"

    monkeypatch.setenv("NEXO_DESKTOP_NODE", desktop_node)
    monkeypatch.setenv("NEXO_DESKTOP_NPM_CLI", bundled_npm_cli)
    monkeypatch.setattr(update, "_read_version", lambda: next(versions))
    monkeypatch.setattr(update, "_backup_databases", lambda: ("backup-dir", None))
    monkeypatch.setattr(update, "_backup_code_tree", lambda: ("code-backup", None))
    monkeypatch.setattr(update, "_reinstall_pip_deps", lambda: None)
    monkeypatch.setattr(update, "_run_migrations", lambda: None)
    monkeypatch.setattr(update, "_verify_import", lambda: None)
    monkeypatch.setattr(update, "_finalize_packaged_runtime_layout", lambda: (True, None))
    monkeypatch.setattr(update, "_sync_packaged_crons", lambda progress_fn=None: (True, None))
    monkeypatch.setattr(update, "_sync_hooks_to_home", lambda: None)
    monkeypatch.setattr(update, "_cleanup_retired_runtime_files", lambda: [])
    monkeypatch.setattr(update, "_update_runtime_dependencies", lambda progress_fn=None: [])
    monkeypatch.setattr(update, "_sync_packaged_clients", lambda: (True, None))
    monkeypatch.setattr(
        update,
        "_reload_launch_agents_after_bump",
        lambda: {"scanned": 1, "reloaded": 1, "errors": []},
    )
    monkeypatch.setattr(
        update,
        "activate_versioned_runtime_snapshot",
        lambda source_root, version: {"ok": True, "version": version},
    )
    monkeypatch.setattr(
        update,
        "write_restart_required_marker",
        lambda from_version, to_version, **_kw: {"path": f"/tmp/mcp-restart-required-{to_version}.json"},
    )
    monkeypatch.setattr(update.Path, "exists", lambda self: str(self) == desktop_node)

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs.get("env", {})
        return mock.Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(update.subprocess, "run", fake_run)

    result = update._handle_packaged_update(include_clis=False)

    assert "UPDATE SUCCESSFUL (packaged install)" in result
    assert captured["args"] == [desktop_node, bundled_npm_cli, "update", "-g", "nexo-brain"]
    assert captured["env"]["ELECTRON_RUN_AS_NODE"] == "1"


def test_handle_packaged_update_finalizes_layout_before_import_verification(monkeypatch):
    from plugins import update

    versions = iter(["7.1.2", "7.1.3"])
    call_order = []

    monkeypatch.setattr(update, "_read_version", lambda: next(versions))
    monkeypatch.setattr(update, "_backup_databases", lambda: ("backup-dir", None))
    monkeypatch.setattr(update, "_backup_code_tree", lambda: ("code-backup", None))
    monkeypatch.setattr(update, "_reinstall_pip_deps", lambda: None)
    monkeypatch.setattr(update, "_run_migrations", lambda: None)
    monkeypatch.setattr(
        update,
        "_finalize_packaged_runtime_layout",
        lambda: (call_order.append("finalize") or True, None),
    )
    monkeypatch.setattr(
        update,
        "_verify_import",
        lambda: (call_order.append("verify") or None),
    )
    monkeypatch.setattr(update, "_sync_packaged_crons", lambda progress_fn=None: (True, None))
    monkeypatch.setattr(update, "_sync_hooks_to_home", lambda: None)
    monkeypatch.setattr(update, "_cleanup_retired_runtime_files", lambda: [])
    monkeypatch.setattr(update, "_update_runtime_dependencies", lambda progress_fn=None: [])
    monkeypatch.setattr(update, "_sync_packaged_clients", lambda: (True, None))
    monkeypatch.setattr(
        update,
        "_reload_launch_agents_after_bump",
        lambda: {"scanned": 1, "reloaded": 1, "errors": []},
    )
    monkeypatch.setattr(
        update,
        "activate_versioned_runtime_snapshot",
        lambda source_root, version: {"ok": True, "version": version},
    )
    monkeypatch.setattr(
        update,
        "write_restart_required_marker",
        lambda from_version, to_version, **_kw: {"path": f"/tmp/mcp-restart-required-{to_version}.json"},
    )

    def fake_run(args, **kwargs):
        if args == ["npm", "update", "-g", "nexo-brain"]:
            return mock.Mock(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess call: {args}")

    monkeypatch.setattr(update.subprocess, "run", fake_run)

    result = update._handle_packaged_update(include_clis=False)

    assert "UPDATE SUCCESSFUL (packaged install)" in result
    assert call_order[:2] == ["finalize", "verify"]


def _common_packaged_update_stubs(monkeypatch, update, versions):
    monkeypatch.setattr(update, "_read_version", lambda: next(versions))
    monkeypatch.setattr(update, "_backup_databases", lambda: ("backup-dir", None))
    monkeypatch.setattr(update, "_backup_code_tree", lambda: ("code-backup", None))
    monkeypatch.setattr(update, "_reinstall_pip_deps", lambda: None)
    monkeypatch.setattr(update, "_run_migrations", lambda: None)
    monkeypatch.setattr(update, "_verify_import", lambda: None)
    monkeypatch.setattr(update, "_finalize_packaged_runtime_layout", lambda: (True, None))
    monkeypatch.setattr(update, "_sync_packaged_crons", lambda progress_fn=None: (True, None))
    monkeypatch.setattr(update, "_sync_hooks_to_home", lambda: None)
    monkeypatch.setattr(update, "_cleanup_retired_runtime_files", lambda: [])
    monkeypatch.setattr(update, "_update_runtime_dependencies", lambda progress_fn=None: [])
    monkeypatch.setattr(update, "_sync_packaged_clients", lambda: (True, None))
    monkeypatch.setattr(
        update,
        "_reload_launch_agents_after_bump",
        lambda: {"scanned": 1, "reloaded": 1, "errors": []},
    )
    monkeypatch.setattr(
        update,
        "activate_versioned_runtime_snapshot",
        lambda source_root, version: {"ok": True, "version": version},
    )

    def fake_run(args, **kwargs):
        if args == ["npm", "update", "-g", "nexo-brain"]:
            return mock.Mock(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess call: {args}")

    monkeypatch.setattr(update.subprocess, "run", fake_run)


def test_packaged_update_skips_restart_marker_when_fingerprint_unchanged(monkeypatch):
    """Doc-only / README-only release path: no `.py` byte changed → no restart."""
    from plugins import update

    versions = iter(["7.10.0", "7.10.1"])
    _common_packaged_update_stubs(monkeypatch, update, versions)
    # Same fingerprint before and after → release did not change MCP code.
    same_fp = "f" * 64
    monkeypatch.setattr(update, "compute_mcp_runtime_fingerprint", lambda root: same_fp)
    monkeypatch.setattr(update, "installed_force_restart_flag", lambda: False)

    marker_calls: list[dict] = []

    def fake_marker(**kwargs):
        marker_calls.append(kwargs)
        return {"path": "/tmp/should-not-be-written.json"}

    monkeypatch.setattr(update, "write_restart_required_marker", fake_marker)

    result = update._handle_packaged_update(include_clis=False)

    assert "UPDATE SUCCESSFUL (packaged install)" in result
    assert marker_calls == [], "Doc-only release must NOT write the restart marker"
    assert "MCP source unchanged" in result
    assert "no restart needed" in result.lower()


def test_packaged_update_writes_marker_when_fingerprint_changes(monkeypatch):
    """Real code change path: marker IS written, with both fingerprints recorded."""
    from plugins import update

    versions = iter(["7.10.1", "7.11.0"])
    _common_packaged_update_stubs(monkeypatch, update, versions)
    fingerprints = iter(["a" * 64, "b" * 64])
    monkeypatch.setattr(
        update, "compute_mcp_runtime_fingerprint", lambda root: next(fingerprints)
    )
    monkeypatch.setattr(update, "installed_force_restart_flag", lambda: False)

    marker_calls: list[dict] = []

    def fake_marker(**kwargs):
        marker_calls.append(kwargs)
        return {"path": "/tmp/mcp-restart-required.json"}

    monkeypatch.setattr(update, "write_restart_required_marker", fake_marker)

    result = update._handle_packaged_update(include_clis=False)

    assert "UPDATE SUCCESSFUL (packaged install)" in result
    assert len(marker_calls) == 1
    call = marker_calls[0]
    assert call["from_fingerprint"] == "a" * 64
    assert call["to_fingerprint"] == "b" * 64
    assert call["reason"] == "brain_update"
    assert "MCP server restart needed to load new code." in result


def test_packaged_update_force_restart_flag_writes_marker_even_when_unchanged(monkeypatch):
    """Opt-in escape hatch: `force_restart: true` in version.json → always restart."""
    from plugins import update

    versions = iter(["7.10.1", "7.11.0"])
    _common_packaged_update_stubs(monkeypatch, update, versions)
    same_fp = "f" * 64
    monkeypatch.setattr(update, "compute_mcp_runtime_fingerprint", lambda root: same_fp)
    monkeypatch.setattr(update, "installed_force_restart_flag", lambda: True)

    marker_calls: list[dict] = []

    def fake_marker(**kwargs):
        marker_calls.append(kwargs)
        return {"path": "/tmp/forced.json"}

    monkeypatch.setattr(update, "write_restart_required_marker", fake_marker)

    result = update._handle_packaged_update(include_clis=False)

    assert "UPDATE SUCCESSFUL (packaged install)" in result
    assert len(marker_calls) == 1
    assert marker_calls[0]["reason"] == "brain_update_force"


def test_packaged_update_missing_fingerprint_falls_back_to_restart(monkeypatch):
    """When fingerprint can't be computed, behave like the legacy path: write marker."""
    from plugins import update

    versions = iter(["7.10.1", "7.11.0"])
    _common_packaged_update_stubs(monkeypatch, update, versions)
    monkeypatch.setattr(update, "compute_mcp_runtime_fingerprint", lambda root: "")
    monkeypatch.setattr(update, "installed_force_restart_flag", lambda: False)

    marker_calls: list[dict] = []

    def fake_marker(**kwargs):
        marker_calls.append(kwargs)
        return {"path": "/tmp/fallback.json"}

    monkeypatch.setattr(update, "write_restart_required_marker", fake_marker)

    result = update._handle_packaged_update(include_clis=False)

    assert "UPDATE SUCCESSFUL (packaged install)" in result
    # Conservative fallback (#186): missing fingerprint → assume MCP changed.
    assert len(marker_calls) == 1


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
    # The installer calls syncRuntimePackageMetadata at three points: the
    # auto-migration flow, the Brain core install, and the legacy migration
    # branch. After the bundle-aware refactor, the first arg may be either
    # `path.join(__dirname, "..")` (no bundle) or `bundleRoot` (when a staged
    # bundle has been resolved). Count both call shapes — the total must be ≥3.
    legacy_calls = text.count('syncRuntimePackageMetadata(path.join(__dirname, ".."), NEXO_HOME);')
    bundle_calls = text.count('syncRuntimePackageMetadata(bundleRoot, NEXO_HOME);')
    assert legacy_calls + bundle_calls >= 3, (
        f"expected >=3 syncRuntimePackageMetadata call sites, got "
        f"{legacy_calls} legacy + {bundle_calls} bundleRoot"
    )


def test_packaged_installer_repairs_same_version_runtime_when_core_current_lags():
    installer = REPO_ROOT / "bin" / "nexo-brain.js"
    text = installer.read_text(encoding="utf-8")

    assert 'function readActiveRuntimeSnapshotVersion(nexoHome = NEXO_HOME)' in text
    assert 'function activateVersionedRuntimeSnapshot(python, nexoHome = NEXO_HOME, version = "")' in text
    assert 'const activeRuntimeVersion = readActiveRuntimeSnapshotVersion(NEXO_HOME);' in text
    assert 'const needsRuntimeRepair = activeRuntimeVersion !== currentVersion;' in text
    assert 'if (installedVersion !== currentVersion || needsRuntimeRepair) {' in text
    assert 'Repairing active runtime snapshot...' in text
    assert 'const migActivation = activateVersionedRuntimeSnapshot(migPython, NEXO_HOME, currentVersion);' in text
    assert 'log(`  Runtime activation: core/current -> versions/${currentVersion}`);' in text


def test_managed_runtime_wrapper_repairs_stale_core_current_before_exec():
    installer = REPO_ROOT / "bin" / "nexo-brain.js"
    source = REPO_ROOT / "src" / "auto_update.py"
    installer_text = installer.read_text(encoding="utf-8")
    source_text = source.read_text(encoding="utf-8")

    for text in (installer_text, source_text):
        assert 'read_runtime_version() {' in text
        assert 'repair_stale_current_runtime() {' in text
        assert 'from runtime_versioning import activate_versioned_runtime_snapshot, read_version_for_path' in text
        assert 'repair_stale_current_runtime' in text


def test_auto_update_falls_back_to_core_product_mode_when_root_shim_is_missing():
    auto_update = REPO_ROOT / "src" / "auto_update.py"
    text = auto_update.read_text(encoding="utf-8")

    assert 'if getattr(exc, "name", "") != "product_mode":' in text
    assert '_core_runtime = Path(__file__).resolve().parent / "core"' in text
    assert 'sys.path.insert(0, core_path)' in text
    assert text.count("from product_mode import enforce_desktop_product_contract") >= 2
