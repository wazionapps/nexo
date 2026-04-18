"""Integration tests for Fase D2 tranche 3 (shadow rules):
R23h shebang-mismatch and R23j global-install.

Tranche 3 rules default to shadow, so tests force-override to `hard`
via guardian.json to observe injection behaviour end-to-end.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import textwrap

import pytest

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")),
)


@pytest.fixture(autouse=True)
def fase_d2_tranche3_runtime(isolated_db, tmp_path, monkeypatch):
    fake_home = tmp_path / "nexo_home"
    (fake_home / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))
    import enforcement_engine
    import guardian_config
    for mod in ["r23h_shebang_mismatch", "r23j_global_install"]:
        importlib.reload(importlib.import_module(mod))
    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)
    yield


def _enforcer_hard(rule_id: str, tmp_path):
    import guardian_config, enforcement_engine
    home = tmp_path / f"{rule_id}_home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    default = guardian_config.load_default_guardian_config()
    default["rules"][rule_id] = "hard"
    (home / "config" / "guardian.json").write_text(json.dumps(default))
    os.environ["NEXO_HOME"] = str(home)
    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)
    return enforcement_engine.HeadlessEnforcer()


def _enforcer_shadow_default():
    from enforcement_engine import HeadlessEnforcer
    return HeadlessEnforcer()


# ──────────────────────────────────────────────────────────────────────
# R23j — global install
# ──────────────────────────────────────────────────────────────────────


def test_r23j_fires_on_npm_global(tmp_path):
    enforcer = _enforcer_hard("R23j_global_install", tmp_path)
    enforcer.on_tool_call("Bash", {"command": "npm install -g typescript"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23j_global_install"]
    assert len(hits) == 1


def test_r23j_fires_on_pip_user(tmp_path):
    enforcer = _enforcer_hard("R23j_global_install", tmp_path)
    enforcer.on_tool_call("Bash", {"command": "pip install --user black"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23j_global_install"]
    assert len(hits) == 1


def test_r23j_fires_on_brew_install(tmp_path):
    enforcer = _enforcer_hard("R23j_global_install", tmp_path)
    enforcer.on_tool_call("Bash", {"command": "brew install postgresql"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23j_global_install"]
    assert len(hits) == 1


def test_r23j_allows_on_permit_marker(tmp_path):
    enforcer = _enforcer_hard("R23j_global_install", tmp_path)
    enforcer.on_user_message("yes install globally the pnpm CLI")
    enforcer.on_tool_call("Bash", {"command": "npm install -g pnpm"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23j_global_install"]
    assert hits == []


def test_r23j_allows_local_scope(tmp_path):
    enforcer = _enforcer_hard("R23j_global_install", tmp_path)
    enforcer.on_tool_call("Bash", {"command": "npm install typescript"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23j_global_install"]
    assert hits == []


def test_r23j_shadow_default_does_not_enqueue():
    enforcer = _enforcer_shadow_default()
    enforcer.on_tool_call("Bash", {"command": "npm install -g typescript"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23j_global_install"]
    assert hits == []


# ──────────────────────────────────────────────────────────────────────
# R23h — shebang mismatch
# ──────────────────────────────────────────────────────────────────────


def _write_script(dir_path, name, shebang, body="echo hi"):
    path = dir_path / name
    path.write_text(f"#!{shebang}\n{body}\n")
    path.chmod(0o755)
    return path


def test_r23h_fires_on_unresolved_interpreter(tmp_path):
    script = _write_script(tmp_path, "deploy.py", "/usr/bin/env python9.99")
    enforcer = _enforcer_hard("R23h_shebang_mismatch", tmp_path)
    enforcer.on_tool_call("Bash", {"command": f"python9.99 {script}"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23h_shebang_mismatch"]
    # `python9.99` doesn't resolve → mismatch with actual=unresolved.
    assert len(hits) == 1
    assert "unresolved" in hits[0]["prompt"]


def test_r23h_quiet_when_no_script_involved(tmp_path):
    enforcer = _enforcer_hard("R23h_shebang_mismatch", tmp_path)
    enforcer.on_tool_call("Bash", {"command": "ls -la"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23h_shebang_mismatch"]
    assert hits == []


def test_r23h_shadow_default_does_not_enqueue(tmp_path):
    script = _write_script(tmp_path, "deploy.py", "/usr/bin/env python9.99")
    enforcer = _enforcer_shadow_default()
    enforcer.on_tool_call("Bash", {"command": f"python9.99 {script}"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23h_shebang_mismatch"]
    assert hits == []
