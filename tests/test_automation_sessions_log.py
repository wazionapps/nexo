"""Tests for the start/end split in agent_runner + migration #41 columns.

Contract guards:
    - automation_runs has caller, session_type, started_at, ended_at, pid,
      resonance_tier columns after migration #41.
    - _record_automation_start inserts a row with ended_at IS NULL, returns
      row_id.
    - _record_automation_end updates that row with ended_at + returncode +
      usage + cost. Missing row_id falls back to a single-shot INSERT.
    - handle_session_log_create / handle_session_log_close are the MCP
      surface and round-trip cleanly.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import db as db_mod
import agent_runner
import tools_automation_sessions as tools_log


def test_migration_41_columns_exist():
    conn = db_mod.get_db()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(automation_runs)")}
    assert {
        "caller",
        "session_type",
        "started_at",
        "ended_at",
        "pid",
        "resonance_tier",
    }.issubset(cols)


def test_start_then_end_persists_row():
    row_id, err = agent_runner._record_automation_start(
        caller="test/harness",
        backend="claude_code",
        session_type="headless",
        task_profile="default",
        model="claude-opus-4-7[1m]",
        reasoning_effort="xhigh",
        resonance_tier="alto",
        cwd=Path("/tmp"),
        output_format="text",
        prompt="hello",
    )
    assert row_id is not None and err == ""

    conn = db_mod.get_db()
    row = conn.execute(
        "SELECT caller, session_type, ended_at, status FROM automation_runs WHERE id=?",
        (row_id,),
    ).fetchone()
    assert row["caller"] == "test/harness"
    assert row["session_type"] == "headless"
    assert row["ended_at"] is None  # in-flight row
    assert row["status"] == "running"

    ok, err2 = agent_runner._record_automation_end(
        row_id=row_id,
        returncode=0,
        duration_ms=1234,
        telemetry={"usage": {"input_tokens": 10, "output_tokens": 5}},
    )
    assert ok is True

    row2 = conn.execute(
        "SELECT ended_at, returncode, duration_ms, status, input_tokens, output_tokens "
        "FROM automation_runs WHERE id=?",
        (row_id,),
    ).fetchone()
    assert row2["ended_at"] is not None
    assert row2["returncode"] == 0
    assert row2["duration_ms"] == 1234
    assert row2["status"] == "ok"
    assert row2["input_tokens"] == 10
    assert row2["output_tokens"] == 5


def test_end_without_start_still_inserts():
    """Backwards-compat: if the start path failed or an older caller jumps
    straight to end, _record_automation_end must still leave a trace."""
    conn = db_mod.get_db()
    before = conn.execute("SELECT COUNT(*) AS n FROM automation_runs").fetchone()["n"]
    ok, err = agent_runner._record_automation_end(
        row_id=None,
        returncode=143,
        duration_ms=500,
        telemetry={"usage": {"input_tokens": 0, "output_tokens": 0}},
    )
    after = conn.execute("SELECT COUNT(*) AS n FROM automation_runs").fetchone()["n"]
    assert ok is True
    assert after == before + 1


def test_session_log_create_requires_caller_and_backend():
    result = tools_log.handle_session_log_create(caller="", backend="claude_code")
    assert result["ok"] is False
    assert "caller" in result["error"]

    result = tools_log.handle_session_log_create(caller="desktop_new_session", backend="")
    assert result["ok"] is False


def test_session_log_create_and_close_roundtrip():
    open_result = tools_log.handle_session_log_create(
        caller="desktop_new_session",
        backend="claude_code",
        session_type="interactive_desktop",
        model="claude-opus-4-7[1m]",
        reasoning_effort="xhigh",
        cwd="/tmp",
        pid=99999,
        context_excerpt="first user prompt",
    )
    assert open_result["ok"] is True
    session_id = open_result["session_id"]
    assert isinstance(session_id, int)

    # Row should exist with ended_at=NULL
    conn = db_mod.get_db()
    row = conn.execute(
        "SELECT caller, session_type, ended_at, pid FROM automation_runs WHERE id=?",
        (session_id,),
    ).fetchone()
    assert row["caller"] == "desktop_new_session"
    assert row["session_type"] == "interactive_desktop"
    assert row["ended_at"] is None
    assert row["pid"] == 99999

    close = tools_log.handle_session_log_close(
        session_id=session_id,
        returncode=0,
        duration_ms=42000,
        input_tokens=120,
        output_tokens=340,
        total_cost_usd=0.0056,
        telemetry_source="desktop_stream",
    )
    assert close["ok"] is True
    row2 = conn.execute(
        "SELECT ended_at, input_tokens, output_tokens, total_cost_usd, telemetry_source "
        "FROM automation_runs WHERE id=?",
        (session_id,),
    ).fetchone()
    assert row2["ended_at"] is not None
    assert row2["input_tokens"] == 120
    assert row2["output_tokens"] == 340
    assert abs((row2["total_cost_usd"] or 0) - 0.0056) < 1e-6
    assert row2["telemetry_source"] == "desktop_stream"


def test_session_log_close_missing_session_id():
    result = tools_log.handle_session_log_close()
    assert result["ok"] is False
