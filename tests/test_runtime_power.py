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


def test_describe_power_policy_macos_reports_best_effort(tmp_path, monkeypatch):
    import runtime_power

    helper = tmp_path / "caffeinate"
    helper.write_text("")
    monkeypatch.setattr(runtime_power, "MACOS_CAFFEINATE_PATH", helper)

    details = runtime_power.describe_power_policy("always_on", system="Darwin")

    assert details["helper"] == "caffeinate"
    assert details["helper_available"] is True
    assert details["closed_lid_behavior"] == "best_effort"
    assert "caffeinate" in details["prompt_note"]


def test_format_power_policy_label_mentions_best_effort_on_macos(tmp_path, monkeypatch):
    import runtime_power

    helper = tmp_path / "caffeinate"
    helper.write_text("")
    monkeypatch.setattr(runtime_power, "MACOS_CAFFEINATE_PATH", helper)

    label = runtime_power.format_power_policy_label("always_on", system="Darwin")

    assert "caffeinate" in label
    assert "best effort" in label


def test_prompt_for_power_policy_mentions_closed_lid_best_effort(monkeypatch):
    import runtime_power

    messages = []
    result = runtime_power.prompt_for_power_policy(
        system="Darwin",
        input_fn=lambda prompt: "later",
        output_fn=messages.append,
    )

    assert result == "unset"
    assert any("caffeinate" in message for message in messages)
    assert any("Closed-lid" in message for message in messages)


def test_apply_macos_power_policy_reports_missing_helper(tmp_path, monkeypatch):
    import runtime_power

    missing_helper = tmp_path / "missing-caffeinate"
    monkeypatch.setattr(runtime_power, "MACOS_CAFFEINATE_PATH", missing_helper)

    details = runtime_power.describe_power_policy("always_on", system="Darwin")
    result = runtime_power._apply_macos_power_policy("always_on", details=details)

    assert result["ok"] is False
    assert result["action"] == "missing-helper"
    assert "caffeinate" in result["message"]
