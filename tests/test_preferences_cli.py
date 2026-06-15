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
    # Pop only the non-db client modules so they re-resolve NEXO_HOME on next
    # import. Do NOT pop the db submodules: popping ``db._reminders`` (etc.)
    # replaces them with NEW objects, orphaning the collection-time
    # ``import db._reminders`` of other test modules (test_semantic_similarity_hybrid)
    # so their monkeypatch lands on a stale object. Reload the db stack IN PLACE
    # instead — ``db/__init__`` reloads its submodules in place, re-resolving
    # DB_PATH while preserving module identity.
    import importlib
    for mod in list(sys.modules):
        if mod in {"paths", "runtime_power", "client_preferences"}:
            sys.modules.pop(mod, None)
    db_module = sys.modules.get("db")
    if db_module is not None:
        importlib.reload(db_module)


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
    assert payload["selected_chat_provider"] == "anthropic"
    assert "openai" in payload["available_providers"]


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


def test_provider_select_openai_updates_chat_and_automation_runtime(tmp_path, monkeypatch, capsys):
    home = tmp_path / "nexo"
    monkeypatch.setenv("NEXO_HOME", str(home))
    _clear_modules()

    import cli

    rc = cli._provider(
        SimpleNamespace(
            provider_command="select",
            provider="openai",
            chat_only=False,
            automation_only=False,
            json=True,
        )
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected_chat_provider"] == "openai"
    assert payload["automation_provider"] == "openai"
    assert payload["automation_backend"] == "codex"
    assert payload["default_terminal_client"] == "codex"


def test_provider_select_rejects_split_chat_automation_flags(tmp_path, monkeypatch, capsys):
    home = tmp_path / "nexo"
    monkeypatch.setenv("NEXO_HOME", str(home))
    _clear_modules()

    import cli

    rc = cli._provider(
        SimpleNamespace(
            provider_command="select",
            provider="openai",
            chat_only=True,
            automation_only=False,
            json=True,
        )
    )

    assert rc == 2
    assert "same provider" in capsys.readouterr().err
