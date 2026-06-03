from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    import db._core as db_core
    import db._schema as db_schema
    import db

    db_core.close_db()
    importlib.reload(db_core)
    importlib.reload(db_schema)
    importlib.reload(db)
    db.init_db()
    yield home
    db_core.close_db()


def test_morning_agent_preferences_validate_live_sources_and_help():
    from automation_preferences import default_automation_preferences, get_automation_preference_schema, validate_automation_preferences

    defaults = default_automation_preferences("morning-agent")
    assert defaults["values"]["priorities"] is True
    assert defaults["values"]["news"] is False
    assert defaults["values"]["weather"] is True

    schema = get_automation_preference_schema("morning-agent")
    items = [item for group in schema["groups"] for item in group["items"]]
    assert "audience" not in {item["id"] for item in items}
    assert all(item.get("help") for item in items)

    validated = validate_automation_preferences("morning-agent", {
        "values": {
            "news": True,
            "weather": True,
            "length": "detailed",
            "tone": "warm",
            "unknown": True,
        }
    })
    assert validated["values"]["news"] is True
    assert validated["values"]["weather"] is True
    assert validated["values"]["length"] == "detailed"
    assert validated["values"]["tone"] == "warm"
    assert validated["warnings"] == []


def test_set_preferences_preserves_extra_instructions(isolated_home):
    from automation_preferences import get_automation_preferences, set_automation_preferences
    from db._personal_scripts import get_personal_script, upsert_personal_script

    script_path = SRC / "scripts" / "nexo-morning-agent.py"
    upsert_personal_script(
        name="morning-agent",
        path=str(script_path),
        description="Morning briefing",
        runtime="python",
        metadata={"operator_extra_instructions": "Keep the opening very direct."},
        created_by="nexo-core",
        source="core-toggle",
        origin="core",
    )

    result = set_automation_preferences("morning-agent", {
        "values": {"length": "short", "priorities": False}
    })
    assert result["ok"] is True

    row = get_personal_script("morning-agent", include_core=True)
    metadata = row["metadata"]
    assert metadata["operator_extra_instructions"] == "Keep the opening very direct."
    assert metadata["automation_preferences"]["values"]["length"] == "short"

    prefs = get_automation_preferences("morning-agent")
    assert prefs["preferences"]["values"]["priorities"] is False


def test_preferences_prompt_block_is_json_and_blocks_unavailable_data(isolated_home):
    from automation_preferences import format_automation_preferences_prompt_block, set_automation_preferences

    set_automation_preferences("morning-agent", {"values": {"weather": True, "format": "bullets"}})
    block = format_automation_preferences_prompt_block("morning-agent")
    payload = block.split("\n")[2]
    values = json.loads(payload)

    assert values["weather"] is True
    assert values["format"] == "bullets"
    assert "professional personal assistant" in block
    assert "Do not ask the user to choose a user type manually" in block
    assert "must not be invented" in block
    assert "news and weather require verified collected data" in block
