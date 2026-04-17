"""Tests for installer shell profile backfill guard.

Regression: v6.0.x `_ensure_runtime_cli_in_shell()` wrote an
``export PATH="$NEXO_HOME/bin:$PATH"`` line into the developer's real
``~/.bash_profile`` / ``~/.bashrc`` / ``~/.zshrc`` even when ``NEXO_HOME``
pointed to a pytest ``tmp_path`` or another non-canonical location.
The fix adds ``_should_skip_shell_profile_backfill()`` which skips the
write whenever ``NEXO_HOME`` is not ``$HOME/.nexo``, or the operator sets
``NEXO_SKIP_SHELL_PROFILE=1``.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _load_auto_update(monkeypatch, nexo_home: Path, home: Path):
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("NEXO_SKIP_SHELL_PROFILE", raising=False)
    import auto_update as au
    importlib.reload(au)
    return au


def test_skip_when_nexo_home_is_pytest_temp_dir(tmp_path, monkeypatch):
    """The classic reported bug: NEXO_HOME=/tmp/pytest-xxx must not write."""
    home = tmp_path / "home"
    home.mkdir()
    nexo_home = tmp_path / "fake_runtime"
    nexo_home.mkdir()
    bash_profile = home / ".bash_profile"
    bashrc = home / ".bashrc"
    zshrc = home / ".zshrc"
    for f in (bash_profile, bashrc, zshrc):
        f.write_text("# pre-existing user content\n")

    monkeypatch.setenv("SHELL", "/bin/bash")
    au = _load_auto_update(monkeypatch, nexo_home, home)

    skip, reason = au._should_skip_shell_profile_backfill()
    assert skip is True
    assert "not the canonical" in reason

    au._ensure_runtime_cli_in_shell()

    for f in (bash_profile, bashrc, zshrc):
        assert "NEXO runtime CLI" not in f.read_text(), f"{f} contaminated"
        assert "export PATH=" not in f.read_text() or f.read_text().count("export PATH") == 0


def test_skip_when_env_flag_set(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    nexo_home = home / ".nexo"
    nexo_home.mkdir()
    bash_profile = home / ".bash_profile"
    bash_profile.write_text("")

    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setenv("NEXO_SKIP_SHELL_PROFILE", "1")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    import auto_update as au
    importlib.reload(au)

    skip, reason = au._should_skip_shell_profile_backfill()
    assert skip is True
    assert "NEXO_SKIP_SHELL_PROFILE" in reason

    au._ensure_runtime_cli_in_shell()
    assert bash_profile.read_text() == ""


def test_writes_when_nexo_home_is_canonical(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    nexo_home = home / ".nexo"
    nexo_home.mkdir()
    bash_profile = home / ".bash_profile"
    bashrc = home / ".bashrc"
    bash_profile.write_text("")
    bashrc.write_text("")

    monkeypatch.setenv("SHELL", "/bin/bash")
    au = _load_auto_update(monkeypatch, nexo_home, home)

    skip, _ = au._should_skip_shell_profile_backfill()
    assert skip is False

    au._ensure_runtime_cli_in_shell()

    assert "NEXO runtime CLI" in bash_profile.read_text()
    assert f"{nexo_home}/bin" in bash_profile.read_text()


def test_env_flag_accepts_multiple_truthy_values(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    nexo_home = home / ".nexo"
    nexo_home.mkdir()

    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("NEXO_SKIP_SHELL_PROFILE", truthy)
        import auto_update as au
        importlib.reload(au)
        skip, reason = au._should_skip_shell_profile_backfill()
        assert skip is True, f"expected skip for NEXO_SKIP_SHELL_PROFILE={truthy}"
        assert "NEXO_SKIP_SHELL_PROFILE" in reason


def test_env_flag_falsy_does_not_skip_when_canonical(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    nexo_home = home / ".nexo"
    nexo_home.mkdir()

    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setenv("NEXO_SKIP_SHELL_PROFILE", "0")

    import auto_update as au
    importlib.reload(au)
    skip, _ = au._should_skip_shell_profile_backfill()
    assert skip is False
