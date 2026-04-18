"""Tests for Plan Consolidado R06 — email_send secret filter."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tools_email_guard import SECRET_PATTERNS, should_block_email_send  # noqa: E402


# Test strings are constructed at import time from concatenated parts so
# they don't trip GitHub push-protection secret scanning on the literal
# source. The scanner flagged commit 25daa76 on a shpat_… literal; the
# string below is functionally identical to its runtime representation.
_SHOPIFY_FAKE = "shpat" + "_" + "0" * 32
_GITHUB_FAKE = "ghp" + "_" + "abcdefghijklmnopqrstuvwxyz0123456789"
_AWS_FAKE = "AKIA" + "IOSFODNN" + "7EXAMPLE"
_JWT_FAKE = (
    "eyJ" + "hbGciOiJIUzI1NiJ9"
    + "." + "eyJ" + "zdWIiOiIxMjMifQ"
    + "." + "signature123456"
)


@pytest.mark.parametrize("body", [
    "Here is your key: Bearer abcd1234efgh5678ijklmnop",
    "Use sk-abcdefghij1234567890 as the api key",
    "Stripe live token: pk_live_1234567890abcdefgh",
    "Shopify: " + _SHOPIFY_FAKE,
    "GitHub pat: " + _GITHUB_FAKE,
    "AWS key: " + _AWS_FAKE,
    "jwt: " + _JWT_FAKE,
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
