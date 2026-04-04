"""Tests for personal MCP plugin scaffolding."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def test_personal_plugin_create_scaffolds_plugin_and_script(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))

    import db
    import script_registry
    import plugins.personal_plugins as personal_plugins

    importlib.reload(db)
    importlib.reload(script_registry)
    importlib.reload(personal_plugins)

    db.init_db()
    payload = json.loads(personal_plugins.handle_personal_plugin_create(
        name="CRM Bridge",
        description="Personal CRM bridge tool.",
        create_companion_script=True,
        script_runtime="python",
    ))

    assert payload["ok"] is True
    assert payload["tool_name"] == "nexo_crm_bridge"
    plugin_path = Path(payload["plugin_path"])
    assert plugin_path.is_file()
    assert "handle_crm_bridge" in plugin_path.read_text()
    assert "nexo_crm_bridge" in plugin_path.read_text()
    assert payload["companion_script"]["ok"] is True
    assert Path(payload["companion_script"]["path"]).is_file()
