import json
import os
import subprocess
from types import SimpleNamespace
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


def test_reconcile_apply_stages_providers_and_reports_health(tmp_path):
    runtime_root = Path(__file__).resolve().parents[1] / "src"

    def fake_npm_runner(stage_dir, package, version):
        return SimpleNamespace(returncode=0, stdout=f"installed {package}@{version}", stderr="")

    applied = reconcile_managed_mcp(
        nexo_home=tmp_path,
        runtime_root=runtime_root,
        apply=True,
        platform="darwin",
        npm_runner=fake_npm_runner,
    )
    status = managed_mcp_status(
        nexo_home=tmp_path,
        runtime_root=runtime_root,
        platform="darwin",
    )

    assert applied["applied"] is True
    assert applied["providers"]
    assert {provider["status"] for provider in applied["providers"].values()} == {"healthy"}
    assert {provider["status"] for provider in status["providers"].values()} == {"healthy"}
    for provider in applied["providers"].values():
        assert Path(provider["staged_path"]).is_dir()
        assert Path(provider["executable"]).is_file()


def test_managed_mcp_runtime_copy_includes_package_and_runner():
    root = Path(__file__).resolve().parents[1]
    brain = (root / "bin" / "nexo-brain.js").read_text(encoding="utf-8")
    auto_update = (root / "src" / "auto_update.py").read_text(encoding="utf-8")

    assert '"managed_mcp"' in brain
    assert '"managed_mcp"' in auto_update
    assert "nexo-managed-mcp" in brain
    assert "nexo-managed-mcp" in auto_update


def test_managed_mcp_runner_respects_kill_switch(tmp_path):
    state_dir = tmp_path / "runtime" / "managed-mcp"
    state_dir.mkdir(parents=True)
    (state_dir / "installed-state.json").write_text(json.dumps({
        "desired": {
            "claude_code": {
                "nexo_chrome_control": {
                    "nexo": {
                        "capability_id": "chrome_control",
                        "provider_id": "chrome-devtools-mcp",
                        "provider_package": "chrome-devtools-mcp",
                        "provider_version": "1.1.1",
                        "provider_bin": "chrome-devtools-mcp",
                    }
                }
            }
        }
    }))

    result = subprocess.run(
        ["node", str(Path(__file__).resolve().parents[1] / "bin" / "nexo-managed-mcp.js"), "run", "chrome_control"],
        text=True,
        capture_output=True,
        env={**os.environ, "NEXO_HOME": str(tmp_path), "NEXO_MANAGED_MCP_DISABLE": "1"},
        timeout=10,
    )

    assert result.returncode == 78
    assert "disabled by policy" in result.stderr


def test_managed_mcp_runner_fails_closed_when_provider_is_unstaged(tmp_path):
    state_dir = tmp_path / "runtime" / "managed-mcp"
    state_dir.mkdir(parents=True)
    (state_dir / "installed-state.json").write_text(json.dumps({
        "desired": {
            "claude_code": {
                "nexo_chrome_control": {
                    "nexo": {
                        "capability_id": "chrome_control",
                        "provider_id": "chrome-devtools-mcp",
                        "provider_package": "chrome-devtools-mcp",
                        "provider_version": "1.1.1",
                        "provider_bin": "chrome-devtools-mcp",
                    }
                }
            }
        }
    }))

    result = subprocess.run(
        ["node", str(Path(__file__).resolve().parents[1] / "bin" / "nexo-managed-mcp.js"), "run", "chrome_control"],
        text=True,
        capture_output=True,
        env={**os.environ, "NEXO_HOME": str(tmp_path), "NEXO_MANAGED_MCP_ALLOW_NPX_FALLBACK": ""},
        timeout=10,
    )

    assert result.returncode == 69
    assert "not staged" in result.stderr
