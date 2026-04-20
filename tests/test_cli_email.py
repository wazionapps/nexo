from __future__ import annotations

import argparse
import inspect
import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def test_cli_email_source_no_longer_contains_spanish_operator_ui_strings():
    import cli_email

    source = inspect.getsource(cli_email)
    forbidden = [
        "Asistente de configuración de email",
        "Ya existe una cuenta",
        "Contraseña (no se mostrará)",
        "¿Pruebo la conexión ahora?",
        "Cuenta guardada",
        "Cuenta '",
        "(cancelado)",
        "Gestionar cuentas de correo NEXO",
        "Activar una cuenta sin borrarla",
        "Desactivar una cuenta sin borrarla",
    ]
    for token in forbidden:
        assert token not in source


def test_cli_email_parser_help_uses_english_copy():
    import cli_email

    parser = argparse.ArgumentParser(prog="nexo")
    subparsers = parser.add_subparsers(dest="command")
    cli_email.register_email_parser(subparsers)
    help_text = parser.format_help()

    assert "Manage NEXO email accounts" in help_text
    assert "Gestionar cuentas de correo NEXO" not in help_text


def test_cli_email_mask_password_uses_neutral_empty_label():
    import cli_email

    assert cli_email._mask_password("") == "(empty)"
    assert cli_email._mask_password("abcd") == "\u2022\u2022\u2022\u2022"


def test_account_to_public_dict_exposes_legacy_migration_flag_for_desktop():
    import cli_email

    payload = cli_email._account_to_public_dict({
        "id": 7,
        "label": "operator-owner",
        "email": "owner@example.com",
        "description": "Operator alias",
        "metadata": {"migrated_from_legacy_email_config": True},
    })

    assert payload["id"] == 7
    assert payload["legacy_migrated"] is True
    assert payload["description_source"] == "legacy_migration"
