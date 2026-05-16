"""Helpers for interactive MCP tools that must not block on SQLite locks."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

from db import get_db


def interactive_db_timeout_ms() -> int:
    """Return the max SQLite busy wait for user-facing MCP calls."""
    try:
        return max(50, min(int(os.environ.get("NEXO_MCP_DB_BUSY_TIMEOUT_MS", "250")), 10000))
    except Exception:
        return 250


def set_interactive_db_timeout() -> None:
    """Make the shared connection fail fast when a background writer owns the DB."""
    try:
        get_db().execute(f"PRAGMA busy_timeout={interactive_db_timeout_ms()}")
    except Exception:
        pass


@contextmanager
def interactive_db_timeout():
    """Temporarily reduce SQLite busy wait for a user-facing MCP call."""
    conn = None
    previous = None
    try:
        conn = get_db()
        row = conn.execute("PRAGMA busy_timeout").fetchone()
        previous = int(row[0]) if row and row[0] is not None else None
        conn.execute(f"PRAGMA busy_timeout={interactive_db_timeout_ms()}")
    except Exception:
        conn = None
    try:
        yield
    finally:
        if conn is not None and previous is not None:
            try:
                conn.execute(f"PRAGMA busy_timeout={previous}")
            except Exception:
                pass


def is_db_busy(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        isinstance(exc, sqlite3.OperationalError)
        and (
            "database is locked" in msg
            or "database is busy" in msg
            or "database table is locked" in msg
        )
    ) or "database is locked" in msg or "database is busy" in msg
