"""Tests for database schema and migrations."""

import db as db_mod


def test_init_db_creates_core_tables():
    """All core tables should exist after init_db."""
    conn = db_mod.get_db()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    tables = {r["name"] for r in rows}

    expected = {
        "sessions", "tracked_files", "messages", "message_reads",
        "questions", "reminders", "followups", "learnings", "credentials",
        "task_history", "task_frequencies", "plugins", "entities",
        "preferences", "agents", "change_log", "decisions",
        "protocol_tasks", "protocol_debt", "item_history", "item_read_tokens",
    }
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"


def test_migrations_idempotent():
    """Running migrations twice should not raise."""
    db_mod.run_migrations()
    db_mod.run_migrations()
    version = db_mod.get_schema_version()
    assert version >= 29


def test_session_crud():
    """Register, update, and clean sessions."""
    info = db_mod.register_session("nexo-9999999-11111", "test task")
    assert info["sid"] == "nexo-9999999-11111"

    active = db_mod.get_active_sessions()
    sids = [s["sid"] for s in active]
    assert "nexo-9999999-11111" in sids

    db_mod.update_session("nexo-9999999-11111", "updated task")

    db_mod.complete_session("nexo-9999999-11111")
    active2 = db_mod.get_active_sessions()
    sids2 = [s["sid"] for s in active2]
    assert "nexo-9999999-11111" not in sids2


def test_learning_crud():
    """Create, search, update, and delete learnings."""
    result = db_mod.create_learning(
        category="test-cat",
        title="Test Learning Title",
        content="Some content about testing patterns.",
    )
    learning_id = result["id"]
    assert learning_id > 0

    found = db_mod.search_learnings("testing patterns")
    assert any(l["id"] == learning_id for l in found)

    db_mod.update_learning(learning_id, title="Updated Title")
    found2 = db_mod.search_learnings("Updated Title")
    assert any(l["id"] == learning_id for l in found2)

    db_mod.delete_learning(learning_id)
    found3 = db_mod.search_learnings("Updated Title")
    assert not any(l["id"] == learning_id for l in found3)


def test_learning_supersede_lifecycle():
    first = db_mod.create_learning(
        category="test-cat",
        title="Old canonical rule",
        content="Do the old thing.",
    )
    second = db_mod.create_learning(
        category="test-cat",
        title="New canonical rule",
        content="Do the new thing.",
    )

    superseded = db_mod.supersede_learning(first["id"], second["id"])
    current = db_mod.get_db().execute(
        "SELECT status FROM learnings WHERE id = ?",
        (first["id"],),
    ).fetchone()
    replacement = db_mod.get_db().execute(
        "SELECT supersedes_id FROM learnings WHERE id = ?",
        (second["id"],),
    ).fetchone()

    assert superseded["status"] == "superseded"
    assert current["status"] == "superseded"
    assert replacement["supersedes_id"] == first["id"]


def test_reminder_followup_crud():
    """Create and complete reminders and followups."""
    db_mod.create_reminder("R-TEST1", "Test reminder", date="2026-12-31")
    reminder = db_mod.get_reminder("R-TEST1", include_history=True)
    assert reminder is not None
    assert reminder["status"] == "PENDING"
    assert reminder["history"][0]["event_type"] == "created"
    assert reminder["read_token"].startswith("IRT-")

    db_mod.complete_reminder("R-TEST1")
    reminder2 = db_mod.get_reminder("R-TEST1", include_history=True)
    assert reminder2["status"] == "COMPLETED"
    assert any(event["event_type"] == "completed" for event in reminder2["history"])

    db_mod.create_followup("NF-TEST1", "Test followup", date="2026-12-31")
    followup = db_mod.get_followup("NF-TEST1", include_history=True)
    assert followup is not None
    assert followup["history"][0]["event_type"] == "created"

    db_mod.complete_followup("NF-TEST1", result="done")
    followup2 = db_mod.get_followup("NF-TEST1", include_history=True)
    assert followup2["status"] == "COMPLETED"
    assert any(event["event_type"] == "completed" for event in followup2["history"])


def test_soft_delete_restore_and_read_token_validation():
    db_mod.create_reminder("R-TEST2", "Delete me", date="2026-12-31")
    reminder = db_mod.get_reminder("R-TEST2", include_history=True)
    token = reminder["read_token"]

    ok, msg = db_mod.validate_item_read_token(token, "reminder", "R-TEST2")
    assert ok is True
    assert msg == ""

    assert db_mod.delete_reminder("R-TEST2") is True
    deleted = db_mod.get_reminder("R-TEST2", include_history=True)
    assert deleted["status"] == "DELETED"
    assert any(event["event_type"] == "deleted" for event in deleted["history"])

    ok2, msg2 = db_mod.validate_item_read_token(token, "reminder", "R-TEST2")
    assert ok2 is False
    assert "History changed" in msg2

    restored = db_mod.restore_reminder("R-TEST2")
    assert restored["status"] == "PENDING"
    restored_view = db_mod.get_reminder("R-TEST2", include_history=True)
    assert any(event["event_type"] == "restored" for event in restored_view["history"])


def test_recurring_followup():
    """Recurring followup: complete archives with date suffix, creates new pending, returns correct IDs."""
    db_mod.create_followup("NF-REC1", "Recurring test", date="2026-03-31", recurrence="weekly:monday")
    followup = db_mod.get_followup("NF-REC1")
    assert followup is not None
    assert followup["recurrence"] == "weekly:monday"

    result = db_mod.complete_followup("NF-REC1", result="done weekly")

    # Result should reference the archived ID, not the recycled NF-REC1
    assert result["status"] == "COMPLETED"
    assert result["id"].startswith("NF-REC1-")  # archived with date suffix
    assert result["next_id"] == "NF-REC1"
    assert result["next_date"] is not None

    # The new NF-REC1 should be PENDING (not the completed one)
    new_followup = db_mod.get_followup("NF-REC1")
    assert new_followup is not None
    assert new_followup["status"] == "PENDING"

    # The archived one should exist with date suffix
    archived = db_mod.get_followup(result["id"])
    assert archived is not None
    assert archived["status"] == "COMPLETED"


def test_credential_crud():
    """Create, get, and delete credentials."""
    db_mod.create_credential("test-service", "api_key", "secret123", notes="test")
    creds = db_mod.get_credential("test-service", "api_key")
    assert len(creds) == 1
    assert creds[0]["value"] == "secret123"

    db_mod.delete_credential("test-service", "api_key")
    creds2 = db_mod.get_credential("test-service", "api_key")
    assert len(creds2) == 0


def test_fts_tables_created():
    """FTS5 virtual tables should exist after init + migrations."""
    conn = db_mod.get_db()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%fts%'"
    ).fetchall()
    # At minimum the learnings FTS should exist (created in init or migration)
    table_names = {r["name"] for r in rows}
    # nexo_fts is the main FTS table
    assert "nexo_fts" in table_names or len(table_names) > 0
