import json
import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def test_schema_uses_non_product_default_assistant_name():
    import desktop_bridge

    fields = desktop_bridge._schema_fields()
    assistant_field = next(field for field in fields if field["path"] == "user.assistant_name")

    assert assistant_field["default"] == desktop_bridge.DEFAULT_ASSISTANT_NAME
    assert assistant_field["default"] != "NEXO"
    assert "NEXO" in assistant_field["reserved_values"]


def test_onboard_steps_reserve_product_name():
    import desktop_bridge

    steps = desktop_bridge._onboard_steps()
    assistant_step = next(step for step in steps if step["id"] == "assistant_name")

    assert assistant_step["default"] == desktop_bridge.DEFAULT_ASSISTANT_NAME
    assert "NEXO" in assistant_step["reserved_values"]


def test_identity_falls_back_to_new_default_for_blank_install(monkeypatch, tmp_path):
    import desktop_bridge
    import paths

    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    brain_dir = paths.brain_dir()
    brain_dir.mkdir(parents=True, exist_ok=True)
    (brain_dir / "calibration.json").write_text(json.dumps({}))

    identity = desktop_bridge._resolve_identity()

    assert identity["name"] == desktop_bridge.DEFAULT_ASSISTANT_NAME


def test_reserved_assistant_name_helper_blocks_product_variants():
    import desktop_bridge

    assert desktop_bridge._is_reserved_assistant_name("NEXO") is True
    assert desktop_bridge._is_reserved_assistant_name("nexo brain") is True
    assert desktop_bridge._is_reserved_assistant_name("NEXO-Desktop") is True
    assert desktop_bridge._is_reserved_assistant_name("Nova") is False
