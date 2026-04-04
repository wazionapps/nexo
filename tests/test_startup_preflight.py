"""Tests for startup preflight orchestration."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_startup_preflight_defers_sync_update_when_runtime_busy(tmp_path, monkeypatch):
    import auto_update
    import runtime_power
    import db
    import script_registry

    nexo_home = tmp_path / "nexo"
    (nexo_home / "config").mkdir(parents=True)
    (nexo_home / "logs").mkdir(parents=True)
    (nexo_home / "config" / "schedule.json").write_text(json.dumps({
        "timezone": "UTC",
        "auto_update": True,
        "power_policy": "disabled",
        "processes": {},
    }))

    monkeypatch.setattr(auto_update, "NEXO_HOME", nexo_home)
    monkeypatch.setattr(auto_update, "UPDATE_SUMMARY_FILE", nexo_home / "logs" / "update-last-summary.json")
    monkeypatch.setattr(auto_update, "UPDATE_HISTORY_FILE", nexo_home / "logs" / "update-history.jsonl")
    monkeypatch.setattr(auto_update, "CHECK_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(auto_update, "_resolve_sync_source", lambda: (tmp_path / "repo" / "src", tmp_path / "repo"))
    monkeypatch.setattr(auto_update, "_run_db_migrations", lambda: True)
    monkeypatch.setattr(auto_update, "run_file_migrations", lambda: [])
    monkeypatch.setattr(auto_update, "_migrate_claude_md", lambda: None)
    monkeypatch.setattr(auto_update, "_sync_watchdog_hash_registry", lambda: None)
    monkeypatch.setattr(auto_update, "_warn_protected_runtime_location", lambda: None)
    monkeypatch.setattr(auto_update, "_ensure_runtime_cli_wrapper", lambda: None)
    monkeypatch.setattr(auto_update, "_ensure_runtime_cli_in_shell", lambda: None)
    monkeypatch.setattr(auto_update, "_runtime_busy_reason", lambda: "active sessions: 1")
    monkeypatch.setattr(auto_update, "_source_repo_status", lambda repo: {
        "is_git": True,
        "dirty": False,
        "diverged": False,
        "behind": True,
    })
    monkeypatch.setattr(auto_update, "_read_last_check", lambda: {})
    monkeypatch.setattr(auto_update, "_write_last_check", lambda data: None)
    monkeypatch.setattr(auto_update, "manual_sync_update", lambda **kwargs: {"ok": True, "updated": True, "actions": ["unexpected"]})
    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(script_registry, "sync_personal_scripts", lambda: {"ok": True})
    monkeypatch.setattr(runtime_power, "ensure_power_policy_choice", lambda **kwargs: {"policy": "disabled", "prompted": False})
    monkeypatch.setattr(runtime_power, "apply_power_policy", lambda policy=None: {"ok": True, "action": "disabled"})
    monkeypatch.setattr(runtime_power, "get_power_policy", lambda schedule=None: "disabled")

    result = auto_update.startup_preflight(entrypoint="chat", interactive=False)

    assert result["deferred_reason"] == "active sessions: 1"
    assert result["updated"] is False
