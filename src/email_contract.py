from __future__ import annotations

"""Email runtime contract: account config is separate from monitor events."""

import sqlite3
from pathlib import Path

import paths
from db import get_db


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _count(conn, sql: str, params: tuple = ()) -> int:
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    except Exception:
        return 0


def _email_event_db_path() -> Path:
    return paths.nexo_email_dir() / "nexo-email.db"


def _accounts_config_snapshot() -> dict:
    conn = get_db()
    exists = _table_exists(conn, "email_accounts")
    out = {
        "store": "nexo.db",
        "table": "email_accounts",
        "table_exists": exists,
        "total": 0,
        "enabled": 0,
        "agent_accounts": 0,
        "operator_accounts": 0,
        "with_credentials": 0,
        "missing_credentials": 0,
    }
    if not exists:
        return out

    rows = [dict(row) for row in conn.execute("SELECT * FROM email_accounts").fetchall()]
    out["total"] = len(rows)
    out["enabled"] = sum(1 for row in rows if int(row.get("enabled") or 0) == 1)
    out["agent_accounts"] = sum(1 for row in rows if (row.get("account_type") or "").lower() == "agent")
    out["operator_accounts"] = sum(1 for row in rows if (row.get("account_type") or "").lower() == "operator")
    out["with_credentials"] = sum(
        1
        for row in rows
        if (row.get("credential_service") or "").strip() and (row.get("credential_key") or "").strip()
    )
    out["missing_credentials"] = out["total"] - out["with_credentials"]
    return out


def _event_store_snapshot(email_db_path: str | Path | None = None) -> dict:
    path = Path(email_db_path) if email_db_path else _email_event_db_path()
    out = {
        "store": str(path),
        "db_exists": path.is_file(),
        "emails_table": False,
        "email_events_table": False,
        "total_emails": 0,
        "total_events": 0,
        "pending": 0,
        "processed": 0,
    }
    if not path.is_file():
        return out

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        out["emails_table"] = _table_exists(conn, "emails")
        out["email_events_table"] = _table_exists(conn, "email_events")
        if out["emails_table"]:
            out["total_emails"] = _count(conn, "SELECT COUNT(*) FROM emails")
            out["pending"] = _count(conn, "SELECT COUNT(*) FROM emails WHERE status='pending'")
            out["processed"] = _count(conn, "SELECT COUNT(*) FROM emails WHERE status='processed'")
        if out["email_events_table"]:
            out["total_events"] = _count(conn, "SELECT COUNT(*) FROM email_events")
    finally:
        conn.close()
    return out


def email_contract_snapshot(email_db_path: str | Path | None = None) -> dict:
    """Return the explicit split between email account config and monitor events."""
    accounts = _accounts_config_snapshot()
    events = _event_store_snapshot(email_db_path)
    return {
        "contract": {
            "layers": ["accounts_config", "event_store"],
            "conflated": False,
            "accounts_config": "nexo.db/email_accounts",
            "event_store": "runtime/nexo-email/nexo-email.db",
        },
        "accounts_config": accounts,
        "event_store": events,
    }
