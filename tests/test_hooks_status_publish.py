"""v6.0.0+ — registerAllCoreHooks() must publish hook status canonically.

We exercise the installer in isolation via Node: a tiny driver script
reads the committed manifest, loads ``bin/nexo-brain.js`` just enough to
call ``registerAllCoreHooks`` against a synthetic settings object, and
then asserts the expected file surfaces in the temp home.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "src" / "hooks" / "manifest.json"
BIN = REPO_ROOT / "bin" / "nexo-brain.js"


def _node_available() -> bool:
    return shutil.which("node") is not None


@pytest.mark.skipif(not _node_available(), reason="node not installed in CI worker")
def test_hooks_status_json_is_published(tmp_path):
    home = tmp_path / "nexo-home"
    home.mkdir()
    hooks_dir = home / "hooks"
    hooks_dir.mkdir()
    # Stage the seven handlers so the publisher marks every entry active.
    manifest = json.loads(MANIFEST.read_text())
    for entry in manifest["hooks"]:
        name = Path(entry["handler"]).name
        (hooks_dir / name).write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n")

    driver = tmp_path / "driver.js"
    driver.write_text(textwrap.dedent(f"""
        const fs = require("fs");
        // Load the installer as a module shim — we only need registerAllCoreHooks.
        let src = fs.readFileSync({json.dumps(str(BIN))}, "utf8");
        // new Function() does not accept a shebang line; drop it.
        if (src.startsWith("#!")) {{ src = src.replace(/^#![^\\n]*\\n/, ""); }}
        const module_ = {{ exports: {{}} }};
        const fn = new Function("module", "exports", "require", "process", "console", "__filename", "__dirname", "Buffer", src + "\\nmodule.exports.registerAllCoreHooks = registerAllCoreHooks;");
        fn(module_, module_.exports, require, process, console, {json.dumps(str(BIN))}, {json.dumps(str(BIN.parent))}, Buffer);
        const settings = {{}};
        module_.exports.registerAllCoreHooks(settings, {json.dumps(str(hooks_dir))}, {json.dumps(str(home))});
        console.log(JSON.stringify({{ settings, ok: true }}));
    """))

    result = subprocess.run(
        ["node", str(driver)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, f"node driver failed: {result.stderr}\n{result.stdout}"

    canonical_path = home / "runtime" / "operations" / "hooks_status.json"
    legacy_path = home / "hooks_status.json"
    assert canonical_path.is_file(), "canonical hooks_status.json should be written under runtime/operations"
    assert legacy_path.exists(), "legacy hooks_status alias should still exist for compatibility"

    payload = json.loads(canonical_path.read_text())
    assert payload["total"] == len(manifest["hooks"])
    assert payload["registered"] == payload["total"]
    assert payload["healthy"] is True
    events = {h["event"] for h in payload["hooks"]}
    expected_events = {h["event"] for h in manifest["hooks"]}
    assert events == expected_events
    for entry in payload["hooks"]:
        assert entry["status"] == "active"


@pytest.mark.skipif(not _node_available(), reason="node not installed in CI worker")
def test_missing_handler_marks_entry_error(tmp_path):
    """If a manifest handler file is missing, its row must be status=error
    and ``healthy`` must flip to False — that is how NEXO Desktop renders
    the red dot in the Estado del sistema tab."""
    home = tmp_path / "nexo-home"
    home.mkdir()
    hooks_dir = home / "hooks"
    hooks_dir.mkdir()
    # Intentionally do not stage any handler files.

    driver = tmp_path / "driver.js"
    driver.write_text(textwrap.dedent(f"""
        const fs = require("fs");
        let src = fs.readFileSync({json.dumps(str(BIN))}, "utf8");
        if (src.startsWith("#!")) {{ src = src.replace(/^#![^\\n]*\\n/, ""); }}
        const module_ = {{ exports: {{}} }};
        const fn = new Function("module", "exports", "require", "process", "console", "__filename", "__dirname", "Buffer", src + "\\nmodule.exports.registerAllCoreHooks = registerAllCoreHooks;");
        fn(module_, module_.exports, require, process, console, {json.dumps(str(BIN))}, {json.dumps(str(BIN.parent))}, Buffer);
        const settings = {{}};
        module_.exports.registerAllCoreHooks(settings, {json.dumps(str(hooks_dir))}, {json.dumps(str(home))});
    """))

    result = subprocess.run(["node", str(driver)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr

    payload = json.loads((home / "runtime" / "operations" / "hooks_status.json").read_text())
    assert payload["healthy"] is False
    assert payload["registered"] == 0
    assert all(entry["status"] == "error" for entry in payload["hooks"])
