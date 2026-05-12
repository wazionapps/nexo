"""Tests for runtime power policy helpers."""
import os
import plistlib
import sys
import types

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


def test_resolve_launchagent_path_includes_managed_claude_bin(tmp_path, monkeypatch):
    import runtime_power

    home = tmp_path / "home"
    managed_bin = home / ".nexo" / "runtime" / "bootstrap" / "npm-global" / "bin"
    managed_bin.mkdir(parents=True)

    monkeypatch.setenv("HOME", str(home))

    path_parts = runtime_power.resolve_launchagent_path().split(":")

    assert path_parts[0] == str(managed_bin)


def test_reload_launchagent_treats_already_loaded_from_same_plist_as_success(tmp_path, monkeypatch):
    import runtime_power

    plist_path = tmp_path / "com.nexo.catchup.plist"
    with plist_path.open("wb") as fh:
        plistlib.dump({"Label": "com.nexo.catchup"}, fh)

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[1] == "print":
            return types.SimpleNamespace(
                returncode=0,
                stdout=f"gui/501/com.nexo.catchup = {{\n\tpath = {plist_path}\n}}\n",
                stderr="",
            )
        if args[1] == "bootstrap":
            return types.SimpleNamespace(returncode=5, stdout="", stderr="Bootstrap failed: 5: Input/output error")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runtime_power.os, "getuid", lambda: 501)
    monkeypatch.setattr(runtime_power.subprocess, "run", fake_run)
    monkeypatch.setattr(runtime_power.time, "sleep", lambda _seconds: None)

    result = runtime_power.reload_launchagent_plist(plist_path)

    assert result["ok"] is True
    assert result["action"] == "already-loaded"
    assert ["launchctl", "bootout", "gui/501/com.nexo.catchup"] in calls
    assert ["launchctl", "bootstrap", "gui/501", str(plist_path)] in calls


def test_reload_launchagent_reports_error_when_bootstrap_and_legacy_load_fail(tmp_path, monkeypatch):
    import runtime_power

    plist_path = tmp_path / "com.nexo.catchup.plist"
    with plist_path.open("wb") as fh:
        plistlib.dump({"Label": "com.nexo.catchup"}, fh)

    def fake_run(args, **kwargs):
        if args[1] == "print":
            return types.SimpleNamespace(returncode=1, stdout="", stderr='Could not find service "com.nexo.catchup"')
        if args[1] == "bootstrap":
            return types.SimpleNamespace(returncode=5, stdout="", stderr="Bootstrap failed: 5")
        if args[1] == "load":
            return types.SimpleNamespace(returncode=1, stdout="", stderr="legacy load denied")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runtime_power.os, "getuid", lambda: 501)
    monkeypatch.setattr(runtime_power.subprocess, "run", fake_run)

    result = runtime_power.reload_launchagent_plist(plist_path)

    assert result["ok"] is False
    assert result["error"] == "legacy load denied"
    assert result["bootstrap_error"] == "Bootstrap failed: 5"


def test_reload_launchagent_skips_launchctl_when_home_is_ephemeral(tmp_path, monkeypatch):
    import runtime_power

    home = tmp_path / "pytest-of-user" / "pytest-1" / "test_case0"
    nexo_home = home / "nexo"
    plist_path = home / "Library" / "LaunchAgents" / "com.nexo.watchdog.plist"
    plist_path.parent.mkdir(parents=True)
    with plist_path.open("wb") as fh:
        plistlib.dump({"Label": "com.nexo.watchdog"}, fh)

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        raise AssertionError("launchctl must not be called for pytest temp homes")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.delenv("NEXO_ALLOW_EPHEMERAL_INSTALL", raising=False)
    monkeypatch.setattr(runtime_power.subprocess, "run", fake_run)

    result = runtime_power.reload_launchagent_plist(plist_path)

    assert result["ok"] is True
    assert result["action"] == "skipped-ephemeral-runtime"
    assert calls == []


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


def test_apply_macos_power_policy_uses_manifest_sync(tmp_path, monkeypatch):
    import runtime_power

    helper = tmp_path / "caffeinate"
    helper.write_text("")
    plist_path = tmp_path / "LaunchAgents" / "com.nexo.prevent-sleep.plist"
    plist_path.parent.mkdir(parents=True)

    monkeypatch.setattr(runtime_power, "MACOS_CAFFEINATE_PATH", helper)
    monkeypatch.setattr(runtime_power, "LAUNCH_AGENTS_DIR", plist_path.parent)
    monkeypatch.setattr(runtime_power, "_sync_core_crons_for_power_policy", lambda: plist_path.write_text("<plist/>"))

    details = runtime_power.describe_power_policy("always_on", system="Darwin")
    result = runtime_power._apply_macos_power_policy("always_on", details=details)

    assert result["ok"] is True
    assert result["action"] == "enabled"
    assert result["plist_path"] == str(plist_path)


def test_apply_linux_power_policy_uses_manifest_sync(tmp_path, monkeypatch):
    import runtime_power

    helper = tmp_path / "systemd-inhibit"
    helper.write_text("")
    helper.chmod(0o755)
    service_path = tmp_path / ".config" / "systemd" / "user" / "nexo-prevent-sleep.service"
    service_path.parent.mkdir(parents=True)

    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")
    monkeypatch.setattr(runtime_power, "LINUX_SYSTEMD_USER_DIR", service_path.parent)
    monkeypatch.setattr(runtime_power, "_sync_core_crons_for_power_policy", lambda: service_path.write_text("[Service]\n"))

    details = runtime_power.describe_power_policy("always_on", system="Linux")
    result = runtime_power._apply_linux_power_policy("always_on", details=details)

    assert result["ok"] is True
    assert result["action"] == "enabled"
    assert result["service_path"] == str(service_path)


def test_full_disk_access_verified_clears_required_state(tmp_path, monkeypatch):
    import runtime_power

    nexo_home = tmp_path / "nexo"
    schedule_file = nexo_home / "config" / "schedule.json"
    state_file = nexo_home / "runtime" / "state" / "full-disk-access-required.json"
    schedule_file.parent.mkdir(parents=True)
    state_file.parent.mkdir(parents=True)
    schedule_file.write_text('{"full_disk_access_status":"granted"}')
    state_file.write_text('{"status":"later"}')

    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setattr(runtime_power, "NEXO_HOME", nexo_home)
    monkeypatch.setattr(runtime_power, "CONFIG_DIR", nexo_home / "config")
    monkeypatch.setattr(runtime_power, "SCHEDULE_FILE", schedule_file)
    monkeypatch.setattr(runtime_power, "FULL_DISK_ACCESS_STATE_FILE", state_file)
    monkeypatch.setattr(runtime_power.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        runtime_power,
        "detect_full_disk_access_reasons",
        lambda **_kwargs: ["Recent background job hit a macOS privacy denial"],
    )

    result = runtime_power.ensure_full_disk_access_choice(
        interactive=False,
        probe_fn=lambda: {"granted": True, "probe_path": "/Users/tester/Library/Application Support/com.apple.TCC/TCC.db"},
    )

    assert result["status"] == "granted"
    assert result["verified"] is True
    assert result["relevant"] is False
    assert result["message"] == ""
    assert state_file.exists() is False
    assert runtime_power.load_schedule_config()["full_disk_access_reasons"] == []


def test_sync_core_crons_for_power_policy_is_noop_when_sync_surface_missing(monkeypatch):
    import runtime_power

    fake_crons = types.ModuleType("crons")
    monkeypatch.setitem(sys.modules, "crons", fake_crons)

    runtime_power._sync_core_crons_for_power_policy()


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


def test_detect_full_disk_access_reasons_reads_tcc_authorization_denied_log(tmp_path, monkeypatch):
    import runtime_power

    nexo_home = tmp_path / "nexo"
    logs_dir = nexo_home / "runtime" / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "tcc-auto-approve.log").write_text(
        'Error: unable to open database "/Users/tester/Library/Application Support/com.apple.TCC/TCC.db": authorization denied\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setattr(runtime_power, "NEXO_HOME", nexo_home)
    monkeypatch.setattr(runtime_power.paths, "logs_dir", lambda: logs_dir)
    monkeypatch.setattr(runtime_power.platform, "system", lambda: "Darwin")

    reasons = runtime_power.detect_full_disk_access_reasons(system="Darwin")

    assert reasons == ["Recent background job hit a macOS privacy denial (tcc-auto-approve.log)"]


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
