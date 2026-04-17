"""v6.0.7 — nexo_heartbeat re-syncs sessions.claude_session_id.

Verifies that when Claude Code rotates its PreToolUse session UUID and
writes the new value to ``$NEXO_HOME/coordination/.claude-session-id``,
the next heartbeat updates ``sessions.claude_session_id`` / ``external_session_id``
to match. Without this sync, the hook guardrail's coordination-file
fallback resolves to no row and surfaces "unknown target".
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    import db._core as _core

    tmp_db = str(tmp_path / "nexo.db")
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    monkeypatch.setenv("NEXO_DB", tmp_db)
    monkeypatch.setenv("NEXO_TEST_DB", tmp_db)
    monkeypatch.setattr(_core, "DB_PATH", tmp_db, raising=False)
    monkeypatch.setattr(_core, "_shared_conn", None, raising=False)

    import importlib
    import tools_sessions as _ts
    importlib.reload(_ts)
    monkeypatch.setattr(_ts, "NEXO_HOME", tmp_path, raising=False)

    import db as db_pkg
    db_pkg.init_db()
    try:
        yield tmp_path
    finally:
        try:
            _core.close_db()
        except Exception:
            pass


def _read_cid(sid: str) -> tuple[str, str]:
    import db as db_pkg
    conn = db_pkg.get_db()
    row = conn.execute(
        "SELECT claude_session_id, external_session_id FROM sessions WHERE sid = ?",
        (sid,),
    ).fetchone()
    return (row["claude_session_id"] or "", row["external_session_id"] or "")


def test_heartbeat_updates_claude_session_id_from_coord_file(isolated_home):
    import db as db_pkg
    from tools_sessions import handle_heartbeat

    sid = f"nexo-{int(time.time())}-10001"
    db_pkg.register_session(sid, "boot", claude_session_id="old-uuid-aaaa")
    assert _read_cid(sid) == ("old-uuid-aaaa", "old-uuid-aaaa")

    coord_dir = isolated_home / "coordination"
    coord_dir.mkdir(parents=True, exist_ok=True)
    (coord_dir / ".claude-session-id").write_text("new-uuid-bbbb\n", encoding="utf-8")

    handle_heartbeat(sid, "work after rotation")

    assert _read_cid(sid) == ("new-uuid-bbbb", "new-uuid-bbbb")


def test_heartbeat_no_coord_file_leaves_cid_unchanged(isolated_home):
    import db as db_pkg
    from tools_sessions import handle_heartbeat

    sid = f"nexo-{int(time.time())}-10002"
    db_pkg.register_session(sid, "boot", claude_session_id="stable-uuid-cccc")

    handle_heartbeat(sid, "work without rotation")

    assert _read_cid(sid) == ("stable-uuid-cccc", "stable-uuid-cccc")


def test_heartbeat_empty_coord_file_leaves_cid_unchanged(isolated_home):
    import db as db_pkg
    from tools_sessions import handle_heartbeat

    sid = f"nexo-{int(time.time())}-10003"
    db_pkg.register_session(sid, "boot", claude_session_id="stable-uuid-dddd")

    coord_dir = isolated_home / "coordination"
    coord_dir.mkdir(parents=True, exist_ok=True)
    (coord_dir / ".claude-session-id").write_text("   \n", encoding="utf-8")

    handle_heartbeat(sid, "work with empty coord")

    assert _read_cid(sid) == ("stable-uuid-dddd", "stable-uuid-dddd")


def test_heartbeat_same_cid_no_op(isolated_home):
    import db as db_pkg
    from tools_sessions import handle_heartbeat

    sid = f"nexo-{int(time.time())}-10004"
    db_pkg.register_session(sid, "boot", claude_session_id="same-uuid-eeee")

    coord_dir = isolated_home / "coordination"
    coord_dir.mkdir(parents=True, exist_ok=True)
    (coord_dir / ".claude-session-id").write_text("same-uuid-eeee\n", encoding="utf-8")

    handle_heartbeat(sid, "work same cid")

    assert _read_cid(sid) == ("same-uuid-eeee", "same-uuid-eeee")
