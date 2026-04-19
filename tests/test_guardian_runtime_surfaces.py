from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def test_build_guardian_runtime_surfaces_from_db(tmp_path, monkeypatch, isolated_db):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo-home"))

    from db import create_entity
    from guardian_runtime_surfaces import build_guardian_runtime_surfaces, write_guardian_runtime_surfaces

    create_entity(
        "maria",
        "host",
        json.dumps({"aliases": ["maria-db"], "access_mode": "read_only"}),
    )
    create_entity(
        "rm",
        "destructive_command",
        json.dumps({"pattern": r"\brm\b"}),
    )
    create_entity(
        "WAzion",
        "project",
        json.dumps({
            "aliases": ["wazion"],
            "require_grep": True,
            "path_patterns": ["/srv/wazion/"],
            "local_path": "/srv/wazion",
            "deploy": {"auto_deploy": True},
        }),
    )
    create_entity(
        "claude-hooks",
        "legacy_path",
        json.dumps({"old": "~/claude/hooks", "canonical": "~/.nexo/hooks"}),
    )
    create_entity(
        "systeam_es",
        "vhost_mapping",
        json.dumps({"domain": "systeam.es", "host": "vicshop", "docroot": "/var/www/systeam"}),
    )
    create_entity(
        "orders",
        "db",
        json.dumps({"env": "production", "uri": "mysql://prod/orders"}),
    )

    payload = build_guardian_runtime_surfaces(nexo_home=tmp_path / "nexo-home")
    assert payload["source"] == "db"
    assert payload["entity_count"] >= 6
    assert "maria" in payload["known_hosts"]
    assert "maria-db" in payload["known_hosts"]
    assert "maria" in payload["read_only_hosts"]
    assert r"\brm\b" in payload["destructive_patterns"]
    assert any(project["name"] == "WAzion" and project["require_grep"] for project in payload["projects"])
    assert {"old": "~/claude/hooks", "canonical": "~/.nexo/hooks"} in payload["legacy_mappings"]
    assert any(item["name"] == "systeam_es" for item in payload["vhost_mappings"])
    assert "mysql://prod/orders" in payload["db_production_markers"]

    result = write_guardian_runtime_surfaces(nexo_home=tmp_path / "nexo-home")
    assert result["ok"] is True
    written = json.loads((tmp_path / "nexo-home" / "personal" / "brain" / "guardian-runtime-surfaces.json").read_text())
    assert written["schema"] == "guardian-runtime-surfaces-v1"


def test_build_guardian_runtime_surfaces_falls_back_to_preset_when_db_empty(tmp_path, monkeypatch, isolated_db):
    home = tmp_path / "nexo-home"
    monkeypatch.setenv("NEXO_HOME", str(home))
    preset_dir = home / "personal" / "brain" / "presets"
    preset_dir.mkdir(parents=True)
    (preset_dir / "entities_universal.json").write_text(json.dumps({
        "entities": [
            {
                "type": "host",
                "name": "vicshop",
                "metadata": {"aliases": ["cpanel-vic"], "access_mode": "read_only"},
            },
            {
                "type": "destructive_command",
                "name": "rm",
                "metadata": {"pattern": r"\brm\b"},
            },
        ],
    }))

    from guardian_runtime_surfaces import build_guardian_runtime_surfaces

    payload = build_guardian_runtime_surfaces(nexo_home=home)
    assert payload["source"] == "preset_fallback"
    assert payload["entity_count"] == 2
    assert "vicshop" in payload["known_hosts"]
    assert "vicshop" in payload["read_only_hosts"]
    assert r"\brm\b" in payload["destructive_patterns"]
