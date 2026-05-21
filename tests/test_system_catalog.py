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
    assert "Signature: nexo_system_catalog(" in explain
    assert "Examples:" in explain

    scripts = tools_system_catalog.handle_system_catalog(section="scripts", limit=20)
    assert "wifi-check" in scripts

    projects = tools_system_catalog.handle_system_catalog(section="projects", limit=20)
    assert "nexo" in projects.lower()

    learning_explain = tools_system_catalog.handle_tool_explain("mcp__nexo__nexo_learning_add")
    assert "CATALOG ENTRY — nexo_learning_add" in learning_explain
    assert "Required args:" in learning_explain
    assert "applies_to" in learning_explain
    assert "Learning linked to a file or pattern" in learning_explain
    assert "severity" in learning_explain

    reminder_explain = tools_system_catalog.handle_tool_explain("nexo_reminder_update")
    assert "READ_TOKEN" in reminder_explain
    assert "nexo_reminder_get" in reminder_explain


def test_tool_explain_prunes_stale_plugin_rows_before_rendering(monkeypatch, tmp_path, isolated_db):
    repo_plugins = tmp_path / "repo-plugins"
    personal_plugins = tmp_path / "personal-plugins"
    repo_plugins.mkdir(parents=True, exist_ok=True)
    personal_plugins.mkdir(parents=True, exist_ok=True)
    (repo_plugins / "alpha.py").write_text(
        "def demo_tool(task: str) -> str:\n"
        "    \"\"\"Demo tool.\n\n"
        "    Args:\n"
        "        task: Work to perform.\n"
        "    \"\"\"\n"
        "    return task\n\n"
        "TOOLS = [(demo_tool, 'nexo_demo_tool', 'Demo plugin tool')]\n",
        encoding="utf-8",
    )

    import plugin_loader
    from db import get_db
    import system_catalog
    import tools_system_catalog

    monkeypatch.setattr(plugin_loader, "PLUGINS_DIR", str(repo_plugins))
    monkeypatch.setattr(plugin_loader, "PERSONAL_PLUGINS_DIR", str(personal_plugins))

    conn = get_db()
    conn.execute(
        "INSERT INTO plugins (filename, tools_count, tool_names, loaded_at, created_by) VALUES (?, ?, ?, ?, ?)",
        ("alpha.py", 1, "nexo_demo_tool", 0, "repo"),
    )
    conn.execute(
        "INSERT INTO plugins (filename, tools_count, tool_names, loaded_at, created_by) VALUES (?, ?, ?, ?, ?)",
        ("alpha 2.py", 1, "nexo_demo_tool", 0, "repo"),
    )
    conn.commit()

    importlib.reload(system_catalog)
    importlib.reload(tools_system_catalog)

    explain = tools_system_catalog.handle_tool_explain("nexo_demo_tool")

    assert "CATALOG ENTRY — nexo_demo_tool" in explain
    assert "Plugin: alpha.py" in explain
    assert "alpha 2.py" not in explain
    assert "Signature: nexo_demo_tool(task: str)" in explain
