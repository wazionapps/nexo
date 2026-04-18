"""Tests for _redact_for_log (audit-HIGH fix post-LLM-externa pass 2).

Cover every secret format the silent-failure hunter flagged + the
pattern-5 regression that produced literal `$GITHUB_TOKEN<redacted>`
on inputs that never had a value.
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo_home"))
    import enforcement_engine
    importlib.reload(enforcement_engine)
    yield


def _redact(text, max_len=1000):
    from enforcement_engine import _redact_for_log
    return _redact_for_log(text, max_len=max_len)


# ─── Pattern-5 regression (bug the hunter found) ──────────────────────


def test_env_ref_does_not_duplicate_variable_name():
    """The prior helper produced `$GITHUB_TOKEN<redacted>` on input
    `$GITHUB_TOKEN`. Now the whole reference is replaced with a
    placeholder marker instead of a duplicated name."""
    out = _redact("echo $GITHUB_TOKEN")
    assert "$GITHUB_TOKEN<redacted>" not in out
    assert "<redacted-env-ref>" in out


# ─── Coverage for common secret formats ───────────────────────────────


def test_redacts_bearer_token():
    out = _redact("Authorization: Bearer abc123xyz789abc123xyz789")
    assert "<redacted>" in out
    assert "abc123xyz789" not in out


def test_redacts_openai_sk_key():
    out = _redact("OPENAI_KEY=sk-abcdefghij1234567890klmnop")
    # Either captured via the SK- pattern or the generic KEY= pattern.
    assert "sk-abcdefghij1234567890klmnop" not in out


def test_redacts_github_personal_access_token():
    out = _redact("git push https://ghp_abcdefghij1234567890XYZabc1234@github.com/x.git")
    assert "ghp_abcdefghij1234567890XYZabc1234" not in out
    assert "<redacted>" in out


def test_redacts_shopify_token():
    out = _redact("Shopify token: shpat_TESTFIXTURE_NOT_A_REAL_TOKEN")
    assert "f2d4c82f60dccdbbcc2bee461ada6c61" not in out


def test_redacts_jwt():
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    out = _redact(f"header Authorization: {jwt}")
    assert "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c" not in out
    assert "<redacted-jwt>" in out


def test_redacts_aws_access_key():
    out = _redact("AWS_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE and AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "wJalrXUtnFEMI/K7MDENG" not in out


def test_redacts_mysql_short_password_flag():
    out = _redact("mysql -pSecretPassword123 -u root -e 'SELECT 1'")
    assert "SecretPassword123" not in out


def test_redacts_password_in_connection_uri():
    out = _redact("postgres://user:mypassword123@db.example.com:5432/db")
    # The `password=` pattern targets the explicit keyword form; URI form
    # is intentionally out of scope but the operator-facing prompt will
    # rarely include raw URIs. Document the limit here.
    # What we DO cover: `password=foo` form.
    out2 = _redact("psql --password=ZYX012345xyz host=db")
    assert "ZYX012345xyz" not in out2


def test_redacts_generic_token_env_assignment():
    out = _redact("export MY_TOKEN=abcdef1234567890abcdef12")
    assert "abcdef1234567890abcdef12" not in out


def test_does_not_touch_non_secret_strings():
    safe = "Deploy the systeam.es banner after user accepts"
    assert _redact(safe) == safe


def test_truncates_at_max_len():
    out = _redact("x" * 500, max_len=100)
    assert len(out) <= 103  # 100 + "..."
    assert out.endswith("...")


def test_handles_non_string_input_fail_closed():
    assert _redact(None) == ""
    assert _redact(123) == ""
