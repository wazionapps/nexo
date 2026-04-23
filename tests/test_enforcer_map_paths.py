from __future__ import annotations

import json
import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def test_headless_enforcer_loads_map_from_installed_core_dir(tmp_path, monkeypatch):
    import enforcement_engine

    core_dir = tmp_path / "core"
    core_dir.mkdir()
    module_path = core_dir / "enforcement_engine.py"
    module_path.write_text("# installed module placeholder\n", encoding="utf-8")
    (core_dir / "tool-enforcement-map.json").write_text(
        json.dumps({"version": "core-test", "tools": {}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(enforcement_engine, "__file__", str(module_path))
    monkeypatch.setattr(enforcement_engine.paths, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(enforcement_engine.paths, "brain_dir", lambda: tmp_path / "brain")
    monkeypatch.setattr(enforcement_engine.paths, "legacy_brain_dir", lambda: tmp_path / "legacy")

    loaded = enforcement_engine._load_map()
    assert loaded is not None
    assert loaded["version"] == "core-test"


def test_agent_runner_prompt_loads_map_from_installed_core_dir(tmp_path, monkeypatch):
    import agent_runner

    core_dir = tmp_path / "core"
    core_dir.mkdir()
    module_path = core_dir / "agent_runner.py"
    module_path.write_text("# installed module placeholder\n", encoding="utf-8")
    (core_dir / "tool-enforcement-map.json").write_text(
        json.dumps(
            {
                "version": "core-test",
                "tools": {
                    "nexo_startup": {
                        "enforcement": {
                            "level": "must",
                            "rules": [
                                {
                                    "type": "on_session_start",
                                    "description": "start every session through NEXO",
                                }
                            ],
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(agent_runner, "__file__", str(module_path))
    monkeypatch.setattr(agent_runner, "NEXO_HOME", tmp_path / "empty-home")

    prompt = agent_runner._build_enforcement_system_prompt()
    assert "nexo_startup" in prompt
    assert "start every session through NEXO" in prompt
