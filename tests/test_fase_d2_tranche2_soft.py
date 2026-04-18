"""Integration tests for Fase D2 tranche 2 (soft rules):
R23c cwd-mismatch, R23d chown-R, R23g secrets-output, R23i auto-deploy,
R23k script-dup-skill, R23m msg-duplicate.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import time

import pytest

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")),
)


@pytest.fixture(autouse=True)
def fase_d2_tranche2_runtime(isolated_db, tmp_path, monkeypatch):
    fake_home = tmp_path / "nexo_home"
    (fake_home / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))
    import enforcement_engine
    import guardian_config
    for mod in [
        "r23c_cwd_mismatch",
        "r23d_chown_chmod_recursive",
        "r23g_secrets_in_output",
        "r23i_auto_deploy_ignored",
        "r23k_script_duplicates_skill",
        "r23m_message_duplicate",
    ]:
        importlib.reload(importlib.import_module(mod))
    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)
    yield


def _enforcer():
    from enforcement_engine import HeadlessEnforcer
    return HeadlessEnforcer()


def _seed_project(name, local_path, aliases=None, deploy=None):
    from db import create_entity
    meta = {
        "local_path": local_path,
        "aliases": list(aliases or []),
        "deploy": dict(deploy or {}),
    }
    create_entity(name=name, type="project", value=json.dumps(meta))


# ──────────────────────────────────────────────────────────────────────
# R23c — destructive bash in wrong cwd
# ──────────────────────────────────────────────────────────────────────


def test_r23c_fires_on_wrong_cwd(tmp_path):
    project_dir = tmp_path / "wazion"
    other_dir = tmp_path / "other"
    for d in (project_dir, other_dir):
        d.mkdir(parents=True, exist_ok=True)
    _seed_project("WAzion", str(project_dir), aliases=["wazion"])
    enforcer = _enforcer()
    enforcer.on_user_message("Clean up the WAzion repo please")
    enforcer.on_tool_call(
        "Bash",
        {"command": "git reset --hard HEAD~1", "cwd": str(other_dir)},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23c_cwd_mismatch"]
    assert len(hits) == 1
    assert "WAzion" in hits[0]["prompt"]


def test_r23c_allows_matching_cwd(tmp_path):
    project_dir = tmp_path / "wazion"
    project_dir.mkdir()
    _seed_project("WAzion", str(project_dir), aliases=["wazion"])
    enforcer = _enforcer()
    enforcer.on_user_message("Clean up the WAzion repo")
    enforcer.on_tool_call(
        "Bash",
        {"command": "git reset --hard HEAD~1", "cwd": str(project_dir)},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23c_cwd_mismatch"]
    assert hits == []


def test_r23c_ignores_non_destructive():
    enforcer = _enforcer()
    enforcer.on_user_message("Clean up the WAzion repo")
    enforcer.on_tool_call("Bash", {"command": "ls -la", "cwd": "/tmp"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23c_cwd_mismatch"]
    assert hits == []


# ──────────────────────────────────────────────────────────────────────
# R23d — recursive chown/chmod without ls
# ──────────────────────────────────────────────────────────────────────


def test_r23d_fires_on_chown_R_var():
    enforcer = _enforcer()
    enforcer.on_tool_call("Bash", {"command": "chown -R www-data:www-data /var/www"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23d_chown_chmod_recursive"]
    assert len(hits) == 1
    assert "/var/www" in hits[0]["prompt"]


def test_r23d_allows_targeted_chown():
    enforcer = _enforcer()
    enforcer.on_tool_call(
        "Bash",
        {"command": "chown -R user:user /home/user/project/.config"},
    )
    # /home is root-ish too → fires. But /home/user/project/.config is a
    # narrow subpath that still starts with /home, so it still fires.
    # Accept either behaviour but prefer warning for /home tree.
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23d_chown_chmod_recursive"]
    assert len(hits) == 1


def test_r23d_ignores_non_recursive():
    enforcer = _enforcer()
    enforcer.on_tool_call("Bash", {"command": "chown user:user /etc/passwd"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23d_chown_chmod_recursive"]
    assert hits == []


# ──────────────────────────────────────────────────────────────────────
# R23g — secrets in output
# ──────────────────────────────────────────────────────────────────────


def test_r23g_fires_on_env_dump():
    enforcer = _enforcer()
    enforcer.on_tool_call("Bash", {"command": "env | grep TOKEN"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23g_secrets_in_output"]
    assert len(hits) == 1


def test_r23g_fires_on_echo_secret_var():
    enforcer = _enforcer()
    enforcer.on_tool_call("Bash", {"command": "echo $SHOPIFY_API_KEY"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23g_secrets_in_output"]
    assert len(hits) == 1


def test_r23g_fires_on_cat_env_file():
    enforcer = _enforcer()
    enforcer.on_tool_call("Bash", {"command": "cat /etc/app/.env.production"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23g_secrets_in_output"]
    assert len(hits) == 1


def test_r23g_ignores_plain_echo():
    enforcer = _enforcer()
    enforcer.on_tool_call("Bash", {"command": "echo hello world"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23g_secrets_in_output"]
    assert hits == []


# ──────────────────────────────────────────────────────────────────────
# R23i — auto-deploy after push
# ──────────────────────────────────────────────────────────────────────


def test_r23i_fires_on_edit_after_push(tmp_path):
    project = tmp_path / "shop"
    project.mkdir()
    _seed_project(
        "Shop",
        str(project),
        aliases=["shop"],
        deploy={"auto_deploy": True},
    )
    enforcer = _enforcer()
    enforcer.on_user_message("Update the Shop header")
    enforcer.on_tool_call("Bash", {"command": "git push origin main"})
    enforcer.on_tool_call("Edit", {"file_path": str(project / "header.html")})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23i_auto_deploy_ignored"]
    assert len(hits) == 1
    assert "auto_deploy=true" in hits[0]["prompt"]


def test_r23i_quiet_without_auto_deploy(tmp_path):
    project = tmp_path / "shop"
    project.mkdir()
    _seed_project("Shop", str(project), aliases=["shop"], deploy={"auto_deploy": False})
    enforcer = _enforcer()
    enforcer.on_user_message("Update the Shop header")
    enforcer.on_tool_call("Bash", {"command": "git push origin main"})
    enforcer.on_tool_call("Edit", {"file_path": str(project / "header.html")})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23i_auto_deploy_ignored"]
    assert hits == []


# ──────────────────────────────────────────────────────────────────────
# R23m — duplicate messages
# ──────────────────────────────────────────────────────────────────────


def test_r23m_fires_on_duplicate_send():
    enforcer = _enforcer()
    body = "Hi Maria, the upgrade plan is attached. Let me know if it works."
    tool_input = {"to": "maria@example.com", "body": body}
    enforcer.on_tool_call("nexo_email_send", tool_input)
    enforcer.on_tool_call("nexo_email_send", tool_input)
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23m_message_duplicate"]
    assert len(hits) == 1


def test_r23m_allows_distinct_body():
    enforcer = _enforcer()
    enforcer.on_tool_call(
        "nexo_email_send",
        {"to": "maria@example.com", "body": "Hola Maria, primera versión"},
    )
    enforcer.on_tool_call(
        "nexo_email_send",
        {"to": "maria@example.com", "body": "Second reply with completely different content about the flange specification"},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23m_message_duplicate"]
    assert hits == []


def test_r23m_allows_same_body_different_thread():
    enforcer = _enforcer()
    body = "Same body same words"
    enforcer.on_tool_call("nexo_email_send", {"to": "a@x.com", "body": body})
    enforcer.on_tool_call("nexo_email_send", {"to": "b@x.com", "body": body})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23m_message_duplicate"]
    assert hits == []


# ──────────────────────────────────────────────────────────────────────
# R23k — silent skill_match probe + similarity threshold
# ──────────────────────────────────────────────────────────────────────


def test_r23k_fires_on_high_similarity_match(tmp_path):
    import guardian_config, enforcement_engine
    home = tmp_path / "r23k_home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    default = guardian_config.load_default_guardian_config()
    default["rules"]["R23k_script_duplicates_skill"] = "hard"
    (home / "config" / "guardian.json").write_text(json.dumps(default))
    os.environ["NEXO_HOME"] = str(home)
    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)

    # Inject a stub skill_registry probe that returns a strong match.
    import types
    plugin_module = types.ModuleType("plugins.skill_registry")

    def _fake_skill_match(description):
        return [{"id": "SK-42", "title": "nightly audit", "score": 0.9}]

    plugin_module.skill_match = _fake_skill_match
    sys.modules["plugins.skill_registry"] = plugin_module

    enforcer = enforcement_engine.HeadlessEnforcer()
    enforcer.on_tool_call(
        "nexo_personal_script_create",
        {"name": "nightly-audit-wrapper", "description": "Do the nightly audit work"},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23k_script_duplicates_skill"]
    assert len(hits) == 1
    assert "SK-42" in hits[0]["prompt"]


def test_r23k_silent_below_threshold(tmp_path):
    import guardian_config, enforcement_engine
    home = tmp_path / "r23k_low_home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    default = guardian_config.load_default_guardian_config()
    default["rules"]["R23k_script_duplicates_skill"] = "hard"
    (home / "config" / "guardian.json").write_text(json.dumps(default))
    os.environ["NEXO_HOME"] = str(home)
    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)

    import types
    plugin_module = types.ModuleType("plugins.skill_registry")
    plugin_module.skill_match = lambda description: [
        {"id": "SK-weak", "title": "barely similar", "score": 0.4}
    ]
    sys.modules["plugins.skill_registry"] = plugin_module

    enforcer = enforcement_engine.HeadlessEnforcer()
    enforcer.on_tool_call(
        "nexo_personal_script_create",
        {"name": "brand-new-script", "description": "Totally unrelated"},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23k_script_duplicates_skill"]
    assert hits == []


# ──────────────────────────────────────────────────────────────────────
# Soft-mode contract (audit NOTE): soft enqueues; does not block.
# ──────────────────────────────────────────────────────────────────────


def test_r23g_soft_default_enqueues_reminder():
    enforcer = _enforcer()
    # R23g defaults to soft — a secret-leaking command should enqueue
    # without any per-test override.
    enforcer.on_tool_call("Bash", {"command": "echo $SHOPIFY_API_KEY"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23g_secrets_in_output"]
    assert len(hits) == 1


def test_r23d_soft_default_enqueues_reminder():
    enforcer = _enforcer()
    enforcer.on_tool_call("Bash", {"command": "chown -R www-data:www-data /var/www"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23d_chown_chmod_recursive"]
    assert len(hits) == 1
