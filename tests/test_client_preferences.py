import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_normalize_client_preferences_preserves_old_defaults(tmp_path):
    import client_preferences

    prefs = client_preferences.normalize_client_preferences({"timezone": "UTC"}, user_home=tmp_path / "home")

    assert prefs["interactive_clients"]["claude_code"] is True
    assert prefs["interactive_clients"]["codex"] is False
    assert prefs["default_terminal_client"] == "claude_code"
    assert prefs["automation_enabled"] is True
    assert prefs["automation_backend"] == "claude_code"
    assert prefs["client_runtime_profiles"]["claude_code"]["model"] == "opus"
    assert prefs["client_runtime_profiles"]["codex"]["model"] == "gpt-5.4"
    assert prefs["client_runtime_profiles"]["codex"]["reasoning_effort"] == "xhigh"


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

    assert prefs["client_runtime_profiles"]["claude_code"]["model"] == "opus"
    assert prefs["client_runtime_profiles"]["codex"]["model"] == "gpt-5.4-mini"
    assert prefs["client_runtime_profiles"]["codex"]["reasoning_effort"] == "high"


def test_resolve_terminal_client_ignores_desktop_as_default_terminal():
    import client_preferences

    prefs = client_preferences.normalize_client_preferences(
        {
            "interactive_clients": {"claude_desktop": True, "codex": True},
            "default_terminal_client": "claude_desktop",
        }
    )

    assert prefs["default_terminal_client"] == "codex"


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
