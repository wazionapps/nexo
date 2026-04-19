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
import os
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

NEXO_HOME = Path(os.environ.get("NEXO_HOME") or (Path.home() / ".nexo"))
LEGACY_CONFIG_PATH = NEXO_HOME / "nexo-email" / "config.json"


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


def _account_to_legacy_shape(account: dict, extra_operator_emails: list[str]) -> dict:
    """Project an email_accounts row onto the dict the legacy code expects."""
    password = _get_credential(
        account.get("credential_service", ""),
        account.get("credential_key", ""),
    )
    return {
        "imap_host": account.get("imap_host", ""),
        "imap_port": int(account.get("imap_port") or 993),
        "smtp_host": account.get("smtp_host", ""),
        "smtp_port": int(account.get("smtp_port") or 465),
        "email": account.get("email", ""),
        "password": password,
        "operator_email": account.get("operator_email", ""),
        "francisco_emails": list(extra_operator_emails or []),
        "trusted_domains": list(account.get("trusted_domains") or []),
        "sender_policy": account.get("metadata", {}).get("sender_policy", "open"),
        "check_interval_seconds": account.get("metadata", {}).get("check_interval_seconds", 60),
        "max_retries": account.get("metadata", {}).get("max_retries", 3),
        "retry_backoff_seconds": account.get("metadata", {}).get("retry_backoff_seconds", 60),
        "claude_binary": account.get("metadata", {}).get("claude_binary", ""),
        "working_dir": account.get("metadata", {}).get("working_dir", str(Path.home())),
        "automation_task_profile": account.get("metadata", {}).get("automation_task_profile", "deep"),
        "max_process_time": account.get("metadata", {}).get("max_process_time"),
        "label": account.get("label", ""),
        "role": account.get("role", "both"),
        "_source": "email_accounts",
    }


def _load_legacy_json() -> dict | None:
    """Read ~/.nexo/nexo-email/config.json if it exists."""
    if not LEGACY_CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(LEGACY_CONFIG_PATH.read_text())
    except Exception as exc:
        _logger.warning("legacy email config unparseable: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    data["_source"] = "legacy-config-json"
    return data


def load_email_config(label: str | None = None) -> dict | None:
    """Return the email config for a given label (or the primary account).

    Preference order:
      1. email_accounts table (via label or get_primary_email_account).
      2. ~/.nexo/nexo-email/config.json legacy file.
      3. None if neither is available.
    """
    account: dict | None = None
    try:
        from db._email_accounts import get_email_account, get_primary_email_account
        if label:
            account = get_email_account(label)
        else:
            account = get_primary_email_account()
    except Exception as exc:
        _logger.warning("email_accounts lookup failed: %s", exc)

    if account:
        extra: list[str] = []
        try:
            from db._core import get_db
            conn = get_db()
            rows = conn.execute(
                "SELECT email FROM email_accounts WHERE role IN ('inbox','both') AND enabled = 1"
            ).fetchall()
            extra = [r[0] for r in rows if r[0]]
        except Exception:
            pass
        # F1 — also surface metadata.operator_aliases (the legacy
        # `francisco_emails` list) so personal aliases keep treated as
        # "operator's own messages".
        aliases = (account.get("metadata") or {}).get("operator_aliases") or []
        for a in aliases:
            if a and a not in extra:
                extra.append(a)
        return _account_to_legacy_shape(account, extra)

    return _load_legacy_json()


__all__ = [
    "load_email_config",
    "LEGACY_CONFIG_PATH",
]
