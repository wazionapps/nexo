from __future__ import annotations

import importlib
import json


def test_system_catalog_surfaces_new_core_tools_and_runtime_metadata(monkeypatch, tmp_path, isolated_db):
    nexo_home = tmp_path / "nexo"
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    scripts_dir = nexo_home / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "wifi-check.py").write_text(
        "# nexo: name=wifi-check\n"
        "# nexo: description=Check DIGI wifi status\n"
        "# nexo: runtime=python\n"
        "print('ok')\n"
    )

    atlas_path = nexo_home / "brain" / "project-atlas.json"
    atlas_path.parent.mkdir(parents=True, exist_ok=True)
    atlas_path.write_text(
        json.dumps(
            {
                "nexo": {
                    "path": "/Users/franciscoc/Documents/_PhpstormProjects/nexo",
                    "aliases": ["brain", "shared brain"],
                    "services": {"mcp": 8000},
                }
            },
            ensure_ascii=False,
        )
    )

    import script_registry
    import system_catalog
    import tools_system_catalog

    importlib.reload(script_registry)
    importlib.reload(system_catalog)
    importlib.reload(tools_system_catalog)

    summary = tools_system_catalog.handle_system_catalog()
    assert "SYSTEM CATALOG SUMMARY" in summary
    assert "core_tools:" in summary

    transcript_section = tools_system_catalog.handle_system_catalog(section="core_tools", query="transcript", limit=20)
    assert "nexo_transcript_" in transcript_section

    explain = tools_system_catalog.handle_tool_explain("nexo_system_catalog")
    assert "CATALOG ENTRY — nexo_system_catalog" in explain
    assert "live NEXO tool/capability" not in explain.lower()  # should be concrete, not fallback text

    scripts = tools_system_catalog.handle_system_catalog(section="scripts", limit=20)
    assert "wifi-check" in scripts

    projects = tools_system_catalog.handle_system_catalog(section="projects", limit=20)
    assert "nexo" in projects.lower()
