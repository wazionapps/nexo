import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def test_resolve_claude_cli_desktop_managed_prefers_managed_binary(monkeypatch, tmp_path):
    import claude_cli

    home = tmp_path / "home"
    nexo_home = home / ".nexo"
    managed = nexo_home / "runtime" / "bootstrap" / "npm-global" / "bin" / "claude"
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_text("#!/bin/sh\n")
    managed.chmod(0o755)

    persisted = nexo_home / "config" / "claude-cli-path"
    persisted.parent.mkdir(parents=True, exist_ok=True)
    persisted.write_text("/usr/local/bin/claude\n", encoding="utf-8")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setenv("NEXO_DESKTOP_MANAGED", "1")
    monkeypatch.setenv("CLAUDE_BIN", "/opt/global/claude")
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "/usr/bin/claude")

    assert claude_cli.resolve_claude_cli() == str(managed)


def test_resolve_claude_cli_desktop_managed_does_not_fallback_to_global(monkeypatch, tmp_path):
    import claude_cli

    home = tmp_path / "home"
    nexo_home = home / ".nexo"
    persisted = nexo_home / "config" / "claude-cli-path"
    persisted.parent.mkdir(parents=True, exist_ok=True)
    persisted.write_text("/usr/local/bin/claude\n", encoding="utf-8")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setenv("NEXO_DESKTOP_MANAGED", "1")
    monkeypatch.setenv("CLAUDE_BIN", "/opt/global/claude")
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "/usr/bin/claude")

    assert claude_cli.resolve_claude_cli() == ""


def test_desktop_product_requested_with_explicit_home_ignores_global_app_marker(monkeypatch, tmp_path):
    import claude_cli

    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    real_exists = claude_cli.Path.exists

    def fake_exists(path_obj):
        if str(path_obj) == "/Applications/NEXO Desktop.app":
            return True
        return real_exists(path_obj)

    monkeypatch.setattr(claude_cli.Path, "exists", fake_exists)
    monkeypatch.delenv("NEXO_DESKTOP_MANAGED", raising=False)

    assert claude_cli.desktop_product_requested(home) is False
    assert claude_cli.desktop_product_requested() is True


def test_long_running_scripts_do_not_redefine_claude_resolution():
    root = Path(__file__).resolve().parent.parent / "src" / "scripts"
    targets = [
        root / "nexo-sleep.py",
        root / "nexo-catchup.py",
        root / "nexo-synthesis.py",
        root / "nexo-daily-self-audit.py",
        root / "nexo-postmortem-consolidator.py",
    ]
    for target in targets:
        text = target.read_text(encoding="utf-8")
        assert "def _resolve_claude_cli" not in text, f"{target.name} still defines a local Claude resolver"
        assert "CLAUDE_CLI = _resolve_claude_cli()" not in text, f"{target.name} still caches a local Claude path"
        assert 'which("claude")' not in text
        assert "which('claude')" not in text
