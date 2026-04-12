from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parents[1] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def test_resolve_nexo_home_prefers_managed_home_for_legacy_symlink(tmp_path, monkeypatch):
    from runtime_home import resolve_nexo_home

    home = tmp_path / "home"
    managed = home / ".nexo"
    legacy = home / "claude"
    managed.mkdir(parents=True)
    legacy.symlink_to(managed)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NEXO_HOME", str(legacy))

    assert resolve_nexo_home() == managed


def test_resolve_nexo_home_keeps_custom_non_managed_path(tmp_path, monkeypatch):
    from runtime_home import resolve_nexo_home

    home = tmp_path / "home"
    custom = tmp_path / "custom-runtime"
    home.mkdir(parents=True)
    custom.mkdir(parents=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NEXO_HOME", str(custom))

    assert resolve_nexo_home() == custom
