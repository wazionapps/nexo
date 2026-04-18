"""Plan Consolidado F1 — interactive `nexo email` subcommands.

Designed for operators who will NEVER open a JSON file. Everything
prompts, confirms, and shows green checks / red errors. Fresh install:
`nexo email setup` is the first thing the operator runs after `nexo init`.

Subcommands:
    nexo email setup           interactive wizard (primary account)
    nexo email list            show all accounts, masked password
    nexo email test <label>    IMAP + SMTP connectivity probe
    nexo email remove <label>  remove account + its credential
    nexo email set-operator    pick which email gets the morning digest
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
            print(f"  ✗ '{raw}' no es un número. Prueba otra vez.")


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
        print("  ✗ Responde y o n.")


def _mask_password(pw: str) -> str:
    if not pw:
        return "(vacío)"
    if len(pw) <= 4:
        return "•" * len(pw)
    return pw[0] + "•" * (len(pw) - 2) + pw[-1]


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
    """Interactive wizard. Fresh install: operator runs this once."""
    print("━" * 60)
    print("NEXO · Asistente de configuración de email")
    print("━" * 60)
    print("Te voy a preguntar los datos de la cuenta de correo que")
    print("NEXO usará para leer y contestar. Si te equivocas, vuelve")
    print("a ejecutar `nexo email setup` en cualquier momento.\n")

    from db import init_db
    from db._email_accounts import add_email_account, get_email_account

    init_db()

    label = _prompt("Etiqueta de la cuenta (ej: 'primary', 'wazion')", "primary")

    existing = get_email_account(label)
    if existing:
        if not _prompt_yes_no(
            f"Ya existe una cuenta '{label}' ({existing.get('email')}). ¿La sobrescribo?",
            default=False,
        ):
            print("Cancelado.")
            return 1

    email = _prompt("Dirección email (ej: nexo@tudominio.com)")
    if not email or "@" not in email:
        print(f"  ✗ '{email}' no parece un email válido.")
        return 1

    imap_host = _prompt("Servidor IMAP (entrada)", "imap.gmail.com")
    imap_port = _prompt_int("Puerto IMAP", 993)
    smtp_host = _prompt("Servidor SMTP (salida)", imap_host.replace("imap", "smtp"))
    smtp_port = _prompt_int("Puerto SMTP", 465)

    try:
        pwd = getpass.getpass("Contraseña (no se mostrará): ")
    except (EOFError, KeyboardInterrupt):
        print("\n(cancelado)")
        return 1
    if not pwd:
        print("  ✗ Necesito una contraseña.")
        return 1

    operator_email = _prompt(
        "Email donde NEXO te enviará el briefing matinal (tu email personal)",
        email,
    )

    trusted_raw = _prompt(
        "Dominios de confianza separados por coma (puedes dejar vacío)",
        "",
    )
    trusted = [d.strip() for d in trusted_raw.split(",") if d.strip()]

    role = _prompt(
        "Rol de la cuenta: inbox (solo leer) / outbox (solo enviar) / both",
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
    )

    print()
    print("✓ Cuenta guardada:")
    print(f"  label:          {account.get('label')}")
    print(f"  email:          {account.get('email')}")
    print(f"  IMAP:           {account.get('imap_host')}:{account.get('imap_port')}")
    print(f"  SMTP:           {account.get('smtp_host')}:{account.get('smtp_port')}")
    print(f"  operator_email: {account.get('operator_email')}")
    print(f"  trusted:        {account.get('trusted_domains') or '(ninguno)'}")
    print(f"  role:           {account.get('role')}")
    print(f"  password:       {_mask_password(pwd)} (guardada en credentials)")
    print()
    if _prompt_yes_no("¿Pruebo la conexión ahora?", default=True):
        return cmd_email_test(type("Args", (), {"label": label})())
    print("Puedes probarla cuando quieras con: nexo email test " + label)
    return 0


def cmd_email_list(args) -> int:
    from db import init_db
    from db._email_accounts import list_email_accounts

    init_db()
    accounts = list_email_accounts(include_disabled=True)
    if not accounts:
        print("(sin cuentas configuradas — corre `nexo email setup`)")
        return 0
    print(f"{'LABEL':<16} {'EMAIL':<40} {'ROLE':<8} {'ENABLED':<8} IMAP")
    for a in accounts:
        print(
            f"{a.get('label',''):<16} {a.get('email',''):<40} "
            f"{a.get('role',''):<8} "
            f"{'✓' if a.get('enabled') else '✗':<8} "
            f"{a.get('imap_host','')}:{a.get('imap_port','')}"
        )
    return 0


def cmd_email_test(args) -> int:
    label = getattr(args, "label", None)
    if not label:
        print("usage: nexo email test <label>")
        return 1
    from db import init_db
    from email_config import load_email_config

    init_db()
    cfg = load_email_config(label=label)
    if cfg is None:
        print(f"✗ Cuenta '{label}' no encontrada.")
        return 1

    ok_imap = False
    ok_smtp = False
    try:
        import imaplib
        imap = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"])
        imap.login(cfg["email"], cfg["password"])
        imap.logout()
        ok_imap = True
        print(f"✓ IMAP {cfg['imap_host']}:{cfg['imap_port']} login OK")
    except Exception as exc:
        print(f"✗ IMAP {cfg['imap_host']}:{cfg['imap_port']} FAILED: {exc}")

    try:
        import smtplib
        smtp = smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], timeout=15)
        smtp.login(cfg["email"], cfg["password"])
        smtp.quit()
        ok_smtp = True
        print(f"✓ SMTP {cfg['smtp_host']}:{cfg['smtp_port']} login OK")
    except Exception as exc:
        print(f"✗ SMTP {cfg['smtp_host']}:{cfg['smtp_port']} FAILED: {exc}")

    return 0 if (ok_imap and ok_smtp) else 1


def cmd_email_remove(args) -> int:
    label = getattr(args, "label", None)
    if not label:
        print("usage: nexo email remove <label>")
        return 1
    from db import init_db
    from db._email_accounts import get_email_account, remove_email_account

    init_db()
    acc = get_email_account(label)
    if not acc:
        print(f"✗ Cuenta '{label}' no encontrada.")
        return 1
    if not _prompt_yes_no(f"¿Eliminar la cuenta '{label}' ({acc.get('email')})?", default=False):
        print("Cancelado.")
        return 0
    _delete_credential(acc.get("credential_service", ""), acc.get("credential_key", ""))
    remove_email_account(label)
    print(f"✓ Cuenta '{label}' eliminada.")
    return 0


def register_email_parser(subparsers) -> None:
    """Hook called by cli.py to add the `email` subcommand tree."""
    p = subparsers.add_parser("email", help="Gestionar cuentas de correo NEXO")
    p.set_defaults(func=lambda a: p.print_help() or 0)
    sub = p.add_subparsers(dest="email_action")

    s = sub.add_parser("setup", help="Asistente interactivo para añadir / reconfigurar una cuenta")
    s.set_defaults(func=cmd_email_setup)

    s = sub.add_parser("list", help="Listar cuentas configuradas")
    s.set_defaults(func=cmd_email_list)

    s = sub.add_parser("test", help="Probar IMAP + SMTP de una cuenta")
    s.add_argument("label")
    s.set_defaults(func=cmd_email_test)

    s = sub.add_parser("remove", help="Eliminar una cuenta")
    s.add_argument("label")
    s.set_defaults(func=cmd_email_remove)


__all__ = [
    "cmd_email_setup",
    "cmd_email_list",
    "cmd_email_test",
    "cmd_email_remove",
    "register_email_parser",
]
