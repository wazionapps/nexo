"""Credential storage for email account passwords.

Email account rows keep only ``credential_service`` + ``credential_key``.
Historically the referenced ``credentials.value`` column stored the password
directly. New writes try to place the secret in the OS keyring and keep a
``keyring://...`` marker in SQLite. Legacy plaintext values remain readable so
older installs keep working until they are rotated or migrated.
"""

from __future__ import annotations

import importlib
import os
import time
from urllib.parse import quote, unquote

KEYRING_SERVICE = "com.nexo.email"
KEYRING_MARKER_PREFIX = "keyring://"


def _db():
    from db._core import get_db

    return get_db()


def _marker(service: str, key: str) -> str:
    return f"{KEYRING_MARKER_PREFIX}{quote(service, safe='')}/{quote(key, safe='')}"


def _parse_marker(value: str) -> tuple[str, str] | None:
    if not value.startswith(KEYRING_MARKER_PREFIX):
        return None
    rest = value[len(KEYRING_MARKER_PREFIX):]
    if "/" not in rest:
        return None
    service, key = rest.split("/", 1)
    return unquote(service), unquote(key)


def _account_name(service: str, key: str) -> str:
    return f"{service}:{key}"


def _keyring_module():
    try:
        return importlib.import_module("keyring")
    except Exception:
        return None


def _read_stored_value(service: str, key: str) -> str:
    if not service or not key:
        return ""
    row = _db().execute(
        "SELECT value FROM credentials WHERE service = ? AND key = ?",
        (service, key),
    ).fetchone()
    if row is None:
        return ""
    return str(row[0] or "")


def _write_db_value(service: str, key: str, value: str, notes: str) -> None:
    conn = _db()
    now = time.time()
    conn.execute(
        """
        INSERT INTO credentials (service, key, value, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(service, key) DO UPDATE SET
            value = excluded.value,
            notes = excluded.notes,
            updated_at = excluded.updated_at
        """,
        (service, key, value, notes, now, now),
    )
    conn.commit()


def store_email_credential(service: str, key: str, value: str, notes: str = "email account password") -> str:
    """Store an email password and return the SQLite value written.

    Uses keyring when available. If the keyring backend is unavailable or
    locked, the legacy SQLite plaintext path is used so existing automation
    does not lose email access.
    """
    service = str(service or "").strip()
    key = str(key or "").strip()
    value = str(value or "")
    if not service or not key:
        return ""

    mode = os.environ.get("NEXO_EMAIL_CREDENTIAL_STORE", "auto").strip().lower()
    keyring = None if mode in {"sqlite", "legacy", "plain", "plaintext"} else _keyring_module()
    if keyring is not None:
        try:
            keyring.set_password(KEYRING_SERVICE, _account_name(service, key), value)
            stored = _marker(service, key)
            _write_db_value(service, key, stored, notes + " (stored in system keyring)")
            return stored
        except Exception:
            pass

    if mode == "keyring":
        return ""

    _write_db_value(service, key, value, notes + " (legacy sqlite fallback)")
    return value


def read_email_credential(service: str, key: str) -> str:
    """Resolve an email password from keyring marker or legacy plaintext."""
    stored = _read_stored_value(str(service or "").strip(), str(key or "").strip())
    parsed = _parse_marker(stored)
    if parsed is None:
        return stored

    keyring = _keyring_module()
    if keyring is None:
        return ""
    try:
        value = keyring.get_password(KEYRING_SERVICE, _account_name(*parsed))
    except Exception:
        return ""
    return str(value or "")


def delete_email_credential(service: str, key: str) -> None:
    service = str(service or "").strip()
    key = str(key or "").strip()
    if not service or not key:
        return

    parsed = _parse_marker(_read_stored_value(service, key))
    keyring = _keyring_module()
    if parsed is not None and keyring is not None:
        try:
            keyring.delete_password(KEYRING_SERVICE, _account_name(*parsed))
        except Exception:
            pass

    conn = _db()
    conn.execute("DELETE FROM credentials WHERE service = ? AND key = ?", (service, key))
    conn.commit()


def is_keyring_marker(value: str) -> bool:
    return _parse_marker(str(value or "")) is not None


__all__ = [
    "KEYRING_MARKER_PREFIX",
    "KEYRING_SERVICE",
    "delete_email_credential",
    "is_keyring_marker",
    "read_email_credential",
    "store_email_credential",
]
