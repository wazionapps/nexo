from __future__ import annotations

import sqlite3

from email_memory_bridge import email_source_for_intent, search_email_memory


def _make_email_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE emails (
            id INTEGER PRIMARY KEY,
            subject TEXT,
            sender TEXT,
            to_email TEXT,
            status TEXT,
            summary TEXT,
            body TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO emails(subject, sender, to_email, status, summary, body) VALUES (?, ?, ?, ?, ?, ?)",
        ("Pedido Leonardo", "leonardo@example.com", "info@example.com", "processed", "Pide numero de pedido", "body secret"),
    )
    conn.commit()
    conn.close()


def test_search_email_memory_returns_safe_metadata_by_default(tmp_path):
    db_path = tmp_path / "email.db"
    _make_email_db(db_path)

    result = search_email_memory("Leonardo", db_path=db_path)

    assert result["ok"] is True
    assert result["results"][0]["subject"] == "Pedido Leonardo"
    assert result["results"][0]["sender"] == "leonardo@example.com"
    assert "body_preview" not in result["results"][0]


def test_search_email_memory_can_include_short_body_preview_when_requested(tmp_path):
    db_path = tmp_path / "email.db"
    _make_email_db(db_path)

    result = search_email_memory("Leonardo", db_path=db_path, include_body=True)

    assert result["results"][0]["body_preview"] == "body secret"


def test_email_source_for_intent_is_limited_to_memory_commitment_intents():
    assert email_source_for_intent("schedule_commitment") is True
    assert email_source_for_intent("prior_work") is True
    assert email_source_for_intent("file_location") is False
