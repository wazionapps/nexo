"""Tests for src/scripts/jargon_first_response.py — NF-DS-32D6E12E."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.jargon_first_response import (  # noqa: E402
    PROHIBITED_TOKENS,
    register_debt_if_violations,
    scan_text,
    user_requested_detail,
)


def test_clean_first_response_returns_no_matches():
    text = "Listo. Ya tienes el verify-bundled-hashes integrado en npm run check."
    assert scan_text(text) == []


def test_detects_each_prohibited_token():
    samples = {
        "Learning #": "Learning #45 aplica aquí.",
        "protocol debt": "abriendo protocol debt al cierre",
        "cortex eval": "el cortex eval salió warn",
        "runtime-core": "runtime-core no respondió",
        "guard_check": "ejecuté guard_check antes de tocar",
        "pre-emptive guard": "saltó la pre-emptive guard",
        "enforcer": "el enforcer pidió heartbeat",
        "task_open": "abrí task_open=create",
        "task_close": "cerré con task_close evidence",
        "heartbeat": "tras el heartbeat de las 22:00",
        "NF-": "el followup NF-ABC1234 sigue abierto",
        "Subscription inactive": "Subscription inactive aparece en la primera respuesta.",
        "WSL": "WSL no está instalado.",
        "scorer": "el scorer lo marcó como warn.",
        "match": "el match semántico falló.",
        "cortex": "cortex devolvió warn.",
    }
    for token, text in samples.items():
        matches = scan_text(text)
        tokens_found = {m["token"] for m in matches}
        assert token in tokens_found, f"missing detection of {token!r} in {text!r}"


def test_token_list_matches_followup_spec():
    expected = {
        "Learning #",
        "protocol debt",
        "cortex eval",
        "runtime-core",
        "guard_check",
        "pre-emptive guard",
        "enforcer",
        "task_open",
        "task_close",
        "heartbeat",
        "NF-",
        "Subscription inactive",
        "WSL",
        "scorer",
        "match",
        "cortex",
    }
    assert set(PROHIBITED_TOKENS) == expected


def test_short_jargon_tokens_use_word_boundaries():
    assert scan_text("El matching interno queda fuera del primer párrafo útil.") == []
    matches = scan_text("El match interno no debe salir sin explicación.")
    assert any(m["token"] == "match" for m in matches)


def test_case_insensitive():
    matches = scan_text("LEARNING #99 + HEARTBEAT a las 22:00 + nf-12345")
    tokens = {m["token"] for m in matches}
    assert "Learning #" in tokens
    assert "heartbeat" in tokens
    assert "NF-" in tokens


def test_only_first_visible_paragraph_is_scanned():
    # 4 paragraphs; jargon only after the 2nd ⇒ should NOT trigger.
    text = (
        "Listo, la verificación pasó.\n\n"
        "Reportaré los matches al final del ciclo.\n\n"
        "Internamente abrí task_open y guard_check, todo OK."
    )
    assert scan_text(text) == []


def test_leading_code_block_does_not_block_detection_in_prose():
    text = (
        "```bash\necho ok\n```\n\n"
        "Listo, sin protocol debt residual."
    )
    matches = scan_text(text)
    assert any(m["token"] == "protocol debt" for m in matches)


def test_user_requested_detail_positive_signals():
    for question in (
        "explícame qué hace el enforcer",
        "stack trace por favor",
        "deep dive on the cortex flow",
        "qué es NF-ABC1234",
        "está fallando el guard_check?",
    ):
        assert user_requested_detail(question), question


def test_user_requested_detail_negative_signals():
    for question in (
        "cómo va el deploy?",
        "publica la release",
        "responde al cliente Maria",
        "envía el briefing matinal",
    ):
        assert not user_requested_detail(question), question


def test_register_debt_skips_when_user_asked_for_detail(monkeypatch):
    calls = []

    def fake_create(*args, **kwargs):
        calls.append((args, kwargs))
        return {"id": 999}

    # Force import path then patch
    import db  # noqa: F401  — populate sys.modules
    monkeypatch.setattr("db.create_protocol_debt", fake_create, raising=False)

    result = register_debt_if_violations(
        "sid-test",
        "Learning #1 y task_open lanzados.",
        user_message="explica qué hace task_open",
    )
    assert result["reason"] == "user_requested_detail"
    assert result["debt_id"] is None
    assert result["violations"]
    assert calls == []


def test_register_debt_skips_when_no_session_id():
    result = register_debt_if_violations(
        "",
        "Learning #1 y task_open lanzados.",
        user_message="cómo va el deploy",
    )
    assert result["reason"] == "missing_session_id"
    assert result["debt_id"] is None


def test_register_debt_called_when_violations_and_no_detail_request(monkeypatch):
    captured = {}

    def fake_create(session_id, debt_type, *, severity="warn", task_id="", evidence=""):
        captured.update(
            session_id=session_id,
            debt_type=debt_type,
            severity=severity,
            task_id=task_id,
            evidence=evidence,
        )
        return {"id": 4242}

    import db  # noqa: F401
    monkeypatch.setattr("db.create_protocol_debt", fake_create, raising=False)

    result = register_debt_if_violations(
        "sid-real",
        "Learning #5 y heartbeat aplicado, NF-ZZ cerrado.",
        user_message="responde al cliente",
        task_id="PT-7",
        evidence_prefix="first_response_check",
    )
    assert result["debt_id"] == 4242
    assert result["reason"] == "debt_registered"
    assert captured["session_id"] == "sid-real"
    assert captured["debt_type"] == "communication_guardrail"
    assert captured["task_id"] == "PT-7"
    assert "first_response_check" in captured["evidence"]


@pytest.mark.parametrize(
    "snippet",
    [
        "",
        "    ",
        "\n\n\n",
    ],
)
def test_empty_inputs_do_not_explode(snippet):
    assert scan_text(snippet) == []
    assert user_requested_detail(snippet) is False
