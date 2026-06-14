"""Tests for the managed-venv module auto-repair check (Release-installer / C).

The user's venv silently lost optional parsers (pypdf...). There was no check that
verified MODULE presence (only the Python version). This adds a boot-tier check that
detects missing critical modules and, with fix=True, reinstalls them automatically —
so the runtime repairs itself on startup with no user action.
"""

import sys
from pathlib import Path

from doctor.providers import boot


def test_missing_venv_modules_detects_absent_module():
    py = Path(sys.executable)
    missing = boot._missing_venv_modules(py, ["sys", "json", "nexo_not_a_real_module_xyz"])
    assert "nexo_not_a_real_module_xyz" in missing
    assert "sys" not in missing
    assert "json" not in missing


def test_check_managed_venv_modules_degraded_when_module_missing(monkeypatch):
    monkeypatch.setattr(boot, "_managed_venv_python_path", lambda: Path(sys.executable))
    monkeypatch.setattr(boot, "_desktop_product_requested", lambda: True)
    monkeypatch.setattr(boot, "_missing_venv_modules", lambda venv, mods: ["pypdf"])
    check = boot.check_managed_venv_modules(fix=False)
    assert check.status == "degraded"
    assert "pypdf" in " ".join(check.evidence)


def test_check_managed_venv_modules_fix_repairs(monkeypatch):
    monkeypatch.setattr(boot, "_managed_venv_python_path", lambda: Path(sys.executable))
    monkeypatch.setattr(boot, "_desktop_product_requested", lambda: True)
    state = {"missing": ["pypdf"]}
    monkeypatch.setattr(boot, "_missing_venv_modules", lambda venv, mods: list(state["missing"]))

    def fake_repair():
        state["missing"] = []
        return True

    monkeypatch.setattr(boot, "_repair_managed_venv_deps", fake_repair)
    check = boot.check_managed_venv_modules(fix=True)
    assert check.status == "healthy"
    assert check.fixed is True


def test_managed_venv_modules_registered_in_boot_checks(monkeypatch):
    # The check must actually run in the boot tier (which startup_preflight executes).
    monkeypatch.setattr(boot, "_managed_venv_python_path", lambda: Path(sys.executable))
    monkeypatch.setattr(boot, "_desktop_product_requested", lambda: True)
    monkeypatch.setattr(boot, "_missing_venv_modules", lambda venv, mods: [])
    ids = [c.id for c in boot.run_boot_checks(fix=False)]
    assert "boot.managed_venv_modules" in ids
