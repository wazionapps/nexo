"""Plan Consolidado F1 — single loader for scripts that used to read
~/.nexo/nexo-email/config.json directly.

The loader prefers the `email_accounts` table. When the table is empty
(fresh install that hasn't run `nexo email setup` yet) it falls back
to the legacy JSON for backwards compatibility — no crons stall while
Francisco migrates.

Usage from any script:

    from email_config import load_email_config
    cfg = load_email_config()  # returns dict with the shape the legacy
                               # config.json used to have
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


def _legacy_config_path() -> Path:
    try:
        from paths import nexo_email_dir

        return nexo_email_dir() / "config.json"
    except Exception:
        return Path.home() / ".nexo" / "nexo-email" / "config.json"


LEGACY_CONFIG_PATH = _legacy_config_path()


def _get_credential(service: str, key: str) -> str:
    """Fetch a password from the credentials table. Returns empty string
    on any miss so the caller can log-and-skip instead of crashing a cron.
    """
    if not service or not key:
        return ""
    try:
        from db._core import get_db
    except Exception:  # pragma: no cover
        return ""
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT value FROM credentials WHERE service = ? AND key = ?",
            (service, key),
        ).fetchone()
        if row is None:
            return ""
        return str(row[0] or "")
    except Exception as exc:  # pragma: no cover
        _logger.warning("credential lookup failed for %s/%s: %s", service, key, exc)
        return ""


def _account_to_runtime_account(account: dict) -> dict:
    password = _get_credential(
        account.get("credential_service", ""),
        account.get("credential_key", ""),
    )
    metadata = account.get("metadata") if isinstance(account.get("metadata"), dict) else {}
    sent_folder = str(metadata.get("sent_folder") or "").strip() or "INBOX.Sent"
    return {
        "label": account.get("label", ""),
        "email": account.get("email", ""),
        "password": password,
        "imap_host": account.get("imap_host", ""),
        "imap_port": int(account.get("imap_port") or 993),
        "smtp_host": account.get("smtp_host", ""),
        "smtp_port": int(account.get("smtp_port") or 465),
        "account_type": account.get("account_type", "agent"),
        "description": account.get("description", ""),
        "operator_email": account.get("operator_email", ""),
        "trusted_domains": list(account.get("trusted_domains") or []),
        "role": account.get("role", "both"),
        "enabled": bool(account.get("enabled", True)),
        "can_read": bool(account.get("can_read")),
        "can_send": bool(account.get("can_send")),
        "is_default": bool(account.get("is_default")),
        "sender_policy": metadata.get("sender_policy", "open"),
        "sent_folder": sent_folder,
        "check_interval_seconds": metadata.get("check_interval_seconds", 60),
        "max_retries": metadata.get("max_retries", 3),
        "retry_backoff_seconds": metadata.get("retry_backoff_seconds", 60),
        "claude_binary": metadata.get("claude_binary", ""),
        "working_dir": metadata.get("working_dir", str(Path.home())),
        "automation_task_profile": metadata.get("automation_task_profile", "deep"),
        "max_process_time": metadata.get("max_process_time"),
        "metadata": metadata,
    }


def _account_to_legacy_shape(
    account: dict,
    operator_accounts: list[dict],
    extra_operator_emails: list[str],
) -> dict:
    """Project an email_accounts row onto the dict the legacy code expects."""
    runtime_account = _account_to_runtime_account(account)
    default_operator = next((a for a in operator_accounts if a.get("is_default")), None)
    default_operator_email = (
        str((default_operator or {}).get("email") or "").strip()
        or str(account.get("operator_email") or "").strip()
    )
    return {
        "imap_host": runtime_account["imap_host"],
        "imap_port": runtime_account["imap_port"],
        "smtp_host": runtime_account["smtp_host"],
        "smtp_port": runtime_account["smtp_port"],
        "email": runtime_account["email"],
        "password": runtime_account["password"],
        "operator_email": default_operator_email,
        "operator_aliases": list(extra_operator_emails or []),
        "francisco_emails": list(extra_operator_emails or []),
        "trusted_domains": runtime_account["trusted_domains"],
        "sender_policy": runtime_account["sender_policy"],
        "sent_folder": runtime_account["sent_folder"],
        "check_interval_seconds": runtime_account["check_interval_seconds"],
        "max_retries": runtime_account["max_retries"],
        "retry_backoff_seconds": runtime_account["retry_backoff_seconds"],
        "claude_binary": runtime_account["claude_binary"],
        "working_dir": runtime_account["working_dir"],
        "automation_task_profile": runtime_account["automation_task_profile"],
        "max_process_time": runtime_account["max_process_time"],
        "label": runtime_account["label"],
        "role": runtime_account["role"],
        "account_type": runtime_account["account_type"],
        "description": runtime_account["description"],
        "can_read": runtime_account["can_read"],
        "can_send": runtime_account["can_send"],
        "is_default": runtime_account["is_default"],
        "agent_account": runtime_account,
        "operator_accounts": operator_accounts,
        "default_operator_account": default_operator,
        "default_operator_email": default_operator_email,
        "_source": "email_accounts",
    }


def _load_legacy_json() -> dict | None:
    """Read ~/.nexo/nexo-email/config.json if it exists."""
    path = _legacy_config_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        _logger.warning("legacy email config unparseable: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    data["_source"] = "legacy-config-json"
    return data


def load_email_runtime_snapshot() -> dict[str, Any] | None:
    """Return the current agent/operator email model.

    Shape:
      {
        "agent_account": {...} | None,
        "operator_accounts": [{...}, ...],
        "default_operator_account": {...} | None,
        "_source": "email_accounts" | "legacy-config-json"
      }
    """
    try:
        from db._email_accounts import (
            get_default_operator_email_account,
            get_primary_email_account,
            list_email_accounts,
        )
        agent = get_primary_email_account()
        operators = list_email_accounts(include_disabled=True, account_type="operator")
    except Exception as exc:
        _logger.warning("email_accounts snapshot lookup failed: %s", exc)
        agent = None
        operators = []

    if agent or operators:
        default_operator = get_default_operator_email_account() if operators else None
        return {
            "agent_account": _account_to_runtime_account(agent) if agent else None,
            "operator_accounts": [_account_to_runtime_account(a) for a in operators],
            "default_operator_account": (
                _account_to_runtime_account(default_operator) if default_operator else None
            ),
            "_source": "email_accounts",
        }

    legacy = _load_legacy_json()
    if legacy is None:
        return None
    return {
        "agent_account": legacy,
        "operator_accounts": [],
        "default_operator_account": None,
        "_source": "legacy-config-json",
    }


def load_email_config(label: str | None = None) -> dict | None:
    """Return the email config for a given label (or the primary account).

    Preference order:
      1. email_accounts table (via label or get_primary_email_account).
      2. ~/.nexo/nexo-email/config.json legacy file.
      3. None if neither is available.
    """
    account: dict | None = None
    operator_accounts: list[dict] = []
    try:
        from db._email_accounts import get_email_account, get_primary_email_account, list_email_accounts
        if label:
            account = get_email_account(label)
        else:
            account = get_primary_email_account()
        operator_accounts = list_email_accounts(include_disabled=True, account_type="operator")
    except Exception as exc:
        _logger.warning("email_accounts lookup failed: %s", exc)

    if account:
        extra: list[str] = []
        for op in operator_accounts:
            value = str(op.get("email") or "").strip().lower()
            if value and value not in extra:
                extra.append(value)
        # F1/F2 — operator aliases are canonical now. Keep the legacy
        # `francisco_emails` compatibility shape in the returned payload
        # so old code paths do not break during transition.
        aliases = (account.get("metadata") or {}).get("operator_aliases") or []
        for a in aliases:
            if a and a not in extra:
                extra.append(a)
        return _account_to_legacy_shape(
            account,
            [_account_to_runtime_account(a) for a in operator_accounts],
            extra,
        )

    return _load_legacy_json()


__all__ = [
    "load_email_config",
    "load_email_runtime_snapshot",
    "LEGACY_CONFIG_PATH",
]
