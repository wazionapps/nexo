"""Regression tests for local_context SQLite concurrency (Release A / A2).

The live 'database is locked' came from an ASYMMETRIC busy_timeout: read-only
connections gave up at 1200 ms while the writer held the lock up to 15000 ms.
A reader must wait at least as long as the writer can hold the lock. The busy
retry also closed the SHARED cached writer handle, invalidating it for everyone.
"""

import sqlite3
import time

from local_context import api
from local_context import db as lcdb


def test_readonly_busy_timeout_is_in_parity_with_writer():
    api.ensure_ready()
    writer_timeout = lcdb._busy_timeout_ms()
    conn = lcdb.connect_local_context_db_readonly()
    try:
        read_timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
    finally:
        conn.close()
    assert read_timeout == writer_timeout, f"read busy_timeout {read_timeout} != writer {writer_timeout}"
    assert read_timeout >= 10000, "readers must wait ~10-15s, not give up at 1200ms"


def test_busy_retry_does_not_close_shared_cached_connection(monkeypatch):
    """The retry for 'database is locked' must NOT close the cached writer handle:
    it is shared, and closing it invalidates the connection for every other caller."""
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    closes = {"n": 0}
    monkeypatch.setattr(api, "close_local_context_db", lambda: closes.__setitem__("n", closes["n"] + 1))

    calls = {"n": 0}

    def callback():
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    result = api._with_sqlite_busy_retry(callback)

    assert result == "ok"
    assert calls["n"] == 2, "must retry after a transient lock"
    assert closes["n"] == 0, "retry must NOT close the shared cached handle"
