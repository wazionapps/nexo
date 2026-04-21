"""Unit + integration coverage for the autonomy-mandate guardrail on
`nexo_followup_create` (followup NF-DS-45569A27).

The guardrail is intentionally surgical: only procrastination-shaped
followups (owner=user or date within 7 days) are blocked when a mandate
is active, and three explicit exceptions are preserved.
"""
from __future__ import annotations

import importlib
import time
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def mandate_module(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    import autonomy_mandate as am
    importlib.reload(am)
    return am


def _today_plus(days: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


def test_marker_detection_ingests_and_activates(mandate_module):
    am = mandate_module
    st = am.maybe_ingest_from_text(
        "Hazlo todo ya, sin esperas", session_id="sid-1"
    )
    assert st is not None
    assert st.active
    assert st.session_id == "sid-1"
    loaded = am.load_state()
    assert loaded is not None and loaded.active


def test_marker_detection_ingests_semantic_mandate(mandate_module):
    am = mandate_module
    st = am.maybe_ingest_from_text(
        "Stop postponing this and just execute the in-scope work now.",
        session_id="sid-semantic",
        classifier=lambda **_: True,
    )
    assert st is not None
    assert st.marker == "semantic-autonomy-mandate"
    loaded = am.load_state()
    assert loaded is not None and loaded.marker == "semantic-autonomy-mandate"


def test_marker_detection_ignores_unrelated_text(mandate_module):
    am = mandate_module
    assert am.maybe_ingest_from_text(
        "pasa al siguiente item",
        "sid",
        classifier=lambda **_: False,
    ) is None
    assert am.load_state() is None


def test_expired_mandate_does_not_block(mandate_module):
    am = mandate_module
    st = am.set_mandate(
        session_id="sid", marker="autonomía total", ttl_seconds=60
    )
    # Force expiry by rewriting the on-disk record.
    import json
    expired = st.to_dict()
    expired["expires_at"] = time.time() - 10
    am.STATE_PATH.write_text(json.dumps(expired))
    assert am.load_state() is None
    assert am.check_followup_against_mandate(
        owner="user", date="", description="doit"
    ) is None


def test_owner_user_blocked_under_mandate(mandate_module):
    am = mandate_module
    am.set_mandate(session_id="sid", marker="autonomía total")
    err = am.check_followup_against_mandate(
        owner="user", date="", description="finish whatever"
    )
    assert err is not None and "blocked" in err.lower()
    assert "owner=user" in err


def test_date_inside_window_blocked(mandate_module):
    am = mandate_module
    am.set_mandate(session_id="sid", marker="sin esperas")
    err = am.check_followup_against_mandate(
        owner="shared", date=_today_plus(3), description="finish whatever"
    )
    assert err is not None
    assert "within 7 days" in err


def test_date_outside_window_allowed(mandate_module):
    am = mandate_module
    am.set_mandate(session_id="sid", marker="autonomía total")
    err = am.check_followup_against_mandate(
        owner="shared", date=_today_plus(14), description="long horizon"
    )
    assert err is None


def test_agent_owner_not_blocked(mandate_module):
    am = mandate_module
    am.set_mandate(session_id="sid", marker="autonomía total")
    err = am.check_followup_against_mandate(
        owner="agent", date="", description="agent bookkeeping"
    )
    assert err is None


def test_exception_keyword_in_description_bypasses(mandate_module):
    am = mandate_module
    am.set_mandate(session_id="sid", marker="autonomía total")
    err = am.check_followup_against_mandate(
        owner="user",
        date="",
        description="Descarga >1GB del dataset raw antes del paso siguiente",
    )
    assert err is None


def test_explicit_exception_param_bypasses(mandate_module):
    am = mandate_module
    am.set_mandate(session_id="sid", marker="autonomía total")
    err = am.check_followup_against_mandate(
        owner="user",
        date=_today_plus(2),
        description="rotar api key",
        exception="credencial que el operador debe introducir a mano",
    )
    assert err is None


def test_maria_presence_session_bypasses(mandate_module):
    am = mandate_module
    am.set_mandate(session_id="sid", marker="sin esperas")
    err = am.check_followup_against_mandate(
        owner="user",
        date=_today_plus(1),
        description="Revisar inbox con María presencial el lunes",
    )
    assert err is None


def test_handle_followup_create_blocks_when_mandate_active(mandate_module):
    # Integration: the CRUD handler wires the guardrail.
    import tools_reminders_crud as crud
    importlib.reload(crud)
    mandate_module.set_mandate(session_id="sid", marker="autonomía total")

    out = crud.handle_followup_create(
        id="NF-TEST-MANDATE-1",
        description="Revisar esto mañana",
        date=_today_plus(1),
        owner="user",
    )
    assert out.startswith("ERROR: Autonomy mandate active")


def test_handle_followup_create_allows_with_exception(mandate_module):
    import tools_reminders_crud as crud
    importlib.reload(crud)
    mandate_module.set_mandate(session_id="sid", marker="autonomía total")

    out = crud.handle_followup_create(
        id="NF-TEST-MANDATE-2",
        description="Descarga >1GB del backup nocturno",
        date=_today_plus(1),
        owner="user",
    )
    assert not out.startswith("ERROR: Autonomy mandate active")
