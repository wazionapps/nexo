"""Tests for retired Evolution runtime surface."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def test_evolution_runtime_files_are_removed():
    removed_paths = [
        REPO_SRC / "evolution_cycle.py",
        REPO_SRC / "public_evolution_queue.py",
        REPO_SRC / "scripts" / "nexo-evolution-run.py",
        REPO_ROOT / "templates" / "core-prompts" / "evolution-weekly.md",
        REPO_ROOT / "templates" / "core-prompts" / "evolution-public-contribution.md",
        REPO_ROOT / "templates" / "core-prompts" / "evolution-public-pr-review.md",
    ]

    assert [str(path) for path in removed_paths if path.exists()] == []


def test_evolution_plugin_exposes_no_tools(monkeypatch, tmp_path):
    home = tmp_path / "nexo-home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "config" / "product-mode.json").write_text(json.dumps({
        "desktop_managed": True,
        "product_mode": "desktop_closed_product",
    }))
    monkeypatch.setenv("NEXO_HOME", str(home))

    import paths
    import plugins.evolution as evolution_plugin

    importlib.reload(paths)
    importlib.reload(evolution_plugin)

    assert evolution_plugin.TOOLS == []
    assert evolution_plugin.handle_evolution_status() == "Evolution has been removed from NEXO Desktop."
    assert evolution_plugin.handle_evolution_history() == "Evolution has been removed from NEXO Desktop."
    assert evolution_plugin.handle_evolution_propose() == "Evolution has been removed from NEXO Desktop."
    assert evolution_plugin.handle_evolution_approve(1, "ok") == "Evolution has been removed from NEXO Desktop."
    assert evolution_plugin.handle_evolution_reject(1, "no") == "Evolution has been removed from NEXO Desktop."


def test_mcp_startup_and_tool_enforcement_do_not_register_evolution_tools():
    import server

    tool_map = json.loads((REPO_ROOT / "tool-enforcement-map.json").read_text())

    assert "evolution.py" not in server._ESSENTIAL_MCP_STARTUP_PLUGINS
    assert [name for name in tool_map if name.startswith("nexo_evolution")] == []
