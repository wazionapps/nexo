"""Tests for Plan Consolidado R06 — email_send secret filter."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tools_email_guard import SECRET_PATTERNS, should_block_email_send  # noqa: E402


@pytest.mark.parametrize("body", [
    "Here is your key: Bearer abcd1234efgh5678ijklmnop",
    "Use sk-abcdefghij1234567890 as the api key",
    "Stripe live token: pk_live_1234567890abcdefgh",
    "Shopify: shpat_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
    "GitHub pat: ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "AWS key: AKIAIOSFODNN7EXAMPLE",
    "jwt: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature123456",
    "-----BEGIN RSA PRIVATE KEY-----\\nMII...\\n-----END RSA PRIVATE KEY-----",
    'api_key="abcd1234efgh5678"',
    "mysql -u root -pmysecretpass db",
    'password: "supersecret123"',
])
def test_regex_blocks_known_secret_shapes(body):
    blocked, reason = should_block_email_send(body)
    assert blocked is True
    assert "secret pattern matched" in reason


def test_plain_email_body_passes():
    body = (
        "Hola María,\n"
        "ya he revisado los informes de la última semana.\n"
        "Avísame si quieres que iteremos el lunes.\n"
    )
    blocked, reason = should_block_email_send(body)
    assert blocked is False
    assert reason == "ok"


def test_classifier_yes_escalates_even_without_regex():
    body = "This is some innocuous-looking message."
    blocked, reason = should_block_email_send(body, classifier=lambda q, b: "yes")
    assert blocked is True
    assert "classifier flagged" in reason


def test_classifier_no_passes():
    blocked, reason = should_block_email_send(
        "Totally fine message",
        classifier=lambda q, b: "no",
    )
    assert blocked is False


def test_classifier_exception_fails_closed_allows():
    def boom(q, b):
        raise RuntimeError("sdk down")

    blocked, reason = should_block_email_send("harmless", classifier=boom)
    assert blocked is False
    assert "classifier unavailable" in reason


def test_empty_or_non_string_body_is_allowed():
    assert should_block_email_send("") == (False, "ok")
    assert should_block_email_send(None)[0] is False  # type: ignore[arg-type]


def test_secret_patterns_set_is_non_empty():
    assert len(SECRET_PATTERNS) >= 10
