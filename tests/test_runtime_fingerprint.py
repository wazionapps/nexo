"""Tests for the MCP runtime fingerprint introduced to gate restart markers.

Goal: a `nexo update` should only force MCP clients to restart when the
release actually altered a `.py` file the running server imports. Doc-only,
README-only and changelog-only releases must leave the marker untouched.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


# Ensure runtime_versioning is the repo copy and uses a sandboxed NEXO_HOME.
@pytest.fixture
def fingerprint_runtime(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir()
    (home / "operations").mkdir()
    monkeypatch.setenv("NEXO_HOME", str(home))
    sys.modules.pop("paths", None)
    sys.modules.pop("runtime_versioning", None)
    import paths
    import runtime_versioning
    importlib.reload(paths)
    importlib.reload(runtime_versioning)
    return runtime_versioning


def _make_runtime_tree(root: Path, *, version: str = "1.0.0") -> Path:
    """Create a synthetic runtime root that compute_mcp_runtime_fingerprint will accept."""
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "server.py").write_text("# entrypoint\n", encoding="utf-8")
    (src / "cli.py").write_text("# cli\n", encoding="utf-8")
    plugins = src / "plugins"
    plugins.mkdir(exist_ok=True)
    (plugins / "__init__.py").write_text("", encoding="utf-8")
    (plugins / "memory.py").write_text("def hello(): return 'v1'\n", encoding="utf-8")
    # Subdirs that MUST be excluded from the fingerprint
    for excluded in ("scripts", "tests", "migrations", "crons", "__pycache__"):
        (src / excluded).mkdir(exist_ok=True)
        (src / excluded / "noise.py").write_text(
            "# this file should NOT affect fingerprint\n", encoding="utf-8"
        )
    # Non-Python noise that should also never affect the fingerprint
    (src / "README.md").write_text("# docs\n", encoding="utf-8")
    (src / "manifest.json").write_text('{"version":"' + version + '"}\n', encoding="utf-8")
    (src / "version.json").write_text('{"version":"' + version + '"}\n', encoding="utf-8")
    return src


def test_fingerprint_is_deterministic(tmp_path, fingerprint_runtime):
    src = _make_runtime_tree(tmp_path)
    a = fingerprint_runtime.compute_mcp_runtime_fingerprint(src)
    b = fingerprint_runtime.compute_mcp_runtime_fingerprint(src)
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_fingerprint_changes_when_plugin_changes(tmp_path, fingerprint_runtime):
    src = _make_runtime_tree(tmp_path)
    before = fingerprint_runtime.compute_mcp_runtime_fingerprint(src)
    (src / "plugins" / "memory.py").write_text(
        "def hello(): return 'v2'\n", encoding="utf-8"
    )
    after = fingerprint_runtime.compute_mcp_runtime_fingerprint(src)
    assert before != after


def test_fingerprint_unchanged_when_only_docs_change(tmp_path, fingerprint_runtime):
    """Doc-only / README-only releases must produce the SAME fingerprint."""
    src = _make_runtime_tree(tmp_path)
    before = fingerprint_runtime.compute_mcp_runtime_fingerprint(src)
    (src / "README.md").write_text("# docs UPDATED\n", encoding="utf-8")
    (src / "manifest.json").write_text('{"version":"1.0.1"}\n', encoding="utf-8")
    # CHANGELOG-style file
    (src / "CHANGELOG.md").write_text("- 1.0.1: typo fix\n", encoding="utf-8")
    # Bumping version.json itself: version string is NOT what we hash, so
    # bumping it for a doc-only release must not invalidate the fingerprint.
    (src / "version.json").write_text('{"version":"1.0.1"}\n', encoding="utf-8")
    after = fingerprint_runtime.compute_mcp_runtime_fingerprint(src)
    assert before == after


def test_fingerprint_ignores_excluded_directories(tmp_path, fingerprint_runtime):
    src = _make_runtime_tree(tmp_path)
    before = fingerprint_runtime.compute_mcp_runtime_fingerprint(src)
    # Mutate every excluded directory; fingerprint must NOT shift.
    for excluded in ("scripts", "tests", "migrations", "crons", "__pycache__"):
        (src / excluded / "noise.py").write_text(
            "# noise after change\n", encoding="utf-8"
        )
    after = fingerprint_runtime.compute_mcp_runtime_fingerprint(src)
    assert before == after


def test_fingerprint_returns_empty_when_dir_missing(tmp_path, fingerprint_runtime):
    missing = tmp_path / "does-not-exist"
    assert fingerprint_runtime.compute_mcp_runtime_fingerprint(missing) == ""


def test_fingerprint_returns_empty_when_no_python_files(tmp_path, fingerprint_runtime):
    empty = tmp_path / "empty-runtime"
    empty.mkdir()
    (empty / "README.md").write_text("# docs only\n", encoding="utf-8")
    assert fingerprint_runtime.compute_mcp_runtime_fingerprint(empty) == ""


def test_resolve_restart_uses_fingerprint_match(monkeypatch, tmp_path, fingerprint_runtime):
    """When fingerprints agree, version mismatch alone must NOT force restart."""
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_version", lambda: "2.0.0"
    )
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_fingerprint", lambda: "abc123"
    )
    fingerprint_runtime.PROCESS_VERSION = "1.9.9"  # divergent on purpose
    fingerprint_runtime.PROCESS_FINGERPRINT = "abc123"
    state = fingerprint_runtime.resolve_restart_required()
    assert state["restart_required"] is False, state


def test_resolve_restart_uses_fingerprint_mismatch(monkeypatch, tmp_path, fingerprint_runtime):
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_version", lambda: "2.0.0"
    )
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_fingerprint", lambda: "newhash"
    )
    fingerprint_runtime.PROCESS_VERSION = "2.0.0"
    fingerprint_runtime.PROCESS_FINGERPRINT = "oldhash"
    state = fingerprint_runtime.resolve_restart_required()
    assert state["restart_required"] is True
    assert state["reason"] == "fingerprint_mismatch"


def test_resolve_restart_falls_back_to_version_when_fingerprint_missing(
    monkeypatch, tmp_path, fingerprint_runtime
):
    """When fingerprints can't be computed, fall back to legacy version check."""
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_version", lambda: "2.0.0"
    )
    monkeypatch.setattr(fingerprint_runtime, "installed_runtime_fingerprint", lambda: "")
    fingerprint_runtime.PROCESS_VERSION = "1.9.9"
    fingerprint_runtime.PROCESS_FINGERPRINT = ""
    state = fingerprint_runtime.resolve_restart_required()
    assert state["restart_required"] is True
    assert state["reason"] == "version_mismatch"


def test_resolve_restart_unknown_process_fingerprint_falls_back(
    monkeypatch, tmp_path, fingerprint_runtime
):
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_version", lambda: "2.0.0"
    )
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_fingerprint", lambda: "abc"
    )
    fingerprint_runtime.PROCESS_VERSION = "1.9.9"
    fingerprint_runtime.PROCESS_FINGERPRINT = "unknown"
    state = fingerprint_runtime.resolve_restart_required()
    # process_fp == "unknown" disables fingerprint comparison; falls back to
    # version check, which here is a mismatch.
    assert state["restart_required"] is True
    assert state["reason"] == "version_mismatch"


def test_marker_required_always_wins(monkeypatch, tmp_path, fingerprint_runtime):
    """Even when fingerprints match, an unack'd marker should still require restart."""
    fingerprint_runtime.write_restart_required_marker(
        from_version="1.0.0",
        to_version="1.0.1",
        from_fingerprint="abc",
        to_fingerprint="abc",
    )
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_version", lambda: "1.0.1"
    )
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_fingerprint", lambda: "abc"
    )
    fingerprint_runtime.PROCESS_VERSION = "1.0.1"
    fingerprint_runtime.PROCESS_FINGERPRINT = "abc"
    state = fingerprint_runtime.resolve_restart_required()
    assert state["restart_required"] is True
    assert state["reason"] == "marker_required"


def test_build_mcp_status_exposes_fingerprint_fields(
    monkeypatch, tmp_path, fingerprint_runtime
):
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_version", lambda: "1.0.0"
    )
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_fingerprint", lambda: "abc"
    )
    fingerprint_runtime.PROCESS_VERSION = "1.0.0"
    fingerprint_runtime.PROCESS_FINGERPRINT = "abc"
    status = fingerprint_runtime.build_mcp_status()
    assert "installed_fingerprint" in status
    assert "process_fingerprint" in status
    assert "fingerprint_match" in status
    assert status["fingerprint_match"] is True


def test_force_restart_flag_read_from_version_file(monkeypatch, tmp_path, fingerprint_runtime):
    """`force_restart: true` in version.json must be honored as opt-in."""
    src = _make_runtime_tree(tmp_path)
    (src / "version.json").write_text(
        '{"version":"1.0.1","force_restart":true}\n', encoding="utf-8"
    )
    monkeypatch.setattr(fingerprint_runtime, "active_runtime_root", lambda: src)
    monkeypatch.setattr(fingerprint_runtime.paths, "home", lambda: src)
    assert fingerprint_runtime.installed_force_restart_flag() is True


def test_force_restart_flag_default_false(tmp_path, fingerprint_runtime, monkeypatch):
    src = _make_runtime_tree(tmp_path)
    (src / "version.json").write_text('{"version":"1.0.1"}\n', encoding="utf-8")
    monkeypatch.setattr(fingerprint_runtime, "active_runtime_root", lambda: src)
    monkeypatch.setattr(fingerprint_runtime.paths, "home", lambda: src)
    assert fingerprint_runtime.installed_force_restart_flag() is False
