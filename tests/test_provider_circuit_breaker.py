"""Fase 1.6 — provider circuit breaker (SPEC-FIABILIDAD-FASES-2026-06 §1.6).

Incident: with the selected engine unavailable (credits/rate/auth) every
headless cron launched doomed sessions, burned retry budgets and spammed the
operator per-item (in English). The breaker must fail fast, queue work and
notify exactly once per opening.
"""

import time

import pytest


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    yield


def test_classify_real_provider_failures():
    from provider_circuit_breaker import classify_session_failure

    assert classify_session_failure(1, "", "API Error: 400 credit balance is too low") == "credits"
    assert classify_session_failure(1, "", "openai: insufficient_quota — exceeded your current quota") == "credits"
    assert classify_session_failure(1, "", "anthropic rate_limit_error: too many requests") == "rate_limit"
    assert classify_session_failure(1, "overloaded_error 529", "") == "rate_limit"
    assert classify_session_failure(1, "", "authentication_error: OAuth token has expired. Please run /login") == "auth"
    assert classify_session_failure(1, "", "segfault somewhere deep") == "generic"
    assert classify_session_failure(0, "all good", "") is None


def test_hard_reasons_open_immediately_and_block():
    from provider_circuit_breaker import (
        ProviderTemporarilyUnavailableError,
        check_provider_available,
        raise_if_unavailable,
        record_session_outcome,
    )

    ok, _ = check_provider_available("claude_code")
    assert ok is True

    record_session_outcome("claude_code", ok=False, reason="credits")
    ok, entry = check_provider_available("claude_code")
    assert ok is False
    assert entry["reason"] == "credits"

    with pytest.raises(ProviderTemporarilyUnavailableError) as excinfo:
        raise_if_unavailable("claude_code")
    assert excinfo.value.backend == "claude_code"
    assert excinfo.value.reason == "credits"
    assert "queued" in str(excinfo.value)


def test_generic_failures_need_three_consecutive():
    from provider_circuit_breaker import check_provider_available, record_session_outcome

    record_session_outcome("codex", ok=False, reason="generic")
    assert check_provider_available("codex")[0] is True
    record_session_outcome("codex", ok=False, reason="generic")
    assert check_provider_available("codex")[0] is True
    record_session_outcome("codex", ok=False, reason="generic")
    assert check_provider_available("codex")[0] is False


def test_success_closes_and_resets():
    from provider_circuit_breaker import check_provider_available, record_session_outcome

    record_session_outcome("codex", ok=False, reason="rate_limit")
    assert check_provider_available("codex")[0] is False
    record_session_outcome("codex", ok=True)
    ok, entry = check_provider_available("codex")
    assert ok is True
    assert entry.get("consecutive_failures") == 0


def test_half_open_probe_after_retry_window():
    from provider_circuit_breaker import check_provider_available, record_session_outcome

    record_session_outcome("claude_code", ok=False, reason="rate_limit", retry_after_s=0.05)
    assert check_provider_available("claude_code")[0] is False
    time.sleep(0.1)
    ok, entry = check_provider_available("claude_code")
    assert ok is True, "past retry_after the next attempt IS the half-open probe"
    assert entry.get("half_open_probe_at")


def test_operator_notified_exactly_once_per_opening():
    from provider_circuit_breaker import record_session_outcome, should_notify_operator

    record_session_outcome("claude_code", ok=False, reason="credits")
    assert should_notify_operator("claude_code") is True
    assert should_notify_operator("claude_code") is False

    # Recovery then a NEW opening notifies again.
    record_session_outcome("claude_code", ok=True)
    assert should_notify_operator("claude_code") is False
    record_session_outcome("claude_code", ok=False, reason="credits")
    assert should_notify_operator("claude_code") is True


def test_breaker_fails_open_on_corrupt_state(tmp_path):
    from provider_circuit_breaker import _state_path, check_provider_available

    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{corrupted json", encoding="utf-8")
    ok, _ = check_provider_available("claude_code")
    assert ok is True, "a broken state file must never block automations"


def test_classify_real_codex_usage_limit_message():
    # Phase 3 — REAL fixture recorded from codex 0.139.0 on 11-jun (the CLI
    # actually ran out of credits mid-recording): the wording is "You've hit
    # your usage limit ... purchase more credits", which the original
    # pattern ("usage limit reached") missed, classifying it as generic.
    from provider_circuit_breaker import classify_session_failure

    real_message = (
        "You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
        "to purchase more credits or try again at 9:30 AM."
    )
    assert classify_session_failure(1, "", real_message) == "credits"
