from __future__ import annotations

import importlib.util
from pathlib import Path


def test_sent_email_events_are_queryable_for_duplicate_checks(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    monkeypatch.setenv("NEXO_HOME", str(home))

    import importlib
    import paths
    import email_sent_events

    importlib.reload(paths)
    importlib.reload(email_sent_events)

    db_path = home / "runtime" / "nexo-email" / "nexo-email.db"
    email_sent_events.record_sent_email(
        message_id="<sent-1@example.test>",
        sender="agent@example.test",
        to_addrs="Client <client@example.test>",
        subject="Release checklist",
        source="test",
        body_text="The release checklist is complete.",
        db_path=db_path,
        record_memory=False,
    )

    found = email_sent_events.find_sent_email(
        to_addr="client@example.test",
        subject="Release checklist",
        db_path=db_path,
    )
    assert found is not None
    assert found["message_id"] == "<sent-1@example.test>"
    assert found["body_text"] == "The release checklist is complete."

    block = email_sent_events.format_recent_sent_email_block(hours=24, limit=5)
    assert "EMAILS ENVIADOS ULTIMAS 24H POR LA OPERATIVA" in block
    assert "Release checklist" in block


def test_check_context_uses_sent_email_events_before_maildir(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    monkeypatch.setenv("NEXO_HOME", str(home))

    import importlib.util
    import sys

    src = Path(__file__).resolve().parents[1] / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    import paths
    import email_sent_events

    importlib.reload(paths)
    importlib.reload(email_sent_events)
    email_sent_events.record_sent_email(
        message_id="<sent-2@example.test>",
        sender="agent@example.test",
        to_addrs="client@example.test",
        subject="Already sent",
        record_memory=False,
    )

    script = src / "scripts" / "check-context.py"
    spec = importlib.util.spec_from_file_location("check_context_sent_email_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    checker = module.ContextChecker()
    assert checker.check_email_sent("client@example.test", "Already sent") is True


def test_sent_email_table_migrates_existing_database_without_body_text(tmp_path):
    import sqlite3
    import email_sent_events

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE sent_email_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT,
            sender TEXT,
            to_addrs TEXT NOT NULL DEFAULT '',
            cc_addrs TEXT NOT NULL DEFAULT '',
            subject TEXT NOT NULL DEFAULT '',
            in_reply_to TEXT NOT NULL DEFAULT '',
            references_header TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'sent',
            sent_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            meta TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.commit()
    conn.close()

    email_sent_events.record_sent_email(
        message_id="<legacy@example.test>",
        to_addrs="client@example.test",
        subject="Legacy",
        body_text="Migrated body",
        db_path=db_path,
        record_memory=False,
    )
    rows = email_sent_events.recent_sent_emails(hours=24, db_path=db_path)
    assert rows[0]["body_text"] == "Migrated body"


def test_quick_smtp_sender_records_sent_email_sink(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts" / "nexo-send-email.py"
    spec = importlib.util.spec_from_file_location("nexo_send_email_quick_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    sent_messages = []
    recorded = {}

    class FakeSMTP:
        def __init__(self, host, port):
            self.host = host
            self.port = port

        def login(self, user, password):
            self.user = user
            self.password = password

        def send_message(self, msg):
            sent_messages.append(msg)

        def quit(self):
            pass

    monkeypatch.setattr(module, "load_smtp_config", lambda: {
        "host": "smtp.example.test",
        "port": "465",
        "user": "agent@example.test",
        "password": "secret",
        "from_email": "agent@example.test",
        "from_name": "NEXO",
    })
    monkeypatch.setattr(module.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(module, "record_sent_email", lambda **kwargs: recorded.update(kwargs))

    module.send("Progress", "Body from quick sender", "client@example.test", "ops@example.test")

    assert sent_messages
    assert recorded["source"] == "nexo-send-email"
    assert recorded["body_text"] == "Body from quick sender"
    assert recorded["to_addrs"] == "client@example.test"
    assert recorded["cc_addrs"] == "ops@example.test"
    assert recorded["message_id"].startswith("<")
