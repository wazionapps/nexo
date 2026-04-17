from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


# ── _update_external_clis ───────────────────────────────────────────


def _stub_helpers(monkeypatch, *, global_versions, registry_versions, valid=True):
    """Patch the plugins.update helpers used by _update_external_clis.

    global_versions: dict[pkg, old_version | None]  (None = not installed)
    registry_versions: dict[pkg, latest | None]
    """
    import plugins.update as plugin_update

    def fake_global(pkg):
        return global_versions.get(pkg)

    def fake_registry(pkg):
        return registry_versions.get(pkg)

    monkeypatch.setattr(plugin_update, "_get_npm_global_version", fake_global)
    monkeypatch.setattr(plugin_update, "_get_npm_registry_version", fake_registry)
    monkeypatch.setattr(plugin_update, "_validate_npm_name", lambda name: bool(valid))


def test_update_external_clis_skips_when_not_installed(monkeypatch):
    import auto_update

    _stub_helpers(
        monkeypatch,
        global_versions={"@anthropic-ai/claude-code": None, "@openai/codex": None},
        registry_versions={"@anthropic-ai/claude-code": "2.1.115", "@openai/codex": "1.0.0"},
    )

    run_called = []

    def fake_run(*args, **kwargs):  # pragma: no cover — must not be called
        run_called.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(auto_update.subprocess, "run", fake_run)

    results = auto_update._update_external_clis()

    assert run_called == [], "npm install must not run when package isn't installed"
    assert results["@anthropic-ai/claude-code"]["status"] == "not_installed"
    assert results["@openai/codex"]["status"] == "not_installed"
    assert results["@anthropic-ai/claude-code"]["updated"] is False


def test_update_external_clis_already_latest(monkeypatch):
    import auto_update

    _stub_helpers(
        monkeypatch,
        global_versions={"@anthropic-ai/claude-code": "2.1.115", "@openai/codex": "1.0.0"},
        registry_versions={"@anthropic-ai/claude-code": "2.1.115", "@openai/codex": "1.0.0"},
    )
    monkeypatch.setattr(
        auto_update.subprocess, "run",
        mock.Mock(side_effect=AssertionError("npm install must not run when already latest")),
    )

    results = auto_update._update_external_clis()

    for pkg in ("@anthropic-ai/claude-code", "@openai/codex"):
        assert results[pkg]["status"] == "already_latest"
        assert results[pkg]["updated"] is False
        assert results[pkg]["old"] == results[pkg]["new"]


def test_update_external_clis_successful_update(monkeypatch):
    import auto_update

    # After the install, _get_npm_global_version reports the new version.
    state = {"@anthropic-ai/claude-code": "2.1.109", "@openai/codex": "1.0.0"}

    def fake_global(pkg):
        return state.get(pkg)

    def fake_registry(pkg):
        return {"@anthropic-ai/claude-code": "2.1.115", "@openai/codex": "1.0.0"}.get(pkg)

    import plugins.update as plugin_update
    monkeypatch.setattr(plugin_update, "_get_npm_global_version", fake_global)
    monkeypatch.setattr(plugin_update, "_get_npm_registry_version", fake_registry)
    monkeypatch.setattr(plugin_update, "_validate_npm_name", lambda name: True)

    run_calls = []

    def fake_run(cmd, capture_output, text, timeout):
        run_calls.append(cmd)
        pkg_arg = cmd[-1]  # "<pkg>@latest"
        pkg = pkg_arg.rsplit("@", 1)[0]
        state[pkg] = "2.1.115" if pkg == "@anthropic-ai/claude-code" else "1.0.0"
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(auto_update.subprocess, "run", fake_run)

    progress_msgs = []
    results = auto_update._update_external_clis(progress_fn=progress_msgs.append)

    # Only the claude-code install is actually a bump — codex was already latest.
    assert run_calls == [["npm", "install", "-g", "@anthropic-ai/claude-code@latest"]]
    cc = results["@anthropic-ai/claude-code"]
    assert cc["status"] == "updated"
    assert cc["updated"] is True
    assert cc["old"] == "2.1.109"
    assert cc["new"] == "2.1.115"
    assert any("Updating @anthropic-ai/claude-code" in m for m in progress_msgs)

    codex = results["@openai/codex"]
    assert codex["status"] == "already_latest"
    assert codex["updated"] is False


def test_update_external_clis_handles_install_failure(monkeypatch):
    import auto_update

    _stub_helpers(
        monkeypatch,
        global_versions={"@anthropic-ai/claude-code": "2.1.109", "@openai/codex": "1.0.0"},
        registry_versions={"@anthropic-ai/claude-code": "2.1.115", "@openai/codex": "1.0.0"},
    )

    def fake_run(cmd, capture_output, text, timeout):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="EACCES: permission denied",
        )

    monkeypatch.setattr(auto_update.subprocess, "run", fake_run)

    results = auto_update._update_external_clis()

    cc = results["@anthropic-ai/claude-code"]
    assert cc["status"] == "failed"
    assert cc["updated"] is False
    assert cc["old"] == "2.1.109"
    assert cc["new"] == "2.1.109"
    assert "EACCES" in cc["error"]
    # Codex was already latest, so the failure of claude-code must not propagate.
    assert results["@openai/codex"]["status"] == "already_latest"


def test_update_external_clis_handles_timeout_explicitly(monkeypatch):
    """Learning #294: TimeoutExpired must be captured explicitly, not bubble up."""
    import auto_update

    _stub_helpers(
        monkeypatch,
        global_versions={"@anthropic-ai/claude-code": "2.1.109", "@openai/codex": "0.9.0"},
        registry_versions={"@anthropic-ai/claude-code": "2.1.115", "@openai/codex": "1.0.0"},
    )

    def fake_run(cmd, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(auto_update.subprocess, "run", fake_run)

    results = auto_update._update_external_clis()

    cc = results["@anthropic-ai/claude-code"]
    assert cc["status"] == "failed"
    assert "timed out" in cc["error"].lower()
    # The next package should still be attempted (not short-circuited).
    codex = results["@openai/codex"]
    assert codex["status"] == "failed"
    assert "timed out" in codex["error"].lower()


def test_update_external_clis_skips_when_npm_missing(monkeypatch):
    """FileNotFoundError (npm not on PATH) marks every CLI as skipped."""
    import auto_update

    _stub_helpers(
        monkeypatch,
        global_versions={"@anthropic-ai/claude-code": "2.1.109", "@openai/codex": "0.9.0"},
        registry_versions={"@anthropic-ai/claude-code": "2.1.115", "@openai/codex": "1.0.0"},
    )

    def fake_run(cmd, capture_output, text, timeout):
        raise FileNotFoundError(2, "No such file or directory: 'npm'")

    monkeypatch.setattr(auto_update.subprocess, "run", fake_run)

    results = auto_update._update_external_clis()

    for pkg in ("@anthropic-ai/claude-code", "@openai/codex"):
        assert results[pkg]["status"] == "skipped"
        assert "npm not found" in results[pkg]["error"]


# ── _format_external_clis_results ───────────────────────────────────


def test_format_results_reports_updates_and_restart_hint():
    import auto_update

    lines = auto_update._format_external_clis_results({
        "@anthropic-ai/claude-code": {
            "old": "2.1.109", "new": "2.1.115", "updated": True, "status": "updated",
        },
        "@openai/codex": {
            "old": "1.0.0", "new": "1.0.0", "updated": False, "status": "already_latest",
        },
    })

    assert any(
        "CLI updated: @anthropic-ai/claude-code 2.1.109 -> 2.1.115" in line
        for line in lines
    )
    assert any("reinicia terminal" in line for line in lines)
    # When at least one CLI updated, we don't emit the "already in latest" line —
    # it would be misleading alongside the updated one.
    assert not any("ya en última versión" in line for line in lines)


def test_format_results_all_latest_emits_info_line():
    import auto_update

    lines = auto_update._format_external_clis_results({
        "@anthropic-ai/claude-code": {
            "old": "2.1.115", "new": "2.1.115", "updated": False, "status": "already_latest",
        },
        "@openai/codex": {
            "old": "1.0.0", "new": "1.0.0", "updated": False, "status": "already_latest",
        },
    })

    assert lines == ["  CLIs externos: ya en última versión"]


def test_format_results_silent_when_nothing_installed():
    import auto_update

    lines = auto_update._format_external_clis_results({
        "@anthropic-ai/claude-code": {
            "old": None, "new": None, "updated": False, "status": "not_installed",
        },
        "@openai/codex": {
            "old": None, "new": None, "updated": False, "status": "not_installed",
        },
    })

    assert lines == [], "Uninstalled third-party CLIs must not spam the summary"


def test_format_results_reports_failure_warning():
    import auto_update

    lines = auto_update._format_external_clis_results({
        "@anthropic-ai/claude-code": {
            "old": "2.1.109", "new": "2.1.109", "updated": False,
            "status": "failed", "error": "EACCES",
        },
    })

    assert any(
        "WARNING: CLI @anthropic-ai/claude-code update failed: EACCES" in line
        for line in lines
    )


# ── include_clis=False short-circuit ────────────────────────────────


def test_manual_sync_update_honors_include_clis_false(monkeypatch, tmp_path):
    """Passing include_clis=False must skip the external CLI check entirely."""
    import auto_update

    # Stub out the heavy sync machinery — we only care about the CLI branch.
    monkeypatch.setattr(
        auto_update, "_resolve_sync_source",
        lambda: (tmp_path / "src", tmp_path / "repo"),
    )
    monkeypatch.setattr(auto_update, "_source_repo_status", lambda repo: {"is_git": False})
    monkeypatch.setattr(auto_update, "_create_validated_db_backup", lambda: (None, None))
    monkeypatch.setattr(auto_update, "_backup_runtime_tree", lambda dest=None: str(tmp_path / "bak"))
    monkeypatch.setattr(
        auto_update, "_copy_runtime_from_source",
        lambda src, repo, dest, progress_fn=None: {
            "packages": 0, "files": 0, "scripts": 0,
            "source": str(src), "repo": str(repo), "script_conflicts": [],
        },
    )
    monkeypatch.setattr(
        auto_update, "_run_runtime_post_sync",
        lambda dest=None, progress_fn=None: (True, []),
    )
    monkeypatch.setattr(auto_update, "_write_update_summary", lambda summary: None)

    def explode(*a, **kw):  # pragma: no cover
        raise AssertionError("_update_external_clis must not be called when include_clis=False")

    monkeypatch.setattr(auto_update, "_update_external_clis", explode)

    result = auto_update.manual_sync_update(
        interactive=False, allow_source_pull=False, include_clis=False,
    )

    assert result["ok"] is True
    assert "external_clis" not in result
