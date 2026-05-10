from __future__ import annotations

import json


def test_memory_events_schema_and_idempotent_record(isolated_db):
    import db

    first = db.record_memory_event(
        event_type="tool_write",
        source_type="tool",
        source_id="tool-1",
        session_id="nexo-1-1",
        tool_name="Edit",
        file_paths=["src/example.py"],
        tool_input={"file_path": "src/example.py", "token": "sk-testsecretvalue0123456789"},
        tool_output="changed file",
        idempotency_key="tool-1",
    )
    second = db.record_memory_event(
        event_type="tool_write",
        source_type="tool",
        source_id="tool-1",
        session_id="nexo-1-1",
        tool_name="Edit",
        file_paths=["src/example.py"],
        tool_input={"file_path": "src/example.py"},
        tool_output="changed file",
        idempotency_key="tool-1",
    )

    assert first["ok"] is True
    assert first["inserted"] is True
    assert second["ok"] is True
    assert second["inserted"] is False
    assert first["event_uid"] == second["event_uid"]

    rows = db.list_memory_events(source_type="tool", source_id="tool-1")
    assert len(rows) == 1
    assert rows[0]["file_paths"] == ["src/example.py"]
    assert rows[0]["input_hash"]
    assert rows[0]["redaction_applied"] is True

    stats = db.memory_event_stats(days=1)
    assert stats["total"] == 1
    assert stats["by_event_type"]["tool_write"] == 1
    assert stats["by_source_type"]["tool"] == 1

    queue_stats = db.memory_observation_stats(days=1)
    assert queue_stats["queue"]["pending"] == 1


def test_memory_events_migration_can_apply_to_existing_db(isolated_db):
    import db

    conn = db.get_db()
    conn.execute("DROP TRIGGER IF EXISTS memory_observations_fts_insert")
    conn.execute("DROP TRIGGER IF EXISTS memory_observations_fts_delete")
    conn.execute("DROP TRIGGER IF EXISTS memory_observations_fts_update")
    conn.execute("DROP TABLE IF EXISTS memory_observations_fts")
    conn.execute("DROP TABLE memory_observation_queue")
    conn.execute("DROP TABLE memory_observations")
    conn.execute("DROP TABLE memory_events")
    conn.execute("DELETE FROM schema_migrations WHERE version IN (59, 60, 61, 62)")
    conn.commit()

    db.run_migrations()

    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_events'"
    ).fetchone()
    obs = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_observations'"
    ).fetchone()
    fts = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_observations_fts'"
    ).fetchone()
    assert row is not None
    assert obs is not None
    assert fts is not None
    assert db.get_schema_version() >= 62


def test_post_edit_change_log_records_memory_event(isolated_db):
    import db
    from hooks.post_edit_change_log import record_post_edit_change

    db.register_session("nexo-2-2", "memory event hook test", external_session_id="claude-session-2")
    payload = {
        "tool_name": "Edit",
        "tool_use_id": "tool-use-2",
        "session_id": "claude-session-2",
        "tool_input": {"file_path": "src/changed.py", "old_string": "a", "new_string": "b"},
        "tool_response": "ok",
    }

    result = record_post_edit_change(payload)

    assert result["ok"] is True
    assert result["memory_event_ok"] is True
    rows = db.list_memory_events(source_type="tool", source_id="tool-use-2")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "tool_write"
    assert rows[0]["session_id"] == "nexo-2-2"
    assert rows[0]["file_paths"] == ["src/changed.py"]

    processed = db.process_memory_observation_queue(limit=10)
    assert processed["processed"] == 1
    observations = db.list_memory_observations(observation_type="code_change")
    assert len(observations) == 1
    assert "src/changed.py" in observations[0]["summary"]
    assert observations[0]["evidence_refs"][0].startswith("memory_event:")


def test_post_edit_change_log_surfaces_memory_event_failure(isolated_db, monkeypatch):
    import db
    from hooks.post_edit_change_log import record_post_edit_change

    db.register_session("nexo-2-3", "memory event hook failure test", external_session_id="claude-session-3")

    def fail_record(*args, **kwargs):
        raise RuntimeError("memory unavailable")

    monkeypatch.setattr(db, "record_memory_event", fail_record)
    result = record_post_edit_change(
        {
            "tool_name": "Edit",
            "tool_use_id": "tool-use-fail",
            "session_id": "claude-session-3",
            "tool_input": {"file_path": "src/failed_memory.py"},
            "tool_response": "ok",
        }
    )

    assert result["ok"] is True
    assert result["memory_event_ok"] is False
    assert "memory unavailable" in result["memory_event"]["error"]


def test_task_close_records_memory_event(isolated_db):
    import db
    from plugins.protocol import handle_task_close

    db.register_session("nexo-3-3", "protocol close memory event")
    task = db.create_protocol_task(
        "nexo-3-3",
        "Record protocol close in memory events",
        task_type="answer",
        area="nexo-brain-memory",
        project_hint="nexo",
        must_verify=False,
    )

    response = json.loads(
        handle_task_close(
            sid="nexo-3-3",
            task_id=task["task_id"],
            outcome="done",
            evidence="Verified by unit test: protocol task close should write a memory event.",
        )
    )

    assert response["ok"] is True
    assert response["memory_event_ok"] is True
    rows = db.list_memory_events(source_type="protocol_task", source_id=task["task_id"])
    assert len(rows) == 1
    assert rows[0]["event_type"] == "protocol_task_done"
    assert rows[0]["project_key"] == "nexo"

    processed = db.process_memory_observation_queue(limit=10)
    assert processed["processed"] == 1
    observations = db.list_memory_observations(query="Record protocol close")
    assert len(observations) == 1
    assert observations[0]["observation_type"] == "task_result"
    assert "Record protocol close in memory events" in observations[0]["summary"]


def test_task_close_surfaces_memory_event_failure(isolated_db, monkeypatch):
    import db
    from plugins.protocol import handle_task_close

    db.register_session("nexo-3-4", "protocol close memory failure")
    task = db.create_protocol_task(
        "nexo-3-4",
        "Surface protocol memory failure",
        task_type="answer",
        area="nexo-brain-memory",
        project_hint="nexo",
        must_verify=False,
    )

    def fail_record(*args, **kwargs):
        raise RuntimeError("memory write failed")

    monkeypatch.setattr(db, "record_memory_event", fail_record)
    response = json.loads(
        handle_task_close(
            sid="nexo-3-4",
            task_id=task["task_id"],
            outcome="done",
            evidence="Task close still succeeds but reports memory failure.",
        )
    )

    assert response["ok"] is True
    assert response["memory_event_ok"] is False
    assert "memory write failed" in response["memory_event"]["error"]


def test_memory_observation_worker_run_once(isolated_db):
    import db
    import memory_observation_worker

    db.record_memory_event(
        event_type="tool_write",
        source_type="tool",
        source_id="tool-worker",
        session_id="nexo-worker",
        tool_name="Write",
        file_paths=["src/worker.py"],
        metadata={"summary": "Write wrote 1 file(s): src/worker.py"},
        idempotency_key="tool-worker",
    )

    result = memory_observation_worker.run_once(limit=5)

    assert result["ok"] is True
    assert result["processed"] == 1
    observations = db.list_memory_observations(query="worker.py")
    assert len(observations) == 1


def test_memory_redacts_metadata_and_observation_payloads(isolated_db):
    import db

    secret = "Bearer abcdefghijklmnopqrstuvwxyz1234567890"
    db.record_memory_event(
        event_type="tool_write",
        source_type="tool",
        source_id="tool-secret",
        session_id="nexo-secret",
        tool_name="Edit",
        file_paths=["src/secret.py"],
        command_digest=f"run with {secret}",
        raw_ref=f"trace {secret}",
        metadata={"summary": f"Edit wrote a file with {secret}", "nested": {"api_key": "api_key=abcdefghijklmnop1234"}},
        idempotency_key="tool-secret",
    )

    event = db.list_memory_events(source_type="tool", source_id="tool-secret")[0]
    db.process_memory_observation_queue(limit=10)
    observation = db.list_memory_observations(query="secret.py")[0]

    assert event["redaction_applied"] is True
    assert secret not in json.dumps(event, ensure_ascii=False)
    assert "[REDACTED]" in event["metadata"]["summary"]
    assert secret not in json.dumps(observation, ensure_ascii=False)
    assert "[REDACTED]" in observation["summary"]


def test_memory_maintenance_processes_queue_and_reports_health(isolated_db):
    import db
    from tools_memory_v2 import handle_memory_health, handle_memory_maintenance

    db.record_memory_event(
        event_type="tool_write",
        source_type="tool",
        source_id="tool-maintenance",
        session_id="nexo-maintenance",
        tool_name="Edit",
        file_paths=["src/maintenance.py"],
        metadata={"summary": "Edit wrote maintenance observation."},
        idempotency_key="tool-maintenance",
    )

    result = db.maintain_memory_observations(process_limit=10)
    health = db.memory_observation_health()
    tool_result = json.loads(handle_memory_maintenance(process_limit=10))
    tool_health = json.loads(handle_memory_health())

    assert result["ok"] is True
    assert result["processed"]["processed"] == 1
    assert health["ok"] is True
    assert health["counts"]["observations"] == 1
    assert tool_result["ok"] is True
    assert tool_health["tables"]["memory_observations"] is True


def test_memory_health_reports_degraded_fts_table(isolated_db):
    import db

    conn = db.get_db()
    conn.execute("DROP TRIGGER IF EXISTS memory_observations_fts_insert")
    conn.execute("DROP TRIGGER IF EXISTS memory_observations_fts_delete")
    conn.execute("DROP TRIGGER IF EXISTS memory_observations_fts_update")
    conn.execute("DROP TABLE memory_observations_fts")
    conn.execute(
        """
        CREATE TABLE memory_observations_fts (
            observation_uid TEXT PRIMARY KEY,
            summary TEXT DEFAULT '',
            subject TEXT DEFAULT '',
            observation_type TEXT DEFAULT '',
            project_key TEXT DEFAULT '',
            entities TEXT DEFAULT ''
        )
        """
    )
    conn.commit()

    health = db.memory_observation_health()

    assert health["tables"]["memory_observations_fts"] is True
    assert health["fts_enabled"] is False
    assert health["fts_degraded"] is True


def test_memory_search_answer_and_timeline_are_evidence_first(isolated_db):
    import db
    from memory_retrieval import answer_memory_question, format_memory_search, memory_search, memory_timeline

    db.record_memory_event(
        event_type="tool_write",
        source_type="tool",
        source_id="tool-search",
        session_id="nexo-search",
        project_key="nexo",
        tool_name="Edit",
        file_paths=["src/search_target.py"],
        metadata={"summary": "Edit wrote 1 file(s): src/search_target.py"},
        idempotency_key="tool-search",
    )

    result = memory_search("search_target", project_hint="nexo", depth="evidence")
    formatted = format_memory_search(result)
    answer = answer_memory_question("search_target", project_hint="nexo")
    timeline = memory_timeline("search_target", project_hint="nexo")

    assert result["count"] >= 1
    assert result["has_evidence"] is True
    assert "memory_event:" in formatted
    assert "Respuesta basada en evidencia" in answer
    assert timeline["candidates"][0]["evidence_refs"][0].startswith("memory_event:")


def test_memory_answer_refuses_candidates_without_evidence_refs(isolated_db):
    import db
    from memory_retrieval import answer_memory_question, memory_search

    db.upsert_memory_observation(
        {
            "observation_uid": "MO-no-evidence",
            "project_key": "nexo",
            "session_id": "nexo-no-evidence",
            "observation_type": "conversation_summary",
            "subject": "unsupported answer",
            "summary": "Unsupported answer candidate without evidence refs.",
            "facts": {},
            "evidence_refs": [],
            "entities": ["unsupported"],
        }
    )

    result = memory_search("unsupported answer", project_hint="nexo")
    answer = answer_memory_question("unsupported answer", project_hint="nexo")

    assert result["count"] >= 1
    assert result["has_evidence"] is False
    assert "No tengo evidencia suficiente" in answer


def test_memory_search_accepts_project_path_hint(isolated_db):
    import db
    from memory_retrieval import memory_search

    db.record_memory_event(
        event_type="tool_write",
        source_type="tool",
        source_id="tool-project-path",
        session_id="nexo-project-path",
        project_key="nexo",
        tool_name="Edit",
        file_paths=["src/project_path.py"],
        metadata={"summary": "Edit wrote project_path evidence."},
        idempotency_key="tool-project-path",
    )

    result = memory_search(
        "project_path",
        project_hint="/Users/franciscoc/Documents/_PhpstormProjects/nexo",
    )

    assert result["count"] >= 1
    assert result["has_evidence"] is True


def test_memory_search_accepts_project_name_hint_when_stored_key_is_path(isolated_db):
    import db
    from memory_retrieval import memory_search

    project_path = "/Users/franciscoc/Documents/_PhpstormProjects/nexo"
    db.record_memory_event(
        event_type="tool_write",
        source_type="tool",
        source_id="tool-project-name",
        session_id="nexo-project-name",
        project_key=project_path,
        tool_name="Edit",
        file_paths=["src/project_name.py"],
        metadata={"summary": "Edit wrote project_name evidence."},
        idempotency_key="tool-project-name",
    )

    result = memory_search("project_name", project_hint="nexo")

    assert result["count"] >= 1
    assert result["has_evidence"] is True


def test_memory_observations_fts_search(isolated_db):
    import db

    inserted = db.upsert_memory_observation(
        {
            "observation_uid": "MO-fts-target",
            "project_key": "nexo",
            "session_id": "nexo-fts",
            "observation_type": "code_change",
            "subject": "src/fast_lookup.py",
            "summary": "Fast lookup indexed the durable memory target for retrieval.",
            "facts": {"file": "src/fast_lookup.py"},
            "evidence_refs": ["memory_event:fts"],
            "entities": ["fast_lookup"],
            "salience": 0.7,
            "confidence": 0.8,
        }
    )

    assert inserted["ok"] is True
    rows = db.search_memory_observations_fts("durable memory target", project_key="nexo")
    assert rows
    assert rows[0]["observation_uid"] == "MO-fts-target"


def test_memory_backfill_from_existing_protocol_tasks(isolated_db):
    import db
    from tools_memory_v2 import handle_memory_backfill

    db.register_session("nexo-4-4", "backfill protocol task")
    task = db.create_protocol_task(
        "nexo-4-4",
        "Backfill existing protocol task into memory observations",
        task_type="execute",
        area="nexo-brain-memory",
        project_hint="nexo",
        must_verify=False,
    )
    db.close_protocol_task(
        task["task_id"],
        outcome="done",
        evidence="Backfill test evidence",
    )

    result = db.backfill_memory_observations(sources=["protocol_tasks"], limit=10)
    observations = db.list_memory_observations(query="Backfill existing protocol task")
    tool_result = json.loads(handle_memory_backfill("protocol_tasks", limit=10))

    assert result["ok"] is True
    assert result["seen"] >= 1
    assert any(item["promotion_state"] == "backfilled" for item in observations)
    assert tool_result["ok"] is True


def test_memory_backfill_pages_past_existing_rows(isolated_db):
    import db

    conn = db.get_db()
    rows = [
        (
            f"nexo-diary-{idx}",
            f"2026-05-10 10:{idx % 60:02d}:00",
            f"decision {idx}",
            f"Session diary bulk backfill row {idx}",
        )
        for idx in range(1002)
    ]
    conn.executemany(
        "INSERT INTO session_diary (session_id, created_at, decisions, summary) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()

    first = db.backfill_memory_observations(sources=["session_diary"], limit=1000)
    second = db.backfill_memory_observations(sources=["session_diary"], limit=1000)
    observation_count = conn.execute(
        "SELECT COUNT(*) FROM memory_observations WHERE observation_type = 'conversation_summary'"
    ).fetchone()[0]

    assert first["created_or_updated"] == 1000
    assert second["created_or_updated"] == 2
    assert observation_count == 1002


def test_startup_runs_small_memory_backfill_for_updates(isolated_db, monkeypatch):
    import db
    from tools_sessions import handle_startup

    monkeypatch.setenv("NEXO_MEMORY_STARTUP_BACKFILL_LIMIT", "10")
    db.register_session("nexo-6-6", "existing update session")
    task = db.create_protocol_task(
        "nexo-6-6",
        "Existing update task should appear after startup",
        task_type="execute",
        area="nexo-brain-memory",
        project_hint="nexo",
        must_verify=False,
    )
    db.close_protocol_task(task["task_id"], outcome="done", evidence="startup backfill evidence")

    startup = handle_startup("startup after update")
    observations = db.list_memory_observations(query="Existing update task")

    assert "SID: nexo-" in startup
    assert observations
    assert observations[0]["promotion_state"] == "backfilled"


def test_memory_answer_refuses_without_evidence(isolated_db):
    from memory_retrieval import answer_memory_question

    answer = answer_memory_question("no existe esta memoria")

    assert "No tengo evidencia suficiente" in answer
