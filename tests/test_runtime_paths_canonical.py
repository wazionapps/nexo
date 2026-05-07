from __future__ import annotations

import importlib
import json
import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_plugin_loader_prefers_personal_plugins_dir_after_f06(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo-home"
    (nexo_home / "personal" / "plugins").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    import plugin_loader
    importlib.reload(plugin_loader)

    assert plugin_loader.PERSONAL_PLUGINS_DIR == str(nexo_home / "personal" / "plugins")


def test_auto_close_sessions_logs_follow_runtime_paths(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo-home"
    (nexo_home / "core" / "db").mkdir(parents=True)
    (nexo_home / "runtime" / "operations").mkdir(parents=True)
    (nexo_home / "runtime" / "coordination").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    for name in ("db", "db._core", "db._schema", "db._reminders", "db._fts", "auto_close_sessions"):
        sys.modules.pop(name, None)

    import auto_close_sessions
    importlib.reload(auto_close_sessions)

    assert auto_close_sessions.LOG_DIR == str(nexo_home / "runtime" / "operations" / "tool-logs")
    assert auto_close_sessions.AUTO_CLOSE_LOG == str(nexo_home / "runtime" / "coordination" / "auto-close.log")


def test_storage_router_default_paths_follow_runtime_layout(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo-home"
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    import storage_router
    importlib.reload(storage_router)

    router = storage_router.StorageRouter("default")
    assert router.nexo_db_path() == str(nexo_home / "runtime" / "data" / "nexo.db")
    assert router.cognitive_db_path() == str(nexo_home / "runtime" / "cognitive" / "cognitive.db")


def test_migrate_embeddings_uses_runtime_cognitive_dir(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo-home"
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    import migrate_embeddings
    importlib.reload(migrate_embeddings)

    assert migrate_embeddings.DB_PATH == str(nexo_home / "runtime" / "cognitive" / "cognitive.db")
    assert migrate_embeddings.BACKUP_PATH.endswith("runtime/cognitive/cognitive.db.bak-embedding-current-pre-upgrade")


def test_local_models_use_runtime_models_dir(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo-home"
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    import local_models
    importlib.reload(local_models)

    assert local_models.models_dir() == nexo_home / "runtime" / "models"


def test_fts_builtin_dirs_follow_runtime_and_personal_layout(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo-home"
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    for name in ("db", "db._core", "db._schema", "db._reminders", "db._fts"):
        sys.modules.pop(name, None)

    import db._fts as db_fts
    importlib.reload(db_fts)

    dirs = set(db_fts._FTS_MD_DIRS)
    assert str(nexo_home / "runtime" / "memory") in dirs
    assert str(nexo_home / "runtime" / "operations") in dirs
    assert str(nexo_home / "personal" / "brain") in dirs
    assert str(nexo_home / "personal" / "skills") in dirs


def test_adaptive_mode_and_reflection_follow_personal_and_runtime_paths(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo-home"
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    import plugins.adaptive_mode as adaptive_mode
    importlib.reload(adaptive_mode)

    reflection_path = SRC / "scripts" / "nexo-reflection.py"
    spec = importlib.util.spec_from_file_location("nexo_reflection_test", reflection_path)
    reflection = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(reflection)

    assert adaptive_mode.ADAPTIVE_STATE_FILE == str(nexo_home / "personal" / "brain" / "adaptive_state.json")
    assert reflection.BUFFER_PATH == str(nexo_home / "personal" / "brain" / "session_buffer.jsonl")
    assert reflection.USER_MODEL_PATH == str(nexo_home / "personal" / "brain" / "user_model.json")
    assert reflection.REFLECTION_LOG_PATH == str(nexo_home / "runtime" / "coordination" / "reflection-log.json")


def test_heartbeat_enforcement_state_file_follows_runtime_operations(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo-home"
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    import importlib.util

    heartbeat_path = SRC / "hooks" / "heartbeat-enforcement.py"
    spec = importlib.util.spec_from_file_location("heartbeat_enforcement_test", heartbeat_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.STATE_FILE == nexo_home / "runtime" / "operations" / ".heartbeat-state.json"


def test_server_backup_dir_follows_runtime_backups(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo-home"
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    import server
    importlib.reload(server)

    assert server._backup_dir() == str(nexo_home / "runtime" / "backups")


def test_tools_sessions_loads_session_tone_from_runtime_operations(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo-home"
    tone_dir = nexo_home / "runtime" / "operations"
    tone_dir.mkdir(parents=True)
    legacy_dir = nexo_home / "operations"
    legacy_dir.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    payload = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "mood_yesterday": 0.9,
        "approach": "focused",
    }
    (tone_dir / "session-tone.json").write_text(json.dumps(payload))
    (legacy_dir / "session-tone.json").write_text(json.dumps({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "mood_yesterday": 0.1,
        "approach": "legacy-root",
    }))

    import tools_sessions
    importlib.reload(tools_sessions)

    rendered = tools_sessions._load_session_tone()
    assert rendered is not None
    assert "Approach today: focused" in rendered
    assert "legacy-root" not in rendered


def test_tools_menu_uses_core_scripts_dir_for_proactive_dashboard(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo-home"
    scripts_dir = nexo_home / "core" / "scripts"
    scripts_dir.mkdir(parents=True)
    script = scripts_dir / "nexo-proactive-dashboard.py"
    script.write_text("#!/usr/bin/env python3\nprint('[]')\n")
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    import tools_menu
    importlib.reload(tools_menu)

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    monkeypatch.setattr(tools_menu.subprocess, "run", fake_run)

    assert tools_menu._get_dashboard_alerts() == []
    assert captured["cmd"][1] == str(script)


def test_paths_core_dir_prefers_populated_core_root_over_current_snapshot(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo-home"
    core_root = nexo_home / "core"
    version_root = core_root / "versions" / "5.3.7"
    version_root.mkdir(parents=True)
    (core_root / "server.py").write_text("print('root')\n")
    (version_root / "server.py").write_text("print('snapshot')\n")
    (core_root / "current").symlink_to(Path("versions") / "5.3.7")
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    import paths
    importlib.reload(paths)

    assert paths.core_dir() == core_root


def test_paths_core_dir_falls_back_to_current_snapshot_when_core_root_is_only_container(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo-home"
    core_root = nexo_home / "core"
    version_root = core_root / "versions" / "7.9.6"
    version_root.mkdir(parents=True)
    (version_root / "server.py").write_text("print('snapshot')\n")
    (core_root / "current").symlink_to(Path("versions") / "7.9.6")
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    import paths
    importlib.reload(paths)

    assert paths.core_dir() == version_root.resolve()
