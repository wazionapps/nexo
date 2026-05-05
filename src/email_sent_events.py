"""Durable sent-email continuity for NEXO automations.

The email monitor tracks inbound lifecycle rows. This module tracks outbound
messages so startup, duplicate checks, and briefings can see what NEXO already
sent even when the send path did not originate from an inbound email row.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import paths


EMAIL_SENT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sent_email_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT,
    sender TEXT,
    to_addrs TEXT NOT NULL DEFAULT '',
    cc_addrs TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    in_reply_to TEXT NOT NULL DEFAULT '',
    references_header TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'sent',
    sent_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sent_email_message_id
ON sent_email_events(message_id)
WHERE message_id IS NOT NULL AND message_id != '';
CREATE INDEX IF NOT EXISTS idx_sent_email_sent_at ON sent_email_events(sent_at);
CREATE INDEX IF NOT EXISTS idx_sent_email_subject ON sent_email_events(subject);
"""

RECENT_SENT_EMAILS_TITLE = "EMAILS ENVIADOS ULTIMAS 24H POR LA OPERATIVA"


def sent_email_db_path() -> Path:
    return paths.nexo_email_dir() / "nexo-email.db"


def ensure_sent_email_table(conn: sqlite3.Connection) -> None:
    conn.executescript(EMAIL_SENT_TABLE_SQL)


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else sent_email_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_sent_email_table(conn)
    return conn


def _clean(value: object, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _safe_meta(meta: dict[str, Any] | None) -> str:
    try:
        return json.dumps(meta or {}, ensure_ascii=True, sort_keys=True)
    except Exception:
        return "{}"


def _record_cognitive_memory(event: dict[str, str]) -> None:
    try:
        import cognitive

        to_value = event.get("to_addrs", "")
        subject = event.get("subject", "")
        message_id = event.get("message_id", "")
        content = (
            "Sent email recorded by NEXO. "
            f"To: {to_value}. Subject: {subject}. Message-ID: {message_id}."
        )
        cognitive.ingest_to_ltm(
            content,
            source_type="email_sent",
            source_id=message_id or f"{to_value}:{subject}",
            source_title=subject,
            domain="email",
            tags="email,sent,continuity",
            bypass_gate=True,
        )
    except Exception:
        pass


def record_sent_email(
    *,
    message_id: str = "",
    sender: str = "",
    to_addrs: str = "",
    cc_addrs: str = "",
    subject: str = "",
    in_reply_to: str = "",
    references_header: str = "",
    source: str = "",
    status: str = "sent",
    meta: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
    record_memory: bool = True,
) -> dict[str, str]:
    event = {
        "message_id": _clean(message_id, 300),
        "sender": _clean(sender, 300),
        "to_addrs": _clean(to_addrs, 800),
        "cc_addrs": _clean(cc_addrs, 800),
        "subject": _clean(subject, 500),
        "in_reply_to": _clean(in_reply_to, 300),
        "references_header": _clean(references_header, 1000),
        "source": _clean(source or "unknown", 120),
        "status": _clean(status or "sent", 80),
        "meta": _safe_meta(meta),
    }
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO sent_email_events (
                message_id, sender, to_addrs, cc_addrs, subject, in_reply_to,
                references_header, source, status, meta
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["message_id"],
                event["sender"],
                event["to_addrs"],
                event["cc_addrs"],
                event["subject"],
                event["in_reply_to"],
                event["references_header"],
                event["source"],
                event["status"],
                event["meta"],
            ),
        )
        conn.commit()
    finally:
        conn.close()

    if record_memory:
        _record_cognitive_memory(event)
    return event


def recent_sent_emails(
    *,
    hours: int = 24,
    limit: int = 10,
    db_path: str | Path | None = None,
) -> list[dict[str, str]]:
    cutoff = (datetime.now() - timedelta(hours=max(1, int(hours)))).strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT message_id, sender, to_addrs, cc_addrs, subject, in_reply_to,
                   references_header, source, status, sent_at, meta
            FROM sent_email_events
            WHERE sent_at >= ?
            ORDER BY sent_at DESC
            LIMIT ?
            """,
            (cutoff, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def find_sent_email(
    *,
    to_addr: str = "",
    subject: str = "",
    since_hours: int = 72,
    db_path: str | Path | None = None,
) -> dict[str, str] | None:
    target_to = _clean(to_addr).lower()
    target_subject = _clean(subject).lower()
    if not target_to or not target_subject:
        return None
    for event in recent_sent_emails(hours=since_hours, limit=100, db_path=db_path):
        if target_to in str(event.get("to_addrs") or "").lower() and target_subject in str(event.get("subject") or "").lower():
            return event
    return None


def format_recent_sent_email_block(*, hours: int = 24, limit: int = 8) -> str:
    rows = recent_sent_emails(hours=hours, limit=limit)
    if not rows:
        return ""
    lines = [f"== {RECENT_SENT_EMAILS_TITLE} =="]
    for row in rows:
        sent_at = str(row.get("sent_at") or "")
        to_value = _clean(row.get("to_addrs"), 120)
        subject = _clean(row.get("subject"), 160)
        source = _clean(row.get("source"), 80)
        lines.append(f"- {sent_at} | to: {to_value} | subject: {subject} | source: {source}")
    return "\n".join(lines)


__all__ = [
    "RECENT_SENT_EMAILS_TITLE",
    "ensure_sent_email_table",
    "find_sent_email",
    "format_recent_sent_email_block",
    "recent_sent_emails",
    "record_sent_email",
    "sent_email_db_path",
]
