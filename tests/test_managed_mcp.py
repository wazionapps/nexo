import json
from pathlib import Path

from client_sync import _sync_json_client
from managed_mcp import (
    build_managed_server_entries,
    load_catalog,
    load_lock,
    managed_mcp_status,
    merge_json_mcp_servers,
    merge_toml_mcp_servers,
    reconcile_managed_mcp,
    validate_catalog_lock,
)


def test_catalog_lock_is_valid_and_pinned():
    validation = validate_catalog_lock(load_catalog(), load_lock())

    assert validation["ok"], validation
    assert {"chrome_control", "desktop_control", "power_control"} <= set(validation["capabilities"])
    lock = load_lock()
    for provider in lock["providers"].values():
        assert "@latest" not in provider["version"]
        assert provider["version"] != "0.0.0-managed"
        assert provider["integrity"].startswith("sha512-")
        assert provider["tarball"].startswith("https://registry.npmjs.org/")
        assert provider["bin"]


def test_builds_default_managed_entries_for_each_client(tmp_path):
    runtime_root = Path(__file__).resolve().parents[1] / "src"
    entries = build_managed_server_entries(
        client="codex",
        nexo_home=tmp_path,
        runtime_root=runtime_root,
        platform="darwin",
    )

    assert {"nexo_chrome_control", "nexo_desktop_control", "nexo_power_control"} <= set(entries)
    assert entries["nexo_chrome_control"]["args"] == ["run", "chrome_control"]
    assert entries["nexo_chrome_control"]["nexo"]["owner"] == "nexo"
    assert entries["nexo_chrome_control"]["nexo"]["provider_package"] == "chrome-devtools-mcp"
    assert entries["nexo_chrome_control"]["nexo"]["provider_version"] == "1.1.1"
    assert entries["nexo_chrome_control"]["nexo"]["provider_bin"] == "chrome-devtools-mcp"
    assert entries["nexo_chrome_control"]["env"]["NEXO_CODE"] == str(runtime_root)
    assert entries["nexo_chrome_control"]["command"].endswith("bin/nexo-managed-mcp.js")


def test_json_merge_preserves_user_owned_server_and_records_metadata(tmp_path):
    runtime_root = Path(__file__).resolve().parents[1] / "src"
    entries = build_managed_server_entries(
        client="claude_code",
        nexo_home=tmp_path,
        runtime_root=runtime_root,
        platform="darwin",
    )
    payload = {
        "mcpServers": {
            "nexo_chrome_control": {"command": "custom-user-command"},
        }
    }

    merged = merge_json_mcp_servers(payload, entries)

    assert merged["mcpServers"]["nexo_chrome_control"]["command"] == "custom-user-command"
    assert "nexo_power_control" in merged["mcpServers"]
    assert merged["nexo"]["managed_mcp"]["schema"] == "nexo.managed_mcp.client.v1"
    assert "nexo_power_control" in merged["nexo"]["managed_mcp"]["servers"]


def test_toml_merge_preserves_user_owned_server_and_records_metadata(tmp_path):
    runtime_root = Path(__file__).resolve().parents[1] / "src"
    entries = build_managed_server_entries(
        client="codex",
        nexo_home=tmp_path,
        runtime_root=runtime_root,
        platform="darwin",
    )
    payload = {
        "mcp_servers": {
            "nexo_power_control": {"command": "user-owned"},
        }
    }

    merged = merge_toml_mcp_servers(payload, entries)

    assert merged["mcp_servers"]["nexo_power_control"]["command"] == "user-owned"
    assert "nexo_chrome_control" in merged["mcp_servers"]
    assert merged["nexo"]["managed_mcp"]["servers"]["nexo_chrome_control"]["owner"] == "nexo"


def test_client_sync_writes_managed_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_MANAGED_MCP_PLATFORM", "darwin")
    runtime_root = Path(__file__).resolve().parents[1] / "src"
    config_path = tmp_path / "claude_desktop_config.json"
    server_config = {
        "command": "/usr/bin/python3",
        "args": [str(runtime_root / "server.py")],
        "env": {
            "NEXO_HOME": str(tmp_path),
            "NEXO_CODE": str(runtime_root),
        },
    }

    result = _sync_json_client(config_path, server_config, "claude_desktop")
    payload = json.loads(config_path.read_text())

    assert result["ok"] is True
    assert result["managed_default_mcp_count"] >= 3
    assert "nexo" in payload["mcpServers"]
    assert {"nexo_chrome_control", "nexo_desktop_control", "nexo_power_control"} <= set(payload["mcpServers"])


def test_reconcile_writes_state_only_when_applied(tmp_path):
    runtime_root = Path(__file__).resolve().parents[1] / "src"

    dry = reconcile_managed_mcp(
        nexo_home=tmp_path,
        runtime_root=runtime_root,
        apply=False,
        platform="darwin",
    )
    status_before = managed_mcp_status(
        nexo_home=tmp_path,
        runtime_root=runtime_root,
        platform="darwin",
    )
    applied = reconcile_managed_mcp(
        nexo_home=tmp_path,
        runtime_root=runtime_root,
        apply=True,
        platform="darwin",
    )

    assert dry["ok"] is True
    assert status_before["state_exists"] is False
    assert applied["applied"] is True
    assert Path(applied["state_path"]).is_file()
