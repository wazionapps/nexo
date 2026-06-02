"""Tests for database schema and migrations."""

import sqlite3

import pytest

import db as db_mod
from db._schema import (
    _m65_diary_quality,
    _m67_diary_quality_backfill_repair,
    _m68_memory_fabric_index,
    _m74_entity_live_profiles,
    _m75_failure_prevention_ledger,
    _m76_semantic_layers,
    run_migrations,
)


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
        "hot_context", "recent_events", "memory_events",
        "memory_observations", "memory_observation_queue",
        "memory_observations_fts",
        "entity_profile_cache", "nexo_managed_assets",
        "asset_context_updated",
        "failure_prevention_cases", "failure_source_events",
        "antibody_actions",
        "semantic_layers", "semantic_layer_source_refs",
    }
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"


def test_migrations_idempotent():
    """Running migrations twice should not raise."""
    db_mod.run_migrations()
    db_mod.run_migrations()
    version = db_mod.get_schema_version()
    assert version >= 76


def test_m76_semantic_layers_migration_is_idempotent_and_constrained():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    _m76_semantic_layers(conn)
    _m76_semantic_layers(conn)

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"semantic_layers", "semantic_layer_source_refs", "workflow_runs", "transcript_index"} <= tables

    now = 1780408200.0
    conn.execute(
        """
        INSERT INTO semantic_layers (
            layer_uid, scope_type, scope_id, layer_kind, policy_version,
            status, quality_state, value_redacted, token_size,
            source_refs_json, evidence_refs_json, source_fingerprint,
            content_hash, privacy_level, allowed_surfaces_json, confidence,
            coverage, generated_at, updated_at
        ) VALUES (
            'SL-test', 'workflow', 'WF-test', 'next_action', 'semantic_layers_v1',
            'fresh', 'complete', 'Next action', 2,
            '["workflow_run:WF-test"]', '[]', 'fp1',
            'ch1', 'normal', '["pre_answer"]', 0.8,
            1.0, ?, ?
        )
        """,
        (now, now),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO semantic_layers (
                layer_uid, scope_type, scope_id, layer_kind, policy_version,
                status, quality_state, value_redacted, token_size,
                source_refs_json, evidence_refs_json, source_fingerprint,
                content_hash, privacy_level, allowed_surfaces_json, confidence,
                coverage, generated_at, updated_at
            ) VALUES (
                'SL-test-2', 'workflow', 'WF-test', 'next_action', 'semantic_layers_v1',
                'fresh', 'complete', 'Next action', 2,
                '["workflow_run:WF-test"]', '[]', 'fp1',
                'ch2', 'normal', '["pre_answer"]', 0.8,
                1.0, ?, ?
            )
            """,
            (now, now),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO semantic_layers (
                layer_uid, scope_type, scope_id, layer_kind, source_fingerprint,
                content_hash, generated_at, updated_at, confidence
            ) VALUES ('SL-bad', 'workflow', 'WF-test', 'next_action', 'fp2', 'ch3', ?, ?, 2.0)
            """,
            (now, now),
        )

    conn.execute(
        """
        INSERT INTO semantic_layer_source_refs (
            layer_uid, source_ref, source_kind, source_version, created_at, updated_at
        ) VALUES ('SL-test', 'workflow_run:WF-test', 'workflow_run', 'v1', ?, ?)
        """,
        (now, now),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO semantic_layer_source_refs (
                layer_uid, source_ref, source_kind, source_version, created_at, updated_at
            ) VALUES ('SL-test', 'workflow_run:WF-test', 'workflow_run', 'v1', ?, ?)
            """,
            (now, now),
        )


def test_m75_failure_prevention_ledger_is_idempotent_and_constrained():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    _m75_failure_prevention_ledger(conn)
    _m75_failure_prevention_ledger(conn)

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {
        "failure_prevention_cases",
        "failure_source_events",
        "antibody_actions",
        "outcomes",
        "protocol_debt",
        "hook_runs",
        "session_correction_requirements",
        "memory_events",
    } <= tables

    now = 1780402400.0
    conn.execute(
        """
        INSERT INTO failure_prevention_cases (
            failure_uid, failure_type, primary_source_type, primary_source_ref,
            opened_at, updated_at, review_due_at, expires_at, last_triggered_at
        ) VALUES (
            'failure-1', 'workflow', 'test_failure', 'test:tests/test_failure_prevention.py::x',
            ?, ?, ?, ?, ?
        )
        """,
        (now, now, now + 1, now + 2, now),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO failure_prevention_cases (
                failure_uid, failure_type, primary_source_type, primary_source_ref,
                opened_at, updated_at, review_due_at, expires_at, last_triggered_at
            ) VALUES (
                'failure-1', 'workflow', 'test_failure', 'test:tests/test_failure_prevention.py::x',
                ?, ?, ?, ?, ?
            )
            """,
            (now, now, now + 1, now + 2, now),
        )

    conn.execute(
        """
        INSERT INTO failure_source_events (
            source_event_uid, failure_uid, source_type, source_ref,
            observed_at, validated, created_at, updated_at
        ) VALUES ('source-1', 'failure-1', 'test_failure', 'test:tests/test_failure_prevention.py::x', ?, 1, ?, ?)
        """,
        (now, now, now),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO failure_source_events (
                source_event_uid, failure_uid, source_type, source_ref,
                observed_at, validated, created_at, updated_at
            ) VALUES ('source-1', 'failure-1', 'test_failure', 'test:tests/test_failure_prevention.py::x', ?, 1, ?, ?)
            """,
            (now, now, now),
        )

    conn.execute(
        """
        INSERT INTO antibody_actions (
            antibody_uid, failure_uid, action_type, target_system, target_ref,
            review_due_at, expires_at, created_at, updated_at
        ) VALUES ('antibody-1', 'failure-1', 'docs_update', 'docs', 'evidence:docs', ?, ?, ?, ?)
        """,
        (now + 1, now + 2, now, now),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO antibody_actions (
                antibody_uid, failure_uid, action_type, target_system, target_ref,
                review_due_at, expires_at, created_at, updated_at
            ) VALUES ('antibody-1', 'failure-1', 'docs_update', 'docs', 'evidence:docs', ?, ?, ?, ?)
            """,
            (now + 1, now + 2, now, now),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO antibody_actions (
                antibody_uid, failure_uid, action_type, target_system, target_ref,
                review_due_at, expires_at, created_at, updated_at
            ) VALUES ('antibody-empty-target', 'failure-1', 'docs_update', 'docs', '', ?, ?, ?, ?)
            """,
            (now + 1, now + 2, now, now),
        )


def test_run_migrations_from_v70_reaches_v76_without_losing_existing_rows():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.executemany(
        "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
        [(version, f"legacy_{version}") for version in range(1, 71)],
    )
    from db._schema import _m32_outcomes

    _m32_outcomes(conn)
    conn.execute(
        """
        INSERT INTO outcomes (
            action_type, description, expected_result, deadline
        ) VALUES ('legacy', 'legacy outcome', 'preserved', '2026-06-03T00:00:00')
        """
    )
    conn.commit()

    run_migrations(conn)
    run_migrations(conn)

    version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version >= 76
    assert conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] == 1
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {
        "failure_prevention_cases", "failure_source_events", "antibody_actions",
        "semantic_layers", "semantic_layer_source_refs",
    } <= tables
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 75").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 76").fetchone()[0] == 1


def test_run_migrations_from_v75_reaches_v76_without_losing_existing_rows():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.executemany(
        "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
        [(version, f"legacy_{version}") for version in range(1, 76)],
    )
    from db._schema import _m22_protocol_discipline_tables

    _m22_protocol_discipline_tables(conn)
    conn.execute(
        """
        INSERT INTO protocol_tasks (
            task_id, session_id, goal, task_type, status
        ) VALUES ('PT-existing', 's1', 'preserve task', 'edit', 'open')
        """
    )
    conn.commit()

    run_migrations(conn)
    run_migrations(conn)

    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] >= 76
    assert conn.execute("SELECT COUNT(*) FROM protocol_tasks WHERE task_id='PT-existing'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 76").fetchone()[0] == 1
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"semantic_layers", "semantic_layer_source_refs"} <= tables


def test_m74_entity_live_profiles_migration_is_idempotent_and_constrained():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    _m74_entity_live_profiles(conn)
    _m74_entity_live_profiles(conn)

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"entity_profile_cache", "nexo_managed_assets", "asset_context_updated", "memory_events"} <= tables

    now = 1780400000.0
    conn.execute(
        """
        INSERT INTO entity_profile_cache (
            profile_uid, profile_version, entity_key, source_refs_hash, input_hash,
            created_at, updated_at
        ) VALUES ('p1', 'entity_live_profile.v1', 'entity:1', 'refs', 'input', ?, ?)
        """,
        (now, now),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO entity_profile_cache (
                profile_uid, profile_version, entity_key, source_refs_hash, input_hash,
                created_at, updated_at
            ) VALUES ('p2', 'entity_live_profile.v1', 'entity:1', 'refs', 'input', ?, ?)
            """,
            (now, now),
        )

    conn.execute("INSERT INTO artifact_registry(kind, canonical_name) VALUES ('service', 'Demo')")
    artifact_id = conn.execute("SELECT id FROM artifact_registry").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO nexo_managed_assets (
            asset_uid, artifact_id, entity_key, provider_ref, external_ref_hash,
            created_at, updated_at
        ) VALUES ('ma1', ?, 'entity:1', '', '', ?, ?)
        """,
        (artifact_id, now, now),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO nexo_managed_assets (
                asset_uid, artifact_id, entity_key, created_at, updated_at
            ) VALUES ('ma2', ?, 'entity:1', ?, ?)
            """,
            (artifact_id, now, now),
        )

    conn.execute(
        """
        INSERT INTO nexo_managed_assets (
            asset_uid, entity_key, provider_ref, external_ref_hash, created_at, updated_at
        ) VALUES ('ma-empty-1', 'entity:1', '', '', ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO nexo_managed_assets (
            asset_uid, entity_key, provider_ref, external_ref_hash, created_at, updated_at
        ) VALUES ('ma-empty-2', 'entity:1', '', '', ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO nexo_managed_assets (
            asset_uid, entity_key, provider_ref, external_ref_hash, created_at, updated_at
        ) VALUES ('ma-provider-1', 'entity:1', 'cloudflare', 'abc', ?, ?)
        """,
        (now, now),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO nexo_managed_assets (
                asset_uid, entity_key, provider_ref, external_ref_hash, created_at, updated_at
            ) VALUES ('ma-provider-2', 'entity:2', 'cloudflare', 'abc', ?, ?)
            """,
            (now, now),
        )

    conn.execute(
        """
        INSERT INTO asset_context_updated (
            event_uid, entity_key, asset_uid, change_type, created_at
        ) VALUES ('ACU-1', 'entity:1', 'ma1', 'updated', ?)
        """,
        (now,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO asset_context_updated (
                event_uid, entity_key, asset_uid, change_type, created_at
            ) VALUES ('ACU-1', 'entity:1', 'ma1', 'updated', ?)
            """,
            (now,),
        )


def test_m65_diary_quality_backfills_legacy_defaults_and_archive():
    """Legacy rows get real quality tiers after ADD COLUMN defaults are applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE session_diary (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            decisions TEXT NOT NULL,
            discarded TEXT,
            pending TEXT,
            context_next TEXT,
            summary TEXT NOT NULL,
            mental_state TEXT,
            user_signals TEXT,
            self_critique TEXT DEFAULT '',
            source TEXT DEFAULT 'claude'
        )
    """)
    conn.execute("""
        CREATE TABLE diary_archive (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            decisions TEXT NOT NULL,
            discarded TEXT,
            pending TEXT,
            context_next TEXT,
            summary TEXT NOT NULL,
            mental_state TEXT,
            domain TEXT,
            user_signals TEXT,
            self_critique TEXT DEFAULT '',
            source TEXT DEFAULT 'claude',
            archived_at TEXT DEFAULT (datetime('now'))
        )
    """)
    rows = [
        ("auto", "2026-05-19T00:00:00Z", "", "", "", "", "Auto close", "", "", "", "auto-close"),
        ("cron", "2026-05-19T00:01:00Z", "", "", "Pending", "", "[AUTO-CRON] fallback", "", "", "", "cron"),
        ("agent", "2026-05-19T00:02:00Z", "Decision", "", "", "Next", "Agent summary", "", "", "Critique", "claude"),
    ]
    conn.executemany("""
        INSERT INTO session_diary (
            session_id, created_at, decisions, discarded, pending, context_next,
            summary, mental_state, user_signals, self_critique, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.executemany("""
        INSERT INTO diary_archive (
            session_id, created_at, decisions, discarded, pending, context_next,
            summary, mental_state, domain, user_signals, self_critique, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
    """, rows)

    _m65_diary_quality(conn)
    _m65_diary_quality(conn)

    for table in ("session_diary", "diary_archive"):
        result = {
            row["session_id"]: (row["quality_tier"], row["quality_score"])
            for row in conn.execute(f"SELECT session_id, quality_tier, quality_score FROM {table}")
        }
        assert result["auto"] == ("auto_close_minimal", 25)
        assert result["cron"] == ("fallback_minimal", 40)
        assert result["agent"] == ("agent_authored", 85)


def test_m67_diary_quality_repairs_databases_that_already_ran_m65_defaults():
    """The repair migration fixes rows left at ADD COLUMN default values."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for table in ("session_diary", "diary_archive"):
        conn.execute(f"""
            CREATE TABLE {table} (
                id INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                decisions TEXT NOT NULL,
                discarded TEXT,
                pending TEXT,
                context_next TEXT,
                summary TEXT NOT NULL,
                mental_state TEXT,
                domain TEXT,
                user_signals TEXT,
                self_critique TEXT DEFAULT '',
                source TEXT DEFAULT 'claude',
                quality_tier TEXT DEFAULT 'agent_authored',
                quality_score INTEGER DEFAULT 0
            )
        """)
        conn.execute(f"""
            INSERT INTO {table} (
                session_id, created_at, decisions, discarded, pending,
                context_next, summary, mental_state, domain, user_signals,
                self_critique, source, quality_tier, quality_score
            ) VALUES (
                'auto', '2026-05-19T00:00:00Z', '', '', '', '',
                'Auto close', '', '', '', '', 'auto-close', 'agent_authored', 0
            )
        """)

    _m67_diary_quality_backfill_repair(conn)
    _m67_diary_quality_backfill_repair(conn)

    for table in ("session_diary", "diary_archive"):
        row = conn.execute(f"""
            SELECT quality_tier, quality_score
            FROM {table}
            WHERE session_id = 'auto'
        """).fetchone()
        assert row["quality_tier"] == "auto_close_minimal"
        assert row["quality_score"] == 25


def test_m68_memory_fabric_index_migration_is_idempotent():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    _m68_memory_fabric_index(conn)
    _m68_memory_fabric_index(conn)

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    indexes = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }

    assert {"memory_fabric_sources", "historical_diary_index"}.issubset(tables)
    assert {
        "idx_historical_diary_session",
        "idx_historical_diary_created",
        "idx_historical_diary_domain",
    }.issubset(indexes)


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
