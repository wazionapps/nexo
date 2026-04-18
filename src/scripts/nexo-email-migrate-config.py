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
    from db._email_accounts import add_email_account, get_email_account

    init_db()

    existing = get_email_account(args.label)
    if existing and not args.force:
        print(
            f"(cuenta '{args.label}' ya existe — email={existing.get('email')}, "
            f"sin cambios). Usa --force para sobrescribir."
        )
        return 0

    email = legacy.get("email", "")
    password = legacy.get("password", "")
    imap_host = legacy.get("imap_host", "")
    imap_port = int(legacy.get("imap_port") or 993)
    smtp_host = legacy.get("smtp_host", imap_host)
    smtp_port = int(legacy.get("smtp_port") or 465)
    operator_email = legacy.get("operator_email", "")
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
        "operator_aliases": francisco,
    }

    cred_service = "email"
    cred_key = args.label

    if args.dry_run:
        print(f"[dry-run] add_email_account(label={args.label}, email={email}, "
              f"imap={imap_host}:{imap_port}, smtp={smtp_host}:{smtp_port}, "
              f"role=both, operator={operator_email}, trusted={trusted})")
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
    )

    print(f"✓ Cuenta '{args.label}' migrada ({account.get('email')}).")
    print(f"  Password guardada en credentials[{cred_service}/{cred_key}].")
    print(f"  Metadata: operator_aliases={len(francisco)}, trusted_domains={len(trusted)}.")
    print(f"  Legacy JSON intacto en {LEGACY_PATH} (borrarlo tras verificar con `nexo email test {args.label}`).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
