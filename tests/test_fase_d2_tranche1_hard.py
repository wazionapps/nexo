"""Integration tests for Fase D2 tranche 1 (hard bloqueantes):
R23b deploy-vhost, R23e force-push, R23f DB-no-WHERE, R23l resource-collision.
"""
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
def fase_d2_tranche1_runtime(isolated_db, tmp_path, monkeypatch):
    fake_home = tmp_path / "nexo_home"
    (fake_home / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))
    import enforcement_engine
    import guardian_config
    import r23b_deploy_vhost
    import r23e_force_push_main
    import r23f_db_no_where
    import r23l_resource_collision
    importlib.reload(r23b_deploy_vhost)
    importlib.reload(r23e_force_push_main)
    importlib.reload(r23f_db_no_where)
    importlib.reload(r23l_resource_collision)
    importlib.reload(guardian_config)
    importlib.reload(enforcement_engine)
    yield


def _enforcer():
    from enforcement_engine import HeadlessEnforcer
    return HeadlessEnforcer()


def _seed_vhost(name, domain, host, docroot):
    from db import create_entity
    create_entity(
        name=name,
        type="vhost_mapping",
        value=json.dumps({"domain": domain, "host": host, "docroot": docroot}),
    )


def _seed_db_production(name, host):
    from db import create_entity
    create_entity(
        name=name,
        type="db",
        value=json.dumps({"env": "production", "host": host}),
    )


# ──────────────────────────────────────────────────────────────────────
# R23e — git push --force protected branch
# ──────────────────────────────────────────────────────────────────────


def test_r23e_blocks_force_push_main():
    enforcer = _enforcer()
    enforcer.on_tool_call("Bash", {"command": "git push --force origin main"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23e_force_push_main"]
    assert len(hits) == 1
    assert "main" in hits[0]["prompt"]


def test_r23e_blocks_force_push_master_shortflag():
    enforcer = _enforcer()
    enforcer.on_tool_call("Bash", {"command": "git push -f upstream master"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23e_force_push_main"]
    assert len(hits) == 1
    assert "master" in hits[0]["prompt"]


def test_r23e_blocks_force_push_release_branch():
    enforcer = _enforcer()
    enforcer.on_tool_call(
        "Bash",
        {"command": "git push --force origin release-v6.1.0"},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23e_force_push_main"]
    assert len(hits) == 1


def test_r23e_blocks_force_push_without_explicit_branch():
    enforcer = _enforcer()
    enforcer.on_tool_call("Bash", {"command": "git push -f"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23e_force_push_main"]
    # Conservative: we still block because current branch is unknown.
    assert len(hits) == 1
    assert "current-branch" in hits[0]["prompt"]


def test_r23e_allows_normal_push():
    enforcer = _enforcer()
    enforcer.on_tool_call("Bash", {"command": "git push origin main"})
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23e_force_push_main"]
    assert hits == []


def test_r23e_allows_force_push_to_feature_branch():
    enforcer = _enforcer()
    enforcer.on_tool_call(
        "Bash",
        {"command": "git push --force origin feature/new-thing"},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23e_force_push_main"]
    assert hits == []


# ──────────────────────────────────────────────────────────────────────
# R23f — production DB DELETE/UPDATE without WHERE
# ──────────────────────────────────────────────────────────────────────


def test_r23f_blocks_bare_delete_on_mysql():
    enforcer = _enforcer()
    enforcer.on_tool_call(
        "Bash",
        {"command": "mysql -h prod-db.example.com -e 'DELETE FROM orders;'"},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23f_db_no_where"]
    assert len(hits) == 1
    assert "DELETE" in hits[0]["prompt"]


def test_r23f_blocks_update_without_where():
    enforcer = _enforcer()
    enforcer.on_tool_call(
        "Bash",
        {"command": "psql -c \"UPDATE users SET active = false\""},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23f_db_no_where"]
    assert len(hits) == 1
    assert "UPDATE" in hits[0]["prompt"]


def test_r23f_allows_delete_with_where():
    enforcer = _enforcer()
    enforcer.on_tool_call(
        "Bash",
        {"command": "mysql -e \"DELETE FROM orders WHERE id=42\""},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23f_db_no_where"]
    assert hits == []


def test_r23f_ignores_non_db_bash():
    enforcer = _enforcer()
    enforcer.on_tool_call(
        "Bash",
        {"command": "echo 'DELETE FROM users;' > readme.sql"},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23f_db_no_where"]
    assert hits == []


def test_r23f_production_marker_match():
    _seed_db_production("vicsys_prod", "db.mundiserver.com")
    enforcer = _enforcer()
    enforcer.on_tool_call(
        "Bash",
        {"command": "mysql -h db.mundiserver.com -e 'DELETE FROM cart;'"},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23f_db_no_where"]
    assert len(hits) == 1


# ──────────────────────────────────────────────────────────────────────
# R23b — deploy path ↔ vhost mismatch
# ──────────────────────────────────────────────────────────────────────


def test_r23b_fires_on_wrong_docroot():
    _seed_vhost("vicshop", "vicshop.com", "vicshop", "/home/vicshopsysteam/vicshop")
    _seed_vhost("systeam_es", "systeam.es", "vicshop", "/home/vicshopsysteam/public_html")
    enforcer = _enforcer()
    # User discussed systeam.es but deploys to vicshop docroot.
    enforcer.on_user_message("Deploy the new systeam.es header to production please")
    enforcer.on_tool_call(
        "Bash",
        {"command": "scp header.html vicshop:/home/vicshopsysteam/vicshop/header.html"},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23b_deploy_vhost"]
    assert len(hits) == 1
    assert "systeam.es" in hits[0]["prompt"]


def test_r23b_allows_matching_docroot():
    _seed_vhost("systeam_es", "systeam.es", "vicshop", "/home/vicshopsysteam/public_html")
    enforcer = _enforcer()
    enforcer.on_user_message("Deploy the systeam.es header")
    enforcer.on_tool_call(
        "Bash",
        {"command": "scp header.html vicshop:/home/vicshopsysteam/public_html/header.html"},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23b_deploy_vhost"]
    assert hits == []


def test_r23b_no_vhost_registry_no_fire():
    enforcer = _enforcer()
    enforcer.on_user_message("Deploy systeam.es header")
    enforcer.on_tool_call(
        "Bash",
        {"command": "scp h.html vicshop:/home/x/y.html"},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23b_deploy_vhost"]
    assert hits == []


# ──────────────────────────────────────────────────────────────────────
# R23l — create resource with existing name
# ──────────────────────────────────────────────────────────────────────


def test_r23l_blocks_cpanel_user_collision():
    from db import create_entity
    create_entity(
        name="maria_client",
        type="cpanel_account",
        value=json.dumps({"host": "mundiserver"}),
    )
    enforcer = _enforcer()
    enforcer.on_tool_call(
        "Bash",
        {"command": "whmapi1 createacct username=maria_client plan=default"},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23l_resource_collision"]
    assert len(hits) == 1
    assert "maria_client" in hits[0]["prompt"]


def test_r23l_blocks_wrangler_kv_collision():
    from db import create_entity
    create_entity(
        name="wazion-sessions",
        type="wrangler_kv",
        value=json.dumps({}),
    )
    enforcer = _enforcer()
    enforcer.on_tool_call(
        "Bash",
        {"command": "wrangler kv:namespace create wazion-sessions"},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23l_resource_collision"]
    assert len(hits) == 1


def test_r23l_allows_new_resource_name():
    from db import create_entity
    create_entity(
        name="existing_user",
        type="cpanel_account",
        value=json.dumps({}),
    )
    enforcer = _enforcer()
    enforcer.on_tool_call(
        "Bash",
        {"command": "whmapi1 createacct username=brand_new_user plan=default"},
    )
    hits = [q for q in enforcer.injection_queue if q["tag"] == "R23l_resource_collision"]
    assert hits == []
