#!/usr/bin/env python3
# nexo: name=nexo-email-migrate-config
# nexo: description=One-shot migrator: copy ~/.nexo/nexo-email/config.json into the email_accounts table (F1).
# nexo: category=automation
# nexo: runtime=python
# nexo: timeout=30
# nexo: idempotent=true

"""Plan Consolidado F1 — one-shot migration.

Reads ~/.nexo/nexo-email/config.json (v6.3.x legacy single-tenant file)
and inserts it into the `email_accounts` table under label 'primary'.
Password lands in the `credentials` table under service='email'/key='primary'.

Idempotent: if a 'primary' row already exists, we skip (no overwrite).
Callers can re-run with --force to overwrite.

Runs automatically from auto_update.py the first time the operator
updates to v6.4.0 or later. Can also be invoked manually.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path


NEXO_HOME = Path(os.environ.get("NEXO_HOME") or (Path.home() / ".nexo"))
CODE_ROOT = Path(os.environ.get("NEXO_CODE") or (Path(__file__).resolve().parents[1]))
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

LEGACY_PATH = NEXO_HOME / "nexo-email" / "config.json"


def _load_legacy() -> dict | None:
    if not LEGACY_PATH.exists():
        return None
    try:
        return json.loads(LEGACY_PATH.read_text())
    except Exception as exc:
        print(f"✗ legacy config unparseable: {exc}", file=sys.stderr)
        return None


def _operator_label(email: str, *, index: int) -> str:
    local, _, domain = email.partition("@")
    seed = f"{local.strip().lower()}-{domain.strip().lower()}".strip("-")
    clean = re.sub(r"[^a-z0-9]+", "-", seed).strip("-")
    suffix = clean or f"inbox-{index}"
    return f"operator-{suffix}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--label", default="primary")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    legacy = _load_legacy()
    if legacy is None:
        print(f"(nada que migrar — {LEGACY_PATH} no existe)")
        return 0

    from db import init_db
    from db._email_accounts import add_email_account, get_email_account, list_email_accounts

    init_db()

    existing = get_email_account(args.label)

    email = legacy.get("email", "")
    password = legacy.get("password", "")
    imap_host = legacy.get("imap_host", "")
    imap_port = int(legacy.get("imap_port") or 993)
    smtp_host = legacy.get("smtp_host", imap_host)
    smtp_port = int(legacy.get("smtp_port") or 465)
    operator_email = legacy.get("operator_email", "")
    escalation_email = legacy.get("escalation_email", "")
    trusted = legacy.get("trusted_domains", []) or []
    francisco = legacy.get("francisco_emails", []) or []

    metadata = {
        "sender_policy": legacy.get("sender_policy", "open"),
        "check_interval_seconds": legacy.get("check_interval_seconds", 60),
        "max_retries": legacy.get("max_retries", 3),
        "retry_backoff_seconds": legacy.get("retry_backoff_seconds", 60),
        "claude_binary": legacy.get("claude_binary", ""),
        "working_dir": legacy.get("working_dir", str(Path.home())),
        "automation_task_profile": legacy.get("automation_task_profile", "deep"),
        "max_process_time": legacy.get("max_process_time"),
        "sent_folder": legacy.get("sent_folder", "INBOX.Sent"),
        "operator_aliases": francisco,
    }

    cred_service = "email"
    cred_key = args.label

    if args.dry_run:
        print(f"[dry-run] add_email_account(label={args.label}, email={email}, "
              f"imap={imap_host}:{imap_port}, smtp={smtp_host}:{smtp_port}, "
              f"role=both, account_type=agent, operator={operator_email}, trusted={trusted})")
        print(f"[dry-run] credentials[{cred_service}/{cred_key}] = <password>")
        return 0

    # Store credential
    from db._core import get_db
    conn = get_db()
    now = time.time()
    conn.execute(
        """
        INSERT INTO credentials (service, key, value, notes, created_at, updated_at)
        VALUES (?, ?, ?, 'migrated from ~/.nexo/nexo-email/config.json (F1)', ?, ?)
        ON CONFLICT(service, key) DO UPDATE SET
            value = excluded.value, updated_at = excluded.updated_at
        """,
        (cred_service, cred_key, password, now, now),
    )
    conn.commit()

    if existing and not args.force:
        existing_metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
        merged_aliases: list[str] = []
        for candidate in [*(existing_metadata.get("operator_aliases") or []), *francisco]:
            value = str(candidate or "").strip().lower()
            if value and value not in merged_aliases:
                merged_aliases.append(value)
        merged_metadata = {
            **metadata,
            **existing_metadata,
            "operator_aliases": merged_aliases,
            "sent_folder": str(existing_metadata.get("sent_folder") or metadata.get("sent_folder") or "INBOX.Sent"),
        }
        normalized_role = str(existing.get("role") or "both")
        account = add_email_account(
            label=args.label,
            email=str(existing.get("email") or email),
            imap_host=str(existing.get("imap_host") or imap_host),
            imap_port=int(existing.get("imap_port") or imap_port),
            smtp_host=str(existing.get("smtp_host") or smtp_host),
            smtp_port=int(existing.get("smtp_port") or smtp_port),
            credential_service=str(existing.get("credential_service") or cred_service),
            credential_key=str(existing.get("credential_key") or cred_key),
            operator_email=str(existing.get("operator_email") or operator_email),
            trusted_domains=list(existing.get("trusted_domains") or trusted),
            role=normalized_role,
            enabled=bool(existing.get("enabled", True)),
            metadata=merged_metadata,
            account_type="agent",
            description=str(existing.get("description") or "Agent mailbox"),
            can_read=normalized_role in ("inbox", "both"),
            can_send=normalized_role in ("outbox", "both"),
            is_default=False,
        )
        print(
            f"(cuenta '{args.label}' ya existe — email={account.get('email')}, "
            "normalizo el contrato agente y sólo completo buzones del operador faltantes)."
        )
    else:
        account = add_email_account(
            label=args.label,
            email=email,
            imap_host=imap_host,
            imap_port=imap_port,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            credential_service=cred_service,
            credential_key=cred_key,
            operator_email=operator_email,
            trusted_domains=trusted,
            role="both",
            metadata=metadata,
            account_type="agent",
            description="Agent mailbox (migrated from legacy config)",
            can_read=True,
            can_send=True,
        )
        print(f"✓ Cuenta agente '{args.label}' migrada ({account.get('email')}).")
        print(f"  Password guardada en credentials[{cred_service}/{cred_key}].")
        print(f"  Metadata: operator_aliases={len(francisco)}, trusted_domains={len(trusted)}.")

    existing_operator_rows = list_email_accounts(include_disabled=True, account_type="operator")
    existing_operator_emails = {
        str(row.get("email") or "").strip().lower() for row in existing_operator_rows if row.get("email")
    }
    operator_candidates: list[str] = []
    for candidate in [operator_email, escalation_email, *francisco]:
        value = str(candidate or "").strip().lower()
        if value and value not in operator_candidates:
            operator_candidates.append(value)
    created_operator_rows = 0
    default_email = escalation_email or operator_email
    for index, candidate in enumerate(operator_candidates, start=1):
        if candidate in existing_operator_emails:
            continue
        is_default = bool(default_email and candidate == default_email.lower())
        if candidate == str(operator_email or "").strip().lower() and is_default:
            description = "Operator primary / default escalation email (migrated from legacy config)"
        elif candidate == str(operator_email or "").strip().lower():
            description = "Operator primary email (migrated from legacy config)"
        elif candidate == str(escalation_email or "").strip().lower():
            description = "Default escalation email (migrated from legacy config)"
        else:
            description = "Operator alias (migrated from legacy config)"
        add_email_account(
            label=_operator_label(candidate, index=index),
            email=candidate,
            role="both",
            enabled=True,
            account_type="operator",
            description=description,
            can_read=False,
            can_send=False,
            is_default=is_default,
            metadata={"migrated_from_legacy_email_config": True},
        )
        created_operator_rows += 1

    print(f"  Operator inboxes creados: {created_operator_rows}.")
    print(f"  Legacy JSON intacto en {LEGACY_PATH} (borrarlo tras verificar con `nexo email test {args.label}`).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
