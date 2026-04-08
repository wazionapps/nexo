"""Tests for NEXO recent 24h hot-context memory."""

from __future__ import annotations

import importlib
import json


def _register_session(sid: str):
    from db import register_session

    register_session(sid, "hot context test")
    return sid


def test_capture_and_bundle_recent_context(isolated_db):
    import db

    db.capture_context_event(
        event_type="context_capture",
        title="DNS recambios external ownership",
        summary="Francisco aclaró que recambiosbmw no es suyo.",
        body="No volver a preguntarle por ese dominio.",
        topic="recambiosbmw ownership",
        state="resolved",
        actor="nexo",
        source_type="manual",
        source_id="ownership-note-1",
    )

    bundle = db.build_pre_action_context(query="recambiosbmw ownership", hours=24, limit=5)
    assert bundle["has_matches"] is True
    assert bundle["contexts"]
    assert any("recambiosbmw" in (item.get("context_key") or "") or "recambios" in (item.get("title") or "").lower() for item in bundle["contexts"])
    assert any(event["event_type"] == "context_capture" for event in bundle["events"])


def test_heartbeat_surfaces_recent_context_from_last_hours(isolated_db):
    import tools_sessions

    importlib.reload(tools_sessions)

    sid_1 = _register_session("nexo-1001-3001")
    sid_2 = _register_session("nexo-1002-3002")

    first = tools_sessions.handle_heartbeat(
        sid_1,
        "Registrar ownership",
        "Francisco explicó que recambiosbmw no es suyo y no debo escalarle ese dominio.",
    )
    assert "OK: nexo-1001-3001" in first

    second = tools_sessions.handle_heartbeat(
        sid_2,
        "Revisar ownership",
        "Necesito recordar si recambiosbmw es suyo o no.",
    )
    assert "RECENT CONTEXT (24h)" in second
    assert "recambiosbmw" in second.lower()


def test_task_open_includes_recent_context_excerpt(isolated_db):
    import db
    from plugins.protocol import handle_task_open

    sid = _register_session("nexo-2001-3001")
    db.capture_context_event(
        event_type="context_capture",
        title="Maria owns holidays2thecanaries",
        summary="Este tema debe perseguirse con María, no con Francisco salvo bloqueo técnico.",
        topic="holidays2thecanaries ownership",
        state="active",
        actor="nexo",
        source_type="manual",
        source_id="ownership-note-2",
    )

    payload = json.loads(
        handle_task_open(
            sid=sid,
            goal="Decidir qué hacer con holidays2thecanaries",
            task_type="analyze",
            area="nexo",
            context_hint="Ver si debe preguntarse a Francisco o a María por holidays2thecanaries.",
            verification_step="inspeccionar contexto reciente",
            evidence_refs='["recent-context"]',
        )
    )
    assert payload["ok"] is True
    assert payload["recent_context"]["has_matches"] is True
    assert "holidays2thecanaries" in payload["recent_context"]["excerpt"].lower()


def test_followup_and_reminder_changes_feed_hot_context(isolated_db):
    import db

    db.create_reminder("R-HOT-1", "Preguntar a María por el dominio", date="2026-04-09")
    db.create_followup(
        "NF-HOT-1",
        "Revisar ownership de holidays2thecanaries",
        date="2026-04-09",
        verification="Confirmar responsable",
        reasoning="Evitar escalar a Francisco lo que es de María.",
    )
    db.add_followup_note("NF-HOT-1", "Francisco indicó que es de María.", actor="nexo")

    bundle = db.build_pre_action_context(query="holidays2thecanaries Maria", hours=24, limit=6)
    assert bundle["has_matches"] is True
    followup_contexts = [item for item in bundle["contexts"] if item.get("context_key") == "followup:NF-HOT-1"]
    reminder_contexts = [item for item in bundle["contexts"] if item.get("context_key") == "reminder:R-HOT-1"]
    assert followup_contexts
    assert reminder_contexts
    assert any(event["event_type"] == "followup_note" for event in bundle["events"])
