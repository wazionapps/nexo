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


def test_heal_misplaced_personal_watchdog_runtime_files_prunes_or_promotes(monkeypatch, tmp_path):
    home = tmp_path
    (home / "core" / "scripts").mkdir(parents=True)
    (home / "personal" / "scripts").mkdir(parents=True)
    (home / "core" / "scripts" / ".watchdog-hashes").write_text("core\n")
    (home / "core" / "scripts" / ".watchdog-fails").write_text("1\n")
    (home / "core" / "scripts" / ".watchdog-nexo-repair.lock").write_text("")
    (home / "personal" / "scripts" / ".watchdog-hashes").write_text("personal-newer\n")
    fail_personal = home / "personal" / "scripts" / ".watchdog-fails"
    fail_personal.write_text("1\n")
    (home / "personal" / "scripts" / ".watchdog-nexo-repair.lock").write_text("")

    # Make the personal hash registry the newest copy so the helper promotes it.
    import os
    newer = (home / "personal" / "scripts" / ".watchdog-hashes")
    os.utime(newer, (newer.stat().st_atime + 10, newer.stat().st_mtime + 10))
    # Keep the core fail counter newer so the personal duplicate is pruned.
    fail_core = home / "core" / "scripts" / ".watchdog-fails"
    os.utime(fail_core, (fail_core.stat().st_atime + 20, fail_core.stat().st_mtime + 20))

    au = _reload_auto_update(monkeypatch, home)
    actions = au._heal_misplaced_personal_watchdog_runtime_files()

    assert "watchdog-runtime-file-promoted:.watchdog-hashes" in actions
    assert "watchdog-runtime-file-pruned:.watchdog-fails" in actions
    assert "watchdog-runtime-file-pruned:.watchdog-nexo-repair.lock" in actions
    assert (home / "core" / "scripts" / ".watchdog-hashes").read_text() == "personal-newer\n"
    assert not (home / "personal" / "scripts" / ".watchdog-hashes").exists()
    assert not (home / "personal" / "scripts" / ".watchdog-fails").exists()
    assert not (home / "personal" / "scripts" / ".watchdog-nexo-repair.lock").exists()


def test_maybe_migrate_f06_does_not_create_legacy_shims_for_already_canonical_install(monkeypatch, tmp_path):
    home = tmp_path
    (home / ".structure-version").write_text("F0.6\n")
    (home / "core").mkdir(parents=True)
    (home / "personal" / "brain").mkdir(parents=True)
    (home / "personal" / "config").mkdir(parents=True)
    (home / "runtime" / "data").mkdir(parents=True)
    (home / "core" / "server.py").write_text("print('runtime')\n")

    au = _reload_auto_update(monkeypatch, home)
    au._maybe_migrate_to_f06_layout()

    assert not (home / "brain").exists()
    assert not (home / "config").exists()
    assert not (home / "server.py").exists()


def test_maybe_migrate_f06_removes_root_pycache_residue(monkeypatch, tmp_path):
    home = tmp_path
    (home / ".structure-version").write_text("F0.6\n")
    (home / "core").mkdir(parents=True)
    cache_dir = home / "__pycache__"
    cache_dir.mkdir(parents=True)
    (cache_dir / "email_config.cpython-314.pyc").write_bytes(b"pyc")

    au = _reload_auto_update(monkeypatch, home)
    au._maybe_migrate_to_f06_layout()

    assert not cache_dir.exists()


def test_maybe_migrate_f06_moves_runtime_state_and_operator_scratch_roots(monkeypatch, tmp_path):
    home = tmp_path
    (home / ".structure-version").write_text("F0.6\n")
    (home / "state").mkdir(parents=True)
    (home / "state" / "vrbo-calendar-monitor.json").write_text('{"ok":true}\n')
    (home / "workdir" / "project-a").mkdir(parents=True)
    (home / "workdir" / "project-a" / "draft.txt").write_text("draft\n")
    (home / "working" / "patches").mkdir(parents=True)
    (home / "working" / "patches" / "fix.patch.md").write_text("patch\n")
    (home / "CLAUDE.md.generated").write_text("# generated\n")

    au = _reload_auto_update(monkeypatch, home)
    au._maybe_migrate_to_f06_layout()

    assert (home / "runtime" / "state" / "vrbo-calendar-monitor.json").is_file()
    assert (home / "state").is_symlink()
    assert (home / "state").resolve() == (home / "runtime" / "state").resolve()

    assert (home / "personal" / "lib" / "workdir" / "project-a" / "draft.txt").is_file()
    assert (home / "workdir").is_symlink()
    assert (home / "workdir").resolve() == (home / "personal" / "lib" / "workdir").resolve()

    assert (home / "personal" / "lib" / "working" / "patches" / "fix.patch.md").is_file()
    assert (home / "working").is_symlink()
    assert (home / "working").resolve() == (home / "personal" / "lib" / "working").resolve()

    assert (home / "personal" / "lib" / "generated" / "CLAUDE.md.generated").is_file()
    assert (home / "CLAUDE.md.generated").is_symlink()
    assert (home / "CLAUDE.md.generated").resolve() == (
        home / "personal" / "lib" / "generated" / "CLAUDE.md.generated"
    ).resolve()
