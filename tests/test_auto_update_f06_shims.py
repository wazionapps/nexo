from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _reload_auto_update(monkeypatch, home: Path):
    monkeypatch.setenv("NEXO_HOME", str(home))
    sys.modules.pop("auto_update", None)
    import auto_update as au

    return importlib.reload(au)


def test_ensure_f06_legacy_shims_moves_config_and_links(monkeypatch, tmp_path):
    home = tmp_path
    (home / ".structure-version").write_text("F0.6\n")
    legacy_config = home / "config"
    canonical_config = home / "personal" / "config"
    legacy_brain = home / "brain"
    canonical_brain = home / "personal" / "brain"
    legacy_config.mkdir(parents=True)
    canonical_config.mkdir(parents=True)
    legacy_brain.mkdir(parents=True)
    canonical_brain.mkdir(parents=True)
    (legacy_config / "schedule.json").write_text('{"auto_update": true}\n')
    (legacy_brain / "session_buffer.jsonl").write_text("x\n")

    au = _reload_auto_update(monkeypatch, home)
    au._ensure_f06_legacy_shims()

    assert (canonical_config / "schedule.json").exists()
    assert (canonical_brain / "session_buffer.jsonl").exists()
    assert (home / "config").is_symlink()
    assert (home / "brain").is_symlink()
    assert (home / "config").resolve() == canonical_config.resolve()
    assert (home / "brain").resolve() == canonical_brain.resolve()


def test_maybe_migrate_f06_promotes_packaged_code_into_core(monkeypatch, tmp_path):
    home = tmp_path
    (home / ".structure-version").write_text("F0.6\n")
    (home / "db").mkdir(parents=True)
    (home / "db" / "__init__.py").write_text("# db package\n")
    (home / "skills-core" / "demo-skill").mkdir(parents=True)
    (home / "skills-core" / "demo-skill" / "skill.json").write_text("{}\n")
    (home / "server.py").write_text("print('runtime')\n")

    au = _reload_auto_update(monkeypatch, home)
    au._maybe_migrate_to_f06_layout()

    assert (home / "core" / "db" / "__init__.py").is_file()
    assert (home / "db").is_symlink()
    assert (home / "db").resolve() == (home / "core" / "db").resolve()

    assert (home / "core" / "skills" / "demo-skill" / "skill.json").is_file()
    assert (home / "skills-core").is_symlink()
    assert (home / "skills-core").resolve() == (home / "core" / "skills").resolve()

    assert (home / "core" / "server.py").is_file()
    assert (home / "server.py").is_symlink()
    assert (home / "server.py").resolve() == (home / "core" / "server.py").resolve()


def test_maybe_migrate_f06_reads_runtime_core_manifest_from_legacy_config_before_config_move(monkeypatch, tmp_path):
    home = tmp_path
    (home / "scripts").mkdir(parents=True)
    (home / "config").mkdir(parents=True)
    # Reproduce the fresh-install sequence: F0.6 will create personal/config
    # before moving the legacy config tree, so the manifest must still be found
    # under ~/.nexo/config during script classification.
    (home / "config" / "runtime-core-artifacts.json").write_text(
        '{"script_names":["nexo-email-monitor.py","nexo-followup-runner.py"],"hook_names":[]}\n'
    )
    (home / "scripts" / "nexo-email-monitor.py").write_text("print('core email')\n")
    (home / "scripts" / "nexo-followup-runner.py").write_text("print('core followup')\n")
    (home / "scripts" / "custom-personal.py").write_text("print('personal')\n")

    au = _reload_auto_update(monkeypatch, home)
    au._maybe_migrate_to_f06_layout()

    assert (home / "core" / "scripts" / "nexo-email-monitor.py").is_file()
    assert (home / "core" / "scripts" / "nexo-followup-runner.py").is_file()
    assert (home / "personal" / "scripts" / "custom-personal.py").is_file()
    assert (home / "scripts").is_symlink()
    assert (home / "scripts").resolve() == (home / "core" / "scripts").resolve()
    assert (home / "personal" / "config" / "runtime-core-artifacts.json").is_file()


def test_maybe_migrate_f06_keeps_watchdog_runtime_files_under_core_scripts(monkeypatch, tmp_path):
    home = tmp_path
    (home / "scripts").mkdir(parents=True)
    (home / "config").mkdir(parents=True)
    (home / "config" / "runtime-core-artifacts.json").write_text(
        '{"script_names":["nexo-watchdog.sh"],"hook_names":[]}\n'
    )
    (home / "scripts" / "nexo-watchdog.sh").write_text("#!/bin/bash\necho watchdog\n")
    (home / "scripts" / ".watchdog-hashes").write_text("/tmp/demo|deadbeef\n")
    (home / "scripts" / ".watchdog-fails").write_text("3\n")

    au = _reload_auto_update(monkeypatch, home)
    au._maybe_migrate_to_f06_layout()

    assert (home / "core" / "scripts" / "nexo-watchdog.sh").is_file()
    assert (home / "core" / "scripts" / ".watchdog-hashes").is_file()
    assert (home / "core" / "scripts" / ".watchdog-fails").is_file()
    assert not (home / "personal" / "scripts" / ".watchdog-hashes").exists()
    assert not (home / "personal" / "scripts" / ".watchdog-fails").exists()
