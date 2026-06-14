"""Tests for offline-first dependency (re)install (Release-installer / B).

The managed-venv repair must install from the BUNDLED wheels (offline) when they
are available, falling back to PyPI only when they are not — so a user with no
internet still gets a self-repairing runtime.
"""

from pathlib import Path

import auto_update


def test_bundled_wheels_dir_prefers_env(monkeypatch, tmp_path):
    wd = tmp_path / "wheels"
    wd.mkdir()
    (wd / "pypdf-4.0-py3-none-any.whl").write_text("x")
    monkeypatch.setenv("NEXO_BUNDLED_WHEELS_DIR", str(wd))
    assert auto_update._bundled_wheels_dir() == wd


def test_bundled_wheels_dir_none_when_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_BUNDLED_WHEELS_DIR", str(tmp_path / "does-not-exist"))
    monkeypatch.setattr(auto_update, "NEXO_HOME", tmp_path / "home")
    assert auto_update._bundled_wheels_dir() is None


def test_pip_install_argv_offline_uses_find_links(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("numpy\n")
    wd = tmp_path / "wheels"
    wd.mkdir()
    argv = auto_update._pip_install_argv("/venv/bin/pip", req, wheels_dir=wd)
    assert "--no-index" in argv
    assert "--find-links" in argv
    assert str(wd) in argv
    assert str(req) in argv
    assert "install" in argv


def test_pip_install_argv_online_when_no_wheels(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("numpy\n")
    argv = auto_update._pip_install_argv("/venv/bin/pip", req, wheels_dir=None)
    assert "--no-index" not in argv
    assert "--find-links" not in argv
    assert str(req) in argv
