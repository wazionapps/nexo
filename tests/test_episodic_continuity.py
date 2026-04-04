"""Tests for diary continuity window and auto-close diary enrichment."""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_db_modules(monkeypatch, nexo_home: Path):
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    monkeypatch.setenv("NEXO_TEST_DB", str(nexo_home / "data" / "nexo.db"))
    import db
    import db._core
    import db._episodic
    import auto_close_sessions

    db.close_db()
    importlib.reload(db._core)
    importlib.reload(db._episodic)
    importlib.reload(db)
    importlib.reload(auto_close_sessions)
    return db, auto_close_sessions


def test_last_day_returns_recent_continuity_window(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    (home / "data").mkdir(parents=True)

    db, _ = _reload_db_modules(monkeypatch, home)
    db.init_db()

    for sid in ("sid-10h", "sid-20h", "sid-40h"):
        db.write_session_diary(
            session_id=sid,
            summary=f"summary {sid}",
            decisions="",
            discarded="",
            pending="",
            context_next="",
            mental_state="interactive",
            domain="",
            user_signals="",
            self_critique="",
            source="claude",
        )

    conn = db.get_db()
    conn.execute("UPDATE session_diary SET created_at = datetime('now', '-10 hours') WHERE session_id = 'sid-10h'")
    conn.execute("UPDATE session_diary SET created_at = datetime('now', '-20 hours') WHERE session_id = 'sid-20h'")
    conn.execute("UPDATE session_diary SET created_at = datetime('now', '-40 hours') WHERE session_id = 'sid-40h'")
    conn.commit()

    results = db.read_session_diary(last_day=True)
    sids = {item["session_id"] for item in results}
    assert "sid-10h" in sids
    assert "sid-20h" in sids
    assert "sid-40h" not in sids


def test_auto_close_promotes_draft_with_checkpoint_context(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    for dirname in ("data", "operations/tool-logs", "coordination"):
        (home / dirname).mkdir(parents=True, exist_ok=True)

    db, auto_close = _reload_db_modules(monkeypatch, home)
    db.init_db()
    sid = "nexo-123-456"
    db.upsert_diary_draft(
        sid=sid,
        tasks_seen=json.dumps(["Draft release notes", "Check cron recovery"]),
        change_ids="[]",
        decision_ids="[]",
        last_context_hint="Francisco explained the overnight memory gap and wanted continuity fixes.",
        heartbeat_count=3,
        summary_draft="Session tasks: Draft release notes, Check cron recovery",
    )
    db.save_checkpoint(
        sid=sid,
        task="Fix continuity",
        current_goal="Preserve prior-evening context in diary recall.",
        reasoning_thread="Need rolling continuity instead of calendar-day recall.",
        next_step="Patch read_session_diary and validate with tests.",
        active_files='["src/db/_episodic.py","src/auto_close_sessions.py"]',
    )

    auto_close.promote_draft_to_diary(sid, db.get_diary_draft(sid), task="Fix continuity")
    rows = db.read_session_diary(session_id=sid, include_automated=True)
    assert rows
    diary = rows[0]
    assert "Latest context:" in diary["summary"]
    assert "Current goal:" in diary["summary"]
    assert "Reasoning:" in diary["context_next"]
    assert "Active files:" in diary["context_next"]
