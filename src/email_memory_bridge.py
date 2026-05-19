from __future__ import annotations

"""Safe email-memory bridge for router/evidence queries."""

import sqlite3
from pathlib import Path


def _connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path).expanduser().resolve()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.DatabaseError:
        return set()


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return bool(row)


def _row_value(row: sqlite3.Row, *names: str) -> str:
    keys = set(row.keys())
    for name in names:
        if name in keys and row[name] is not None:
            return str(row[name])
    return ""


def search_email_memory(
    query: str,
    *,
    db_path: str | Path,
    limit: int = 5,
    include_body: bool = False,
) -> dict:
    clean_query = str(query or "").strip()
    if not clean_query:
        return {"ok": False, "reason": "empty_query", "results": []}
    try:
        conn = _connect_readonly(db_path)
    except sqlite3.Error as exc:
        return {"ok": False, "reason": f"db_unavailable:{type(exc).__name__}", "results": []}
    try:
        if not _has_table(conn, "emails"):
            return {"ok": False, "reason": "emails_table_missing", "results": []}
        columns = _table_columns(conn, "emails")
        searchable = [name for name in ("subject", "sender", "from_email", "to_email", "status", "summary") if name in columns]
        if not searchable:
            return {"ok": False, "reason": "no_searchable_columns", "results": []}
        where = " OR ".join([f"LOWER({name}) LIKE ?" for name in searchable])
        params = [f"%{clean_query.lower()}%" for _ in searchable]
        select_columns = ["id"] + sorted(searchable)
        if include_body and "body" in columns:
            select_columns.append("body")
        sql = f"SELECT {', '.join(select_columns)} FROM emails WHERE {where} ORDER BY id DESC LIMIT ?"
        rows = conn.execute(sql, [*params, max(1, int(limit or 5))]).fetchall()
        results = []
        for row in rows:
            result = {
                "source": "email",
                "id": _row_value(row, "id"),
                "subject": _row_value(row, "subject"),
                "sender": _row_value(row, "sender", "from_email"),
                "to": _row_value(row, "to_email"),
                "status": _row_value(row, "status"),
                "summary": _row_value(row, "summary"),
            }
            if include_body and "body" in row.keys():
                body = _row_value(row, "body")
                result["body_preview"] = body[:500]
            results.append(result)
        return {"ok": True, "reason": "ok", "results": results}
    finally:
        conn.close()


def email_source_for_intent(intent: str) -> bool:
    clean_intent = str(intent or "").strip().lower().replace("-", "_")
    return clean_intent in {"schedule_commitment", "prior_work", "memory_question", "identity_authorship"}
