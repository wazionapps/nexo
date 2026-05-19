import json
import sqlite3

import db
import evidence_ledger


def _debug_candidate_task_sources():
    sources = []
    for index, conn in enumerate(evidence_ledger._candidate_dbs()):  # noqa: SLF001 - regression diagnostics
        try:
            database_list = [dict(row) for row in conn.execute("PRAGMA database_list").fetchall()]
        except Exception as exc:
            database_list = [{"error": repr(exc)}]
        try:
            rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT task_id, goal FROM protocol_tasks ORDER BY opened_at DESC LIMIT 8"
                ).fetchall()
            ]
        except Exception as exc:
            rows = [{"error": repr(exc)}]
        sources.append({"index": index, "id": id(conn), "database": database_list, "tasks": rows})
    return sources


def test_record_evidence_redacts_secrets_and_searches_by_task_and_file():
    entry = evidence_ledger.record_evidence(
        session_id="sid-g06",
        client="codex",
        actor="G06",
        object_type="file_path",
        object_ref="src/evidence_ledger.py",
        action="verified",
        summary="G06 evidence ledger verified with token=abcdefghijklmnopqrstuvwxyz",
        refs=[{"kind": "artifact", "value": "artifact:g06-ledger"}],
        file_paths=["src/evidence_ledger.py"],
        task_id="PT-G06-1",
        output="raw command output sk-abcdefghijklmnopqrstuvwxyz123456",
        error="password=verysecretvalue",
        verification="pytest tests/test_evidence_ledger.py with api_key=secretapikey123",
        idempotency_key="g06-record-1",
    )

    assert entry.source_type == "evidence_record"
    assert entry.object_ref == "src/evidence_ledger.py"

    by_task = evidence_ledger.search_evidence(task_id="PT-G06-1", include_transcripts=False)
    by_file = evidence_ledger.search_evidence(file_path="evidence_ledger.py", include_transcripts=False)
    payload = json.dumps(evidence_ledger.evidence_to_dicts(by_task), ensure_ascii=False)

    assert any(item.evidence_id == entry.evidence_id for item in by_task)
    assert any(item.evidence_id == entry.evidence_id for item in by_file)
    assert "[REDACTED]" in payload
    assert "verysecretvalue" not in payload
    assert "abcdefghijklmnopqrstuvwxyz123456" not in payload
    assert "secretapikey123" not in payload


def test_search_evidence_unifies_existing_operational_sources():
    conn = db.get_db()
    conn.execute(
        """
        INSERT INTO protocol_tasks (
            task_id, session_id, goal, task_type, files, evidence_refs, verification_step,
            status, close_evidence, files_changed, outcome_notes, opened_at, closed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            "PT-G06-LEDGER",
            "sid-g06",
            "G06 evidence ledger task",
            "edit",
            '["src/evidence_ledger.py"]',
            '["artifact:g06-ledger"]',
            "pytest evidence ledger query",
            "done",
            "evidence ledger task verification passed",
            '["src/evidence_ledger.py"]',
            "evidence ledger close evidence stored",
        ),
    )
    conn.execute(
        """
        INSERT INTO workflow_runs (
            run_id, session_id, goal, workflow_kind, status, next_action, owner,
            last_checkpoint_label, opened_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            "WF-G06-LEDGER",
            "sid-g06",
            "G06 evidence ledger workflow",
            "implementation",
            "completed",
            "search evidence ledger",
            "G06",
            "Verify evidence ledger",
        ),
    )
    conn.execute(
        """
        INSERT INTO workflow_checkpoints (
            run_id, step_key, checkpoint_label, run_status, step_status, summary, evidence, actor, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            "WF-G06-LEDGER",
            "verify",
            "Verify evidence ledger",
            "completed",
            "completed",
            "evidence ledger workflow checkpoint",
            "pytest evidence ledger passed",
            "G06",
        ),
    )
    conn.execute(
        """
        INSERT INTO change_log (session_id, files, what_changed, why, verify, created_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            "sid-g06",
            "src/evidence_ledger.py",
            "evidence ledger module added",
            "G06 virtual ledger over existing stores",
            "pytest evidence ledger",
        ),
    )
    conn.execute(
        """
        INSERT INTO session_diary (session_id, decisions, summary, context_next, source, created_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            "sid-g06",
            "use virtual evidence ledger without schema change",
            "evidence ledger diary summary",
            "router can consult evidence ledger",
            "codex",
        ),
    )
    conn.execute(
        """
        INSERT INTO continuity_snapshots (
            conversation_id, session_id, client, event_type, payload_json, trace_id, idempotency_key,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            "conv-g06-evidence",
            "sid-g06",
            "desktop",
            "turn_end",
            '{"summary": "evidence ledger continuity snapshot"}',
            "trace-g06",
            "idem-g06-evidence",
        ),
    )
    conn.execute(
        """
        INSERT INTO lifecycle_events (
            event_id, source, action, conversation_id, session_id, reason,
            payload_snapshot, delivery_status, processed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            "LE-G06",
            "desktop",
            "close",
            "conv-g06-evidence",
            "sid-g06",
            "evidence ledger lifecycle event",
            '{"summary": "evidence ledger lifecycle payload"}',
            "canonical_done",
        ),
    )
    conn.execute(
        """
        INSERT INTO local_context_queries (
            query_hash, intent, result_count, confidence, warnings_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("hash-g06", "evidence_ledger_prior_work", 3, 0.82, "[]", 1779170000.0),
    )
    conn.commit()

    results = evidence_ledger.search_evidence("evidence ledger", include_transcripts=False, limit=50)
    source_types = {item.source_type for item in results}

    assert "task" in source_types, _debug_candidate_task_sources()
    assert "workflow" in source_types
    assert "workflow_checkpoint" in source_types
    assert "change_log" in source_types
    assert "diary" in source_types
    assert "lifecycle" in source_types
    assert "continuity" in source_types
    assert "local_context" in source_types

    conversation_results = evidence_ledger.search_evidence(
        conversation_id="conv-g06-evidence",
        include_transcripts=False,
        limit=20,
    )
    assert {item.source_type for item in conversation_results} >= {"lifecycle", "continuity"}

    file_results = evidence_ledger.search_evidence(file_path="src/evidence_ledger.py", include_transcripts=False)
    assert any(item.source_type == "change_log" for item in file_results)
    assert any(item.source_type == "task" for item in file_results)


def test_search_evidence_uses_core_db_when_package_alias_is_stale(monkeypatch):
    from db._core import get_db as core_get_db

    conn = core_get_db()
    conn.execute(
        """
        INSERT INTO protocol_tasks (task_id, session_id, goal, task_type, files, verification_step, status, opened_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            "PT-G06-STALE-ALIAS",
            "sid-g06-stale-alias",
            "G06 evidence ledger stale alias task",
            "edit",
            '["src/evidence_ledger.py"]',
            "pytest stale alias",
            "open",
        ),
    )
    conn.commit()
    stale = sqlite3.connect(":memory:")
    stale.row_factory = sqlite3.Row
    monkeypatch.setattr(db, "get_db", lambda: stale)
    try:
        results = evidence_ledger.search_evidence("stale alias task", include_transcripts=False, limit=20)

        assert any(item.source_type == "task" and item.source_id == "PT-G06-STALE-ALIAS" for item in results), _debug_candidate_task_sources()
    finally:
        stale.close()


def test_search_evidence_includes_source_module_stale_db_alias(monkeypatch):
    import db._protocol as db_protocol

    stale = sqlite3.connect(":memory:")
    stale.row_factory = sqlite3.Row
    schema = db.get_db().execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'protocol_tasks'"
    ).fetchone()
    stale.execute(schema["sql"])
    stale.execute(
        """
        INSERT INTO protocol_tasks (task_id, session_id, goal, task_type, files, verification_step, status, opened_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            "PT-G06-MODULE-STALE",
            "sid-g06-stale-module",
            "G06 evidence ledger stale module task",
            "edit",
            '["src/evidence_ledger.py"]',
            "pytest stale module alias",
            "open",
        ),
    )
    stale.commit()
    monkeypatch.setattr(db_protocol, "get_db", lambda: stale)
    try:
        results = evidence_ledger.search_evidence("stale module task", include_transcripts=False, limit=20)

        assert any(item.source_type == "task" and item.source_id == "PT-G06-MODULE-STALE" for item in results), _debug_candidate_task_sources()
    finally:
        stale.close()


def test_transcripts_are_explicit_fallback_and_redacted(monkeypatch):
    import transcript_utils

    monkeypatch.setattr(
        transcript_utils,
        "search_transcripts",
        lambda query, hours=24, limit=10, client="": [
            {
                "session_file": "codex:g06.jsonl",
                "display_name": "g06.jsonl",
                "session_path": "/tmp/g06.jsonl",
                "client": "codex",
                "modified": "2026-05-19T06:30:00",
                "message_count": 4,
                "_score": 0.91,
                "matched_messages": [
                    {
                        "role": "assistant",
                        "index": 12,
                        "snippet": "prior work evidence with password=transcriptsecret",
                        "score": 0.91,
                    }
                ],
            }
        ],
    )

    assert evidence_ledger.search_evidence(
        "prior work evidence",
        source_types=["transcript"],
        include_transcripts=False,
    ) == []

    results = evidence_ledger.search_evidence(
        "prior work evidence",
        source_types=["transcript"],
        include_transcripts=True,
        limit=5,
    )
    payload = json.dumps(evidence_ledger.evidence_to_dicts(results), ensure_ascii=False)

    assert len(results) == 1
    assert results[0].source_type == "transcript"
    assert "[REDACTED]" in payload
    assert "transcriptsecret" not in payload
