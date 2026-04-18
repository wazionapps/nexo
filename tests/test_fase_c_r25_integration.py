"""Integration tests for R25 Nora/María read-only guard (Fase C Capa 2)."""
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
def r25_runtime(isolated_db, tmp_path, monkeypatch):
    fake_home = tmp_path / "nexo_home"
    (fake_home / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))
    import enforcement_engine
    import guardian_config
    import r25_nora_maria_read_only
    importlib.reload(r25_nora_maria_read_only)
    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)
    yield


def _enforcer():
    from enforcement_engine import HeadlessEnforcer
    return HeadlessEnforcer()


def _seed_maria_readonly():
    """Seed the entities table so _r25_context picks Maria as a read-only host."""
    from db import create_entity
    create_entity(
        name="maria",
        type="host",
        value=json.dumps({"access_mode": "read_only", "aliases": ["maria-imac"]}),
        notes="iMac de María — observar, nunca tocar",
    )


def _seed_destructive_patterns():
    """Seed a minimal destructive-command entity list for the tests."""
    from db import create_entity
    create_entity(
        name="rm",
        type="destructive_command",
        value=json.dumps({"pattern": r"\brm\b", "severity": "high"}),
    )
    create_entity(
        name="mv_overwrite",
        type="destructive_command",
        value=json.dumps({"pattern": r"\bmv\b", "severity": "medium"}),
    )


# ──────────────────────────────────────────────────────────────────────
# Decision module
# ──────────────────────────────────────────────────────────────────────


def test_r25_decision_fires_on_read_only_host_destructive_without_permit():
    from r25_nora_maria_read_only import should_inject_r25
    r = should_inject_r25(
        "ssh maria rm -rf /tmp/foo",
        read_only_hosts={"maria"},
        destructive_patterns=[r"\brm\b"],
        last_user_text="hola",
    )
    assert r is not None
    assert r["host"] == "maria"
    assert r["tag"] == "r25:maria"


def test_r25_decision_quiet_with_explicit_permit():
    from r25_nora_maria_read_only import should_inject_r25
    r = should_inject_r25(
        "ssh maria rm -rf /tmp/foo",
        read_only_hosts={"maria"},
        destructive_patterns=[r"\brm\b"],
        last_user_text="yes force OK",
    )
    assert r is None


def test_r25_decision_quiet_on_read_write_host():
    from r25_nora_maria_read_only import should_inject_r25
    assert should_inject_r25(
        "ssh vicshop rm -rf /tmp/foo",
        read_only_hosts={"maria"},
        destructive_patterns=[r"\brm\b"],
    ) is None


def test_r25_decision_quiet_on_non_destructive_command():
    from r25_nora_maria_read_only import should_inject_r25
    assert should_inject_r25(
        "ssh maria ls /tmp",
        read_only_hosts={"maria"},
        destructive_patterns=[r"\brm\b"],
    ) is None


def test_r25_host_extraction_variants():
    from r25_nora_maria_read_only import extract_remote_host
    assert extract_remote_host("ssh maria ls") == "maria"
    assert extract_remote_host("ssh franciscoc@nora ls") == "nora"
    assert extract_remote_host("scp file maria:/tmp/") == "maria"
    assert extract_remote_host("rsync -av src/ maria:/var/www/") == "maria"
    assert extract_remote_host("ls /tmp") is None


# ──────────────────────────────────────────────────────────────────────
# HeadlessEnforcer integration
# ──────────────────────────────────────────────────────────────────────


def test_r25_injects_on_ssh_maria_rm():
    _seed_maria_readonly()
    _seed_destructive_patterns()
    enforcer = _enforcer()
    enforcer.on_user_message("borra eso de tmp", correction_detector=lambda _: False)
    enforcer.on_tool_call("Bash", {"command": "ssh maria rm -rf /tmp/foo"})
    r25_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r25:")]
    assert len(r25_q) == 1
    assert "maria" in r25_q[0]["prompt"]


def test_r25_quiet_when_user_permits():
    _seed_maria_readonly()
    _seed_destructive_patterns()
    enforcer = _enforcer()
    enforcer.on_user_message("yes, force OK: borra", correction_detector=lambda _: False)
    enforcer.on_tool_call("Bash", {"command": "ssh maria rm -rf /tmp/foo"})
    r25_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r25:")]
    assert r25_q == []


def test_r25_quiet_on_non_bash_tool():
    _seed_maria_readonly()
    _seed_destructive_patterns()
    enforcer = _enforcer()
    enforcer.on_tool_call("Edit", {"file_path": "/repo/foo.py"})
    r25_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r25:")]
    assert r25_q == []


def test_r25_quiet_on_non_destructive_bash():
    _seed_maria_readonly()
    _seed_destructive_patterns()
    enforcer = _enforcer()
    enforcer.on_tool_call("Bash", {"command": "ssh maria ls /tmp"})
    r25_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r25:")]
    assert r25_q == []


def test_r25_quiet_when_no_entities_seeded():
    """Fail-closed: empty entity_list → no R25 injection."""
    enforcer = _enforcer()
    enforcer.on_tool_call("Bash", {"command": "ssh maria rm -rf /tmp/foo"})
    r25_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r25:")]
    assert r25_q == []


def test_r25_shadow_mode_logs_only(tmp_path, monkeypatch):
    import guardian_config
    import enforcement_engine

    home = tmp_path / "shadow_home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    default = guardian_config.load_default_guardian_config()
    default["rules"]["R25_nora_maria_read_only"] = "shadow"
    (home / "config" / "guardian.json").write_text(json.dumps(default))
    monkeypatch.setenv("NEXO_HOME", str(home))

    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)

    _seed_maria_readonly()
    _seed_destructive_patterns()
    enforcer = enforcement_engine.HeadlessEnforcer()
    enforcer.on_tool_call("Bash", {"command": "ssh maria rm -rf /tmp"})
    r25_q = [q for q in enforcer.injection_queue if q["tag"].startswith("r25:")]
    assert r25_q == []
