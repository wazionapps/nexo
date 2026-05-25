import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def test_normalize_client_preferences_preserves_old_defaults(tmp_path):
    import client_preferences

    prefs = client_preferences.normalize_client_preferences({"timezone": "UTC"}, user_home=tmp_path / "home")

    assert prefs["interactive_clients"]["claude_code"] is True
    assert prefs["interactive_clients"]["codex"] is False
    assert prefs["default_terminal_client"] == "claude_code"
    assert prefs["last_terminal_client"] == ""
    assert prefs["automation_enabled"] is True
    assert prefs["automation_backend"] == "claude_code"
    assert prefs["provider_runtime"]["selected_chat_provider"] == "anthropic"
    assert prefs["provider_runtime"]["automation_provider"] == "anthropic"
    assert prefs["provider_runtime"]["automation_backend"] == "claude_code"
    assert prefs["provider_runtime"]["providers"]["anthropic"]["client"] == "claude_code"
    assert prefs["provider_runtime"]["providers"]["openai"]["client"] == "codex"
    assert prefs["provider_runtime"]["fallback_policy"]["automation"] == "fail_closed"
    assert prefs["client_runtime_profiles"]["claude_code"]["model"] == "claude-opus-4-7[1m]"
    assert prefs["client_runtime_profiles"]["codex"]["model"] == "gpt-5.4"
    assert prefs["client_runtime_profiles"]["codex"]["reasoning_effort"] == "xhigh"
    assert prefs["automation_task_profiles"]["fast"]["backend"] == ""
    assert prefs["automation_task_profiles"]["fast"]["model"] == ""
    assert prefs["automation_task_profiles"]["deep"]["backend"] == ""
    assert prefs["automation_task_profiles"]["deep"]["model"] == ""
    # New: acknowledged_model_recommendations is part of the normalized
    # preferences schema so cron updates can record ack state silently.
    assert prefs["acknowledged_model_recommendations"] == {"claude_code": 0, "codex": 0}


def test_apply_client_preferences_forces_backend_none_when_automation_disabled():
    import client_preferences

    schedule = client_preferences.apply_client_preferences(
        {},
        interactive_clients={"codex": True},
        default_terminal_client="codex",
        automation_enabled=False,
        automation_backend="codex",
    )

    assert schedule["default_terminal_client"] == "codex"
    assert schedule["automation_enabled"] is False
    assert schedule["automation_backend"] == "none"
    assert schedule["provider_runtime"]["automation_provider"] == "none"
    assert schedule["provider_runtime"]["automation_backend"] == "none"


def test_provider_runtime_maps_openai_to_codex_without_byok():
    import client_preferences

    prefs = client_preferences.normalize_client_preferences(
        {
            "interactive_clients": {"claude_code": True, "codex": True},
            "default_terminal_client": "codex",
            "automation_backend": "codex",
            "provider_runtime": {
                "selected_chat_provider": "openai",
                "automation_provider": "openai",
                "providers": {
                    "openai": {"client": "byok", "runtime_account_status": {"status": "logged_in"}},
                    "anthropic": {"client": "api_key"},
                },
                "fallback_policy": {"automation": "fallback"},
            },
        }
    )

    assert prefs["provider_runtime"]["selected_chat_provider"] == "openai"
    assert prefs["provider_runtime"]["automation_provider"] == "openai"
    assert prefs["provider_runtime"]["automation_backend"] == "codex"
    assert prefs["automation_backend"] == "codex"
    assert prefs["provider_runtime"]["providers"]["openai"]["client"] == "codex"
    assert prefs["provider_runtime"]["providers"]["anthropic"]["client"] == "claude_code"
    assert prefs["provider_runtime"]["fallback_policy"]["automation"] == "fail_closed"


def test_apply_client_preferences_provider_selection_updates_backend():
    import client_preferences

    schedule = client_preferences.apply_client_preferences(
        {
            "interactive_clients": {"claude_code": True, "codex": True},
            "default_terminal_client": "claude_code",
            "automation_backend": "claude_code",
        },
        selected_chat_provider="openai",
        automation_provider="openai",
    )

    assert schedule["provider_runtime"]["selected_chat_provider"] == "openai"
    assert schedule["provider_runtime"]["automation_provider"] == "openai"
    assert schedule["default_terminal_client"] == "codex"
    assert schedule["automation_backend"] == "codex"


def test_apply_client_preferences_backend_override_updates_provider_runtime():
    import client_preferences

    schedule = client_preferences.apply_client_preferences(
        {
            "interactive_clients": {"claude_code": True, "codex": True},
            "default_terminal_client": "claude_code",
            "automation_backend": "claude_code",
            "provider_runtime": {
                "selected_chat_provider": "anthropic",
                "automation_provider": "anthropic",
            },
        },
        automation_backend="codex",
    )

    assert schedule["automation_backend"] == "codex"
    assert schedule["provider_runtime"]["automation_provider"] == "openai"
    assert schedule["provider_runtime"]["automation_backend"] == "codex"


def test_apply_client_preferences_backend_override_can_return_to_claude():
    import client_preferences

    schedule = client_preferences.apply_client_preferences(
        {
            "interactive_clients": {"claude_code": True, "codex": True},
            "default_terminal_client": "codex",
            "automation_backend": "codex",
            "provider_runtime": {
                "selected_chat_provider": "openai",
                "automation_provider": "openai",
            },
        },
        automation_backend="claude_code",
    )

    assert schedule["automation_backend"] == "claude_code"
    assert schedule["provider_runtime"]["automation_provider"] == "anthropic"
    assert schedule["provider_runtime"]["automation_backend"] == "claude_code"


def test_apply_client_preferences_automation_provider_none_survives_reload():
    import client_preferences

    schedule = client_preferences.apply_client_preferences(
        {
            "interactive_clients": {"claude_code": True, "codex": True},
            "default_terminal_client": "codex",
            "automation_backend": "codex",
        },
        automation_provider="none",
    )
    reloaded = client_preferences.normalize_client_preferences(schedule)

    assert reloaded["automation_backend"] == "none"
    assert reloaded["provider_runtime"]["automation_provider"] == "none"
    assert reloaded["provider_runtime"]["automation_backend"] == "none"


def test_provider_runtime_selected_chat_provider_drives_terminal_client():
    import client_preferences

    prefs = client_preferences.normalize_client_preferences(
        {
            "interactive_clients": {"claude_code": True, "codex": False},
            "default_terminal_client": "claude_code",
            "provider_runtime": {
                "selected_chat_provider": "openai",
                "automation_provider": "anthropic",
            },
        }
    )

    assert prefs["provider_runtime"]["selected_chat_provider"] == "openai"
    assert prefs["interactive_clients"]["codex"] is True
    assert prefs["default_terminal_client"] == "codex"
    assert client_preferences.resolve_terminal_client(preferences=prefs) == "codex"


def test_apply_client_preferences_keeps_default_and_last_terminal_client_separate():
    import client_preferences

    schedule = client_preferences.apply_client_preferences(
        {},
        interactive_clients={"claude_code": True, "codex": True},
        default_terminal_client="claude_code",
        last_terminal_client="codex",
    )

    assert schedule["default_terminal_client"] == "claude_code"
    assert schedule["last_terminal_client"] == "codex"


def test_client_runtime_profiles_normalize_and_default():
    import client_preferences

    prefs = client_preferences.normalize_client_preferences(
        {
            "client_runtime_profiles": {
                "codex": {
                    "model": "gpt-5.4-mini",
                    "reasoning_effort": "high",
                }
            }
        }
    )

    assert prefs["client_runtime_profiles"]["claude_code"]["model"] == "claude-opus-4-7[1m]"
    assert prefs["client_runtime_profiles"]["codex"]["model"] == "gpt-5.4-mini"
    assert prefs["client_runtime_profiles"]["codex"]["reasoning_effort"] == "high"


def test_resolve_automation_task_profile_returns_semantic_tier_only():
    import client_preferences

    prefs = client_preferences.normalize_client_preferences(
        {
            "automation_backend": "claude_code",
            "client_runtime_profiles": {
                "claude_code": {"model": "claude-sonnet-4-6", "reasoning_effort": "medium"},
                "codex": {"model": "gpt-5.4", "reasoning_effort": "high"},
            },
            "automation_task_profiles": {
                "deep": {"backend": "claude_code", "model": "", "reasoning_effort": ""},
                "fast": {"backend": "codex", "model": "gpt-5.4-mini", "reasoning_effort": "medium"},
            },
        }
    )

    deep = client_preferences.resolve_automation_task_profile("deep", preferences=prefs)
    fast = client_preferences.resolve_automation_task_profile("fast", preferences=prefs)

    assert prefs["automation_task_profiles"]["deep"] == {
        "backend": "",
        "model": "",
        "reasoning_effort": "",
    }
    assert prefs["automation_task_profiles"]["fast"] == {
        "backend": "",
        "model": "",
        "reasoning_effort": "",
    }
    assert deep == {
        "name": "deep",
        "backend": "",
        "model": "",
        "reasoning_effort": "",
        "tier": "maximo",
    }
    assert fast == {
        "name": "fast",
        "backend": "",
        "model": "",
        "reasoning_effort": "",
        "tier": "bajo",
    }


def test_resolve_terminal_client_ignores_desktop_as_default_terminal():
    import client_preferences

    prefs = client_preferences.normalize_client_preferences(
        {
            "interactive_clients": {"claude_desktop": True, "codex": True},
            "default_terminal_client": "claude_desktop",
        }
    )

    assert prefs["default_terminal_client"] == "codex"


def test_normalize_last_terminal_client_clears_disabled_client(tmp_path):
    import client_preferences

    prefs = client_preferences.normalize_client_preferences(
        {
            "interactive_clients": {"claude_code": True, "codex": False},
            "default_terminal_client": "claude_code",
            "last_terminal_client": "codex",
        },
        user_home=tmp_path / "home",
    )

    assert prefs["default_terminal_client"] == "claude_code"
    assert prefs["last_terminal_client"] == ""


def test_detect_installed_clients_reports_binary_and_desktop(monkeypatch, tmp_path):
    import client_preferences

    home = tmp_path / "home"
    config_path = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}")

    monkeypatch.setattr(
        client_preferences.shutil,
        "which",
        lambda name: f"/tmp/{name}" if name in {"claude", "codex"} else None,
    )
    monkeypatch.setattr(client_preferences, "sys", type("SysStub", (), {"platform": "darwin"})())
    monkeypatch.setattr(client_preferences, "os", type("OSStub", (), {"name": "posix", "environ": os.environ})())

    detected = client_preferences.detect_installed_clients(home)

    assert detected["claude_code"]["installed"] is True
    assert detected["codex"]["installed"] is True
    assert detected["claude_desktop"]["installed"] is True
    assert detected["claude_desktop"]["detected_by"] in {"app", "config"}


def test_detect_installed_clients_finds_managed_bootstrap_claude_binary(monkeypatch, tmp_path):
    import client_preferences

    home = tmp_path / "home"
    managed_claude = home / ".nexo" / "runtime" / "bootstrap" / "npm-global" / "bin" / "claude"
    managed_claude.parent.mkdir(parents=True)
    managed_claude.write_text("#!/bin/sh\n")

    monkeypatch.setattr(client_preferences.shutil, "which", lambda name: None)
    monkeypatch.setattr(client_preferences, "sys", type("SysStub", (), {"platform": "linux"})())
    monkeypatch.setattr(client_preferences, "os", type("OSStub", (), {"name": "posix", "environ": os.environ})())

    detected = client_preferences.detect_installed_clients(home)

    assert detected["claude_code"]["installed"] is True
    assert detected["claude_code"]["path"] == str(managed_claude)
    assert detected["claude_code"]["detected_by"] == "binary"


def test_detect_installed_clients_ignores_global_claude_when_desktop_managed(monkeypatch, tmp_path):
    import client_preferences

    home = tmp_path / "home"

    monkeypatch.setenv("NEXO_DESKTOP_MANAGED", "1")
    monkeypatch.setattr(client_preferences.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name == "claude" else None)
    monkeypatch.setattr(client_preferences, "sys", type("SysStub", (), {"platform": "darwin"})())
    monkeypatch.setattr(client_preferences, "os", type("OSStub", (), {"name": "posix", "environ": dict(os.environ)})())

    detected = client_preferences.detect_installed_clients(home)

    assert detected["claude_code"]["installed"] is False
    assert detected["claude_code"]["path"] == ""
    assert detected["claude_code"]["detected_by"] == "missing"


def test_detect_installed_clients_desktop_managed_requires_codex_vendor(monkeypatch, tmp_path):
    import client_preferences

    home = tmp_path / "home"
    managed_codex = home / ".nexo" / "runtime" / "bootstrap" / "npm-global" / "bin" / "codex"
    managed_codex.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("NEXO_DESKTOP_MANAGED", "1")
    monkeypatch.setattr(client_preferences.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name == "codex" else None)
    monkeypatch.setattr(client_preferences, "sys", type("SysStub", (), {"platform": "darwin"})())
    monkeypatch.setattr(client_preferences, "os", type("OSStub", (), {"name": "posix", "environ": dict(os.environ)})())

    detected = client_preferences.detect_installed_clients(home)
    assert detected["codex"]["installed"] is False
    assert detected["codex"]["path"] == ""

    managed_codex.write_text("#!/bin/sh\n")
    detected = client_preferences.detect_installed_clients(home)
    assert detected["codex"]["installed"] is False
    assert detected["codex"]["path"] == ""

    vendor_bin = home / ".nexo" / "runtime" / "bootstrap" / "npm-global" / "lib" / "node_modules" / "@openai" / "codex" / "vendor" / "x64" / "bin" / "codex"
    vendor_bin.parent.mkdir(parents=True, exist_ok=True)
    vendor_bin.write_text("#!/bin/sh\n")
    detected = client_preferences.detect_installed_clients(home)
    assert detected["codex"]["installed"] is True
    assert detected["codex"]["path"] == str(managed_codex)


def test_normalize_client_preferences_backfills_existing_codex_artifacts(tmp_path):
    import client_preferences

    home = tmp_path / "home"
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text(
        '[mcp_servers.nexo]\ncommand = "python3"\nargs = ["server.py"]\n'
    )

    prefs = client_preferences.normalize_client_preferences(
        {
            "interactive_clients": {
                "claude_code": True,
                "codex": False,
                "claude_desktop": False,
            }
        },
        user_home=home,
    )

    assert prefs["interactive_clients"]["codex"] is True
