"""Tests for episodic memory diary warnings."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def test_session_diary_write_distinguishes_recent_and_historical_commit_ref_gaps(monkeypatch, tmp_path):
    from plugins import episodic_memory
    import db

    db_path = tmp_path / "nexo.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE change_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            files TEXT DEFAULT '',
            commit_ref TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        """CREATE TABLE decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outcome TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        "INSERT INTO change_log (files, commit_ref, created_at) VALUES ('src/plugins/protocol.py', '', datetime('now', '-1 day'))"
    )
    conn.execute(
        "INSERT INTO change_log (files, commit_ref, created_at) VALUES ('src/plugins/protocol.py', '', datetime('now', '-20 days'))"
    )
    conn.execute(
        "INSERT INTO change_log (files, commit_ref, created_at) VALUES ('/Users/franciscoc/.nexo/operations/orchestrator-state.json', '', datetime('now', '-1 day'))"
    )
    conn.commit()

    monkeypatch.setattr(db, "delete_diary_draft", lambda sid: None)
    monkeypatch.setattr(
        episodic_memory,
        "write_session_diary",
        lambda *args, **kwargs: {"id": 1},
    )
    monkeypatch.setattr(episodic_memory, "get_db", lambda: conn)
    monkeypatch.setattr(db, "get_db", lambda: conn)

    result = episodic_memory.handle_session_diary_write(
        decisions="none",
        summary="Resumen corto",
        session_id="sid-test",
        self_critique="critica",
        domain="nexo",
    )
    conn.close()

    assert "1 cambio reciente repo changes without commit_ref (2 cambios de repo total)" in result


def test_change_log_message_distinguishes_repo_and_local_commit_refs(monkeypatch):
    from plugins import episodic_memory

    captured = []

    def _fake_log_change(session_id, files, what_changed, why, triggered_by, affects, risks, verify, commit_ref):
        captured.append((session_id, files, commit_ref))
        return {"id": len(captured)}

    monkeypatch.setattr(episodic_memory, "log_change", _fake_log_change)
    monkeypatch.setattr(episodic_memory, "_cognitive_ingest_safe", lambda *args, **kwargs: None)

    repo_msg = episodic_memory.handle_change_log(
        files="src/plugins/protocol.py",
        what_changed="Ajuste de validación",
        why="Corregir warning engañoso",
        session_id="sid-test",
    )
    local_msg = episodic_memory.handle_change_log(
        files="/Users/franciscoc/.nexo/operations/orchestrator-state.json",
        what_changed="Checkpoint local",
        why="Persistir continuidad",
        session_id="sid-test",
    )
    benchmark_msg = episodic_memory.handle_change_log(
        files="benchmarks/README.md",
        what_changed="Actualizar benchmark",
        why="Documentar comparativa",
        session_id="sid-test",
    )

    assert "nexo_change_commit(1, 'hash')" in repo_msg
    assert "'server-direct'" in local_msg
    assert "'local-uncommitted'" in local_msg
    assert "nexo_change_commit(3, 'hash')" in benchmark_msg


def test_session_diary_write_accepts_payload_json_for_long_multifield_calls(monkeypatch):
    from plugins import episodic_memory
    import db

    captured = {}

    monkeypatch.setattr(db, "delete_diary_draft", lambda sid: None)

    def _fake_write_session_diary(session_id, decisions, summary, discarded="", pending="", context_next="", mental_state="", domain="", user_signals="", self_critique="", source="claude"):
        captured.update({
            "session_id": session_id,
            "decisions": decisions,
            "summary": summary,
            "discarded": discarded,
            "pending": pending,
            "context_next": context_next,
            "mental_state": mental_state,
            "domain": domain,
            "user_signals": user_signals,
            "self_critique": self_critique,
            "source": source,
        })
        return {"id": 7}

    monkeypatch.setattr(episodic_memory, "write_session_diary", _fake_write_session_diary)
    monkeypatch.setattr(episodic_memory, "_cognitive_ingest_safe", lambda *args, **kwargs: None)

    payload = {
        "session_id": "sid-json",
        "summary": "Resumen final",
        "decisions": ["Cerrar el bug del diary", "Mantener el cierre graceful testeado"],
        "pending": "Revisar el siguiente bloque UX",
        "context_next": "Seguir por Desktop runtime",
        "mental_state": "Tengo el mapa claro",
        "user_signals": "Directo y sin pedir relleno",
        "self_critique": "Debí cerrar antes el contrato del diario",
        "domain": "nexo",
        "source": "claude",
    }

    result = episodic_memory.handle_session_diary_write(payload_json=json.dumps(payload, ensure_ascii=False))

    assert "Session diary #7 [nexo] saved: Resumen final" in result
    assert captured["session_id"] == "sid-json"
    assert captured["summary"] == "Resumen final"
    assert '"Cerrar el bug del diary"' in captured["decisions"]
    assert captured["pending"] == "Revisar el siguiente bloque UX"
    assert captured["context_next"] == "Seguir por Desktop runtime"
    assert captured["mental_state"] == "Tengo el mapa claro"
    assert captured["self_critique"] == "Debí cerrar antes el contrato del diario"


def test_session_diary_write_payload_json_rejects_missing_summary(monkeypatch):
    from plugins import episodic_memory

    monkeypatch.setattr(episodic_memory, "_cognitive_ingest_safe", lambda *args, **kwargs: None)

    result = episodic_memory.handle_session_diary_write(
        payload_json=json.dumps({"decisions": "solo decisiones"}, ensure_ascii=False)
    )

    assert result == "ERROR: summary is required. Provide it directly or inside payload_json."


def test_session_diary_quality_marks_auto_close_minimal():
    import db

    row = db.write_session_diary(
        session_id="sid-auto",
        decisions="",
        summary="[AUTO-CLOSE] Minimal diary",
        mental_state="0 heartbeats; Minimal diary",
        source="auto-close",
    )

    assert row["quality_tier"] == "auto_close_minimal"
    assert row["quality_score"] < 25


def test_session_diary_reads_prefer_agent_authored_over_minimal_fallback():
    import db

    fallback = db.write_session_diary(
        session_id="sid-fallback",
        decisions="",
        summary="[AUTO-CLOSE] Minimal diary",
        mental_state="0 heartbeats; Minimal diary",
        source="auto-close",
    )
    agent = db.write_session_diary(
        session_id="sid-agent",
        decisions="Keep the release gate explicit",
        summary="NEXO release scope reviewed",
        pending="Finish transcript index",
        context_next="Continue with N10 and N20",
        mental_state="Context is complete",
        self_critique="Avoid relying on raw transcript fallback first",
        source="claude",
    )

    rows = db.read_session_diary(last_n=10, include_automated=True)

    assert rows[0]["id"] == agent["id"]
    assert rows[-1]["id"] == fallback["id"]
    assert rows[0]["quality_tier"] == "agent_authored"
    assert rows[0]["quality_score"] > rows[-1]["quality_score"]


def test_session_diary_default_read_is_client_neutral_and_excludes_automation():
    import db

    codex = db.write_session_diary(
        session_id="sid-codex",
        decisions="Continue release",
        summary="Codex interactive session",
        pending="Ship release",
        context_next="Run focused checks",
        source="codex",
    )
    desktop = db.write_session_diary(
        session_id="sid-desktop",
        decisions="Continue review",
        summary="Desktop interactive session",
        pending="Review map",
        context_next="Open Preferences",
        source="desktop",
    )
    db.write_session_diary(
        session_id="sid-cron",
        decisions="",
        summary="Cron maintenance",
        mental_state="scheduled automation",
        source="cron",
    )
    db.write_session_diary(
        session_id="sid-auto",
        decisions="",
        summary="[AUTO-CLOSE] Minimal diary",
        mental_state="0 heartbeats; Minimal diary",
        source="auto-close",
    )

    rows = db.read_session_diary(last_n=10)
    session_ids = {row["session_id"] for row in rows}

    assert codex["session_id"] in session_ids
    assert desktop["session_id"] in session_ids
    assert "sid-cron" not in session_ids
    assert "sid-auto" not in session_ids


def test_change_log_retention_policy_uses_env(monkeypatch):
    import db

    monkeypatch.setenv("NEXO_CHANGE_LOG_RETENTION_DAYS", "7")

    assert db.change_log_retention_days() == 7
    assert db.change_log_retention_policy()["retention_days"] == 7
