from __future__ import annotations

from .db import get_local_context_db
from .util import json_dumps, now


def log_event(level: str, event: str, message: str, **metadata) -> None:
    conn = get_local_context_db()
    conn.execute(
        """
        INSERT INTO local_index_logs(created_at, level, event, message, metadata_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (now(), level, event, message, json_dumps(metadata)),
    )
    conn.commit()


def tail(limit: int = 100) -> list[dict]:
    conn = get_local_context_db()
    rows = conn.execute(
        """
        SELECT created_at, level, event, message, metadata_json
        FROM local_index_logs
        ORDER BY id DESC
        LIMIT ?
        """,
        (max(1, min(int(limit or 100), 500)),),
    ).fetchall()
    items = []
    for row in reversed(rows):
        items.append({
            "created_at": row["created_at"],
            "level": row["level"],
            "event": row["event"],
            "message": row["message"],
            "metadata_json": row["metadata_json"],
        })
    return items
