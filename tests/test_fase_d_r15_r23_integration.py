"""Integration tests for Fase D R15 project-context + R23 SSH-sin-atlas."""
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
def fase_d2_runtime(isolated_db, tmp_path, monkeypatch):
    fake_home = tmp_path / "nexo_home"
    (fake_home / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))
    import enforcement_engine
    import guardian_config
    import r15_project_context
    import r23_ssh_without_atlas
    importlib.reload(r15_project_context)
    importlib.reload(r23_ssh_without_atlas)
    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)
    yield


def _enforcer():
    from enforcement_engine import HeadlessEnforcer
    return HeadlessEnforcer()


def _seed_wazion_project():
    from db import create_entity
    create_entity(
        name="WAzion",
        type="project",
        value=json.dumps({"aliases": ["wazion-cloud", "bmw-whatsapp"]}),
    )


def _seed_known_host(name, aliases=None):
    from db import create_entity
    meta = {"aliases": list(aliases or [])}
    create_entity(name=name, type="host", value=json.dumps(meta))


# ──────────────────────────────────────────────────────────────────────
# R15 decision
# ──────────────────────────────────────────────────────────────────────


def test_r15_match_project_by_name_or_alias():
    from r15_project_context import match_project_names
    projects = [{"name": "WAzion", "aliases": ["wazion-cloud"]}]
    assert match_project_names("revisar wazion-cloud", projects) == ["WAzion"]
    assert match_project_names("wazion necesita fix", projects) == ["WAzion"]
    assert match_project_names("random talk", projects) == []


def test_r15_recent_context_recall_matches():
    from r15_project_context import recent_context_loaded
    from r13_pre_edit_guard import ToolCallRecord
    records = [ToolCallRecord(tool="nexo_recall", ts=1.0, files=("wazion",))]
    assert recent_context_loaded("WAzion", records) is True


def test_r15_atlas_read_counts():
    from r15_project_context import recent_context_loaded
    from r13_pre_edit_guard import ToolCallRecord
    records = [ToolCallRecord(tool="Read", ts=1.0, files=("/x/.nexo/brain/project-atlas.json",))]
    assert recent_context_loaded("WAzion", records) is True


# ──────────────────────────────────────────────────────────────────────
# R15 integration
# ──────────────────────────────────────────────────────────────────────


def test_r15_injects_on_project_mention_without_recall():
    _seed_wazion_project()
    enforcer = _enforcer()
    enforcer.on_user_message_r15("necesito tocar WAzion hoy")
    r15 = [q for q in enforcer.injection_queue if q["tag"].startswith("r15:")]
    assert len(r15) == 1
    assert "WAzion" in r15[0]["prompt"]


def test_r15_silent_when_project_not_mentioned():
    _seed_wazion_project()
    enforcer = _enforcer()
    enforcer.on_user_message_r15("charla sin proyecto")
    r15 = [q for q in enforcer.injection_queue if q["tag"].startswith("r15:")]
    assert r15 == []


def test_r15_silent_when_project_not_registered():
    """No project entities in DB → R15 does not fire at all."""
    enforcer = _enforcer()
    enforcer.on_user_message_r15("tocar recambios-bmw urgente")
    r15 = [q for q in enforcer.injection_queue if q["tag"].startswith("r15:")]
    assert r15 == []


# ──────────────────────────────────────────────────────────────────────
# R23 decision
# ──────────────────────────────────────────────────────────────────────


def test_r23_extracts_ssh_and_curl_hosts():
    from r23_ssh_without_atlas import extract_remote_host
    assert extract_remote_host("ssh vicshop ls") == "vicshop"
    assert extract_remote_host("curl https://api.example.com/x") == "api.example.com"
    assert extract_remote_host("ls /tmp") is None


def test_r23_empty_known_hosts_stays_silent():
    from r23_ssh_without_atlas import should_inject_r23
    r = should_inject_r23("ssh maria ls", known_hosts=set())
    assert r is None


def test_r23_known_host_no_inject():
    from r23_ssh_without_atlas import should_inject_r23
    r = should_inject_r23("ssh maria ls", known_hosts={"maria"})
    assert r is None


def test_r23_unknown_host_injects():
    from r23_ssh_without_atlas import should_inject_r23
    r = should_inject_r23("ssh random-box ls", known_hosts={"maria", "nora"})
    assert r is not None
    assert r["host"] == "random-box"


# ──────────────────────────────────────────────────────────────────────
# R23 integration
# ──────────────────────────────────────────────────────────────────────


def test_r23_injects_on_unknown_host_in_bash():
    _seed_known_host("maria")
    enforcer = _enforcer()
    enforcer.on_tool_call("Bash", {"command": "ssh random-box ls"})
    r23 = [q for q in enforcer.injection_queue if q["tag"].startswith("r23:")]
    assert len(r23) == 1
    assert "random-box" in r23[0]["prompt"]


def test_r23_silent_on_known_host():
    _seed_known_host("maria", aliases=["maria-imac"])
    enforcer = _enforcer()
    enforcer.on_tool_call("Bash", {"command": "ssh maria-imac ls"})
    r23 = [q for q in enforcer.injection_queue if q["tag"].startswith("r23:")]
    assert r23 == []


def test_r23_silent_without_any_host_entity():
    """Fresh install with no host entities → R23 silent (fail-closed)."""
    enforcer = _enforcer()
    enforcer.on_tool_call("Bash", {"command": "ssh whatever ls"})
    r23 = [q for q in enforcer.injection_queue if q["tag"].startswith("r23:")]
    assert r23 == []
