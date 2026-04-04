"""Tests for public Draft PR contribution preferences and lifecycle."""

from __future__ import annotations

import json
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[1] / "src") not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _patch_schedule_paths(monkeypatch, nexo_home: Path):
    import runtime_power
    import public_contribution

    schedule_file = nexo_home / "config" / "schedule.json"
    schedule_file.parent.mkdir(parents=True, exist_ok=True)
    schedule_file.write_text('{"timezone":"UTC","auto_update":true,"processes":{}}')

    monkeypatch.setattr(runtime_power, "NEXO_HOME", nexo_home)
    monkeypatch.setattr(runtime_power, "CONFIG_DIR", nexo_home / "config")
    monkeypatch.setattr(runtime_power, "SCHEDULE_FILE", schedule_file)

    monkeypatch.setattr(public_contribution, "NEXO_HOME", nexo_home)
    monkeypatch.setattr(public_contribution, "CONTRIB_ROOT", nexo_home / "contrib" / "public-core")
    monkeypatch.setattr(public_contribution, "CONTRIB_REPO_DIR", nexo_home / "contrib" / "public-core" / "repo")
    monkeypatch.setattr(public_contribution, "CONTRIB_WORKTREES_DIR", nexo_home / "contrib" / "public-core" / "worktrees")
    monkeypatch.setattr(public_contribution, "CONTRIB_ARTIFACTS_DIR", nexo_home / "operations" / "public-contrib")
    return schedule_file


def test_ensure_public_contribution_choice_persists_enabled_state(tmp_path, monkeypatch):
    import public_contribution

    nexo_home = tmp_path / "nexo"
    schedule_file = _patch_schedule_paths(monkeypatch, nexo_home)
    monkeypatch.setattr(public_contribution, "github_auth_status", lambda: {"ok": True, "message": "", "login": "alice"})
    monkeypatch.setattr(public_contribution, "ensure_fork", lambda login: {"ok": True, "message": "", "fork_repo": f"{login}/nexo"})

    result = public_contribution.ensure_public_contribution_choice(
        interactive=True,
        reason="update",
        input_fn=lambda prompt: "y",
        output_fn=lambda message: None,
    )

    assert result["mode"] == "draft_prs"
    assert result["status"] == "active"
    saved = json.loads(schedule_file.read_text())
    assert saved["public_contribution"]["enabled"] is True
    assert saved["public_contribution"]["fork_repo"] == "alice/nexo"


def test_refresh_public_contribution_state_pauses_when_draft_pr_is_open(tmp_path, monkeypatch):
    import public_contribution

    nexo_home = tmp_path / "nexo"
    _patch_schedule_paths(monkeypatch, nexo_home)
    config = public_contribution.normalize_public_contribution_config({
        "enabled": True,
        "mode": "draft_prs",
        "status": "active",
        "github_user": "alice",
        "fork_repo": "alice/nexo",
        "active_pr_url": "https://github.com/wazionapps/nexo/pull/123",
        "active_pr_number": 123,
    })
    public_contribution.save_public_contribution_config(config)

    class Result:
        returncode = 0
        stdout = json.dumps({"state": "OPEN", "isDraft": True, "url": config["active_pr_url"]})
        stderr = ""

    monkeypatch.setattr(public_contribution, "_gh", lambda *args, **kwargs: Result())

    refreshed = public_contribution.refresh_public_contribution_state()
    assert refreshed["status"] == "paused_open_pr"
    assert refreshed["active_pr_number"] == 123


def test_refresh_public_contribution_state_enters_cooldown_after_merge(tmp_path, monkeypatch):
    import public_contribution

    nexo_home = tmp_path / "nexo"
    _patch_schedule_paths(monkeypatch, nexo_home)
    config = public_contribution.normalize_public_contribution_config({
        "enabled": True,
        "mode": "draft_prs",
        "status": "paused_open_pr",
        "github_user": "alice",
        "fork_repo": "alice/nexo",
        "active_pr_url": "https://github.com/wazionapps/nexo/pull/456",
        "active_pr_number": 456,
    })
    public_contribution.save_public_contribution_config(config)

    class Result:
        returncode = 0
        stdout = json.dumps({"state": "CLOSED", "isDraft": False, "url": config["active_pr_url"], "mergedAt": "2026-04-04T10:00:00Z"})
        stderr = ""

    monkeypatch.setattr(public_contribution, "_gh", lambda *args, **kwargs: Result())

    refreshed = public_contribution.refresh_public_contribution_state()
    assert refreshed["status"] == "cooldown"
    assert refreshed["active_pr_url"] == ""
    assert refreshed["active_pr_number"] is None
    assert refreshed["cooldown_until"]


def test_can_run_public_contribution_reports_pending_auth(tmp_path, monkeypatch):
    import public_contribution

    nexo_home = tmp_path / "nexo"
    _patch_schedule_paths(monkeypatch, nexo_home)
    config = public_contribution.normalize_public_contribution_config({
        "enabled": False,
        "mode": "pending_auth",
        "status": "pending_auth",
    })
    public_contribution.save_public_contribution_config(config)

    ready, reason, refreshed = public_contribution.can_run_public_contribution()
    assert ready is False
    assert "pending" in reason
    assert refreshed["status"] == "pending_auth"
