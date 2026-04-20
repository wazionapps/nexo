"""Plan Consolidado F1 — `nexo email` subcommands.

Two consumers, one CLI:

1. Operators on a terminal — friendly interactive wizard with prompts,
   confirmations, and green checks / red errors.
2. NEXO Desktop renderer (Plan F1 panel) — every command also accepts
   `--json` for machine-readable I/O and a `--password-stdin` flag to
   accept secrets without leaking them on argv.

Subcommands:
    nexo email setup                interactive wizard (primary account)
    nexo email add ...              non-interactive (Desktop / scripts)
    nexo email list [--json]        show all accounts, masked password
    nexo email test <label> [--json]   IMAP + SMTP connectivity probe
    nexo email remove <label> [--yes] [--json]  remove account + cred
"""

from __future__ import annotations

import getpass
import json
import sys
import time
from typing import Any


def _prompt(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"{msg}{suffix}: ")
    except (EOFError, KeyboardInterrupt):
        print("\n(cancelled)")
        sys.exit(1)
    return raw.strip() or default


def _prompt_int(msg: str, default: int) -> int:
    while True:
        raw = _prompt(msg, str(default))
        try:
            return int(raw)
        except ValueError:
            print(f"  ✗ '{raw}' is not a valid number. Try again.")


def _prompt_yes_no(msg: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    while True:
        raw = _prompt(f"{msg} [{d}]").lower()
        if not raw:
            return default
        if raw in ("y", "yes", "s", "si", "sí"):
            return True
        if raw in ("n", "no"):
            return False
        print("  ✗ Answer with y or n.")


def _mask_password(pw: str) -> str:
    if not pw:
        return "(empty)"
    if len(pw) <= 4:
        return "•" * len(pw)
    return pw[0] + "•" * (len(pw) - 2) + pw[-1]


def _sent_folder_from_account(account: dict | None) -> str:
    metadata = {}
    if isinstance(account, dict) and isinstance(account.get("metadata"), dict):
        metadata = account.get("metadata") or {}
    value = str(metadata.get("sent_folder") or "").strip()
    return value or "INBOX.Sent"


def _store_credential(service: str, key: str, value: str) -> None:
    """Write password to the `credentials` table (simple cleartext by
    default — upgrading to keychain is a v7 follow-up). Never echo the
    password back to stdout."""
    from db._core import get_db
    conn = get_db()
    now = time.time()
    conn.execute(
        """
        INSERT INTO credentials (service, key, value, notes, created_at, updated_at)
        VALUES (?, ?, ?, 'email account password (nexo email setup)', ?, ?)
        ON CONFLICT(service, key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (service, key, value, now, now),
    )
    conn.commit()


def _delete_credential(service: str, key: str) -> None:
    from db._core import get_db
    conn = get_db()
    conn.execute("DELETE FROM credentials WHERE service = ? AND key = ?", (service, key))
    conn.commit()


def cmd_email_setup(args) -> int:
    """Interactive wizard for the primary agent mailbox."""
    print("━" * 60)
    print("NEXO · Email setup wizard")
    print("━" * 60)
    print("I will ask for the mailbox details NEXO should use to")
    print("read and reply. If you make a mistake, just run")
    print("`nexo email setup` again at any time.\n")

    from db import init_db
    from db._email_accounts import add_email_account, get_email_account

    init_db()

    label = _prompt("Account label (example: 'primary', 'wazion')", "primary")

    existing = get_email_account(label)
    if existing:
        if not _prompt_yes_no(
            f"An account named '{label}' already exists ({existing.get('email')}). Overwrite it?",
            default=False,
        ):
            print("Cancelled.")
            return 1

    email = _prompt("Email address (example: nexo@yourdomain.com)")
    if not email or "@" not in email:
        print(f"  ✗ '{email}' does not look like a valid email address.")
        return 1

    imap_host = _prompt("IMAP server (incoming mail)", "imap.gmail.com")
    imap_port = _prompt_int("IMAP port", 993)
    smtp_host = _prompt("SMTP server (outgoing mail)", imap_host.replace("imap", "smtp"))
    smtp_port = _prompt_int("SMTP port", 465)

    try:
        pwd = getpass.getpass("Password (hidden input): ")
    except (EOFError, KeyboardInterrupt):
        print("\n(cancelled)")
        return 1
    if not pwd:
        print("  ✗ A password is required.")
        return 1

    operator_email = _prompt(
        "Operator email for the daily briefing",
        email,
    )

    trusted_raw = _prompt(
        "Trusted domains (comma-separated, optional)",
        "",
    )
    trusted = [d.strip() for d in trusted_raw.split(",") if d.strip()]
    sent_folder = _prompt("IMAP sent folder", "INBOX.Sent").strip() or "INBOX.Sent"

    role = _prompt(
        "Account role: inbox (read only) / outbox (send only) / both",
        "both",
    )
    if role not in ("inbox", "outbox", "both"):
        role = "both"

    cred_service = "email"
    cred_key = label
    _store_credential(cred_service, cred_key, pwd)

    account = add_email_account(
        label=label,
        email=email,
        imap_host=imap_host,
        imap_port=imap_port,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        credential_service=cred_service,
        credential_key=cred_key,
        operator_email=operator_email,
        trusted_domains=trusted,
        role=role,
        account_type="agent",
        description="Agent mailbox",
        can_read=role in ("inbox", "both"),
        can_send=role in ("outbox", "both"),
        is_default=False,
        metadata={"sent_folder": sent_folder},
    )

    print()
    print("✓ Account saved:")
    print(f"  label:          {account.get('label')}")
    print(f"  email:          {account.get('email')}")
    print(f"  IMAP:           {account.get('imap_host')}:{account.get('imap_port')}")
    print(f"  SMTP:           {account.get('smtp_host')}:{account.get('smtp_port')}")
    print(f"  operator_email: {account.get('operator_email')}")
    print(f"  trusted:        {account.get('trusted_domains') or '(none)'}")
    print(f"  role:           {account.get('role')}")
    print(f"  sent_folder:    {_sent_folder_from_account(account)}")
    print(f"  password:       {_mask_password(pwd)} (stored in credentials)")
    print()
    if _prompt_yes_no("Test the connection now?", default=True):
        return cmd_email_test(type("Args", (), {"label": label})())
    print("You can test it later with: nexo email test " + label)
    return 0


def _emit_json(payload: dict) -> None:
    """Print a JSON payload on stdout. Used so machine consumers
    (NEXO Desktop renderer) can parse cleanly; the human path keeps
    its rich text output."""
    print(json.dumps(payload, ensure_ascii=False))


def _account_to_public_dict(account: dict) -> dict:
    """Return the operator-safe view of an email account row.
    NEVER includes the password; only flags whether a credential is
    stored so the UI can show a 'no password yet' marker."""
    if not account:
        return {}
    metadata = account.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    legacy_migrated = bool(metadata.get("migrated_from_legacy_email_config"))
    return {
        "id": account.get("id"),
        "label": account.get("label"),
        "email": account.get("email"),
        "account_type": account.get("account_type", "agent"),
        "description": account.get("description", ""),
        "description_source": "legacy_migration" if legacy_migrated else "",
        "legacy_migrated": legacy_migrated,
        "imap_host": account.get("imap_host"),
        "imap_port": account.get("imap_port"),
        "smtp_host": account.get("smtp_host"),
        "smtp_port": account.get("smtp_port"),
        "sent_folder": _sent_folder_from_account(account),
        "operator_email": account.get("operator_email"),
        "trusted_domains": account.get("trusted_domains") or [],
        "role": account.get("role", "both"),
        "enabled": bool(account.get("enabled", True)),
        "can_read": bool(account.get("can_read")),
        "can_send": bool(account.get("can_send")),
        "is_default": bool(account.get("is_default")),
        "has_credential": bool(account.get("credential_service")
                               and account.get("credential_key")),
    }


def _selector_from_args(args) -> tuple[int | None, str]:
    raw_id = getattr(args, "account_id", None)
    label = str(getattr(args, "label", None) or getattr(args, "label_pos", None) or "").strip()
    try:
        account_id = int(raw_id) if raw_id not in (None, "") else None
    except Exception:
        account_id = None
    if account_id is not None and account_id <= 0:
        account_id = None
    return account_id, label


def _selector_usage(command: str) -> str:
    return f"usage: nexo email {command} <label> [--id ACCOUNT_ID]"


def cmd_email_list(args) -> int:
    from db import init_db
    from db._email_accounts import list_email_accounts

    init_db()
    accounts = list_email_accounts(include_disabled=True)
    if getattr(args, "json", False):
        _emit_json({
            "ok": True,
            "accounts": [_account_to_public_dict(a) for a in accounts],
        })
        return 0
    if not accounts:
        print("(no accounts configured — run `nexo email setup`)")
        return 0
    print(f"{'LABEL':<18} {'TYPE':<9} {'EMAIL':<34} {'PERMS':<7} {'DEF':<4} IMAP")
    for a in accounts:
        perms = []
        if a.get("can_read"):
            perms.append("R")
        if a.get("can_send"):
            perms.append("S")
        print(
            f"{a.get('label',''):<18} "
            f"{a.get('account_type','agent'):<9} "
            f"{a.get('email',''):<34} "
            f"{(''.join(perms) or '-'): <7}"
            f"{'✓' if a.get('is_default') else '-':<4} "
            f"{a.get('imap_host','')}:{a.get('imap_port','')}"
        )
    return 0


def _resolve_permissions_and_role(
    *,
    account_type: str,
    role: str,
    can_read: bool | None,
    can_send: bool | None,
) -> tuple[bool, bool, str]:
    if account_type == "agent":
        resolved_read = role in ("inbox", "both") if can_read is None else bool(can_read)
        resolved_send = role in ("outbox", "both") if can_send is None else bool(can_send)
        return resolved_read, resolved_send, role

    resolved_read = role in ("inbox", "both") if can_read is None else bool(can_read)
    resolved_send = role in ("outbox", "both") if can_send is None else bool(can_send)
    if resolved_read and resolved_send:
        resolved_role = "both"
    elif resolved_read:
        resolved_role = "inbox"
    elif resolved_send:
        resolved_role = "outbox"
    else:
        resolved_role = "both"
    return resolved_read, resolved_send, resolved_role


def cmd_email_add(args) -> int:
    """Non-interactive add. Used by the Desktop email panel and any
    script. Password is read from stdin when ``--password-stdin`` is
    set (so it never appears on argv / ps output)."""
    json_mode = getattr(args, "json", False)
    label = (getattr(args, "label", None) or "").strip()
    email = (getattr(args, "email", None) or "").strip()
    imap_host = (getattr(args, "imap_host", None) or "").strip()
    smtp_host = (getattr(args, "smtp_host", None) or "").strip()
    if not (label and email):
        msg = "missing required field (--label, --email)"
        if json_mode:
            _emit_json({"ok": False, "message": msg})
        else:
            print(f"✗ {msg}")
        return 1
    if "@" not in email:
        msg = f"'{email}' does not look like a valid email address."
        if json_mode:
            _emit_json({"ok": False, "message": msg})
        else:
            print(f"✗ {msg}")
        return 1
    imap_port = int(getattr(args, "imap_port", None) or 993)
    smtp_port = int(getattr(args, "smtp_port", None) or 465)
    role = (getattr(args, "role", None) or "both").strip()
    if role not in ("inbox", "outbox", "both"):
        role = "both"
    account_type = (getattr(args, "account_type", None) or "agent").strip().lower()
    if account_type not in ("agent", "operator"):
        account_type = "agent"
    description = (getattr(args, "description", None) or "").strip()
    can_read_flag = getattr(args, "can_read", None)
    can_send_flag = getattr(args, "can_send", None)
    operator_email = (getattr(args, "operator", None) or "").strip()
    trusted_raw = (getattr(args, "trusted_domains", None) or "").strip()
    trusted = [d.strip() for d in trusted_raw.split(",") if d.strip()] if trusted_raw else []
    sent_folder_arg = getattr(args, "sent_folder", None)

    from db import init_db
    from db._email_accounts import add_email_account, get_email_account

    init_db()
    existing = get_email_account(label)
    is_default_arg = getattr(args, "is_default", None)
    is_default = (
        bool(existing.get("is_default")) if (account_type == "operator" and is_default_arg is None and existing)
        else bool(is_default_arg) if account_type == "operator"
        else False
    )
    can_read, can_send, role = _resolve_permissions_and_role(
        account_type=account_type,
        role=role,
        can_read=can_read_flag,
        can_send=can_send_flag,
    )
    if (account_type == "agent" or can_read) and not imap_host:
        msg = "missing required field (--imap-host)"
        if json_mode:
            _emit_json({"ok": False, "message": msg})
        else:
            print(f"✗ {msg}")
        return 1
    if (account_type == "agent" or can_send) and not smtp_host:
        msg = "missing required field (--smtp-host)"
        if json_mode:
            _emit_json({"ok": False, "message": msg})
        else:
            print(f"✗ {msg}")
        return 1

    if getattr(args, "password_stdin", False):
        try:
            pwd = sys.stdin.read()
        except Exception as exc:
            msg = f"could not read password from stdin: {exc}"
            if json_mode:
                _emit_json({"ok": False, "message": msg})
            else:
                print(f"✗ {msg}")
            return 1
        # Trim a single trailing newline so the operator can pipe with `echo`,
        # but preserve internal whitespace / leading spaces (rare but legal).
        if pwd.endswith("\n"):
            pwd = pwd[:-1]
        if pwd.endswith("\r"):
            pwd = pwd[:-1]
    else:
        pwd = getattr(args, "password", None) or ""
    needs_runtime_credentials = account_type == "agent" or can_read or can_send
    if needs_runtime_credentials and not pwd and not (
        existing and existing.get("credential_service") and existing.get("credential_key")
    ):
        msg = "missing password (use --password-stdin or --password, or edit an account with an existing saved credential)"
        if json_mode:
            _emit_json({"ok": False, "message": msg})
        else:
            print(f"✗ {msg}")
        return 1

    if existing and existing.get("credential_service") and existing.get("credential_key"):
        cred_service = str(existing.get("credential_service") or "").strip()
        cred_key = str(existing.get("credential_key") or "").strip()
    elif pwd:
        cred_service = "email"
        cred_key = label
    else:
        cred_service = ""
        cred_key = ""
    if pwd:
        _store_credential(cred_service, cred_key, pwd)
    metadata = None
    if existing and isinstance(existing.get("metadata"), dict):
        metadata = dict(existing.get("metadata") or {})
    if sent_folder_arg is not None:
        if metadata is None:
            metadata = {}
        clean_sent_folder = str(sent_folder_arg).strip()
        if clean_sent_folder:
            metadata["sent_folder"] = clean_sent_folder
        else:
            metadata.pop("sent_folder", None)
    account = add_email_account(
        label=label,
        email=email,
        imap_host=imap_host,
        imap_port=imap_port,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        credential_service=cred_service,
        credential_key=cred_key,
        operator_email=operator_email,
        trusted_domains=trusted,
        role=role,
        account_type=account_type,
        description=description,
        can_read=can_read,
        can_send=can_send,
        is_default=is_default,
        metadata=metadata,
    )
    public = _account_to_public_dict(account)
    if json_mode:
        _emit_json({"ok": True, "account": public})
    else:
        print(f"✓ Account '{label}' saved.")
    return 0


def cmd_email_test(args) -> int:
    json_mode = getattr(args, "json", False)
    account_id, label = _selector_from_args(args)
    if account_id is None and not label:
        msg = _selector_usage("test")
        if json_mode:
            _emit_json({"ok": False, "message": msg})
        else:
            print(msg)
        return 1
    from db import init_db
    from email_config import load_email_config

    init_db()
    cfg = load_email_config(label=label or None, account_id=account_id)
    if cfg is None:
        selector = f"id={account_id}" if account_id is not None else label
        msg = f"Account '{selector}' not found."
        if json_mode:
            _emit_json({"ok": False, "message": msg})
        else:
            print(f"✗ {msg}")
        return 1

    ok_imap = False
    err_imap = ""
    ok_smtp = False
    err_smtp = ""
    try:
        import imaplib
        imap = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"])
        imap.login(cfg["email"], cfg["password"])
        imap.logout()
        ok_imap = True
    except Exception as exc:
        err_imap = str(exc)

    try:
        import smtplib
        smtp = smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], timeout=15)
        smtp.login(cfg["email"], cfg["password"])
        smtp.quit()
        ok_smtp = True
    except Exception as exc:
        err_smtp = str(exc)

    if json_mode:
        _emit_json({
            "ok": ok_imap and ok_smtp,
            "id": cfg.get("id"),
            "label": cfg.get("label") or label,
            "imap": {"ok": ok_imap, "host": cfg["imap_host"], "port": cfg["imap_port"], "error": err_imap},
            "smtp": {"ok": ok_smtp, "host": cfg["smtp_host"], "port": cfg["smtp_port"], "error": err_smtp},
            "message": "Login OK" if (ok_imap and ok_smtp) else (err_imap or err_smtp or "test failed"),
        })
    else:
        if ok_imap:
            print(f"✓ IMAP {cfg['imap_host']}:{cfg['imap_port']} login OK")
        else:
            print(f"✗ IMAP {cfg['imap_host']}:{cfg['imap_port']} FAILED: {err_imap}")
        if ok_smtp:
            print(f"✓ SMTP {cfg['smtp_host']}:{cfg['smtp_port']} login OK")
        else:
            print(f"✗ SMTP {cfg['smtp_host']}:{cfg['smtp_port']} FAILED: {err_smtp}")
    return 0 if (ok_imap and ok_smtp) else 1


def cmd_email_remove(args) -> int:
    json_mode = getattr(args, "json", False)
    account_id, label = _selector_from_args(args)
    if account_id is None and not label:
        msg = _selector_usage("remove")
        if json_mode:
            _emit_json({"ok": False, "message": msg})
        else:
            print(msg)
        return 1
    from db import init_db
    from db._email_accounts import get_email_account, get_email_account_by_id, remove_email_account

    init_db()
    acc = get_email_account_by_id(account_id) if account_id is not None else get_email_account(label)
    if not acc:
        selector = f"id={account_id}" if account_id is not None else label
        msg = f"Account '{selector}' not found."
        if json_mode:
            _emit_json({"ok": False, "message": msg})
        else:
            print(f"✗ {msg}")
        return 1
    if not getattr(args, "yes", False):
        if json_mode:
            _emit_json({"ok": False, "message": "missing --yes (interactive confirmation required)"})
            return 1
        if not _prompt_yes_no(f"Delete account '{label}' ({acc.get('email')})?", default=False):
            print("Cancelled.")
            return 0
    _delete_credential(acc.get("credential_service", ""), acc.get("credential_key", ""))
    remove_email_account(account_id=acc.get("id"))
    if json_mode:
        _emit_json({"ok": True, "id": acc.get("id"), "label": acc.get("label"), "message": "removed"})
    else:
        print(f"✓ Account '{acc.get('label')}' removed.")
    return 0


def cmd_email_set_enabled(args) -> int:
    account_id, label = _selector_from_args(args)
    json_mode = bool(getattr(args, "json", False))
    enabled = bool(getattr(args, "enabled", True))
    if account_id is None and not label:
        msg = _selector_usage("enable|disable")
        if json_mode:
            _emit_json({"ok": False, "message": msg})
        else:
            print(msg)
        return 1
    from db import init_db
    from db._email_accounts import get_email_account, get_email_account_by_id, set_email_account_enabled

    init_db()
    acc = get_email_account_by_id(account_id) if account_id is not None else get_email_account(label)
    if not acc:
        selector = f"id={account_id}" if account_id is not None else label
        msg = f"Account '{selector}' not found."
        if json_mode:
            _emit_json({"ok": False, "message": msg})
        else:
            print(f"✗ {msg}")
        return 1
    changed = set_email_account_enabled(account_id=acc.get("id"), enabled=enabled)
    if not changed:
        msg = f"Could not update account '{acc.get('label')}'."
        if json_mode:
            _emit_json({"ok": False, "message": msg})
        else:
            print(f"✗ {msg}")
        return 1
    updated = get_email_account_by_id(acc.get("id")) or {}
    payload = {
        "ok": True,
        "id": updated.get("id"),
        "label": updated.get("label") or label,
        "enabled": bool(updated.get("enabled", enabled)),
        "message": "enabled" if enabled else "disabled",
    }
    if json_mode:
        _emit_json(payload)
    else:
        print(
            f"✓ Account '{payload['label']}' "
            + ("enabled." if payload["enabled"] else "disabled.")
        )
    return 0


def register_email_parser(subparsers) -> None:
    """Hook called by cli.py to add the `email` subcommand tree."""
    p = subparsers.add_parser("email", help="Manage NEXO email accounts")
    p.set_defaults(func=lambda a: p.print_help() or 0)
    sub = p.add_subparsers(dest="email_action")

    s = sub.add_parser("setup", help="Interactive wizard to add or reconfigure an account")
    s.set_defaults(func=cmd_email_setup)

    s = sub.add_parser("add", help="Add an account non-interactively (Desktop / scripts)")
    s.add_argument("--label", required=True)
    s.add_argument("--email", required=True)
    s.add_argument("--imap-host", dest="imap_host", default="")
    s.add_argument("--imap-port", dest="imap_port", type=int, default=993)
    s.add_argument("--smtp-host", dest="smtp_host", default="")
    s.add_argument("--smtp-port", dest="smtp_port", type=int, default=465)
    s.add_argument("--account-type", dest="account_type", default="agent",
                   choices=["agent", "operator"])
    s.add_argument("--description", dest="description", default="")
    s.add_argument("--operator", dest="operator", default="")
    s.add_argument("--trusted-domains", dest="trusted_domains", default="")
    s.add_argument("--sent-folder", dest="sent_folder", default=None,
                   help="IMAP folder where sent copies should be appended (default: INBOX.Sent).")
    s.add_argument("--role", dest="role", default="both", choices=["inbox", "outbox", "both"])
    read_group = s.add_mutually_exclusive_group()
    read_group.add_argument("--can-read", dest="can_read", action="store_true", default=None)
    read_group.add_argument("--no-can-read", dest="can_read", action="store_false")
    send_group = s.add_mutually_exclusive_group()
    send_group.add_argument("--can-send", dest="can_send", action="store_true", default=None)
    send_group.add_argument("--no-can-send", dest="can_send", action="store_false")
    s.add_argument("--default", dest="is_default", action="store_true", default=None,
                   help="Mark this operator inbox as the default fallback destination.")
    pwd_group = s.add_mutually_exclusive_group()
    pwd_group.add_argument("--password", dest="password",
                           help="Password on argv (NOT recommended; visible to ps).")
    pwd_group.add_argument("--password-stdin", dest="password_stdin", action="store_true",
                           help="Read password from stdin (recommended).")
    s.add_argument("--json", dest="json", action="store_true")
    s.set_defaults(func=cmd_email_add)

    s = sub.add_parser("list", help="List configured accounts")
    s.add_argument("--json", dest="json", action="store_true")
    s.set_defaults(func=cmd_email_list)

    s = sub.add_parser("test", help="Test IMAP + SMTP for an account")
    s.add_argument("label_pos", nargs="?", default=None,
                   help="Account label (legacy positional)")
    s.add_argument("--label", dest="label", default=None)
    s.add_argument("--id", dest="account_id", type=int, default=None)
    s.add_argument("--json", dest="json", action="store_true")
    s.set_defaults(func=cmd_email_test)

    s = sub.add_parser("remove", help="Remove an account")
    s.add_argument("label_pos", nargs="?", default=None,
                   help="Account label (legacy positional)")
    s.add_argument("--label", dest="label", default=None)
    s.add_argument("--id", dest="account_id", type=int, default=None)
    s.add_argument("--yes", dest="yes", action="store_true",
                   help="Skip the interactive confirmation (required for --json).")
    s.add_argument("--json", dest="json", action="store_true")
    s.set_defaults(func=cmd_email_remove)

    s = sub.add_parser("enable", help="Enable an account without deleting it")
    s.add_argument("label_pos", nargs="?", default=None,
                   help="Account label (legacy positional)")
    s.add_argument("--label", dest="label", default=None)
    s.add_argument("--id", dest="account_id", type=int, default=None)
    s.add_argument("--json", dest="json", action="store_true")
    s.set_defaults(func=cmd_email_set_enabled, enabled=True)

    s = sub.add_parser("disable", help="Disable an account without deleting it")
    s.add_argument("label_pos", nargs="?", default=None,
                   help="Account label (legacy positional)")
    s.add_argument("--label", dest="label", default=None)
    s.add_argument("--id", dest="account_id", type=int, default=None)
    s.add_argument("--json", dest="json", action="store_true")
    s.set_defaults(func=cmd_email_set_enabled, enabled=False)


__all__ = [
    "cmd_email_setup",
    "cmd_email_add",
    "cmd_email_list",
    "cmd_email_test",
    "cmd_email_remove",
    "cmd_email_set_enabled",
    "register_email_parser",
]
