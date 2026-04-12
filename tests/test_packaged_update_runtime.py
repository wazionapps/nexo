from __future__ import annotations

import json
import sys
from pathlib import Path

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

    monkeypatch.setattr(update, "NEXO_HOME", runtime_home)
    monkeypatch.setattr(client_sync, "sync_all_clients", fake_sync_all_clients)

    ok, err = update._sync_packaged_clients()

    assert ok is True
    assert err is None
    assert captured["nexo_home"] == runtime_home
    assert captured["runtime_root"] == runtime_home
    updated_schedule = json.loads(schedule_path.read_text())
    assert "interactive_clients" in updated_schedule
    assert "automation_enabled" in updated_schedule
