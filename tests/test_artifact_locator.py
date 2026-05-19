from __future__ import annotations

from artifact_locator import locate_artifact, resolve_project


ATLAS = {
    "nexo": {
        "aliases": ["brain", "mcp"],
        "description": "Sistema operativo NEXO",
        "locations": {
            "mcp_server": "/repo/nexo/src",
            "data": "~/.nexo/runtime/data",
        },
    }
}


def test_resolve_project_uses_key_alias_and_description():
    assert resolve_project(ATLAS, "nexo")["key"] == "nexo"
    assert resolve_project(ATLAS, "brain")["key"] == "nexo"
    assert resolve_project(ATLAS, "sistema operativo")["key"] == "nexo"


def test_locate_artifact_returns_project_atlas_locations_first():
    result = locate_artifact(atlas=ATLAS, query="brain", artifact_kind="mcp")

    assert result["project_key"] == "nexo"
    assert result["used_fallback"] is False
    assert result["matches"] == [{
        "source": "project_atlas",
        "project_key": "nexo",
        "kind": "mcp_server",
        "path": "/repo/nexo/src",
        "confidence": 1.0,
    }]


def test_locate_artifact_uses_fallback_when_atlas_misses():
    result = locate_artifact(
        atlas=ATLAS,
        query="unknown file",
        fallback_search=lambda _query, _limit: [{"source": "rg", "path": "/tmp/file.py", "score": 0.7}],
    )

    assert result["project_key"] == ""
    assert result["used_fallback"] is True
    assert result["matches"][0]["source"] == "rg"
    assert result["matches"][0]["path"] == "/tmp/file.py"
