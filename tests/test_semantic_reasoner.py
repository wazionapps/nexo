"""Tests for src/semantic_reasoner.py — Plan ONEPASS LLM Coverage.

Stubs the local classifier and the remote LLM so tests never download
models or reach the network. Verifies the majority-vote logic (Mode A)
and the cache hit/miss/TTL behaviour (Mode B).
"""
from __future__ import annotations

import json
import time

import pytest


# ---------------------------------------------------------------------------
# Mode A — multipass_local
# ---------------------------------------------------------------------------


class _StubClassifier:
    """Return a pre-programmed sequence of (label, confidence) tuples, one
    per pass. Ignores the text argument so we can drive deterministic
    scenarios from the tests."""

    def __init__(self, results):
        self._results = list(results)
        self._idx = 0

    def classify(self, text, labels, *, multi_label=False):  # noqa: ARG002
        import classifier_local as cl

        if self._idx >= len(self._results):
            return None
        label, confidence = self._results[self._idx]
        self._idx += 1
        scores = {lbl: (confidence if lbl == label else 0.0) for lbl in labels}
        return cl.ClassificationResult(
            label=label,
            confidence=float(confidence),
            scores=scores,
            latency_ms=1.0,
        )


def _install_stub_classifier(monkeypatch, results):
    import classifier_local

    def factory(**kwargs):  # noqa: ARG001 — accept the same kwargs as real class
        return _StubClassifier(results)

    monkeypatch.setattr(classifier_local, "LocalZeroShotClassifier", factory)


def test_multipass_majority_vote_accepts_two_of_three(monkeypatch):
    import semantic_reasoner as sr

    _install_stub_classifier(
        monkeypatch,
        results=[("correction", 0.80), ("correction", 0.85), ("noise", 0.40)],
    )

    result = sr.reason(
        decision_kind="r14_correction",
        question="no, así no",
        labels=("correction", "noise"),
        mode="multipass_local",
        confidence_floor=0.75,
    )
    assert result.ok is True
    assert result.verdict == "correction"
    assert result.route_used == "semantic_reasoner"
    assert result.meta["mode"] == "multipass_local"
    assert result.meta["aggregate"]["votes_for_best"] == 2


def test_multipass_refuses_when_no_majority(monkeypatch):
    import semantic_reasoner as sr

    _install_stub_classifier(
        monkeypatch,
        results=[("a", 0.80), ("b", 0.85), ("c", 0.90)],
    )

    result = sr.reason(
        decision_kind="r14_correction",
        question="ambiguous",
        labels=("a", "b", "c"),
        mode="multipass_local",
        confidence_floor=0.75,
    )
    assert result.ok is False
    assert result.route_used == "semantic_reasoner"
    assert result.degraded is True
    assert result.error == "no_majority"


def test_multipass_refuses_when_confidence_below_threshold(monkeypatch):
    import semantic_reasoner as sr

    _install_stub_classifier(
        monkeypatch,
        results=[("done", 0.50), ("done", 0.55), ("noise", 0.40)],
    )

    result = sr.reason(
        decision_kind="r16_declared_done",
        question="weak signal",
        labels=("done", "noise"),
        mode="multipass_local",
        confidence_floor=0.75,
    )
    assert result.ok is False
    assert result.error == "below_threshold"


def test_multipass_requires_labels():
    import semantic_reasoner as sr

    result = sr.reason(
        decision_kind="session_end_intent",
        question="hasta mañana",
        labels=None,
        mode="multipass_local",
        confidence_floor=0.75,
    )
    assert result.ok is False
    assert "requires labels" in (result.error or "")


# ---------------------------------------------------------------------------
# Mode B — cached_llm
# ---------------------------------------------------------------------------


def _patch_cache_path(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "NEXO_SEMANTIC_REASONER_CACHE_PATH", str(tmp_path / "cache.json")
    )


def _install_stub_call_model_raw(monkeypatch, responses):
    """Install a fake `call_model_raw` callable that returns responses in
    sequence. ``responses`` can contain strings (return value) or
    Exceptions (raise on that call)."""
    import semantic_reasoner as sr

    class _StubExcModule:
        class ClassifierUnavailableError(RuntimeError):
            pass

    calls = {"n": 0}

    def stub(prompt, *, system=None, caller=None, tier=None, max_tokens=None, temperature=None):  # noqa: ARG001, E501
        idx = calls["n"]
        calls["n"] = idx + 1
        if idx >= len(responses):
            raise _StubExcModule.ClassifierUnavailableError("exhausted")
        item = responses[idx]
        if isinstance(item, Exception):
            raise item
        return item

    fake_module = type("m", (), {})()
    fake_module.call_model_raw = stub
    fake_module.ClassifierUnavailableError = _StubExcModule.ClassifierUnavailableError

    import sys
    monkeypatch.setitem(sys.modules, "call_model_raw", fake_module)
    return calls


def test_cached_llm_miss_then_hit(monkeypatch, tmp_path):
    import semantic_reasoner as sr

    _patch_cache_path(monkeypatch, tmp_path)
    calls = _install_stub_call_model_raw(monkeypatch, ["t4_bypass"])

    first = sr.reason(
        decision_kind="t4_r15",
        question="rm -rf build/",
        context="scripts/deploy.sh line 12",
        labels=("t4_bypass", "safe"),
        mode="cached_llm",
        confidence_floor=0.60,
    )
    assert first.ok is True
    assert first.verdict == "t4_bypass"
    assert first.meta["cache_hit"] is False
    assert calls["n"] == 1

    # Second call with identical inputs hits the cache and does NOT call
    # the LLM again.
    second = sr.reason(
        decision_kind="t4_r15",
        question="rm -rf build/",
        context="scripts/deploy.sh line 12",
        labels=("t4_bypass", "safe"),
        mode="cached_llm",
        confidence_floor=0.60,
    )
    assert second.ok is True
    assert second.verdict == "t4_bypass"
    assert second.meta["cache_hit"] is True
    assert calls["n"] == 1, "cache hit must not invoke the LLM"


def test_cached_llm_expired_entry_triggers_llm(monkeypatch, tmp_path):
    import semantic_reasoner as sr

    _patch_cache_path(monkeypatch, tmp_path)
    monkeypatch.setenv("NEXO_SEMANTIC_REASONER_TTL", "1")
    _install_stub_call_model_raw(monkeypatch, ["t4_bypass", "safe"])

    first = sr.reason(
        decision_kind="t4_r15",
        question="rm -rf build/",
        labels=("t4_bypass", "safe"),
        mode="cached_llm",
        confidence_floor=0.60,
    )
    assert first.ok is True

    # Simulate passage of time past the TTL.
    time.sleep(1.2)

    second = sr.reason(
        decision_kind="t4_r15",
        question="rm -rf build/",
        labels=("t4_bypass", "safe"),
        mode="cached_llm",
        confidence_floor=0.60,
    )
    assert second.ok is True
    assert second.meta["cache_hit"] is False
    assert second.verdict == "safe"


def test_cached_llm_scope_is_per_decision_kind(monkeypatch, tmp_path):
    import semantic_reasoner as sr

    _patch_cache_path(monkeypatch, tmp_path)
    _install_stub_call_model_raw(monkeypatch, ["t4_bypass", "safe"])

    first = sr.reason(
        decision_kind="t4_r15",
        question="shared snippet",
        labels=("t4_bypass", "safe"),
        mode="cached_llm",
        confidence_floor=0.60,
    )
    second = sr.reason(
        decision_kind="t4_r23e",  # different kind, same question
        question="shared snippet",
        labels=("t4_bypass", "safe"),
        mode="cached_llm",
        confidence_floor=0.60,
    )
    assert first.verdict == "t4_bypass"
    assert second.verdict == "safe"
    assert second.meta["cache_hit"] is False


def test_cached_llm_reports_unavailable_when_llm_refuses(monkeypatch, tmp_path):
    import semantic_reasoner as sr

    _patch_cache_path(monkeypatch, tmp_path)

    import sys
    fake_module = type("m", (), {})()

    class _ClassifierUnavailableError(RuntimeError):
        pass

    def stub(*args, **kwargs):  # noqa: ARG001
        raise _ClassifierUnavailableError("offline")

    fake_module.call_model_raw = stub
    fake_module.ClassifierUnavailableError = _ClassifierUnavailableError
    monkeypatch.setitem(sys.modules, "call_model_raw", fake_module)

    result = sr.reason(
        decision_kind="t4_r23f",
        question="anything",
        labels=("bypass", "safe"),
        mode="cached_llm",
        confidence_floor=0.60,
    )
    assert result.ok is False
    assert "remote_unavailable" in (result.error or "")
    assert result.degraded is True


def test_cache_key_ignores_whitespace_and_case(monkeypatch, tmp_path):
    import semantic_reasoner as sr

    _patch_cache_path(monkeypatch, tmp_path)
    calls = _install_stub_call_model_raw(monkeypatch, ["t4_bypass"])

    sr.reason(
        decision_kind="t4_r15",
        question="rm -rf build/",
        context="scripts/deploy.sh LINE 12",
        labels=("t4_bypass", "safe"),
        mode="cached_llm",
        confidence_floor=0.60,
    )
    sr.reason(
        decision_kind="t4_r15",
        question="  RM -RF   build/  ",
        context="scripts/deploy.sh line 12",
        labels=("t4_bypass", "safe"),
        mode="cached_llm",
        confidence_floor=0.60,
    )
    assert calls["n"] == 1, "equivalent normalized inputs must share the cache"


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_unknown_mode_returns_error():
    import semantic_reasoner as sr

    result = sr.reason(
        decision_kind="r14_correction",
        question="x",
        labels=("a", "b"),
        mode="does_not_exist",
        confidence_floor=0.75,
    )
    assert result.ok is False
    assert "unknown reasoner mode" in (result.error or "")


def test_normalize_verdict_maps_labels_case_insensitively():
    import semantic_reasoner as sr

    assert sr._normalize_verdict("DONE_CLAIM", ("done_claim", "noise")) == "done_claim"
    assert sr._normalize_verdict("I think done_claim", ("done_claim",)) == "done_claim"
    assert sr._normalize_verdict("unknown", ("a", "b")) is None
    assert sr._normalize_verdict("", ("a",)) is None


# ---------------------------------------------------------------------------
# Audit-driven hardening — release-blocker fixes
# ---------------------------------------------------------------------------


def test_reasoner_degrades_when_call_model_raw_throws_unexpected_exception(
    monkeypatch, tmp_path
):
    """Audit A2 equivalent for the reasoner path: if the LLM module raises
    an exception type OTHER than ClassifierUnavailableError (e.g. provider
    APIError, TimeoutError, KeyError from a mocked shim), the reasoner
    must still return a degraded RouterResult instead of propagating.

    Expose both symbols on the stub so the typed ``except`` clause does
    not accidentally swallow the unrelated error via its catch-all
    fallback.
    """
    import sys

    import semantic_reasoner as sr

    _patch_cache_path(monkeypatch, tmp_path)

    class _ClassifierUnavailableError(RuntimeError):
        pass

    class _UnrelatedError(RuntimeError):
        pass

    def stub(*args, **kwargs):  # noqa: ARG001
        raise _UnrelatedError("boom-from-provider-we-did-not-anticipate")

    fake_module = type("m", (), {})()
    fake_module.call_model_raw = stub
    fake_module.ClassifierUnavailableError = _ClassifierUnavailableError
    monkeypatch.setitem(sys.modules, "call_model_raw", fake_module)

    result = sr.reason(
        decision_kind="t4_r15",
        question="rm -rf /",
        labels=("t4_bypass", "safe"),
        mode="cached_llm",
        confidence_floor=0.60,
    )
    assert result.ok is False
    assert result.route_used == "semantic_reasoner"
    assert result.degraded is True
    assert "remote_error" in (result.error or "")


def test_reasoner_degrades_when_call_model_raw_fn_missing(monkeypatch, tmp_path):
    """The stub module is present but has no call_model_raw attribute.
    Without the getattr guard this crashes with AttributeError deep inside
    the reasoner."""
    import sys

    import semantic_reasoner as sr

    _patch_cache_path(monkeypatch, tmp_path)

    fake_module = type("m", (), {})()
    monkeypatch.setitem(sys.modules, "call_model_raw", fake_module)

    result = sr.reason(
        decision_kind="t4_r15",
        question="anything",
        labels=("t4_bypass", "safe"),
        mode="cached_llm",
        confidence_floor=0.60,
    )
    assert result.ok is False
    assert "call_model_raw callable missing" in (result.error or "")


def test_nexo_semantic_reasoner_env_kill_switch(monkeypatch, tmp_path):
    """Audit D1: honour NEXO_SEMANTIC_REASONER=0 runtime kill switch."""
    import semantic_reasoner as sr

    _patch_cache_path(monkeypatch, tmp_path)

    for value in ("0", "off", "false", "no", "disable", "disabled", "OFF"):
        monkeypatch.setenv("NEXO_SEMANTIC_REASONER", value)
        result = sr.reason(
            decision_kind="r14_correction",
            question="no, así no",
            labels=("correction", "noise"),
            mode="multipass_local",
            confidence_floor=0.75,
        )
        assert result.ok is False, f"kill switch value {value!r} did not refuse"
        assert result.error == "reasoner_disabled_by_env"
        assert result.route_used == "semantic_reasoner"
        assert result.degraded is True


def test_env_kill_switch_ignores_empty_and_truthy_values(monkeypatch, tmp_path):
    """A blank or truthy value leaves the reasoner enabled. We simulate
    Mode A here so the test does not need the HF model installed."""
    import semantic_reasoner as sr

    _patch_cache_path(monkeypatch, tmp_path)
    _install_stub_classifier(
        monkeypatch,
        results=[("correction", 0.80), ("correction", 0.85), ("noise", 0.40)],
    )

    for value in ("", "1", "on", "true", "yes"):
        monkeypatch.setenv("NEXO_SEMANTIC_REASONER", value)
        result = sr.reason(
            decision_kind="r14_correction",
            question="no, así no",
            labels=("correction", "noise"),
            mode="multipass_local",
            confidence_floor=0.75,
        )
        assert result.ok is True, f"value {value!r} unexpectedly disabled reasoner"


def test_corrupt_cache_entry_is_dropped_not_returned_as_success(
    monkeypatch, tmp_path
):
    """Audit A7: a cached entry with verdict=None must not be returned as
    ok=True. The reasoner must drop the corrupt entry and attempt a live
    LLM call (or degrade if that is also unavailable)."""
    import json
    import time

    import semantic_reasoner as sr

    cache_path = tmp_path / "cache.json"
    _patch_cache_path(monkeypatch, tmp_path)
    # Pre-seed the cache with a corrupt entry that matches the key the
    # reasoner will compute for the inputs below.
    key = sr._cache_key(
        decision_kind="t4_r15",
        question="rm -rf /tmp",
        labels=("t4_bypass", "safe"),
        context="",
    )
    cache_path.write_text(
        json.dumps(
            {key: {"verdict": None, "confidence": 0.7, "ts": time.time()}}
        )
    )

    calls = _install_stub_call_model_raw(monkeypatch, ["t4_bypass"])
    result = sr.reason(
        decision_kind="t4_r15",
        question="rm -rf /tmp",
        labels=("t4_bypass", "safe"),
        mode="cached_llm",
        confidence_floor=0.60,
    )
    assert result.ok is True
    assert result.verdict == "t4_bypass"
    assert result.meta["cache_hit"] is False
    assert calls["n"] == 1


def test_null_llm_response_does_not_crash_meta_slice(monkeypatch, tmp_path):
    """Audit A6: raw[:80] must tolerate None returns from the LLM stub."""
    import sys

    import semantic_reasoner as sr

    _patch_cache_path(monkeypatch, tmp_path)

    def stub(*args, **kwargs):  # noqa: ARG001
        return None

    fake_module = type("m", (), {})()
    fake_module.call_model_raw = stub
    monkeypatch.setitem(sys.modules, "call_model_raw", fake_module)

    result = sr.reason(
        decision_kind="t4_r15",
        question="x",
        labels=("t4_bypass", "safe"),
        mode="cached_llm",
        confidence_floor=0.60,
    )
    assert result.ok is False
    assert result.error == "llm_returned_unknown_or_unparseable"
    assert result.meta["raw"] == ""


def test_ttl_env_var_parses_defensively(monkeypatch):
    """Audit D4: malformed NEXO_SEMANTIC_REASONER_TTL must fall back to
    the default rather than crashing with ValueError."""
    import semantic_reasoner as sr

    monkeypatch.setenv("NEXO_SEMANTIC_REASONER_TTL", "not-a-number")
    assert sr._parse_ttl_env() == sr._DEFAULT_CACHE_TTL_SECONDS

    monkeypatch.setenv("NEXO_SEMANTIC_REASONER_TTL", "-5")
    assert sr._parse_ttl_env() == sr._DEFAULT_CACHE_TTL_SECONDS

    monkeypatch.setenv("NEXO_SEMANTIC_REASONER_TTL", "0")
    assert sr._parse_ttl_env() == sr._DEFAULT_CACHE_TTL_SECONDS

    monkeypatch.setenv("NEXO_SEMANTIC_REASONER_TTL", "120")
    assert sr._parse_ttl_env() == 120


def test_concurrent_cache_writes_do_not_stomp_each_other(monkeypatch, tmp_path):
    """Audit A3: two concurrent writes must not overlap tmp filenames.
    Simulates concurrency by calling _cache_put twice with distinct
    keys; with the old shared `.tmp` path the second write could clobber
    the first. Now each write uses a pid+uuid tmp file so both entries
    survive.
    """
    import semantic_reasoner as sr

    _patch_cache_path(monkeypatch, tmp_path)
    sr._cache_put("aaa", {"verdict": "x", "confidence": 0.7})
    sr._cache_put("bbb", {"verdict": "y", "confidence": 0.7})
    cache = sr._read_cache()
    assert cache.get("aaa", {}).get("verdict") == "x"
    assert cache.get("bbb", {}).get("verdict") == "y"
