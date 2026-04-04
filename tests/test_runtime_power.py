"""Tests for runtime power policy helpers."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_ensure_power_policy_choice_prompts_once(tmp_path, monkeypatch):
    import runtime_power

    nexo_home = tmp_path / "nexo"
    schedule_file = nexo_home / "config" / "schedule.json"
    schedule_file.parent.mkdir(parents=True)
    schedule_file.write_text('{"timezone":"UTC","auto_update":true,"processes":{}}')

    monkeypatch.setattr(runtime_power, "NEXO_HOME", nexo_home)
    monkeypatch.setattr(runtime_power, "CONFIG_DIR", nexo_home / "config")
    monkeypatch.setattr(runtime_power, "SCHEDULE_FILE", schedule_file)

    asked = []
    result = runtime_power.ensure_power_policy_choice(
        interactive=True,
        reason="update",
        input_fn=lambda prompt: asked.append(prompt) or "y",
        output_fn=lambda msg: None,
    )

    assert asked
    assert result["policy"] == "always_on"
    assert runtime_power.load_schedule_config()["power_policy"] == "always_on"


def test_ensure_power_policy_choice_skips_when_noninteractive(tmp_path, monkeypatch):
    import runtime_power

    nexo_home = tmp_path / "nexo"
    schedule_file = nexo_home / "config" / "schedule.json"
    schedule_file.parent.mkdir(parents=True)
    schedule_file.write_text('{"timezone":"UTC","auto_update":true,"processes":{}}')

    monkeypatch.setattr(runtime_power, "NEXO_HOME", nexo_home)
    monkeypatch.setattr(runtime_power, "CONFIG_DIR", nexo_home / "config")
    monkeypatch.setattr(runtime_power, "SCHEDULE_FILE", schedule_file)

    result = runtime_power.ensure_power_policy_choice(interactive=False)

    assert result["policy"] == "unset"
    assert result["prompted"] is False
