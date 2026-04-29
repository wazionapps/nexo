from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "bin" / "windows-wsl-bridge.js"


def _node_available() -> bool:
    return shutil.which("node") is not None


@pytest.mark.skipif(not _node_available(), reason="node not available")
def test_bridge_builds_wsl_exec_spec_with_sanitized_env() -> None:
    driver = f"""
const helper = require({json.dumps(str(HELPER))});
const payload = helper.buildWslExecSpec({{
  scriptPath: "C:\\\\Users\\\\franciscoc\\\\AppData\\\\Roaming\\\\npm\\\\node_modules\\\\nexo-brain\\\\bin\\\\nexo.js",
  args: ["doctor", "--json"],
  env: {{
    NEXO_HOME: "C:\\\\Users\\\\franciscoc\\\\.nexo",
    NEXO_CODE: "C:\\\\Users\\\\franciscoc\\\\src\\\\nexo\\\\src",
    NEXO_WSL_DISTRO: "Ubuntu-24.04",
    NEXO_WSL_HOME: "/home/franciscoc/.nexo"
  }},
  platform: "win32"
}});
console.log(JSON.stringify(payload));
"""
    result = subprocess.run(
        ["node", "-e", driver],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout.strip())
    assert payload["command"] == "wsl.exe"
    assert payload["translatedScriptPath"] == "/mnt/c/Users/franciscoc/AppData/Roaming/npm/node_modules/nexo-brain/bin/nexo.js"
    assert payload["linuxEnv"] == {
        "NEXO_WINDOWS_BRIDGE": "1",
        "NEXO_WINDOWS_HOST": "1",
        "NEXO_HOME": "/home/franciscoc/.nexo",
    }
    assert payload["args"][:10] == [
        "-d",
        "Ubuntu-24.04",
        "--exec",
        "env",
        "-u",
        "NEXO_HOME",
        "-u",
        "NEXO_CODE",
        "-u",
        "NEXO_WSL_HOME",
    ]
    assert payload["args"][10:14] == ["-u", "NEXO_WSL_CODE", "NEXO_WINDOWS_BRIDGE=1", "NEXO_WINDOWS_HOST=1"]
    assert payload["args"][14:] == [
        "NEXO_HOME=/home/franciscoc/.nexo",
        "node",
        "/mnt/c/Users/franciscoc/AppData/Roaming/npm/node_modules/nexo-brain/bin/nexo.js",
        "doctor",
        "--json",
    ]


@pytest.mark.skipif(not _node_available(), reason="node not available")
def test_bridge_infers_distro_from_wsl_unc_paths() -> None:
    driver = f"""
const helper = require({json.dumps(str(HELPER))});
const payload = helper.buildWslExecSpec({{
  scriptPath: "\\\\\\\\wsl$\\\\Ubuntu-24.04\\\\home\\\\franciscoc\\\\repo\\\\bin\\\\nexo-brain.js",
  args: ["--version"],
  env: {{}},
  platform: "win32"
}});
console.log(JSON.stringify(payload));
"""
    result = subprocess.run(
        ["node", "-e", driver],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout.strip())
    assert payload["translatedScriptPath"] == "/home/franciscoc/repo/bin/nexo-brain.js"
    assert payload["args"][:2] == ["-d", "Ubuntu-24.04"]


def test_public_launchers_use_shared_wsl_bridge_helper() -> None:
    cli_text = (REPO_ROOT / "bin" / "nexo.js").read_text(encoding="utf-8")
    installer_text = (REPO_ROOT / "bin" / "nexo-brain.js").read_text(encoding="utf-8")
    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))

    assert 'const { runViaWsl } = require("./windows-wsl-bridge");' in cli_text
    assert 'const { runViaWsl } = require("./windows-wsl-bridge");' in installer_text
    assert 'label: "NEXO CLI"' in cli_text
    assert 'label: "NEXO Brain"' in installer_text
    assert "bin/windows-wsl-bridge.js" in package["files"]
