#!/usr/bin/env python3
"""housekeeping_stale_sessions — clean up orphan protocol tasks + stale sessions.

Fase 2 follow-up. Observed symptom: every time a NEXO session is stopped
(nexo_stop) without closing its open protocol task(s) first, the tasks
stay in status='open' forever. Over time the protocol_tasks table
accumulates dozens of orphan rows, and nexo_heartbeat starts dragging
through stale debts that no longer reflect reality.

Policy (conservative — only clean clearly dead state):

  1. A SESSION is "stale" when last_update_epoch is older than
     STALE_SESSION_HOURS (default 24h) AND the session is NOT listed as
     active in the in-memory session manager (approximated by
     last_update_epoch being the authoritative signal).
  2. A TASK is "orphan" when its session is stale AND the task status
     is still 'open'.
  3. Cancel orphans with outcome='cancelled', note="auto-cancelled by
     housekeeping_stale_sessions on <ts>", preserving the original
     opened_at / goal fields for audit.
  4. Delete sessions only when explicitly requested (--prune-sessions).
     Default keeps the session row so historical diary / transcript
     queries still resolve.

Dry-run by default. --apply is required to mutate. Prints a summary
before mutating so the operator can abort.

Run from the repo root:
  python3 scripts/housekeeping_stale_sessions.py
  python3 scripts/housekeeping_stale_sessions.py --stale-hours 12
  python3 scripts/housekeeping_stale_sessions.py --apply
  python3 scripts/housekeeping_stale_sessions.py --apply --prune-sessions
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path


DEFAULT_STALE_HOURS = 24.0


def resolve_db_path() -> Path:
    """Find nexo.db — prefers NEXO_HOME env, falls back to ~/.nexo/data/nexo.db."""
    env = os.environ.get("NEXO_HOME", "").strip()
    base = Path(env) if env else Path.home() / ".nexo"
    for candidate in [base / "data" / "nexo.db", base / "nexo.db"]:
        if candidate.is_file():
            return candidate
    return base / "data" / "nexo.db"  # canonical path even if missing


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def find_stale_sessions(conn: sqlite3.Connection, stale_hours: float) -> list[sqlite3.Row]:
    cutoff_epoch = time.time() - (stale_hours * 3600.0)
    return list(
        conn.execute(
            """SELECT sid, task, started_epoch, last_update_epoch
               FROM sessions
               WHERE last_update_epoch < ?
               ORDER BY last_update_epoch ASC""",
            (cutoff_epoch,),
        ).fetchall()
    )


def find_orphan_tasks(conn: sqlite3.Connection, stale_sids: list[str]) -> list[sqlite3.Row]:
    if not stale_sids:
        return []
    placeholders = ",".join("?" * len(stale_sids))
    return list(
        conn.execute(
            f"""SELECT task_id, session_id, status, goal, opened_at
                FROM protocol_tasks
                WHERE status = 'open' AND session_id IN ({placeholders})
                ORDER BY opened_at ASC""",
            stale_sids,
        ).fetchall()
    )


def find_deleted_session_tasks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Tasks whose session row no longer exists (most common orphan path).

    nexo_stop deletes the session row but does NOT close the open
    protocol tasks, leaving them referring to a non-existent session.
    These are ALWAYS orphan regardless of the stale_hours threshold —
    the parent session has physically disappeared, so the task can
    never be closed through the normal flow.
    """
    return list(
        conn.execute(
            """SELECT pt.task_id, pt.session_id, pt.status, pt.goal, pt.opened_at
               FROM protocol_tasks pt
               LEFT JOIN sessions s ON pt.session_id = s.sid
               WHERE pt.status = 'open' AND s.sid IS NULL
               ORDER BY pt.opened_at ASC"""
        ).fetchall()
    )


def cancel_orphans(conn: sqlite3.Connection, orphans: list[sqlite3.Row]) -> int:
    if not orphans:
        return 0
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    note = f"auto-cancelled by housekeeping_stale_sessions on {now_iso}"
    for row in orphans:
        conn.execute(
            """UPDATE protocol_tasks
               SET status = 'cancelled',
                   outcome_notes = ?,
                   closed_at = datetime('now')
               WHERE task_id = ? AND status = 'open'""",
            (note, row["task_id"]),
        )
    conn.commit()
    return len(orphans)


def prune_sessions(conn: sqlite3.Connection, stale_sids: list[str]) -> int:
    if not stale_sids:
        return 0
    placeholders = ",".join("?" * len(stale_sids))
    cur = conn.execute(
        f"DELETE FROM sessions WHERE sid IN ({placeholders})",
        stale_sids,
    )
    conn.commit()
    return cur.rowcount or 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NEXO housekeeping — orphan tasks + stale sessions")
    parser.add_argument(
        "--stale-hours",
        type=float,
        default=DEFAULT_STALE_HOURS,
        help=f"Sessions untouched for N hours are stale (default {DEFAULT_STALE_HOURS}).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually mutate the DB. Without --apply this is a dry-run.",
    )
    parser.add_argument(
        "--prune-sessions",
        action="store_true",
        help="Also DELETE the stale session rows (otherwise kept for history).",
    )
    parser.add_argument(
        "--db",
        default="",
        help="Override the DB path (defaults to NEXO_HOME/data/nexo.db).",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db) if args.db else resolve_db_path()
    if not db_path.is_file():
        print(f"ERROR: nexo.db not found at {db_path}", file=sys.stderr)
        return 2

    conn = connect(db_path)

    stale_sessions = find_stale_sessions(conn, args.stale_hours)
    stale_sids = [row["sid"] for row in stale_sessions]
    orphan_in_stale = find_orphan_tasks(conn, stale_sids)
    deleted_session_tasks = find_deleted_session_tasks(conn)

    # Deduplicate by task_id — a task cannot be both "deleted session"
    # and "stale session" at the same time, but the code stays robust
    # against future schema changes.
    seen_ids: set[str] = set()
    orphan_tasks: list[sqlite3.Row] = []
    for row in deleted_session_tasks + orphan_in_stale:
        if row["task_id"] in seen_ids:
            continue
        seen_ids.add(row["task_id"])
        orphan_tasks.append(row)

    print(f"DB: {db_path}")
    print(f"Stale threshold: {args.stale_hours:g} hours")
    print(f"Stale sessions (last_update < threshold): {len(stale_sessions)}")
    print(f"Tasks whose session row was deleted:      {len(deleted_session_tasks)}")
    print(f"Tasks open in stale (but present) sessions: {len(orphan_in_stale)}")
    print(f"Total orphan tasks to cancel:               {len(orphan_tasks)}")
    if stale_sessions:
        print("\nSample stale sessions (oldest first):")
        for row in stale_sessions[:10]:
            age_h = (time.time() - row["last_update_epoch"]) / 3600
            task = (row["task"] or "")[:60]
            print(f"  {row['sid']:<28} {age_h:6.1f}h  {task}")
    if orphan_tasks:
        print("\nSample orphan tasks (oldest first):")
        for row in orphan_tasks[:10]:
            goal = (row["goal"] or "")[:60]
            print(f"  {row['task_id']:<24} {row['session_id']:<28} {goal}")

    if not args.apply:
        print("\nDRY-RUN: use --apply to cancel orphan tasks.")
        if args.prune_sessions:
            print("          --prune-sessions would DELETE the listed sessions.")
        return 0

    cancelled = cancel_orphans(conn, orphan_tasks)
    pruned = 0
    if args.prune_sessions:
        pruned = prune_sessions(conn, stale_sids)

    print(f"\nAPPLIED: cancelled {cancelled} orphan tasks.")
    if args.prune_sessions:
        print(f"         pruned {pruned} stale session rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
