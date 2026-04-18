"""Integration tests for Fase D tranche 3 — R19 + R21 + R22."""
from __future__ import annotations

import importlib
import json
import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")),
)


@pytest.fixture(autouse=True)
def fase_d3_runtime(isolated_db, tmp_path, monkeypatch):
    fake_home = tmp_path / "nexo_home"
    (fake_home / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))
    import enforcement_engine
    import guardian_config
    import r19_project_grep
    import r21_legacy_path
    import r22_personal_script
    importlib.reload(r19_project_grep)
    importlib.reload(r21_legacy_path)
    importlib.reload(r22_personal_script)
    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)
    yield


def _enforcer():
    from enforcement_engine import HeadlessEnforcer
    return HeadlessEnforcer()


def _seed_project_with_grep_flag(name="WAzion"):
    from db import create_entity
    create_entity(
        name=name,
        type="project",
        value=json.dumps({
            "require_grep": True,
            "path_patterns": ["/wazion/", "wazion"],
        }),
    )


def _seed_legacy_mapping(old="~/claude/hooks", canonical="~/.nexo/hooks"):
    from db import create_entity
    create_entity(
        name="claude_hooks",
        type="legacy_path",
        value=json.dumps({"old": old, "canonical": canonical}),
    )


# ──────────────────────────────────────────────────────────────────────
# R19 integration
# ──────────────────────────────────────────────────────────────────────


def test_r19_injects_on_edit_to_project_without_grep():
    _seed_project_with_grep_flag()
    enforcer = _enforcer()
    enforcer.on_tool_call("Edit", {"file_path": "/repo/wazion/api/foo.php"})
    r19 = [q for q in enforcer.injection_queue if q["tag"].startswith("r19:")]
    assert len(r19) == 1


def test_r19_silent_after_grep():
    _seed_project_with_grep_flag()
    enforcer = _enforcer()
    enforcer.on_tool_call("Grep", {"pattern": "foo"})
    enforcer.on_tool_call("Edit", {"file_path": "/repo/wazion/api/foo.php"})
    r19 = [q for q in enforcer.injection_queue if q["tag"].startswith("r19:")]
    assert r19 == []


def test_r19_silent_on_non_project_path():
    _seed_project_with_grep_flag()
    enforcer = _enforcer()
    enforcer.on_tool_call("Edit", {"file_path": "/repo/other/bar.py"})
    r19 = [q for q in enforcer.injection_queue if q["tag"].startswith("r19:")]
    assert r19 == []


# ──────────────────────────────────────────────────────────────────────
# R21 integration
# ──────────────────────────────────────────────────────────────────────


def _enforcer_with_mode(rule_id: str, mode: str, tmp_path):
    """Spawn an enforcer with an explicit guardian.json override so tests
    can exercise hard-mode behaviour even for rules that ship shadow by
    default (R21, R24)."""
    import guardian_config, enforcement_engine
    home = tmp_path / f"{rule_id}_home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    default = guardian_config.load_default_guardian_config()
    default["rules"][rule_id] = mode
    (home / "config" / "guardian.json").write_text(json.dumps(default))
    os.environ["NEXO_HOME"] = str(home)
    import importlib
    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)
    return enforcement_engine.HeadlessEnforcer()


def test_r21_injects_on_legacy_path_write(tmp_path):
    _seed_legacy_mapping()
    enforcer = _enforcer_with_mode("R21_legacy_path", "hard", tmp_path)
    enforcer.on_tool_call("Edit", {
        "file_path": os.path.expanduser("~/claude/hooks/test.sh"),
    })
    r21 = [q for q in enforcer.injection_queue if q["tag"].startswith("r21:")]
    assert len(r21) == 1
    assert "~/.nexo/hooks" in r21[0]["prompt"]


def test_r21_injects_on_bash_legacy_reference(tmp_path):
    _seed_legacy_mapping()
    enforcer = _enforcer_with_mode("R21_legacy_path", "hard", tmp_path)
    enforcer.on_tool_call("Bash", {"command": "cat ~/claude/hooks/old.sh"})
    r21 = [q for q in enforcer.injection_queue if q["tag"].startswith("r21:")]
    assert len(r21) == 1


def test_r21_shadow_default_does_not_enqueue():
    """Default guardian config puts R21 in shadow; no injection expected."""
    _seed_legacy_mapping()
    enforcer = _enforcer()
    enforcer.on_tool_call("Edit", {
        "file_path": os.path.expanduser("~/claude/hooks/test.sh"),
    })
    r21 = [q for q in enforcer.injection_queue if q["tag"].startswith("r21:")]
    assert r21 == []


def test_r21_silent_on_canonical_path():
    _seed_legacy_mapping()
    enforcer = _enforcer()
    enforcer.on_tool_call("Edit", {
        "file_path": os.path.expanduser("~/.nexo/hooks/test.sh"),
    })
    r21 = [q for q in enforcer.injection_queue if q["tag"].startswith("r21:")]
    assert r21 == []


def test_r21_silent_without_mappings():
    enforcer = _enforcer()
    enforcer.on_tool_call("Edit", {"file_path": "~/claude/hooks/x.sh"})
    r21 = [q for q in enforcer.injection_queue if q["tag"].startswith("r21:")]
    assert r21 == []


# ──────────────────────────────────────────────────────────────────────
# R22 integration
# ──────────────────────────────────────────────────────────────────────


def test_r22_injects_on_personal_script_create_without_context():
    enforcer = _enforcer()
    enforcer.on_tool_call("nexo_personal_script_create", {"name": "myhelper"})
    r22 = [q for q in enforcer.injection_queue if q["tag"].startswith("r22:")]
    assert len(r22) == 1


def test_r22_silent_when_context_probes_present():
    enforcer = _enforcer()
    enforcer.on_tool_call("nexo_personal_scripts_list", {})
    enforcer.on_tool_call("nexo_skill_match", {"task": "write helper"})
    enforcer.on_tool_call("nexo_learning_search", {"query": "helper"})
    enforcer.on_tool_call("nexo_personal_script_create", {"name": "myhelper"})
    r22 = [q for q in enforcer.injection_queue if q["tag"].startswith("r22:")]
    assert r22 == []


def test_r22_injects_on_write_to_personal_scripts_dir():
    enforcer = _enforcer()
    enforcer.on_tool_call("Write", {
        "file_path": "/Users/x/.nexo/scripts/helper.sh",
        "content": "#!/bin/bash\necho hi",
    })
    r22 = [q for q in enforcer.injection_queue if q["tag"].startswith("r22:")]
    assert len(r22) == 1
