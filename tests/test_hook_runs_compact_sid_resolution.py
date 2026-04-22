"""Tests for the NEXO sid resolution rails in the compact hooks.

v7.8.2 — pre_compact.py and post_compact.py used to store the raw
CLAUDE_SESSION_ID token in hook_runs.session_id. That produced empty
rows (env missing) or non-NEXO rows (raw token != nexo-N-N). This test
pins the rails `compact_session_resolver.resolve_nexo_sid` walks: DB
sessions match → DB alias match → per-conv sidecar → legacy global
sidecar → empty with source=none.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest


HOOKS_SRC = Path(__file__).resolve().parents[1] / "src" / "hooks"


def _ensure_hooks_on_path() -> None:
    src = str(HOOKS_SRC.parent)
    if src not in sys.path:
        sys.path.insert(0, src)
    hooks = str(HOOKS_SRC)
    if hooks not in sys.path:
        sys.path.insert(0, hooks)


def _create_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE sessions (sid TEXT PRIMARY KEY, claude_session_id TEXT)"
    )
    conn.execute(
        "CREATE TABLE session_claude_aliases ("
        "  sid TEXT, claude_session_id TEXT, last_seen REAL"
        ")"
    )
    conn.commit()
    return conn


@pytest.fixture
def nexo_home(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    data_dir = home / "runtime" / "data"
    data_dir.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    # Cachebust resolver in case another test already imported it.
    _ensure_hooks_on_path()
    if "compact_session_resolver" in sys.modules:
        importlib.reload(sys.modules["compact_session_resolver"])
    return home


def _reload_resolver():
    _ensure_hooks_on_path()
    mod = sys.modules.get("compact_session_resolver")
    if mod is None:
        mod = importlib.import_module("compact_session_resolver")
    else:
        mod = importlib.reload(mod)
    return mod


# ---------------------------------------------------------------------------
# resolve_nexo_sid rails
# ---------------------------------------------------------------------------

def test_resolve_env_sessions_match(nexo_home):
    conn = _create_db(nexo_home / "runtime" / "data" / "nexo.db")
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?)",
        ("nexo-2000000001-1", "claude-aa-001"),
    )
    conn.commit()
    conn.close()
    mod = _reload_resolver()
    sid, source = mod.resolve_nexo_sid("claude-aa-001")
    assert sid == "nexo-2000000001-1"
    assert source == "sessions"


def test_resolve_env_alias_match_when_no_sessions_row(nexo_home):
    conn = _create_db(nexo_home / "runtime" / "data" / "nexo.db")
    # Newer alias wins when two rows exist.
    conn.execute(
        "INSERT INTO session_claude_aliases VALUES (?, ?, ?)",
        ("nexo-2000000002-5", "claude-bb-002", 1000.0),
    )
    conn.execute(
        "INSERT INTO session_claude_aliases VALUES (?, ?, ?)",
        ("nexo-2000000002-6", "claude-bb-002", 2000.0),
    )
    conn.commit()
    conn.close()
    mod = _reload_resolver()
    sid, source = mod.resolve_nexo_sid("claude-bb-002")
    assert sid == "nexo-2000000002-6"
    assert source == "alias"


def test_resolve_env_falls_back_to_per_conv_sidecar(nexo_home):
    # DB with no matching rows, sidecar present for this token.
    conn = _create_db(nexo_home / "runtime" / "data" / "nexo.db")
    conn.close()
    side_dir = nexo_home / "runtime" / "data" / "compacting"
    side_dir.mkdir(parents=True)
    (side_dir / "claude-cc-003.txt").write_text("nexo-2000000003-7\n", encoding="utf-8")
    mod = _reload_resolver()
    sid, source = mod.resolve_nexo_sid("claude-cc-003")
    assert sid == "nexo-2000000003-7"
    assert source == "sidecar"


def test_resolve_no_env_falls_back_to_legacy_global_sidecar(nexo_home, monkeypatch):
    conn = _create_db(nexo_home / "runtime" / "data" / "nexo.db")
    conn.close()
    (nexo_home / "runtime" / "data" / "compacting-sid.txt").write_text(
        "nexo-2000000004-9\n", encoding="utf-8"
    )
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    mod = _reload_resolver()
    sid, source = mod.resolve_nexo_sid("")
    assert sid == "nexo-2000000004-9"
    assert source == "sidecar_legacy"


def test_resolve_returns_none_when_nothing_matches(nexo_home, monkeypatch):
    conn = _create_db(nexo_home / "runtime" / "data" / "nexo.db")
    conn.close()
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    mod = _reload_resolver()
    sid, source = mod.resolve_nexo_sid("")
    assert sid == ""
    assert source == "none"


def test_resolve_rejects_malformed_sid_in_sidecar(nexo_home):
    conn = _create_db(nexo_home / "runtime" / "data" / "nexo.db")
    conn.close()
    side_dir = nexo_home / "runtime" / "data" / "compacting"
    side_dir.mkdir(parents=True)
    # Malformed content must be ignored so the resolver never hands a
    # bogus value to hook_runs.session_id.
    (side_dir / "claude-dd-004.txt").write_text("not-a-nexo-sid\n", encoding="utf-8")
    mod = _reload_resolver()
    sid, source = mod.resolve_nexo_sid("claude-dd-004")
    assert sid == ""
    assert source == "none"


# ---------------------------------------------------------------------------
# pre_compact.py / post_compact.py wrappers
# ---------------------------------------------------------------------------

def _install_hook_observability_stub(monkeypatch, captured: list[dict]) -> None:
    _ensure_hooks_on_path()

    class _Stub:
        @staticmethod
        def record_hook_run(hook_name: str, **kwargs):
            captured.append({"hook_name": hook_name, **kwargs})
            return 1

    monkeypatch.setitem(sys.modules, "hook_observability", _Stub())


def test_pre_compact_wrapper_stores_nexo_sid_and_metadata(nexo_home, monkeypatch):
    conn = _create_db(nexo_home / "runtime" / "data" / "nexo.db")
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?)",
        ("nexo-2000000005-11", "claude-ee-005"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("CLAUDE_SESSION_ID", "claude-ee-005")
    captured: list[dict] = []
    _install_hook_observability_stub(monkeypatch, captured)
    # Force module reload so the wrapper picks up the stub.
    for name in ("compact_session_resolver", "pre_compact"):
        if name in sys.modules:
            del sys.modules[name]
    pre_compact = importlib.import_module("pre_compact")
    pre_compact._record(123, 0)
    assert len(captured) == 1
    row = captured[0]
    assert row["hook_name"] == "pre_compact"
    assert row["session_id"] == "nexo-2000000005-11"
    assert row["duration_ms"] == 123
    assert row["exit_code"] == 0
    assert row["metadata"]["claude_session_id"] == "claude-ee-005"
    assert row["metadata"]["sid_source"] == "sessions"


def test_post_compact_wrapper_stores_nexo_sid_and_metadata(nexo_home, monkeypatch):
    conn = _create_db(nexo_home / "runtime" / "data" / "nexo.db")
    conn.execute(
        "INSERT INTO session_claude_aliases VALUES (?, ?, ?)",
        ("nexo-2000000006-13", "claude-ff-006", 3000.0),
    )
    conn.commit()
    conn.close()
    captured: list[dict] = []
    _install_hook_observability_stub(monkeypatch, captured)
    for name in ("compact_session_resolver", "post_compact"):
        if name in sys.modules:
            del sys.modules[name]
    post_compact = importlib.import_module("post_compact")
    post_compact._record(77, 0, "claude-ff-006")
    assert len(captured) == 1
    row = captured[0]
    assert row["hook_name"] == "post_compact"
    assert row["session_id"] == "nexo-2000000006-13"
    assert row["metadata"]["claude_session_id"] == "claude-ff-006"
    assert row["metadata"]["sid_source"] == "alias"


def test_wrapper_keeps_session_id_empty_when_nothing_resolves(nexo_home, monkeypatch):
    # Empty DB + no sidecars + no env. We must still store a row with
    # session_id='' and sid_source='none' so the audit trail is clean.
    conn = _create_db(nexo_home / "runtime" / "data" / "nexo.db")
    conn.close()
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    captured: list[dict] = []
    _install_hook_observability_stub(monkeypatch, captured)
    for name in ("compact_session_resolver", "pre_compact"):
        if name in sys.modules:
            del sys.modules[name]
    pre_compact = importlib.import_module("pre_compact")
    pre_compact._record(10, 0)
    assert captured[0]["session_id"] == ""
    assert captured[0]["metadata"]["sid_source"] == "none"
