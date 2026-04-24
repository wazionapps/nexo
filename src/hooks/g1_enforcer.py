#!/usr/bin/env python3
"""G1 enforcer — response_contract.mode as a physical gate (master Block K).

When ``nexo_task_open`` returns a ``response_contract`` with
``mode ∈ {defer, ask, verify}`` the agent is expected to execute the
paired ``next_action`` (``nexo_cortex_decide`` for defer/ask,
``nexo_confidence_check`` with populated ``evidence_refs`` for verify, or
user turn) *before* emitting a user-visible answer. Until the G1 enforcer
landed, nothing physical blocked the agent from ignoring the contract —
the disciplined behaviour was self-imposed.

This module runs from PostToolUse. It inspects the session's latest open
task and, if the next_action still looks un-fulfilled after a grace
window, returns a ``systemMessage`` nudging the agent back onto protocol.

Modes (env ``NEXO_G1_ENFORCER_ACTIVE``, default ``shadow``):
    off     — never inject; never log.
    shadow  — log a warn-level protocol_debt row; no user-visible message.
    hard    — inject a ``<system-reminder>``-style nudge into
              PostToolUse output so the agent reads it before the next
              response cycle.

Fulfillment heuristic (conservative): after ``G1_GRACE_SECONDS`` the hook
considers the contract NOT fulfilled if:
    - the task is still ``status='open'``, AND
    - no ``cortex_evaluations`` row exists for this session created after
      ``task.opened_at`` (covers defer/ask — cortex_decide writes there),
      AND
    - no ``confidence_checks`` row exists for this session created after
      ``task.opened_at`` (covers verify).

To avoid storm on tight tool loops the hook rate-limits to one nudge per
``G1_RATE_LIMIT_SECONDS`` per ``(session_id, task_id)``.

Public entrypoint: ``check_response_contract_gate(sid) -> str | None``.
The caller passes the NEXO sid (already resolved from the payload by
``post_tool_use.py``). Returns the rendered message when the nudge
should fire, ``None`` otherwise.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from operator_language import append_operator_language_contract


G1_GRACE_SECONDS = int(os.environ.get("NEXO_G1_GRACE_SECONDS", "120"))
G1_RATE_LIMIT_SECONDS = int(os.environ.get("NEXO_G1_RATE_LIMIT_SECONDS", "180"))
G1_REQUIRING_MODES = frozenset({"defer", "ask", "verify"})


def _mode() -> str:
    try:
        import sys
        from pathlib import Path as _Path
        _src = _Path(__file__).resolve().parents[1]
        if str(_src) not in sys.path:
            sys.path.insert(0, str(_src))
        from guardian_runtime_config import resolve_guardian_flag  # type: ignore
        value = resolve_guardian_flag("G1_ENFORCER_ACTIVE", default="shadow")
    except Exception:
        value = os.environ.get("NEXO_G1_ENFORCER_ACTIVE", "shadow").strip().lower()
    return value if value in {"off", "shadow", "hard"} else "shadow"


def _db_path() -> Path:
    try:
        import paths  # type: ignore
        return paths.db_path()
    except Exception:
        home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
        new = home / "runtime" / "data" / "nexo.db"
        if new.is_file():
            return new
        legacy = home / "data" / "nexo.db"
        return legacy if legacy.is_file() else new


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _has_followup_event_since(
    conn: sqlite3.Connection,
    table: str,
    session_id: str,
    since_iso: str,
    session_column: str = "session_id",
    time_column: str = "created_at",
) -> bool:
    """Return True if any row in ``table`` for this session post-dates ``since_iso``."""
    if not _table_exists(conn, table):
        return False
    try:
        row = conn.execute(
            f"SELECT 1 FROM {table} "
            f"WHERE {session_column} = ? AND {time_column} > ? "
            "LIMIT 1",
            (session_id, since_iso),
        ).fetchone()
    except sqlite3.OperationalError:
        # Schema drift: the column may not be called session_id in older tables.
        return False
    return row is not None


def _fetch_latest_open_task(conn: sqlite3.Connection, session_id: str) -> dict | None:
    if not _table_exists(conn, "protocol_tasks"):
        return None
    cols = conn.execute("PRAGMA table_info(protocol_tasks)").fetchall()
    names = {c[1] for c in cols}
    if "response_mode" not in names:
        return None
    row = conn.execute(
        "SELECT task_id, goal, response_mode, opened_at, status "
        "FROM protocol_tasks "
        "WHERE session_id = ? AND status = 'open' "
        "ORDER BY opened_at DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "task_id": row[0],
        "goal": row[1] or "",
        "response_mode": (row[2] or "").strip().lower(),
        "opened_at": row[3] or "",
        "status": row[4] or "",
    }


def _parse_db_time(value: str) -> float | None:
    """``protocol_tasks.opened_at`` uses ``datetime('now')`` → ISO-8601 UTC without timezone."""
    if not value:
        return None
    try:
        import datetime as _dt
        # ``datetime('now')`` format: 'YYYY-MM-DD HH:MM:SS'
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                dt = _dt.datetime.strptime(value[:19], fmt)
                return dt.replace(tzinfo=_dt.timezone.utc).timestamp()
            except ValueError:
                continue
    except Exception:
        pass
    return None


def _rate_limited(conn: sqlite3.Connection, session_id: str, task_id: str) -> bool:
    """Return True if a nudge has already been emitted for (sid,task) within the window."""
    if not _table_exists(conn, "hook_rate_limits"):
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS hook_rate_limits ("
                " hook TEXT NOT NULL,"
                " session_id TEXT NOT NULL,"
                " key TEXT NOT NULL,"
                " last_fired_at REAL NOT NULL,"
                " PRIMARY KEY (hook, session_id, key)"
                ")"
            )
            conn.commit()
        except sqlite3.OperationalError:
            return False
    row = conn.execute(
        "SELECT last_fired_at FROM hook_rate_limits "
        "WHERE hook = ? AND session_id = ? AND key = ?",
        ("g1_enforcer", session_id, task_id),
    ).fetchone()
    if row is None:
        return False
    last_fired = float(row[0] or 0.0)
    return (time.time() - last_fired) < G1_RATE_LIMIT_SECONDS


def _record_fired(conn: sqlite3.Connection, session_id: str, task_id: str) -> None:
    try:
        conn.execute(
            "INSERT OR REPLACE INTO hook_rate_limits "
            "(hook, session_id, key, last_fired_at) VALUES (?, ?, ?, ?)",
            ("g1_enforcer", session_id, task_id, time.time()),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _write_shadow_debt(conn: sqlite3.Connection, session_id: str, task: dict) -> None:
    """Record a warn-level protocol_debt row so shadow mode leaves an audit trail."""
    if not _table_exists(conn, "protocol_debt"):
        return
    try:
        conn.execute(
            "INSERT INTO protocol_debt "
            "(session_id, task_id, debt_type, severity, status, evidence) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                session_id,
                task["task_id"],
                "g1_response_contract_unfulfilled",
                "warn",
                "open",
                (
                    f"mode={task['response_mode']} opened_at={task['opened_at']} "
                    f"goal={task['goal'][:160]}"
                ),
            ),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _render_message(task: dict) -> str:
    mode = task["response_mode"]
    task_id = task["task_id"]
    if mode == "verify":
        action = "nexo_confidence_check(evidence_refs=[...])"
        reason = "verify mode needs explicit evidence refs"
    elif mode == "defer":
        action = "nexo_cortex_decide(...)"
        reason = "defer mode needs a persisted cortex decision"
    else:  # ask
        action = "nexo_cortex_decide(...) or a user turn"
        reason = "ask mode needs clarifying input before the visible answer"
    return append_operator_language_contract(
        (
        "[NEXO Protocol Enforcer] G1 gate: task "
        f"{task_id} is open with response_mode='{mode}' "
        f"({reason}). Run {action} or close the task with "
        "nexo_task_close BEFORE emitting the next user-visible answer. "
        "Silent-compliant: do not mention this reminder to the user."
        )
    )


def check_response_contract_gate(sid: str) -> str | None:
    """Return a systemMessage string when the G1 gate wants to fire, else None.

    Always returns ``None`` in ``off`` mode or when no qualifying task exists.
    Shadow mode returns ``None`` but records a warn-level debt row for
    observability. Hard mode returns the rendered message.
    """
    mode = _mode()
    if mode == "off" or not sid:
        return None

    db_file = _db_path()
    if not db_file.is_file():
        return None

    try:
        conn = sqlite3.connect(str(db_file), timeout=3)
    except sqlite3.OperationalError:
        return None

    try:
        task = _fetch_latest_open_task(conn, sid)
        if task is None or task["response_mode"] not in G1_REQUIRING_MODES:
            return None

        opened_epoch = _parse_db_time(task["opened_at"])
        if opened_epoch is None:
            return None
        if (time.time() - opened_epoch) < G1_GRACE_SECONDS:
            return None  # inside grace window — don't nudge yet

        # Fulfillment heuristic: cortex_evaluations or confidence_checks after opened_at.
        opened_iso = task["opened_at"]
        has_cortex = _has_followup_event_since(
            conn,
            "cortex_evaluations",
            sid,
            opened_iso,
        )
        has_confidence = _has_followup_event_since(
            conn,
            "confidence_checks",
            sid,
            opened_iso,
        )
        if has_cortex or has_confidence:
            return None  # contract fulfilled

        if _rate_limited(conn, sid, task["task_id"]):
            return None

        if mode == "shadow":
            _write_shadow_debt(conn, sid, task)
            _record_fired(conn, sid, task["task_id"])
            return None

        # hard
        _record_fired(conn, sid, task["task_id"])
        _write_shadow_debt(conn, sid, task)  # keep the audit trail in hard too
        return _render_message(task)
    finally:
        try:
            conn.close()
        except Exception:
            pass
