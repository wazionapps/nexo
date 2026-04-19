"""Plan F1 — email_accounts CRUD + loader fallback."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


SRC = str(Path(__file__).resolve().parents[1] / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    (home / "data").mkdir(parents=True)
    (home / "nexo-email").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    # Invalidate any cached db module so DB_PATH picks up the new NEXO_HOME.
    for mod in list(sys.modules):
        if mod == "db" or mod.startswith("db."):
            sys.modules.pop(mod, None)
    from db import init_db
    init_db()
    yield home


def test_add_and_list(isolated_home):
    from db._email_accounts import add_email_account, list_email_accounts, get_email_account

    a = add_email_account(
        label="primary",
        email="me@example.com",
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        credential_service="email",
        credential_key="primary",
        operator_email="me@example.com",
        trusted_domains=["example.com"],
    )
    assert a["label"] == "primary"
    assert a["imap_port"] == 993

    rows = list_email_accounts()
    assert len(rows) == 1
    assert rows[0]["email"] == "me@example.com"

    fetched = get_email_account("primary")
    assert fetched["trusted_domains"] == ["example.com"]


def test_upsert_on_label(isolated_home):
    from db._email_accounts import add_email_account, list_email_accounts

    add_email_account(label="primary", email="a@x.com")
    add_email_account(label="primary", email="b@x.com")
    rows = list_email_accounts()
    assert len(rows) == 1
    assert rows[0]["email"] == "b@x.com"


def test_role_validated(isolated_home):
    from db._email_accounts import add_email_account

    with pytest.raises(ValueError):
        add_email_account(label="bad", email="x@x.com", role="wrong_role")


def test_remove(isolated_home):
    from db._email_accounts import add_email_account, remove_email_account, list_email_accounts

    add_email_account(label="tmp", email="x@x.com")
    assert len(list_email_accounts()) == 1
    assert remove_email_account("tmp") is True
    assert list_email_accounts() == []


def test_primary_picks_most_recent(isolated_home):
    import time as _time
    from db._email_accounts import (
        add_email_account,
        get_primary_email_account,
    )

    add_email_account(label="older", email="old@x.com")
    _time.sleep(1.1)
    add_email_account(label="newer", email="new@x.com")

    primary = get_primary_email_account()
    assert primary["label"] == "newer"


def test_loader_prefers_table_over_legacy_json(isolated_home):
    from db._email_accounts import add_email_account
    from db._core import get_db
    import time as _time

    conn = get_db()
    now = _time.time()
    conn.execute(
        "INSERT INTO credentials (service, key, value, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("email", "primary", "sekret-1", now, now),
    )
    conn.commit()
    add_email_account(
        label="primary",
        email="real@table.com",
        imap_host="imap.real.com",
        smtp_host="smtp.real.com",
        credential_service="email",
        credential_key="primary",
    )

    legacy = isolated_home / "nexo-email" / "config.json"
    legacy.write_text(json.dumps({"email": "legacy@json.com", "password": "old-pw"}))

    # Ensure loader re-reads the freshly-set NEXO_HOME.
    for mod in list(sys.modules):
        if mod == "email_config":
            sys.modules.pop(mod, None)
    from email_config import load_email_config

    cfg = load_email_config()
    assert cfg["email"] == "real@table.com"
    assert cfg["password"] == "sekret-1"
    assert cfg["_source"] == "email_accounts"


def test_loader_falls_back_to_legacy_when_table_empty(isolated_home):
    legacy = isolated_home / "nexo-email" / "config.json"
    legacy.write_text(json.dumps({
        "email": "legacy@json.com",
        "password": "old-pw",
        "imap_host": "imap.legacy.com",
        "smtp_host": "smtp.legacy.com",
    }))

    for mod in list(sys.modules):
        if mod == "email_config":
            sys.modules.pop(mod, None)
    from email_config import load_email_config

    cfg = load_email_config()
    assert cfg is not None
    assert cfg["email"] == "legacy@json.com"
    assert cfg["_source"] == "legacy-config-json"


def test_migrate_script_populates_table(isolated_home):
    legacy = isolated_home / "nexo-email" / "config.json"
    legacy.write_text(json.dumps({
        "email": "migrate@me.com",
        "password": "hidden",
        "imap_host": "imap.migrate.com",
        "imap_port": 993,
        "smtp_host": "smtp.migrate.com",
        "smtp_port": 465,
        "operator_email": "me@personal.com",
        "trusted_domains": ["migrate.com"],
        "francisco_emails": ["me@personal.com", "me@work.com"],
        "sender_policy": "open",
    }))

    import importlib.util
    migrate_path = Path(SRC) / "scripts" / "nexo-email-migrate-config.py"
    spec = importlib.util.spec_from_file_location("nexo_email_migrate", migrate_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    rc = mod.main([])
    assert rc == 0

    from db._email_accounts import get_email_account
    a = get_email_account("primary")
    assert a is not None
    assert a["email"] == "migrate@me.com"
    assert a["trusted_domains"] == ["migrate.com"]
    assert a["operator_email"] == "me@personal.com"
    assert a["metadata"]["operator_aliases"] == ["me@personal.com", "me@work.com"]

    from db._core import get_db
    conn = get_db()
    row = conn.execute(
        "SELECT value FROM credentials WHERE service='email' AND key='primary'"
    ).fetchone()
    assert row is not None
    assert row[0] == "hidden"


def test_metadata_preserved_when_re_adding_without_metadata_arg(isolated_home):
    """Audit H2 regression: re-running add_email_account on an existing
    label without passing `metadata=` must keep whatever metadata the
    operator (or another subsystem) had stored. Otherwise editing one
    field via the wizard silently wipes auto-capture / poll-tuning
    state."""
    from db._email_accounts import add_email_account, get_email_account

    add_email_account(
        label="primary",
        email="me@example.com",
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        credential_service="email",
        credential_key="primary",
        operator_email="me@example.com",
        trusted_domains=["example.com"],
        metadata={"check_interval_seconds": 90, "operator_aliases": ["me@personal.com"]},
    )
    # Re-add as the wizard would (no metadata kwarg) — change role only.
    add_email_account(
        label="primary",
        email="me@example.com",
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        credential_service="email",
        credential_key="primary",
        operator_email="me@example.com",
        trusted_domains=["example.com"],
        role="inbox",
    )
    out = get_email_account("primary")
    assert out["role"] == "inbox"
    assert out["metadata"].get("check_interval_seconds") == 90
    assert out["metadata"].get("operator_aliases") == ["me@personal.com"]


def test_metadata_can_be_explicitly_cleared(isolated_home):
    """Counterpart to the H2 fix: explicit metadata={} still overwrites
    so callers retain a way to wipe state when they really mean it."""
    from db._email_accounts import add_email_account, get_email_account

    add_email_account(
        label="primary",
        email="me@example.com",
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        credential_service="email",
        credential_key="primary",
        operator_email="me@example.com",
        trusted_domains=[],
        metadata={"keep_me": "yes"},
    )
    add_email_account(
        label="primary",
        email="me@example.com",
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        credential_service="email",
        credential_key="primary",
        operator_email="me@example.com",
        trusted_domains=[],
        metadata={},
    )
    out = get_email_account("primary")
    assert out["metadata"] == {}
