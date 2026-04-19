"""Plan Consolidado F1 — email_accounts CRUD.

First-class multi-account email config. Replaces the legacy flat JSON
at ~/.nexo/nexo-email/config.json (single tenant, password cleartext,
operator-specific fields) with a structured table. Credentials never
land in this row — they live in the `credentials` table referenced by
`credential_service` + `credential_key`.
"""

from __future__ import annotations

import json
import time
from typing import Any

from db._core import get_db


DEFAULT_IMAP_PORT = 993
DEFAULT_SMTP_PORT = 465
VALID_ROLES = ("inbox", "outbox", "both")


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    d = dict(row)
    try:
        d["trusted_domains"] = json.loads(d.get("trusted_domains") or "[]") or []
    except Exception:
        d["trusted_domains"] = []
    try:
        d["metadata"] = json.loads(d.get("metadata") or "{}") or {}
    except Exception:
        d["metadata"] = {}
    d["enabled"] = bool(d.get("enabled", 1))
    return d


def add_email_account(
    *,
    label: str,
    email: str,
    imap_host: str = "",
    imap_port: int = DEFAULT_IMAP_PORT,
    smtp_host: str = "",
    smtp_port: int = DEFAULT_SMTP_PORT,
    credential_service: str = "",
    credential_key: str = "",
    operator_email: str = "",
    trusted_domains: list[str] | None = None,
    role: str = "both",
    enabled: bool = True,
    metadata: dict | None = None,
) -> dict:
    if not label or not email:
        raise ValueError("label and email are required")
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {VALID_ROLES}, got {role!r}")
    conn = get_db()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    # Audit H2: when the caller does not pass `metadata` explicitly,
    # an upsert would otherwise wipe whatever the operator (or another
    # subsystem like auto_capture / poll-tuning) had previously stored
    # on this label. Preserve the existing metadata in that case so
    # the only way to clear it is `metadata={}` explicit.
    if metadata is None:
        existing = get_email_account(label) or {}
        metadata = existing.get("metadata") if existing.get("metadata") else {}
    conn.execute(
        """
        INSERT INTO email_accounts (
            label, email, imap_host, imap_port, smtp_host, smtp_port,
            credential_service, credential_key, operator_email,
            trusted_domains, role, enabled, metadata, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(label) DO UPDATE SET
            email = excluded.email,
            imap_host = excluded.imap_host,
            imap_port = excluded.imap_port,
            smtp_host = excluded.smtp_host,
            smtp_port = excluded.smtp_port,
            credential_service = excluded.credential_service,
            credential_key = excluded.credential_key,
            operator_email = excluded.operator_email,
            trusted_domains = excluded.trusted_domains,
            role = excluded.role,
            enabled = excluded.enabled,
            metadata = excluded.metadata,
            updated_at = excluded.updated_at
        """,
        (
            label,
            email,
            imap_host,
            int(imap_port),
            smtp_host,
            int(smtp_port),
            credential_service,
            credential_key,
            operator_email,
            json.dumps(trusted_domains or [], ensure_ascii=False),
            role,
            1 if enabled else 0,
            json.dumps(metadata or {}, ensure_ascii=False),
            now,
            now,
        ),
    )
    conn.commit()
    return get_email_account(label) or {}


def list_email_accounts(include_disabled: bool = True) -> list[dict]:
    conn = get_db()
    where = "" if include_disabled else "WHERE enabled = 1"
    rows = conn.execute(
        f"SELECT * FROM email_accounts {where} ORDER BY label COLLATE NOCASE"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_email_account(label: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM email_accounts WHERE label = ?",
        (label,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def get_primary_email_account() -> dict | None:
    """Most-recently-updated enabled account. Returns None if table empty."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM email_accounts WHERE enabled = 1 "
        "ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    return _row_to_dict(row) if row else None


def set_email_account_enabled(label: str, enabled: bool) -> bool:
    conn = get_db()
    cur = conn.execute(
        "UPDATE email_accounts SET enabled = ?, updated_at = datetime('now') "
        "WHERE label = ?",
        (1 if enabled else 0, label),
    )
    conn.commit()
    return cur.rowcount > 0


def remove_email_account(label: str) -> bool:
    conn = get_db()
    cur = conn.execute("DELETE FROM email_accounts WHERE label = ?", (label,))
    conn.commit()
    return cur.rowcount > 0


__all__ = [
    "add_email_account",
    "list_email_accounts",
    "get_email_account",
    "get_primary_email_account",
    "set_email_account_enabled",
    "remove_email_account",
    "DEFAULT_IMAP_PORT",
    "DEFAULT_SMTP_PORT",
    "VALID_ROLES",
]
