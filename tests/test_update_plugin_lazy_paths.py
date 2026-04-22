"""Regression tests for the B10 mini-refactor of src/plugins/update.py.

Prior to the refactor, the default argument of ``_venv_python_path``,
``_venv_pip_path`` and ``_ensure_managed_venv`` was evaluated at module
import time: ``def _venv_python_path(runtime_root: Path = NEXO_HOME)``.
A test that monkeypatched NEXO_HOME after import kept getting the
original path because Python binds default values once at definition.

The refactored signatures accept ``Path | None = None`` and call
``_nexo_home()`` inside the body, so the resolution follows the live
env / config state on every call.

The ``_is_packaged_install()`` helper runs the .git probe on every
call so fixtures that stage or hide a synthetic .git marker after
import also see the fresh state.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest


REPO_SRC = Path(__file__).resolve().parent.parent / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


@pytest.fixture
def fresh_update_module(tmp_path, monkeypatch):
    """Import plugins.update with NEXO_HOME pointing at tmp_path/a."""
    home_a = tmp_path / "nexo-a"
    home_a.mkdir()
    monkeypatch.setenv("NEXO_HOME", str(home_a))
    from plugins import update as plugin_update  # noqa: E402
    importlib.reload(plugin_update)
    return plugin_update, home_a


def test_venv_python_path_follows_env_change_after_import(fresh_update_module, tmp_path, monkeypatch):
    plugin_update, home_a = fresh_update_module

    original = plugin_update._venv_python_path()
    assert str(home_a) in str(original), f"expected path rooted in {home_a}, got {original}"

    home_b = tmp_path / "nexo-b"
    home_b.mkdir()
    monkeypatch.setenv("NEXO_HOME", str(home_b))

    updated = plugin_update._venv_python_path()
    assert str(home_b) in str(updated), (
        "default-arg bug regressed: _venv_python_path returned a path under "
        f"the original NEXO_HOME ({home_a}) instead of the new one ({home_b}). "
        f"Got {updated}."
    )
    assert str(home_a) not in str(updated)


def test_venv_pip_path_follows_env_change_after_import(fresh_update_module, tmp_path, monkeypatch):
    plugin_update, home_a = fresh_update_module

    original = plugin_update._venv_pip_path()
    assert str(home_a) in str(original)

    home_b = tmp_path / "nexo-b"
    home_b.mkdir()
    monkeypatch.setenv("NEXO_HOME", str(home_b))

    updated = plugin_update._venv_pip_path()
    assert str(home_b) in str(updated)
    assert str(home_a) not in str(updated)


def test_explicit_runtime_root_overrides_env(fresh_update_module, tmp_path):
    """Signature-compat: callers that pass runtime_root explicitly still
    win over the lazy default."""
    plugin_update, _ = fresh_update_module

    explicit = tmp_path / "explicit-root"
    explicit.mkdir()
    path = plugin_update._venv_python_path(explicit)
    assert str(explicit) in str(path)


def test_is_packaged_install_is_lazy(tmp_path, monkeypatch):
    """_PACKAGED_INSTALL used to freeze at module import. The helper must
    re-probe the filesystem on every call so tests that stage a repo layout
    after import are honoured."""
    from plugins import update as plugin_update
    importlib.reload(plugin_update)

    # The real repo under which the test runs is a git checkout — so the
    # helper should report False there.
    assert plugin_update._is_packaged_install() is False

    # Redirect the probe's _REPO_CANDIDATE to a directory with no .git.
    fake_candidate = tmp_path / "packaged-root"
    fake_candidate.mkdir()
    monkeypatch.setattr(plugin_update, "_REPO_CANDIDATE", fake_candidate)
    assert plugin_update._is_packaged_install() is True

    # Materialise a .git marker and the helper flips back without re-importing.
    (fake_candidate / ".git").mkdir()
    assert plugin_update._is_packaged_install() is False
