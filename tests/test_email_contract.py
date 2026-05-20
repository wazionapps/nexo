from __future__ import annotations

import sqlite3


def test_email_contract_splits_accounts_config_from_event_store(tmp_path):
    from db._email_accounts import add_email_account
    from email_contract import email_contract_snapshot

    add_email_account(
        label="primary",
        email="agent@example.com",
        credential_service="email",
        credential_key="primary",
        account_type="agent",
        enabled=True,
    )

    event_db = tmp_path / "nexo-email.db"
    conn = sqlite3.connect(str(event_db))
    conn.execute(
        "CREATE TABLE emails (message_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending', received_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE email_events (id INTEGER PRIMARY KEY AUTOINCREMENT, email_id TEXT, event TEXT)"
    )
    conn.execute("INSERT INTO emails (message_id, status) VALUES ('m1', 'pending')")
    conn.execute("INSERT INTO emails (message_id, status) VALUES ('m2', 'processed')")
    conn.execute("INSERT INTO email_events (email_id, event) VALUES ('m2', 'replied')")
    conn.commit()
    conn.close()

    snapshot = email_contract_snapshot(event_db)

    assert snapshot["contract"]["conflated"] is False
    assert snapshot["accounts_config"]["table"] == "email_accounts"
    assert snapshot["accounts_config"]["total"] == 1
    assert snapshot["accounts_config"]["with_credentials"] == 1
    assert snapshot["event_store"]["emails_table"] is True
    assert snapshot["event_store"]["email_events_table"] is True
    assert snapshot["event_store"]["pending"] == 1
    assert snapshot["event_store"]["processed"] == 1
    assert snapshot["event_store"]["total_events"] == 1
