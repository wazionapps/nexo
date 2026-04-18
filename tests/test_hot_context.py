"""Tests for NEXO recent 24h hot-context memory."""

from __future__ import annotations

import importlib
import json
import time


def _register_session(sid: str):
    from db import register_session

    register_session(sid, "hot context test")
    return sid


def test_capture_and_bundle_recent_context(isolated_db):
    import db

    db.capture_context_event(
        event_type="context_capture",
        title="DNS example-shop external ownership",
        summary="Alice aclaró que example-shop no es suyo.",
        body="No volver a preguntarle por ese dominio.",
        topic="example-shop ownership",
        state="resolved",
        actor="nexo",
        source_type="manual",
        source_id="ownership-note-1",
    )

    bundle = db.build_pre_action_context(query="example-shop ownership", hours=24, limit=5)
    assert bundle["has_matches"] is True
    assert bundle["contexts"]
    assert any("example-shop" in (item.get("context_key") or "") or "example-shop" in (item.get("title") or "").lower() for item in bundle["contexts"])
    assert any(event["event_type"] == "context_capture" for event in bundle["events"])


def test_heartbeat_surfaces_recent_context_from_last_hours(isolated_db):
    import tools_sessions

    importlib.reload(tools_sessions)

    sid_1 = _register_session("nexo-1001-3001")
    sid_2 = _register_session("nexo-1002-3002")

    first = tools_sessions.handle_heartbeat(
        sid_1,
        "Registrar ownership",
        "Alice explicó que example-shop no es suyo y no debo escalarle ese dominio.",
    )
    assert "OK: nexo-1001-3001" in first

    second = tools_sessions.handle_heartbeat(
        sid_2,
        "Revisar ownership",
        "Necesito recordar si example-shop es suyo o no.",
    )
    assert "RECENT CONTEXT (24h)" in second
    assert "example-shop" in second.lower()


def test_task_open_includes_recent_context_excerpt(isolated_db):
    import db
    from plugins.protocol import handle_task_open

    sid = _register_session("nexo-2001-3001")
    db.capture_context_event(
        event_type="context_capture",
        title="Bob owns example-travel",
        summary="Este tema debe perseguirse con Bob, no con Alice salvo bloqueo técnico.",
        topic="example-travel ownership",
        state="active",
        actor="nexo",
        source_type="manual",
        source_id="ownership-note-2",
    )

    payload = json.loads(
        handle_task_open(
            sid=sid,
            goal="Decidir qué hacer con example-travel",
            task_type="analyze",
            area="nexo",
            context_hint="Ver si debe preguntarse a Alice o a Bob por example-travel.",
            verification_step="inspeccionar contexto reciente",
            evidence_refs='["recent-context"]',
        )
    )
    assert payload["ok"] is True
    assert payload["recent_context"]["has_matches"] is True
    assert "example-travel" in payload["recent_context"]["excerpt"].lower()


def test_followup_and_reminder_changes_feed_hot_context(isolated_db):
    import db

    db.create_reminder("R-HOT-1", "Preguntar a Bob por el dominio", date="2026-04-09")
    db.create_followup(
        "NF-HOT-1",
        "Revisar ownership de example-travel",
        date="2026-04-09",
        verification="Confirmar responsable",
        reasoning="Evitar escalar a Alice lo que es de Bob.",
    )
    db.add_followup_note("NF-HOT-1", "Alice indicó que es de Bob.", actor="nexo")

    bundle = db.build_pre_action_context(query="example-travel Bob", hours=24, limit=6)
    assert bundle["has_matches"] is True
    followup_contexts = [item for item in bundle["contexts"] if item.get("context_key") == "followup:NF-HOT-1"]
    reminder_contexts = [item for item in bundle["contexts"] if item.get("context_key") == "reminder:R-HOT-1"]
    assert followup_contexts
    assert reminder_contexts
    assert any(event["event_type"] == "followup_note" for event in bundle["events"])


def test_heartbeat_warns_when_user_correction_has_no_recent_learning(isolated_db):
    import tools_sessions

    importlib.reload(tools_sessions)

    sid = _register_session("nexo-3001-4001")
    output = tools_sessions.handle_heartbeat(
        sid,
        "Ajustar flujo",
        "Eso está mal, corrige esto y no repitas el mismo error.",
    )

    assert "LEARNING REMINDER" in output
    assert "nexo_learning_add" in output


def test_heartbeat_drive_detection_disables_llm_by_default(isolated_db, monkeypatch):
    import tools_drive
    import tools_sessions

    importlib.reload(tools_sessions)

    captured: dict[str, object] = {}

    def _fake_detect(context_hint, source, source_id="", area="", *, allow_llm=False):
        captured["allow_llm"] = allow_llm
        return None

    monkeypatch.delenv("NEXO_DRIVE_LLM_IN_HEARTBEAT", raising=False)
    monkeypatch.setattr(tools_drive, "detect_drive_signal", _fake_detect)

    sid = _register_session("nexo-3003-4003")
    tools_sessions.handle_heartbeat(
        sid,
        "Comprobar drive",
        "Esto parece un texto suficientemente largo para que el heartbeat pruebe drive.",
    )

    assert captured["allow_llm"] is False


def test_heartbeat_drive_detection_can_enable_llm_via_env(isolated_db, monkeypatch):
    import tools_drive
    import tools_sessions

    importlib.reload(tools_sessions)

    captured: dict[str, object] = {}

    def _fake_detect(context_hint, source, source_id="", area="", *, allow_llm=False):
        captured["allow_llm"] = allow_llm
        return None

    monkeypatch.setenv("NEXO_DRIVE_LLM_IN_HEARTBEAT", "1")
    monkeypatch.setattr(tools_drive, "detect_drive_signal", _fake_detect)

    sid = _register_session("nexo-3004-4004")
    tools_sessions.handle_heartbeat(
        sid,
        "Comprobar drive",
        "Esto parece un texto suficientemente largo para que el heartbeat pruebe drive.",
    )

    assert captured["allow_llm"] is True


def test_heartbeat_skips_learning_reminder_when_recent_learning_exists(isolated_db):
    import db
    import tools_sessions

    importlib.reload(tools_sessions)

    sid = _register_session("nexo-3002-4002")
    now = time.time()
    conn = db.get_db()
    conn.execute(
        """
        INSERT INTO learnings (category, title, content, reasoning, prevention, applies_to, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "nexo",
            "Recent correction learning",
            "Keep correction learnings fresh.",
            "",
            "",
            "",
            "active",
            now,
            now,
        ),
    )
    conn.commit()

    output = tools_sessions.handle_heartbeat(
        sid,
        "Ajustar flujo",
        "Wrong approach. Corrígelo y evita repetirlo.",
    )

    assert "LEARNING REMINDER" not in output
