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
VALID_ACCOUNT_TYPES = ("agent", "operator")


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
    d["account_type"] = str(d.get("account_type") or "agent")
    d["description"] = str(d.get("description") or "")
    role = str(d.get("role") or "both")
    d["can_read"] = bool(
        d.get("can_read", 1 if role in ("inbox", "both") else 0)
    )
    d["can_send"] = bool(
        d.get("can_send", 1 if role in ("outbox", "both") else 0)
    )
    d["is_default"] = bool(d.get("is_default", 0))
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
    account_type: str | None = None,
    description: str | None = None,
    can_read: bool | None = None,
    can_send: bool | None = None,
    is_default: bool | None = None,
) -> dict:
    if not label or not email:
        raise ValueError("label and email are required")
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {VALID_ROLES}, got {role!r}")
    existing = get_email_account(label) or {}
    clean_account_type = str(account_type or existing.get("account_type") or "agent").strip().lower()
    if clean_account_type not in VALID_ACCOUNT_TYPES:
        raise ValueError(
            f"account_type must be one of {VALID_ACCOUNT_TYPES}, got {clean_account_type!r}"
        )
    clean_description = (
        str(description).strip()
        if description is not None
        else str(existing.get("description") or "")
    )
    if can_read is None:
        resolved_can_read = bool(existing.get("can_read")) if existing else role in ("inbox", "both")
    else:
        resolved_can_read = bool(can_read)
    if can_send is None:
        resolved_can_send = bool(existing.get("can_send")) if existing else role in ("outbox", "both")
    else:
        resolved_can_send = bool(can_send)
    resolved_is_default = bool(existing.get("is_default")) if is_default is None else bool(is_default)
    if clean_account_type != "operator":
        resolved_is_default = False
    conn = get_db()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    # Audit H2: when the caller does not pass `metadata` explicitly,
    # an upsert would otherwise wipe whatever the operator (or another
    # subsystem like auto_capture / poll-tuning) had previously stored
    # on this label. Preserve the existing metadata in that case so
    # the only way to clear it is `metadata={}` explicit.
    if metadata is None:
        metadata = existing.get("metadata") if existing.get("metadata") else {}
    if resolved_is_default:
        conn.execute(
            "UPDATE email_accounts SET is_default = 0, updated_at = ? "
            "WHERE label != ? AND is_default = 1",
            (now, label),
        )
    conn.execute(
        """
        INSERT INTO email_accounts (
            label, email, imap_host, imap_port, smtp_host, smtp_port,
            credential_service, credential_key, operator_email,
            trusted_domains, role, enabled, metadata, account_type,
            description, can_read, can_send, is_default, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            account_type = excluded.account_type,
            description = excluded.description,
            can_read = excluded.can_read,
            can_send = excluded.can_send,
            is_default = excluded.is_default,
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
            clean_account_type,
            clean_description,
            1 if resolved_can_read else 0,
            1 if resolved_can_send else 0,
            1 if resolved_is_default else 0,
            now,
            now,
        ),
    )
    conn.commit()
    return get_email_account(label) or {}


def list_email_accounts(
    include_disabled: bool = True,
    account_type: str | None = None,
) -> list[dict]:
    if account_type is not None and account_type not in VALID_ACCOUNT_TYPES:
        raise ValueError(
            f"account_type must be one of {VALID_ACCOUNT_TYPES}, got {account_type!r}"
        )
    conn = get_db()
    clauses: list[str] = []
    params: list[Any] = []
    if not include_disabled:
        clauses.append("enabled = 1")
    if account_type is not None:
        clauses.append("account_type = ?")
        params.append(account_type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM email_accounts {where} "
        "ORDER BY CASE account_type WHEN 'agent' THEN 0 ELSE 1 END, "
        "is_default DESC, label COLLATE NOCASE",
        tuple(params),
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
    """Most-recently-updated enabled agent account. Returns None if table empty."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM email_accounts WHERE enabled = 1 "
        "AND COALESCE(account_type, 'agent') = 'agent' "
        "ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    return _row_to_dict(row) if row else None


def get_default_operator_email_account() -> dict | None:
    """Return the explicit default operator mailbox, if any."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM email_accounts "
        "WHERE enabled = 1 AND COALESCE(account_type, 'agent') = 'operator' "
        "AND is_default = 1 "
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
    "get_default_operator_email_account",
    "set_email_account_enabled",
    "remove_email_account",
    "DEFAULT_IMAP_PORT",
    "DEFAULT_SMTP_PORT",
    "VALID_ROLES",
    "VALID_ACCOUNT_TYPES",
]
