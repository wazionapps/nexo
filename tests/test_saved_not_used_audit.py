from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from saved_not_used_audit import SavedNotUsedConfig, audit_saved_not_used, format_markdown


def _db(path: Path, schema: str) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(schema)
    conn.commit()
    return conn


def _base_main_db(path: Path) -> sqlite3.Connection:
    return _db(
        path,
        """
        CREATE TABLE memory_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_uid TEXT,
            created_at REAL,
            source_type TEXT
        );
        CREATE TABLE memory_observation_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_uid TEXT,
            status TEXT,
            created_at REAL,
            updated_at REAL,
            processed_at REAL
        );
        CREATE TABLE memory_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observation_uid TEXT,
            created_at REAL,
            updated_at REAL
        );
        CREATE TABLE session_diary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            created_at TEXT,
            summary TEXT
        );
        CREATE TABLE reminders (
            id TEXT PRIMARY KEY,
            status TEXT,
            created_at REAL,
            updated_at REAL
        );
        CREATE TABLE followups (
            id TEXT PRIMARY KEY,
            status TEXT,
            created_at REAL,
            updated_at REAL
        );
        CREATE TABLE item_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type TEXT,
            item_id TEXT,
            event_type TEXT,
            created_at REAL
        );
        CREATE TABLE workflow_runs (
            run_id TEXT PRIMARY KEY,
            status TEXT,
            opened_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE workflow_goals (
            goal_id TEXT PRIMARY KEY,
            status TEXT,
            opened_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE workflow_checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            created_at TEXT
        );
        CREATE TABLE change_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            verify TEXT
        );
        CREATE TABLE continuity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE lifecycle_events (
            event_id TEXT PRIMARY KEY,
            delivery_status TEXT,
            created_at TEXT,
            processed_at TEXT,
            canonical_dispatched_at TEXT,
            canonical_done_at TEXT
        );
        CREATE TABLE plugins (
            filename TEXT PRIMARY KEY,
            tools_count INTEGER,
            tool_names TEXT,
            loaded_at REAL,
            created_by TEXT
        );
        CREATE TABLE cron_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cron_id TEXT,
            started_at TEXT,
            ended_at TEXT,
            exit_code INTEGER
        );
        CREATE TABLE local_assets (
            asset_id TEXT,
            updated_at REAL
        );
        CREATE TABLE local_chunks (
            chunk_id TEXT,
            created_at REAL
        );
        CREATE TABLE local_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL
        );
        CREATE TABLE local_embeddings (
            embedding_id TEXT,
            created_at REAL
        );
        CREATE TABLE local_context_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL
        );
        """,
    )


def _local_context_db(path: Path, *, with_query: bool) -> None:
    conn = _db(
        path,
        """
        CREATE TABLE local_assets (
            asset_id TEXT PRIMARY KEY,
            updated_at REAL,
            last_seen_at REAL
        );
        CREATE TABLE local_chunks (
            chunk_id TEXT PRIMARY KEY,
            created_at REAL
        );
        CREATE TABLE local_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL
        );
        CREATE TABLE entity_facts (
            fact_id TEXT PRIMARY KEY,
            created_at REAL
        );
        CREATE TABLE local_relations (
            relation_id TEXT PRIMARY KEY,
            created_at REAL
        );
        CREATE TABLE local_embeddings (
            embedding_id TEXT PRIMARY KEY,
            created_at REAL
        );
        CREATE TABLE local_context_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL
        );
        """,
    )
    conn.execute("INSERT INTO local_assets VALUES ('a1', 2000, 2000)")
    conn.execute("INSERT INTO local_chunks VALUES ('c1', 2000)")
    conn.execute("INSERT INTO local_entities(created_at) VALUES (2000)")
    if with_query:
        conn.execute("INSERT INTO local_context_queries(created_at) VALUES (1999)")
    conn.commit()
    conn.close()


def _email_db(path: Path) -> None:
    conn = _db(
        path,
        """
        CREATE TABLE sent_email_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT,
            sent_at TEXT
        );
        """,
    )
    conn.execute("INSERT INTO sent_email_events(subject, sent_at) VALUES ('S1', '2026-05-19 07:00:00')")
    conn.commit()
    conn.close()


def _config(
    tmp_path: Path,
    *,
    main_db: Path,
    local_context_db: Path,
    email_db: Path,
    live_tools=frozenset({"nexo_alpha"}),
) -> SavedNotUsedConfig:
    transcript_root = tmp_path / "transcripts"
    transcript_root.mkdir()
    (transcript_root / "short.jsonl").write_text(
        json.dumps({"type": "user", "message": {"content": "one"}}) + "\n",
        encoding="utf-8",
    )
    (transcript_root / "usable.jsonl").write_text(
        "\n".join(json.dumps({"type": "user", "message": {"content": str(i)}}) for i in range(3)) + "\n",
        encoding="utf-8",
    )
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    (desktop / "conversations.json").write_text(json.dumps({"conversations": [{"id": "c1"}]}), encoding="utf-8")
    (desktop / "continuity-queue.json").write_text(json.dumps([]), encoding="utf-8")
    cron_spool = tmp_path / "cron-spool"
    cron_spool.mkdir()
    return SavedNotUsedConfig(
        nexo_db_path=main_db,
        local_context_db_path=local_context_db,
        email_db_path=email_db,
        transcript_roots=(transcript_root,),
        desktop_conversations_path=desktop / "conversations.json",
        desktop_continuity_queue_path=desktop / "continuity-queue.json",
        cron_spool_dir=cron_spool,
        live_tools=frozenset(live_tools),
    )


def test_flags_local_context_saved_without_query_and_ignores_empty_legacy_tables(tmp_path):
    main = tmp_path / "nexo.db"
    local = tmp_path / "local-context.db"
    email = tmp_path / "nexo-email.db"
    _base_main_db(main).close()
    _local_context_db(local, with_query=False)
    _email_db(email)

    report = audit_saved_not_used(_config(tmp_path, main_db=main, local_context_db=local, email_db=email))

    alerts = {item["alert_id"]: item for item in report["findings"]}
    assert alerts["local_context_saved_not_used"]["severity"] == "P0"
    assert "local_context_legacy_tables_non_empty" not in alerts


def test_covers_required_stores_and_detects_unconsumed_fixtures(tmp_path):
    main = tmp_path / "nexo.db"
    local = tmp_path / "local-context.db"
    email = tmp_path / "nexo-email.db"
    conn = _base_main_db(main)
    conn.execute("INSERT INTO memory_events(event_uid, created_at, source_type) VALUES ('e1', 1000, 'tool')")
    conn.execute("INSERT INTO memory_observation_queue(event_uid, status, created_at, updated_at) VALUES ('e1', 'pending', 1000, 1001)")
    conn.execute("INSERT INTO session_diary(session_id, created_at, summary) VALUES ('s1', '2026-05-19 06:00:00', 'ok')")
    conn.execute("INSERT INTO followups(id, status, created_at, updated_at) VALUES ('f1', 'PENDING', 1000, 1001)")
    conn.execute("INSERT INTO reminders(id, status, created_at, updated_at) VALUES ('r1', 'PENDING', 1000, 1001)")
    conn.execute("INSERT INTO item_history(item_type, item_id, event_type, created_at) VALUES ('followup', 'f1', 'read', 1002)")
    conn.execute("INSERT INTO workflow_runs(run_id, status, opened_at, updated_at) VALUES ('w1', 'open', '2026-05-19 06:00:00', '2026-05-19 06:10:00')")
    conn.execute("INSERT INTO workflow_goals(goal_id, status, opened_at, updated_at) VALUES ('g1', 'active', '2026-05-19 06:00:00', '2026-05-19 06:10:00')")
    conn.execute("INSERT INTO change_log(created_at, verify) VALUES ('2026-05-19 06:00:00', '')")
    conn.execute("INSERT INTO continuity_snapshots(conversation_id, created_at, updated_at) VALUES ('c1', '2026-05-19 06:00:00', '2026-05-19 06:00:00')")
    conn.execute(
        "INSERT INTO lifecycle_events(event_id, delivery_status, created_at, canonical_dispatched_at) VALUES ('l1', 'canonical_pending', '2026-05-19 06:00:00', '2026-05-19 06:01:00')"
    )
    conn.execute("INSERT INTO plugins(filename, tools_count, tool_names, loaded_at, created_by) VALUES ('p.py', 2, 'nexo_alpha,nexo_missing', 1000, 'repo')")
    conn.execute("INSERT INTO cron_runs(cron_id, started_at, ended_at, exit_code) VALUES ('followup-runner', '2026-05-19 06:00:00', NULL, NULL)")
    conn.commit()
    conn.close()
    _local_context_db(local, with_query=True)
    _email_db(email)
    cfg = _config(tmp_path, main_db=main, local_context_db=local, email_db=email)
    (cfg.cron_spool_dir / "followup-runner-1.json").write_text("{}", encoding="utf-8")
    cfg.desktop_continuity_queue_path.write_text(json.dumps([{"id": "q1"}]), encoding="utf-8")

    report = audit_saved_not_used(cfg)

    store_ids = {item["store_id"] for item in report["stores"]}
    assert {
        "local_context",
        "memory_observations_pipeline",
        "session_diary",
        "followups_reminders",
        "workflows",
        "change_log",
        "continuity_lifecycle",
        "transcripts",
        "email_db",
        "plugins_catalog_live",
        "cron_spool",
    } <= store_ids
    for row in report["stores"]:
        assert row["producer"]
        assert row["store"] is not None
        assert row["consumer"]
        assert row["risk"]
        assert row["test"]

    alerts = {item["alert_id"]: item for item in report["findings"]}
    assert alerts["memory_events_without_observations"]["severity"] == "P1"
    assert alerts["memory_observation_queue_pending"]["severity"] == "P1"
    assert alerts["workflow_open_without_checkpoint"]["severity"] == "P1"
    assert alerts["continuity_lifecycle_not_consumed"]["severity"] == "P0"
    assert alerts["plugin_catalog_not_live"]["severity"] == "P0"
    assert alerts["cron_spool_unreconciled"]["severity"] == "P1"
    assert alerts["change_log_missing_verify"]["severity"] == "P2"
    assert alerts["email_db_without_memory_projection"]["severity"] == "P2"


def test_plugins_ok_when_catalog_matches_live(tmp_path):
    main = tmp_path / "nexo.db"
    local = tmp_path / "local-context.db"
    email = tmp_path / "nexo-email.db"
    conn = _base_main_db(main)
    conn.execute("INSERT INTO plugins(filename, tools_count, tool_names, loaded_at, created_by) VALUES ('p.py', 1, 'nexo_alpha', 1000, 'repo')")
    conn.commit()
    conn.close()
    _local_context_db(local, with_query=True)
    _email_db(email)

    report = audit_saved_not_used(_config(tmp_path, main_db=main, local_context_db=local, email_db=email, live_tools=frozenset({"nexo_alpha"})))

    plugin_row = next(row for row in report["stores"] if row["store_id"] == "plugins_catalog_live")
    assert plugin_row["severity"] == "OK"
    assert "plugin_catalog_not_live" not in {item["alert_id"] for item in report["findings"]}


def test_markdown_fragment_has_required_columns(tmp_path):
    main = tmp_path / "nexo.db"
    local = tmp_path / "local-context.db"
    email = tmp_path / "nexo-email.db"
    _base_main_db(main).close()
    _local_context_db(local, with_query=False)
    _email_db(email)

    md = format_markdown(audit_saved_not_used(_config(tmp_path, main_db=main, local_context_db=local, email_db=email)))

    assert "| Store | Producer | Consumer | Last write | Last use | Risk | Test | Status |" in md
    assert "`local_context_saved_not_used`" in md
