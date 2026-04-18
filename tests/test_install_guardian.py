"""Tests for Fase E installer (scripts/install_guardian.py)."""
from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))


@pytest.fixture
def import_installer():
    # install_guardian.py doesn't use NEXO_HOME env var directly; it
    # takes an explicit nexo_home argument. Clean import per test.
    import importlib
    import install_guardian  # noqa
    importlib.reload(install_guardian)
    return install_guardian


def test_dry_run_on_fresh_home_reports_all_creates(tmp_path, import_installer):
    result = import_installer.install(tmp_path / "fresh", dry_run=True, force=False)
    assert result["dry_run"] is True
    assert result["config_dir_created"] is True
    assert result["preset_dir_created"] is True
    assert result["guardian_json"] == "would-create"
    assert result["entities_preset_copy"] in {"would-copy", "skipped-source-missing"}
    assert result["guardian_default_preset_copy"] in {"would-copy", "skipped-source-missing"}


def test_installs_guardian_on_fresh_home(tmp_path, import_installer):
    home = tmp_path / "fresh"
    result = import_installer.install(home, dry_run=False, force=False)
    assert result["config_dir_created"] is True
    assert result["guardian_json"] == "created"
    cfg = json.loads((home / "config" / "guardian.json").read_text())
    assert isinstance(cfg, dict)
    assert "rules" in cfg
    # Core rules present with non-off mode.
    for core in ["R13_pre_edit_guard", "R16_declared_done", "R25_nora_maria_read_only"]:
        if core in cfg["rules"]:
            assert cfg["rules"][core] in {"shadow", "soft", "hard"}


def test_does_not_overwrite_user_guardian(tmp_path, import_installer):
    home = tmp_path / "seeded"
    (home / "config").mkdir(parents=True)
    user_cfg = {"version": "0.0.1", "rules": {"R13_pre_edit_guard": "shadow", "custom": "soft"}}
    (home / "config" / "guardian.json").write_text(json.dumps(user_cfg))
    result = import_installer.install(home, dry_run=False, force=False)
    # Merge path — user override for R13 stays; missing keys added.
    merged = json.loads((home / "config" / "guardian.json").read_text())
    assert merged["rules"]["R13_pre_edit_guard"] == "shadow"  # user-set preserved
    assert merged["rules"]["custom"] == "soft"  # user-only key preserved
    assert "R16_declared_done" in merged["rules"] or "merged" in result["guardian_json"] or "skipped" in result["guardian_json"]


def test_force_overwrites_user_guardian(tmp_path, import_installer):
    home = tmp_path / "seeded"
    (home / "config").mkdir(parents=True)
    user_cfg = {"version": "0.0.1", "rules": {"R13_pre_edit_guard": "shadow"}}
    (home / "config" / "guardian.json").write_text(json.dumps(user_cfg))
    result = import_installer.install(home, dry_run=False, force=True)
    assert result["guardian_json"] == "force-overwritten"
    fresh = json.loads((home / "config" / "guardian.json").read_text())
    assert fresh["rules"].get("R13_pre_edit_guard") != "shadow"  # overwritten to packaged default


def test_preset_copy_is_idempotent(tmp_path, import_installer):
    home = tmp_path / "idem"
    r1 = import_installer.install(home, dry_run=False, force=False)
    r2 = import_installer.install(home, dry_run=False, force=False)
    # Second run should be a no-op / up-to-date.
    assert "skipped-up-to-date" in (r2["entities_preset_copy"], r2["guardian_default_preset_copy"])


def test_ssh_import_noop_when_config_missing(tmp_path, import_installer, monkeypatch):
    # Point HOME to a tmp without .ssh/config.
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    (tmp_path / "nohome").mkdir()
    result = import_installer.install(tmp_path / "noconf", dry_run=False, force=False)
    assert result["ssh_import"]["status"].startswith("skipped-no-ssh-config")
    assert result["ssh_import"]["hosts"] == []


def test_ssh_import_parses_explicit_hosts(tmp_path, import_installer, monkeypatch):
    fake_home = tmp_path / "ssh_home"
    (fake_home / ".ssh").mkdir(parents=True)
    (fake_home / ".ssh" / "config").write_text(
        "# comment\nHost vicshop\n  HostName 1.2.3.4\n\nHost wazion-gcp gcp-backup\n  User root\n\nHost *\n  ForwardAgent yes\n"
    )
    monkeypatch.setenv("HOME", str(fake_home))
    # pathlib.Path.home() reads HOME env on posix.
    import pathlib as _pl
    assert _pl.Path.home() == fake_home
    result = import_installer.install(tmp_path / "ssh_nexo", dry_run=False, force=False)
    hosts = result["ssh_import"]["hosts"]
    names = {h["name"] for h in hosts}
    # Wildcards filtered out; explicit hosts imported.
    assert "vicshop" in names
    assert "wazion-gcp" in names
    assert "gcp-backup" in names
    assert "*" not in names
    for h in hosts:
        assert h["metadata"]["access_mode"] == "unknown"


def test_automation_backend_set_on_fresh_install(tmp_path, import_installer):
    home = tmp_path / "auto1"
    result = import_installer.install(home, dry_run=False, force=False)
    assert result["automation_backend"].startswith("set automation_backend="), result
    schedule = json.loads((home / "config" / "schedule.json").read_text())
    assert schedule["automation_backend"] in {"claude_code", "codex"}


def test_automation_backend_respects_user_override(tmp_path, import_installer):
    home = tmp_path / "auto2"
    (home / "config").mkdir(parents=True)
    (home / "config" / "schedule.json").write_text(json.dumps({
        "automation_user_override": True,
        "automation_backend": "none",
    }))
    result = import_installer.install(home, dry_run=False, force=False)
    assert result["automation_backend"] == "skipped-user-override"
    schedule = json.loads((home / "config" / "schedule.json").read_text())
    assert schedule["automation_backend"] == "none"
