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
    NEXO_WSL_HOME: "/home/franciscoc/.nexo",
    NEXO_DESKTOP_MANAGED: "1",
    NEXO_SKIP_SHELL_PROFILE: "1",
    NEXO_SKIP_MODEL_WARMUP: "1"
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
        "NEXO_DESKTOP_MANAGED": "1",
        "NEXO_SKIP_SHELL_PROFILE": "1",
        "NEXO_SKIP_MODEL_WARMUP": "1",
    }
    # New interface (post Win11 clean-install fix iterations):
    # -d <distro>, -u root, --, env -i, PATH=..., NEXO_MANAGED_PATH=...,
    # linuxEnv vars, /bin/dash <linuxScriptPath>
    assert payload["args"][:5] == [
        "-d",
        "Ubuntu-24.04",
        "-u",
        "root",
        "--",
    ]
    assert payload["args"][5:7] == ["env", "-i"]
    assert payload["args"][7].startswith("PATH=")
    assert payload["args"][8].startswith("NEXO_MANAGED_PATH=/home/franciscoc/.nexo/bin:/home/franciscoc/.nexo/runtime/bootstrap/npm-global/bin:")
    # linuxEnv exported via env: NEXO_WINDOWS_BRIDGE / NEXO_WINDOWS_HOST / NEXO_HOME
    assert "NEXO_WINDOWS_BRIDGE=1" in payload["args"]
    assert "NEXO_WINDOWS_HOST=1" in payload["args"]
    assert "NEXO_HOME=/home/franciscoc/.nexo" in payload["args"]
    assert "NEXO_DESKTOP_MANAGED=1" in payload["args"]
    assert "NEXO_SKIP_SHELL_PROFILE=1" in payload["args"]
    assert "NEXO_SKIP_MODEL_WARMUP=1" in payload["args"]
    # Last two args: /bin/dash <path-to-staged-script>. Path is os.homedir()
    # of the host running the test (translated to Linux form when on Win/WSL,
    # left as-is on macOS test runners). Either way must end in the staged
    # script filename.
    assert payload["args"][-2] == "/bin/dash"
    assert payload["args"][-1].endswith("bootstrap-script.sh")


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
    # Distro inferred from UNC path. Then -u root, --, env -i, ...
    assert payload["args"][:5] == ["-d", "Ubuntu-24.04", "-u", "root", "--"]


def test_public_launchers_use_shared_wsl_bridge_helper() -> None:
    cli_text = (REPO_ROOT / "bin" / "nexo.js").read_text(encoding="utf-8")
    installer_text = (REPO_ROOT / "bin" / "nexo-brain.js").read_text(encoding="utf-8")
    bridge_text = (REPO_ROOT / "bin" / "windows-wsl-bridge.js").read_text(encoding="utf-8")
    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))

    assert 'const { runViaWsl } = require("./windows-wsl-bridge");' in cli_text
    assert 'const { runViaWsl } = require("./windows-wsl-bridge");' in installer_text
    assert 'label: "NEXO CLI"' in cli_text
    assert 'label: "NEXO Brain"' in installer_text
    assert "windowsHide: platform === \"win32\"" in bridge_text
    assert "bin/windows-wsl-bridge.js" in package["files"]
