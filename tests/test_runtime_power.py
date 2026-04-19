"""Tests for runtime power policy helpers."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def test_ensure_power_policy_choice_prompts_once(tmp_path, monkeypatch):
    import runtime_power

    nexo_home = tmp_path / "nexo"
    schedule_file = nexo_home / "config" / "schedule.json"
    schedule_file.parent.mkdir(parents=True)
    schedule_file.write_text('{"timezone":"UTC","auto_update":true,"processes":{}}')

    monkeypatch.setenv("NEXO_HOME", str(nexo_home))


    monkeypatch.setenv("NEXO_HOME", str(nexo_home))


    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    monkeypatch.setenv("NEXO_HOME", str(nexo_home))


    monkeypatch.setenv("NEXO_HOME", str(nexo_home))


    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
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

    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
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


def test_detect_full_disk_access_reasons_flags_protected_runtime(tmp_path, monkeypatch):
    import runtime_power

    nexo_home = tmp_path / "Documents" / "nexo"
    nexo_home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setattr(runtime_power, "NEXO_HOME", nexo_home)
    monkeypatch.setattr(runtime_power.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(runtime_power, "_protected_macos_roots", lambda home=None: (tmp_path / "Documents",))

    reasons = runtime_power.detect_full_disk_access_reasons(system="Darwin")

    assert reasons
    assert "protected macOS folder" in reasons[0]


def test_ensure_full_disk_access_choice_prompts_and_marks_granted(tmp_path, monkeypatch):
    import runtime_power

    nexo_home = tmp_path / "nexo"
    schedule_file = nexo_home / "config" / "schedule.json"
    schedule_file.parent.mkdir(parents=True)
    schedule_file.write_text('{"timezone":"UTC","auto_update":true,"processes":{}}')

    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setattr(runtime_power, "NEXO_HOME", nexo_home)
    monkeypatch.setattr(runtime_power, "CONFIG_DIR", nexo_home / "config")
    monkeypatch.setattr(runtime_power, "SCHEDULE_FILE", schedule_file)
    monkeypatch.setattr(runtime_power.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(runtime_power, "detect_full_disk_access_reasons", lambda **kwargs: ["NEXO_HOME is inside a protected macOS folder"])

    prompts = []
    answers = iter(["y", ""])

    def fake_input(prompt):
        prompts.append(prompt)
        return next(answers)

    result = runtime_power.ensure_full_disk_access_choice(
        interactive=True,
        reason="update",
        input_fn=fake_input,
        output_fn=lambda msg: None,
        open_fn=lambda: {"ok": True, "opened": True, "message": ""},
        probe_fn=lambda: {"checked": True, "granted": True, "probe_path": "/tmp/probe", "message": ""},
    )

    assert prompts
    assert result["status"] == "granted"
    assert result["verified"] is True
    assert runtime_power.load_schedule_config()["full_disk_access_status"] == "granted"


def test_ensure_full_disk_access_choice_defers_when_grant_cannot_be_verified(tmp_path, monkeypatch):
    import runtime_power

    nexo_home = tmp_path / "nexo"
    schedule_file = nexo_home / "config" / "schedule.json"
    schedule_file.parent.mkdir(parents=True)
    schedule_file.write_text('{"timezone":"UTC","auto_update":true,"full_disk_access_status":"granted","processes":{}}')

    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setattr(runtime_power, "NEXO_HOME", nexo_home)
    monkeypatch.setattr(runtime_power, "CONFIG_DIR", nexo_home / "config")
    monkeypatch.setattr(runtime_power, "SCHEDULE_FILE", schedule_file)
    monkeypatch.setattr(runtime_power.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(runtime_power, "detect_full_disk_access_reasons", lambda **kwargs: ["Recent background job stderr hit 'Operation not permitted'"])

    result = runtime_power.ensure_full_disk_access_choice(
        interactive=False,
        reason="update",
        probe_fn=lambda: {"checked": True, "granted": False, "probe_path": "/tmp/probe", "message": "denied"},
    )

    assert result["status"] == "later"
    assert runtime_power.load_schedule_config()["full_disk_access_status"] == "later"
