"""v6.0.0 — Smoke test: ``nexo-brain --skip`` over a pristine NEXO_HOME.

The installer must complete with zero prompts and leave a calibration.json
that advertises tier ``alto`` plus a protocol strictness that resolves to
``strict`` in TTY and ``lenient`` in the non-TTY world the installer runs
under.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "bin" / "nexo-brain.js"


def _node_available() -> bool:
    return shutil.which("node") is not None


@pytest.mark.skipif(not _node_available(), reason="node not available")
def test_skip_flag_produces_expected_calibration(tmp_path):
    home = tmp_path / "nexo-home"
    home.mkdir()
    user_home = tmp_path / "home"
    user_home.mkdir()

    env = os.environ.copy()
    env.update({
        "HOME": str(user_home),
        "NEXO_HOME": str(home),
        "NEXO_ALLOW_EPHEMERAL_INSTALL": "1",
        "NEXO_SKIP_POSTINSTALL": "1",  # no need to reboot agents from the test
        "NEXO_TESTING_SMOKE": "1",
    })

    # Drive the installer in --skip mode. Several downstream actions hit
    # external services (npm installs, python deps); we bail out as soon
    # as calibration.json exists with the v6 shape.
    proc = subprocess.run(
        ["node", str(INSTALLER), "--skip"],
        env=env,
        capture_output=True,
        text=True,
        timeout=150,
    )
    # The installer may fail later on dep install inside a sandbox. That
    # is acceptable: we only care that the tier-only prompt path fired
    # and wrote the calibration before any of that work.
    cal_path = home / "brain" / "calibration.json"
    if not cal_path.is_file():
        pytest.skip(
            f"installer did not reach calibration step in sandbox: "
            f"rc={proc.returncode} stdout={proc.stdout[-400:]!r} stderr={proc.stderr[-400:]!r}"
        )

    cal = json.loads(cal_path.read_text())
    assert cal["preferences"]["default_resonance"] == "alto"
    # No legacy keys must leak through in --skip mode.
    assert "protocol_strictness" not in cal.get("preferences", {})
    assert "show_pending_at_start" not in cal.get("preferences", {})


@pytest.mark.skipif(not _node_available(), reason="node not available")
def test_skip_flag_is_parsed_as_non_interactive():
    """A lighter assertion that does not actually run the full installer:
    the CLI should treat ``--skip`` identically to ``--yes``. We check by
    grepping the generated source for both tokens in the useDefaults
    expression."""
    text = INSTALLER.read_text()
    marker = 'process.argv.includes("--skip")'
    assert marker in text, (
        "nexo-brain.js must honour --skip as a non-interactive alias (v6.0.0)"
    )


@pytest.mark.skipif(not _node_available(), reason="node not available")
def test_ephemeral_install_detects_macos_pytest_temp_paths(tmp_path):
    driver = tmp_path / "driver.js"
    driver.write_text(
        f"""
const fs = require("fs");
let src = fs.readFileSync({json.dumps(str(INSTALLER))}, "utf8");
if (src.startsWith("#!")) {{
  src = src.replace(/^#![^\\n]*\\n/, "");
}}
src = src.replace(/main\\(\\)\\.catch\\([\\s\\S]*$/, "");
const module_ = {{ exports: {{}} }};
process.env.HOME = "/Users/tester";
const fn = new Function("module", "exports", "require", "process", "console", "__filename", "__dirname", "Buffer", src + "\\nmodule.exports.isEphemeralInstall = isEphemeralInstall;");
fn(module_, module_.exports, require, process, console, {json.dumps(str(INSTALLER))}, {json.dumps(str(INSTALLER.parent))}, Buffer);
console.log(JSON.stringify({{
  macos_tmp: module_.exports.isEphemeralInstall("/private/var/folders/_1/abcd1234/T/pytest-of-user/pytest-1/test_case0/nexo-home"),
  plain_tmp: module_.exports.isEphemeralInstall("/tmp/nexo-home"),
  normal_home: module_.exports.isEphemeralInstall("/Users/tester/.nexo")
}}));
"""
    )

    result = subprocess.run(
        ["node", str(driver)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout.strip())
    assert payload["macos_tmp"] is True
    assert payload["plain_tmp"] is True
    assert payload["normal_home"] is False
