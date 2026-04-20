from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import tarfile
import types
from pathlib import Path

import pytest


def _seed_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS sample (id INTEGER PRIMARY KEY, body TEXT)")
        conn.execute("INSERT INTO sample (body) VALUES (?)", ("hello",))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def portability_env(tmp_path, monkeypatch):
    nexo_home = tmp_path / "nexo_home"
    (nexo_home / "runtime" / "data").mkdir(parents=True)
    (nexo_home / "runtime" / "exports").mkdir(parents=True)
    (nexo_home / "runtime" / "backups").mkdir(parents=True)
    (nexo_home / "personal" / "brain").mkdir(parents=True)
    _seed_db(nexo_home / "runtime" / "data" / "nexo.db")
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    import user_data_portability as portability

    importlib.reload(portability)
    portability._reset_export_rate_limit_state_for_tests()
    monkeypatch.setattr(portability, "_load_personal_scripts", lambda: ([], []))
    return {"home": nexo_home, "mod": portability}


def test_export_user_bundle_includes_personal_f06_artifacts(portability_env):
    home = portability_env["home"]
    mod = portability_env["mod"]

    files = {
        home / "personal" / "brain" / "calibration.json": '{"language":"en"}\n',
        home / "runtime" / "coordination" / "queue.json": '{"items":[]}\n',
        home / "runtime" / "nexo-email" / "accounts.json": '{"accounts":[]}\n',
        home / "assets" / "brand.txt": "asset\n",
        home / "personal" / "plugins" / "hello.py": "print('plugin')\n",
        home / "personal" / "hooks" / "hook.sh": "#!/bin/sh\necho hook\n",
        home / "personal" / "rules" / "rule.json": '{"id":"rule"}\n',
        home / "personal" / "skills" / "sk-demo" / "skill.json": '{"id":"SK-DEMO","name":"Demo"}\n',
        home / "personal" / "config" / "guardian.json": '{"mode":"hard"}\n',
        home / "personal" / "config" / ".keychain-pass": "secret\n",
        home / "personal" / "lib" / "helper.py": "VALUE = 1\n",
        home / "personal" / "overrides" / "local.patch": "patch\n",
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    output = home / "runtime" / "exports" / "bundle.tgz"
    result = mod.export_user_bundle(str(output))
    assert result["ok"] is True

    with tarfile.open(output, "r:gz") as tar:
        names = set(tar.getnames())
        assert "bundle/brain/calibration.json" in names
        assert "bundle/coordination/queue.json" in names
        assert "bundle/nexo-email/accounts.json" in names
        assert "bundle/assets/brand.txt" in names
        assert "bundle/personal-plugins/hello.py" in names
        assert "bundle/personal-hooks/hook.sh" in names
        assert "bundle/personal-rules/rule.json" in names
        assert "bundle/personal-skills/sk-demo/skill.json" in names
        assert "bundle/personal-config/guardian.json" in names
        assert "bundle/personal-lib/helper.py" in names
        assert "bundle/personal-overrides/local.patch" in names
        assert "bundle/personal-config/.keychain-pass" not in names

        manifest = json.loads(tar.extractfile("bundle/manifest.json").read().decode("utf-8"))
        assert "personal-plugins" in manifest["sections"]
        assert "personal-hooks" in manifest["sections"]
        assert "personal-rules" in manifest["sections"]
        assert "personal-skills" in manifest["sections"]
        assert "personal-config" in manifest["sections"]
        assert "personal-lib" in manifest["sections"]
        assert "personal-overrides" in manifest["sections"]


def test_import_user_bundle_restores_personal_f06_artifacts_and_reconciles(portability_env, monkeypatch, tmp_path):
    home = portability_env["home"]
    mod = portability_env["mod"]

    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir(parents=True)
    manifest = {
        "kind": "nexo-user-data-bundle",
        "version": "7.1.0",
        "created_at": "2026-04-20T00:00:00+00:00",
        "sections": {
            "brain": {"path": "brain"},
            "personal-plugins": {"path": "personal-plugins"},
            "personal-hooks": {"path": "personal-hooks"},
            "personal-rules": {"path": "personal-rules"},
            "personal-skills": {"path": "personal-skills"},
            "personal-config": {"path": "personal-config"},
            "personal-lib": {"path": "personal-lib"},
            "personal-overrides": {"path": "personal-overrides"},
        },
    }
    (bundle_root / "manifest.json").write_text(json.dumps(manifest) + "\n")
    payloads = {
        bundle_root / "brain" / "calibration.json": '{"language":"es"}\n',
        bundle_root / "personal-plugins" / "hello.py": "print('plugin')\n",
        bundle_root / "personal-hooks" / "hook.sh": "#!/bin/sh\necho hook\n",
        bundle_root / "personal-rules" / "rule.json": '{"id":"rule"}\n',
        bundle_root / "personal-skills" / "sk-demo" / "skill.json": '{"id":"SK-DEMO","name":"Demo"}\n',
        bundle_root / "personal-config" / "guardian.json": '{"mode":"soft"}\n',
        bundle_root / "personal-lib" / "helper.py": "VALUE = 2\n",
        bundle_root / "personal-overrides" / "local.patch": "patch\n",
    }
    for path, text in payloads.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    archive_path = tmp_path / "portable-bundle.tgz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(bundle_root, arcname="bundle")

    calls: list[object] = []
    fake_db = types.ModuleType("db")
    fake_db.init_db = lambda: calls.append("init_db")
    fake_db.sync_skill_directories = lambda: calls.append("sync_skills") or {"synced": 1, "ids": ["SK-DEMO"], "issues": []}
    fake_db.retire_superseded_personal_skills = lambda dry_run=False: calls.append(("retire_skills", dry_run)) or {"retired": []}

    fake_script_registry = types.ModuleType("script_registry")
    fake_script_registry.classify_scripts_dir = lambda: {"entries": []}
    fake_script_registry.discover_personal_schedules = lambda: []
    fake_script_registry.retire_superseded_personal_scripts = lambda dry_run=False: calls.append(("retire_scripts", dry_run)) or {"archived": []}
    fake_script_registry.reconcile_personal_scripts = lambda dry_run=False: calls.append(("reconcile_scripts", dry_run)) or {"ok": True}

    monkeypatch.setitem(sys.modules, "db", fake_db)
    monkeypatch.setitem(sys.modules, "script_registry", fake_script_registry)

    result = mod.import_user_bundle(str(archive_path))
    assert result["ok"] is True
    assert result["bundle_version"] == "7.1.0"
    assert "safety_backup" in result
    assert result["skill_sync"]["synced"] == 1
    assert result["reconciled"]["ok"] is True
    assert "init_db" in calls
    assert "sync_skills" in calls
    assert ("retire_skills", False) in calls
    assert ("retire_scripts", False) in calls
    assert ("reconcile_scripts", False) in calls
    assert result["retired_superseded_scripts"]["archived"] == []

    assert (home / "personal" / "brain" / "calibration.json").read_text() == '{"language":"es"}\n'
    assert (home / "personal" / "plugins" / "hello.py").read_text() == "print('plugin')\n"
    assert (home / "personal" / "hooks" / "hook.sh").read_text() == "#!/bin/sh\necho hook\n"
    assert (home / "personal" / "rules" / "rule.json").read_text() == '{"id":"rule"}\n'
    assert (home / "personal" / "skills" / "sk-demo" / "skill.json").read_text() == '{"id":"SK-DEMO","name":"Demo"}\n'
    assert (home / "personal" / "config" / "guardian.json").read_text() == '{"mode":"soft"}\n'
    assert (home / "personal" / "lib" / "helper.py").read_text() == "VALUE = 2\n"
    assert (home / "personal" / "overrides" / "local.patch").read_text() == "patch\n"


def test_inspect_user_bundle_reports_version_relation_and_sections(portability_env, tmp_path):
    home = portability_env["home"]
    mod = portability_env["mod"]
    (home / "version.json").write_text('{"version":"7.1.2"}\n')

    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir(parents=True)
    (bundle_root / "manifest.json").write_text(json.dumps({
        "kind": "nexo-user-data-bundle",
        "version": "7.0.0",
        "created_at": "2026-04-20T00:00:00+00:00",
        "sections": {
            "brain": {"path": "brain"},
            "personal-config": {"path": "personal-config"},
        },
    }) + "\n")
    archive_path = tmp_path / "inspect-bundle.tgz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(bundle_root, arcname="bundle")

    result = mod.inspect_user_bundle(str(archive_path))
    assert result["ok"] is True
    assert result["bundle_version"] == "7.0.0"
    assert result["current_version"] == "7.1.2"
    assert result["version_relation"] == "bundle_older"
    assert result["section_count"] == 2
    assert result["section_names"] == ["brain", "personal-config"]
    assert "bundle_older" in result["warning_codes"]


def test_import_user_bundle_bypasses_export_rate_limit_for_safety_backup(portability_env, monkeypatch, tmp_path):
    home = portability_env["home"]
    mod = portability_env["mod"]

    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir(parents=True)
    (bundle_root / "manifest.json").write_text(json.dumps({
        "kind": "nexo-user-data-bundle",
        "version": "7.1.2",
        "created_at": "2026-04-20T00:00:00+00:00",
        "sections": {},
    }) + "\n")
    archive_path = tmp_path / "portable-bundle.tgz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(bundle_root, arcname="bundle")

    export_calls: list[bool] = []

    def _fake_export(output_path: str = "", *, enforce_rate_limit: bool = True):
        export_calls.append(enforce_rate_limit)
        return {"ok": True, "path": output_path}

    calls: list[object] = []
    fake_db = types.ModuleType("db")
    fake_db.init_db = lambda: calls.append("init_db")
    fake_db.sync_skill_directories = lambda: calls.append("sync_skills") or {"synced": 0, "ids": [], "issues": []}
    fake_db.retire_superseded_personal_skills = lambda dry_run=False: calls.append(("retire_skills", dry_run)) or {"retired": []}

    fake_script_registry = types.ModuleType("script_registry")
    fake_script_registry.classify_scripts_dir = lambda: {"entries": []}
    fake_script_registry.discover_personal_schedules = lambda: []
    fake_script_registry.retire_superseded_personal_scripts = lambda dry_run=False: calls.append(("retire_scripts", dry_run)) or {"archived": []}
    fake_script_registry.reconcile_personal_scripts = lambda dry_run=False: calls.append(("reconcile_scripts", dry_run)) or {"ok": True}

    monkeypatch.setattr(mod, "export_user_bundle", _fake_export)
    monkeypatch.setitem(sys.modules, "db", fake_db)
    monkeypatch.setitem(sys.modules, "script_registry", fake_script_registry)

    result = mod.import_user_bundle(str(archive_path))
    assert result["ok"] is True
    assert export_calls == [False]
    assert result["warning_codes"] == ["no_sections"]
    assert "init_db" in calls
