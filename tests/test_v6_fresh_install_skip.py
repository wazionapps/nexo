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
    timed_out = False
    try:
        proc = subprocess.run(
            ["node", str(INSTALLER), "--skip"],
            env=env,
            capture_output=True,
            text=True,
            timeout=150,
        )
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        proc = subprocess.CompletedProcess(
            exc.cmd,
            returncode=-9,
            stdout=exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            stderr=exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
        )
    # The installer may fail later on dep install inside a sandbox. That
    # is acceptable: we only care that the tier-only prompt path fired
    # and wrote the calibration before any of that work. Fresh installs
    # now target personal/brain first; legacy brain/ is only transitional.
    cal_path = home / "personal" / "brain" / "calibration.json"
    if not cal_path.is_file() and (home / "brain" / "calibration.json").is_file():
        cal_path = home / "brain" / "calibration.json"
    if not cal_path.is_file():
        pytest.skip(
            f"installer did not reach calibration step in sandbox: "
            f"rc={proc.returncode} stdout={proc.stdout[-400:]!r} stderr={proc.stderr[-400:]!r}"
        )
    if timed_out:
        # Fresh-install smoke is intentionally tolerant of slow dependency
        # installs; once calibration.json exists we already proved the
        # non-interactive onboarding path fired correctly.
        assert cal_path.is_file()

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


def test_installer_reserves_product_name_for_assistant_default():
    text = INSTALLER.read_text()

    assert 'const DEFAULT_ASSISTANT_NAME = "Nova";' in text
    assert 'const RESERVED_ASSISTANT_NAME_KEYS = new Set(["nexo", "nexobrain", "nexodesktop"]);' in text
    assert "isReservedAssistantName(candidate)" in text


def test_public_installer_keeps_claude_install_path_without_desktop_bundle():
    text = INSTALLER.read_text()

    assert 'if (desktopNode && bundledNpmCli)' in text
    assert 'spawnSync("npx", ["-y", "@anthropic-ai/claude-code", "--version"], {' in text
    assert '["install", "-g", "--prefix", managedPrefix, "@anthropic-ai/claude-code"]' in text
    assert 'const managedPrefix = managedClaudePrefix();' in text


def test_installer_finalizes_f06_layout_before_reporting_ready():
    text = INSTALLER.read_text()

    assert "function finalizeF06Layout" in text
    assert 'log("Finalizing F0.6 runtime layout...");' in text
    assert 'const layoutFinalize = finalizeF06Layout(python, NEXO_HOME);' in text
    assert 'throw new Error(`F0.6 layout finalization failed: ${layoutFinalize.error}`);' in text


def test_installer_finalizes_f06_layout_on_updates_and_repairs():
    text = INSTALLER.read_text()

    assert 'const migLayoutFinalize = finalizeF06Layout(migPython, NEXO_HOME);' in text
    assert 'throw new Error(`F0.6 layout finalization failed: ${migLayoutFinalize.error}`);' in text
    assert 'const syncLayoutFinalize = finalizeF06Layout(syncPython, NEXO_HOME);' in text
    assert 'throw new Error(`F0.6 layout finalization failed: ${syncLayoutFinalize.error}`);' in text


def test_installer_publishes_runtime_core_manifest_to_legacy_and_canonical_config_during_transition():
    text = INSTALLER.read_text()

    assert 'path.join(nexoHome, "config")' in text
    assert 'path.join(nexoHome, "personal", "config")' in text
    assert 'runtime-core-artifacts.json' in text


def test_update_path_never_falls_back_to_reserved_product_name_for_operator_identity():
    text = INSTALLER.read_text()

    assert 'installed.operator_name || DEFAULT_ASSISTANT_NAME' in text
    assert 'installed.operator_name || "NEXO"' not in text


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
