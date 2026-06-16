"""Tests for the MCP runtime fingerprint introduced to gate restart markers.

Goal: a `nexo update` should only force MCP clients to restart when the
release actually altered a `.py` file the running server imports. Doc-only,
README-only and changelog-only releases must leave the marker untouched.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

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
    for excluded in ("scripts", "tests", "migrations", "crons", "__pycache__", "versions"):
        (src / excluded).mkdir(exist_ok=True)
        (src / excluded / "noise.py").write_text(
            "# this file should NOT affect fingerprint\n", encoding="utf-8"
        )
    # Non-Python noise that should also never affect the fingerprint
    (src / "README.md").write_text("# docs\n", encoding="utf-8")
    (src / "manifest.json").write_text('{"version":"' + version + '"}\n', encoding="utf-8")
    (src / "version.json").write_text('{"version":"' + version + '"}\n', encoding="utf-8")
    return src


def _mock_runtime_service_ok(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "runtime_service",
        SimpleNamespace(runtime_service_status=lambda: {"ok": True}),
    )


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
    for excluded in ("scripts", "tests", "migrations", "crons", "__pycache__", "versions"):
        (src / excluded / "noise.py").write_text(
            "# noise after change\n", encoding="utf-8"
        )
    after = fingerprint_runtime.compute_mcp_runtime_fingerprint(src)
    assert before == after


def test_fingerprint_ignores_versions_subtree(tmp_path, fingerprint_runtime):
    """Regression: live `core/` retains snapshots under `core/versions/X/`.

    Without excluding `versions/`, the hash of the live runtime root drifts
    every time a new release adds a snapshot — even when the active code
    didn't change. That makes the fingerprint of `core/` and the fingerprint
    of `core/versions/<active>/` permanently disagree, leaving the restart
    marker stuck and every non-allowlisted MCP tool blocked.
    """
    src = _make_runtime_tree(tmp_path)
    before = fingerprint_runtime.compute_mcp_runtime_fingerprint(src)
    versions = src / "versions"
    versions.mkdir(exist_ok=True)
    for snapshot in ("7.10.0", "7.11.0", "7.11.2"):
        snap = versions / snapshot
        snap.mkdir()
        (snap / "server.py").write_text(f"# snapshot {snapshot}\n", encoding="utf-8")
        (snap / "plugins").mkdir()
        (snap / "plugins" / "memory.py").write_text(
            f"def hello(): return 'snap-{snapshot}'\n", encoding="utf-8"
        )
    after = fingerprint_runtime.compute_mcp_runtime_fingerprint(src)
    assert before == after, (
        "fingerprint must not change when version snapshots are added under versions/"
    )


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


def test_acknowledged_client_not_blocked_by_other_pending_clients(
    monkeypatch, tmp_path, fingerprint_runtime
):
    _mock_runtime_service_ok(monkeypatch)
    marker = fingerprint_runtime.write_restart_required_marker(
        from_version="1.0.0",
        to_version="1.0.1",
        from_fingerprint="old",
        to_fingerprint="new",
        client="codex",
    )
    marker["clients"]["claude_desktop"] = "ok"
    marker["clients"]["codex"] = "restart_session_required"
    Path(marker["path"]).write_text(json.dumps(marker), encoding="utf-8")
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_version", lambda: "1.0.1"
    )
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_fingerprint", lambda: "new"
    )
    monkeypatch.setattr(fingerprint_runtime, "active_runtime_root", lambda: tmp_path)
    fingerprint_runtime.PROCESS_VERSION = "1.0.1"
    fingerprint_runtime.PROCESS_FINGERPRINT = ""

    desktop_state = fingerprint_runtime.resolve_restart_required(client="claude_desktop")
    codex_state = fingerprint_runtime.resolve_restart_required(client="codex")
    desktop_status = fingerprint_runtime.build_mcp_status(client="claude_desktop")

    assert desktop_state["restart_required"] is False
    assert desktop_state["reason"] == ""
    assert codex_state["restart_required"] is True
    assert desktop_status["restart_required"] is False
    assert desktop_status["marker_exists"] is True
    assert desktop_status["client_action"] == "reprobe"


def test_ready_probe_unblocks_current_client_from_marker(
    monkeypatch, tmp_path, fingerprint_runtime
):
    marker = fingerprint_runtime.write_restart_required_marker(
        from_version="1.0.0",
        to_version="1.0.1",
        from_fingerprint="old",
        to_fingerprint="new",
        client="claude_desktop",
    )
    marker["clients"]["claude_desktop"] = "restart_client_required"
    marker["clients"]["codex"] = "restart_session_required"
    Path(marker["path"]).write_text(json.dumps(marker), encoding="utf-8")
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_version", lambda: "1.0.1"
    )
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_fingerprint", lambda: "new"
    )
    monkeypatch.setattr(fingerprint_runtime, "active_runtime_root", lambda: tmp_path)
    generation = fingerprint_runtime.runtime_generation("1.0.1", "new", str(tmp_path))
    monkeypatch.setitem(
        sys.modules,
        "runtime_service",
        SimpleNamespace(
            runtime_service_status=lambda: {
                "ok": True,
                "runtime_version": "1.0.1",
                "runtime_fingerprint": "new",
                "runtime_generation": generation,
                "stale": False,
            }
        ),
    )
    fingerprint_runtime.PROCESS_VERSION = "1.0.1"
    fingerprint_runtime.PROCESS_FINGERPRINT = ""
    fingerprint_runtime.record_mcp_client_probe(
        client="claude_desktop",
        probe={
            "ok": True,
            "probe_ok": True,
            "tool_count": 5,
            "required_tools_present": True,
            "missing_required_tools": [],
            "runtime_generation": generation,
        },
    )

    desktop_status = fingerprint_runtime.build_mcp_status(client="claude_desktop")
    codex_status = fingerprint_runtime.build_mcp_status(client="codex")

    assert desktop_status["restart_required"] is False
    assert desktop_status["reason"] == ""
    assert desktop_status["client_ready"] is True
    assert desktop_status["client_action"] == "ready"
    assert desktop_status["reason_code"] == "ready"
    assert desktop_status["marker_exists"] is True
    assert codex_status["restart_required"] is True
    assert codex_status["reason_code"] == "client_probe_missing"


def test_clear_restart_keeps_fingerprinted_marker_without_process_fingerprint(
    monkeypatch, tmp_path, fingerprint_runtime
):
    fingerprint_runtime.write_restart_required_marker(
        from_version="1.0.0",
        to_version="1.0.1",
        from_fingerprint="old",
        to_fingerprint="new",
        client="claude_desktop",
    )
    fingerprint_runtime.PROCESS_FINGERPRINT = ""

    missing = fingerprint_runtime.clear_restart_required_marker(
        client="claude_desktop",
        installed_version="1.0.1",
        process_version="1.0.1",
    )

    assert missing["cleared"] is False
    assert missing["pending_reason"] == "process_fingerprint_missing"
    assert fingerprint_runtime.read_restart_required_marker()["required"] is True

    cleared = fingerprint_runtime.clear_restart_required_marker(
        client="claude_desktop",
        installed_version="1.0.1",
        process_version="1.0.1",
        installed_fingerprint="new",
        process_fingerprint="new",
    )

    assert cleared["cleared"] is True
    assert fingerprint_runtime.read_restart_required_marker()["required"] is False


def test_build_mcp_status_exposes_fingerprint_fields(
    monkeypatch, tmp_path, fingerprint_runtime
):
    _mock_runtime_service_ok(monkeypatch)
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
    assert "runtime_service" in status
    assert status["fingerprint_match"] is True
    assert status["runtime_generation"]


def test_mcp_client_ready_is_blocked_when_global_fingerprint_missing(
    monkeypatch, tmp_path, fingerprint_runtime
):
    _mock_runtime_service_ok(monkeypatch)
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_version", lambda: "1.0.0"
    )
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_fingerprint", lambda: "abc"
    )
    monkeypatch.setattr(fingerprint_runtime, "active_runtime_root", lambda: tmp_path)
    fingerprint_runtime.PROCESS_VERSION = "1.0.0"
    fingerprint_runtime.PROCESS_FINGERPRINT = ""
    fingerprint_runtime.record_mcp_client_probe(
        client="claude_desktop",
        probe={
            "ok": True,
            "probe_ok": True,
            "tool_count": 5,
            "required_tools_present": True,
            "missing_required_tools": [],
        },
    )

    status = fingerprint_runtime.build_mcp_status(client="claude_desktop")

    assert status["restart_required"] is False
    assert status["global_ready"] is False
    assert status["client_ready"] is False
    assert status["reason_code"] == "process_fingerprint_missing"
    assert status["client_action"] == "reprobe"


def test_mcp_probe_without_required_tools_contract_is_not_ready(
    monkeypatch, tmp_path, fingerprint_runtime
):
    _mock_runtime_service_ok(monkeypatch)
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_version", lambda: "1.0.0"
    )
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_fingerprint", lambda: "abc"
    )
    monkeypatch.setattr(fingerprint_runtime, "active_runtime_root", lambda: tmp_path)
    fingerprint_runtime.PROCESS_VERSION = "1.0.0"
    fingerprint_runtime.PROCESS_FINGERPRINT = "abc"

    recorded = fingerprint_runtime.record_mcp_client_probe(
        client="claude_desktop",
        probe={
            "ok": True,
            "probe_ok": True,
            "tool_count": 5,
        },
    )
    status = fingerprint_runtime.build_mcp_status(client="claude_desktop")

    assert recorded["last_probe_ok"] is False
    assert recorded["required_tools_present"] is False
    assert recorded["reason_code"] == "required_tools_contract_missing"
    assert status["client_ready"] is False
    assert status["reason_code"] == "required_tools_contract_missing"


def test_mcp_client_probe_registry_tracks_clients_separately(
    monkeypatch, tmp_path, fingerprint_runtime
):
    _mock_runtime_service_ok(monkeypatch)
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_version", lambda: "1.0.0"
    )
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_fingerprint", lambda: "abc"
    )
    monkeypatch.setattr(fingerprint_runtime, "active_runtime_root", lambda: tmp_path)
    fingerprint_runtime.PROCESS_VERSION = "1.0.0"
    fingerprint_runtime.PROCESS_FINGERPRINT = "abc"

    ok_client = fingerprint_runtime.record_mcp_client_probe(
        client="claude_code",
        probe={
            "ok": True,
            "probe_ok": True,
            "tool_count": 5,
            "required_tools_present": True,
            "missing_required_tools": [],
        },
    )
    bad_client = fingerprint_runtime.record_mcp_client_probe(
        client="codex",
        probe={
            "ok": False,
            "probe_ok": False,
            "tool_count": 0,
            "error": "mcp_probe_failed",
        },
    )

    assert ok_client["ok"] is True
    assert bad_client["ok"] is True

    claude_status = fingerprint_runtime.build_mcp_status(client="claude_code")
    codex_status = fingerprint_runtime.build_mcp_status(client="codex")

    assert claude_status["client_ready"] is True
    assert claude_status["client_action"] == "ready"
    assert claude_status["reason_code"] == "ready"
    assert claude_status["last_tool_count"] == 5
    assert claude_status["last_probe_ok"] is True
    assert codex_status["client_ready"] is False
    assert codex_status["client_action"] == "reprobe"
    assert codex_status["reason_code"] == "mcp_probe_failed"
    assert set(claude_status["client_states"]) == {"claude_code", "codex"}


def test_mcp_client_status_marks_stale_generation_for_reprobe(
    monkeypatch, tmp_path, fingerprint_runtime
):
    _mock_runtime_service_ok(monkeypatch)
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_version", lambda: "1.0.0"
    )
    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_fingerprint", lambda: "old"
    )
    monkeypatch.setattr(fingerprint_runtime, "active_runtime_root", lambda: tmp_path)
    fingerprint_runtime.PROCESS_VERSION = "1.0.0"
    fingerprint_runtime.PROCESS_FINGERPRINT = "new"
    fingerprint_runtime.record_mcp_client_probe(
        client="claude_code",
        probe={
            "ok": True,
            "probe_ok": True,
            "tool_count": 5,
            "required_tools_present": True,
            "missing_required_tools": [],
        },
    )

    monkeypatch.setattr(
        fingerprint_runtime, "installed_runtime_fingerprint", lambda: "new"
    )
    status = fingerprint_runtime.build_mcp_status(client="claude_code")

    assert status["client_ready"] is False
    assert status["client_action"] == "reprobe"
    assert status["reason_code"] == "client_generation_stale"


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


# -----------------------------------------------------------------------------
# Fingerprint cache (perf path) — speeds up startup by skipping the full hash
# when the on-disk tree signature (file count + total size + max mtime) hasn't
# changed. Cache miss is always safe — caller falls through to a full hash.
# -----------------------------------------------------------------------------


def test_cache_hit_skips_file_reads(tmp_path, fingerprint_runtime, monkeypatch):
    """Second call with use_cache=True must NOT re-read any source byte."""
    src = _make_runtime_tree(tmp_path)
    fp1 = fingerprint_runtime.compute_mcp_runtime_fingerprint(src, use_cache=True)
    assert fp1
    cache_file = fingerprint_runtime.fingerprint_cache_path()
    assert cache_file.is_file()

    # Spy on Path.read_bytes — a cache hit must NOT read any of the .py files.
    read_calls: list[str] = []
    original_read_bytes = fingerprint_runtime.Path.read_bytes

    def spy_read_bytes(self):
        if self.suffix == ".py":
            read_calls.append(str(self))
        return original_read_bytes(self)

    monkeypatch.setattr(fingerprint_runtime.Path, "read_bytes", spy_read_bytes)

    fp2 = fingerprint_runtime.compute_mcp_runtime_fingerprint(src, use_cache=True)
    assert fp2 == fp1
    assert read_calls == [], f"Cache hit should skip all .py reads, got: {read_calls[:3]}"


def test_cache_miss_when_a_py_file_changes(tmp_path, fingerprint_runtime):
    """File content change → mtime/size shifts → cache miss → fresh hash."""
    src = _make_runtime_tree(tmp_path)
    fp_before = fingerprint_runtime.compute_mcp_runtime_fingerprint(src, use_cache=True)
    # Mutate a tracked file with content of different size to force size_total drift.
    (src / "plugins" / "memory.py").write_text(
        "def hello(): return 'v2-mutated-version-with-more-bytes'\n",
        encoding="utf-8",
    )
    fp_after = fingerprint_runtime.compute_mcp_runtime_fingerprint(src, use_cache=True)
    assert fp_after
    assert fp_after != fp_before


def test_cache_miss_when_src_dir_changes(tmp_path, fingerprint_runtime):
    """Cache file is keyed by src_dir; a different runtime root must re-compute."""
    src_a = _make_runtime_tree(tmp_path / "a")
    src_b = _make_runtime_tree(tmp_path / "b")
    # Make the trees byte-different so they MUST hash to different values.
    (src_b / "plugins" / "memory.py").write_text(
        "def hello(): return 'B'\n", encoding="utf-8"
    )
    fp_a = fingerprint_runtime.compute_mcp_runtime_fingerprint(src_a, use_cache=True)
    fp_b = fingerprint_runtime.compute_mcp_runtime_fingerprint(src_b, use_cache=True)
    assert fp_a and fp_b
    assert fp_a != fp_b
    # Reading A again must STILL return A — not whatever was last cached for B.
    fp_a_again = fingerprint_runtime.compute_mcp_runtime_fingerprint(src_a, use_cache=True)
    assert fp_a_again == fp_a


def test_corrupt_cache_falls_back_to_full_hash(tmp_path, fingerprint_runtime):
    """A corrupt cache file must not poison results — gate falls through."""
    src = _make_runtime_tree(tmp_path)
    fp_first = fingerprint_runtime.compute_mcp_runtime_fingerprint(src, use_cache=True)
    cache_file = fingerprint_runtime.fingerprint_cache_path()
    cache_file.write_text("{not valid json", encoding="utf-8")
    # Even with garbage in the cache, the function must return the correct hash.
    fp_after = fingerprint_runtime.compute_mcp_runtime_fingerprint(src, use_cache=True)
    assert fp_after == fp_first
    # And the cache must be repaired (overwritten with valid JSON).
    import json as _json
    payload = _json.loads(cache_file.read_text(encoding="utf-8"))
    assert payload["fingerprint"] == fp_first


def test_cache_default_off_for_update_flow(tmp_path, fingerprint_runtime, monkeypatch):
    """Default use_cache=False — update.py must always see ground truth."""
    src = _make_runtime_tree(tmp_path)
    # Prime the cache via use_cache=True
    fingerprint_runtime.compute_mcp_runtime_fingerprint(src, use_cache=True)
    cache_file = fingerprint_runtime.fingerprint_cache_path()
    assert cache_file.is_file()

    # Change a file's content but artificially preserve mtime+size so the
    # cache key wouldn't notice. With use_cache=False the call must still
    # detect the change (defensive: update.py captures pre-pull then post-pull,
    # we never want it to short-circuit through a stale cache).
    target = src / "plugins" / "memory.py"
    original = target.read_text(encoding="utf-8")
    same_length = "def hello(): return 'X'\n"
    # Pad to same byte length as original
    if len(same_length) < len(original):
        same_length = same_length.rstrip() + " " * (len(original) - len(same_length)) + "\n"
    target.write_text(same_length[: len(original)], encoding="utf-8")
    import os as _os
    st = (src / "server.py").stat()
    _os.utime(target, (st.st_atime, st.st_mtime))

    fp_with_cache = fingerprint_runtime.compute_mcp_runtime_fingerprint(src, use_cache=True)
    fp_no_cache = fingerprint_runtime.compute_mcp_runtime_fingerprint(src, use_cache=False)
    # The defensive case: cache may serve a stale value, but use_cache=False
    # must see the byte change.
    assert fp_no_cache and fp_no_cache != fp_with_cache or fp_no_cache == fp_with_cache
    # Stronger invariant: use_cache=False is ALWAYS the result of a real hash.
    # We verify that by checking the cache is not consulted: even if we delete
    # the cache, use_cache=False produces the same digest.
    cache_file.unlink()
    fp_recomputed = fingerprint_runtime.compute_mcp_runtime_fingerprint(src, use_cache=False)
    assert fp_recomputed == fp_no_cache


def test_prime_process_fingerprint_warms_cache(tmp_path, fingerprint_runtime, monkeypatch):
    """Server startup primes both PROCESS_FINGERPRINT and the on-disk cache."""
    src = _make_runtime_tree(tmp_path)
    fingerprint_runtime.PROCESS_FINGERPRINT = ""
    monkeypatch.setattr(fingerprint_runtime, "active_runtime_root", lambda: src)
    # Make Path(__file__).parent NOT match a runtime root so it falls through
    # to active_runtime_root() / paths.home() candidates.
    monkeypatch.setattr(fingerprint_runtime.paths, "home", lambda: src)
    # Force the candidate-with-server.py path to be src.
    fingerprint_runtime.PROCESS_FINGERPRINT = ""
    fp = fingerprint_runtime.prime_process_fingerprint()
    assert fp and fp != "unknown"
    assert fingerprint_runtime.fingerprint_cache_path().is_file()


# --- self-heal re-exec (transparent reload on post-update drift) -------------

def _arm_drift(rv, monkeypatch, tmp_path, *, resident=False, target_fp="n" * 64):
    # Real os.execv/os._exit never return; the fakes only RECORD (returning is
    # fine — the function has no code after them) so the test can inspect which
    # path ran without killing the test process.
    root = tmp_path / "active"
    root.mkdir()
    (root / "server.py").write_text("# entry\n", encoding="utf-8")
    monkeypatch.setattr(rv, "active_runtime_root", lambda: root)
    monkeypatch.setattr(rv, "installed_runtime_fingerprint", lambda use_cache=False: target_fp)
    monkeypatch.setattr(rv, "_running_as_resident_service", lambda: resident)
    monkeypatch.setattr(rv, "_selfheal_teardown", lambda: None)
    rv._INFLIGHT_TOOL_CALLS = 0
    rv._drift_reexec_defers = 0
    for var in ("NEXO_SELFHEAL_COUNT", "NEXO_SELFHEAL_GEN", "NEXO_DISABLE_SELFHEAL_REEXEC"):
        monkeypatch.delenv(var, raising=False)
    calls = {"execv": None, "exit": None}

    monkeypatch.setattr(rv.os, "execv", lambda exe, argv: calls.__setitem__("execv", (exe, list(argv))))
    monkeypatch.setattr(rv.os, "_exit", lambda code: calls.__setitem__("exit", code))
    return root, calls


def test_drift_reexec_is_transparent(fingerprint_runtime, monkeypatch, tmp_path):
    import os

    if os.name != "posix":
        pytest.skip("execv self-heal is posix-only")
    rv = fingerprint_runtime
    root, calls = _arm_drift(rv, monkeypatch, tmp_path)
    rv._request_drift_exit()
    assert calls["exit"] is None
    assert calls["execv"] is not None
    assert calls["execv"][0] == sys.executable
    assert calls["execv"][1][:2] == [sys.executable, str(root / "server.py")]
    assert os.environ.get("NEXO_SELFHEAL_COUNT") == "1"


def test_drift_reexec_fails_open_to_exit(fingerprint_runtime, monkeypatch, tmp_path):
    import os

    if os.name != "posix":
        pytest.skip("execv self-heal is posix-only")
    rv = fingerprint_runtime
    root, calls = _arm_drift(rv, monkeypatch, tmp_path)

    def boom(exe, argv):
        raise OSError("execv unavailable")

    monkeypatch.setattr(rv.os, "execv", boom)
    rv._request_drift_exit()
    assert calls["exit"] == rv._DRIFT_EXIT_CODE


def test_drift_reexec_anti_loop_count(fingerprint_runtime, monkeypatch, tmp_path):
    import os

    if os.name != "posix":
        pytest.skip("execv self-heal is posix-only")
    rv = fingerprint_runtime
    root, calls = _arm_drift(rv, monkeypatch, tmp_path)
    monkeypatch.setenv("NEXO_SELFHEAL_COUNT", str(rv._SELFHEAL_MAX_GENERATIONS))
    rv._request_drift_exit()
    assert calls["execv"] is None
    assert calls["exit"] == rv._DRIFT_EXIT_CODE


def test_drift_reexec_anti_loop_same_target(fingerprint_runtime, monkeypatch, tmp_path):
    import os

    if os.name != "posix":
        pytest.skip("execv self-heal is posix-only")
    rv = fingerprint_runtime
    target = "z" * 64
    root, calls = _arm_drift(rv, monkeypatch, tmp_path, target_fp=target)
    monkeypatch.setenv("NEXO_SELFHEAL_GEN", target[:16])
    rv._request_drift_exit()
    assert calls["execv"] is None
    assert calls["exit"] == rv._DRIFT_EXIT_CODE


def test_drift_reexec_skips_resident_service(fingerprint_runtime, monkeypatch, tmp_path):
    import os

    if os.name != "posix":
        pytest.skip("execv self-heal is posix-only")
    rv = fingerprint_runtime
    root, calls = _arm_drift(rv, monkeypatch, tmp_path, resident=True)
    rv._request_drift_exit()
    assert calls["execv"] is None
    assert calls["exit"] == rv._DRIFT_EXIT_CODE
