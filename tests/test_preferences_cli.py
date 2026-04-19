from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


SRC = str(Path(__file__).resolve().parents[1] / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


def _clear_modules():
    for mod in list(sys.modules):
        if mod in {"paths", "runtime_power", "client_preferences"} or mod.startswith("db."):
            sys.modules.pop(mod, None)


def test_preferences_show_includes_automation_fields(tmp_path, monkeypatch, capsys):
    home = tmp_path / "nexo"
    monkeypatch.setenv("NEXO_HOME", str(home))
    _clear_modules()

    import cli

    rc = cli._preferences(
        SimpleNamespace(
            resonance=None,
            automation_enabled=False,
            automation_disabled=False,
            automation_backend=None,
            show=True,
            json=True,
        )
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["automation_enabled"] is True
    assert payload["automation_backend"] == "claude_code"
    assert "codex" in payload["available_automation_backends"]


def test_preferences_reenables_automation_with_default_terminal_backend(tmp_path, monkeypatch, capsys):
    home = tmp_path / "nexo"
    monkeypatch.setenv("NEXO_HOME", str(home))
    _clear_modules()

    import cli
    import client_preferences

    client_preferences.save_client_preferences(
        interactive_clients={"claude_code": True, "codex": True},
        default_terminal_client="codex",
        automation_enabled=False,
        automation_backend="none",
    )

    rc = cli._preferences(
        SimpleNamespace(
            resonance=None,
            automation_enabled=True,
            automation_disabled=False,
            automation_backend=None,
            show=False,
            json=True,
        )
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["automation_enabled"] is True
    assert payload["automation_backend"] == "codex"

