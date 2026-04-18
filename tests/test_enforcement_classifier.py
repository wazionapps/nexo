"""Tests for triple-reinforced yes/no classifier (Fase 2 spec 0.7)."""
from __future__ import annotations

import pytest


def test_parser_basic():
    from enforcement_classifier import _parse_yes_no
    assert _parse_yes_no("yes") is True
    assert _parse_yes_no("YES") is True
    assert _parse_yes_no("no") is False
    assert _parse_yes_no("No.") is False
    assert _parse_yes_no("  yes  ") is True
    assert _parse_yes_no("yes, because ...") is True
    assert _parse_yes_no("yesterday") is None  # word boundary
    assert _parse_yes_no("") is None
    assert _parse_yes_no("probably") is None
    assert _parse_yes_no(None) is None  # type-safe


def test_first_yes(monkeypatch):
    from enforcement_classifier import classify, _TTLCache
    cache = _TTLCache()
    calls = []
    def fake(prompt, **kwargs):
        calls.append(("first", kwargs.get("system", "")))
        return "yes"
    result = classify("Q?", "", call_raw=fake, cache=cache)
    assert result is True
    assert len(calls) == 1


def test_retry_on_garbage(monkeypatch):
    from enforcement_classifier import classify, _TTLCache, _STRICT_SYSTEM_PROMPT, _RETRY_SYSTEM_PROMPT
    cache = _TTLCache()
    calls = []
    def fake(prompt, **kwargs):
        calls.append(kwargs.get("system", ""))
        if len(calls) == 1:
            return "well it depends..."
        return "no"
    result = classify("Q?", "ctx", call_raw=fake, cache=cache)
    assert result is False
    assert len(calls) == 2
    assert calls[0] == _STRICT_SYSTEM_PROMPT
    assert calls[1] == _RETRY_SYSTEM_PROMPT


def test_fallback_no_when_both_garbage(monkeypatch):
    from enforcement_classifier import classify, _TTLCache
    cache = _TTLCache()
    calls = []
    def fake(prompt, **kwargs):
        calls.append(True)
        return "maybe, possibly"
    # Conservative: after both attempts fail, return False (no).
    result = classify("Q?", "ctx", call_raw=fake, cache=cache)
    assert result is False
    assert len(calls) == 2


def test_cache_hit_skips_call(monkeypatch):
    from enforcement_classifier import classify, _TTLCache
    cache = _TTLCache()
    calls = []
    def fake(prompt, **kwargs):
        calls.append(True)
        return "yes"
    classify("same Q?", "same ctx", call_raw=fake, cache=cache)
    classify("same Q?", "same ctx", call_raw=fake, cache=cache)
    assert len(calls) == 1, "cache should short-circuit the second call"


def test_cache_miss_on_different_context(monkeypatch):
    from enforcement_classifier import classify, _TTLCache
    cache = _TTLCache()
    calls = []
    def fake(prompt, **kwargs):
        calls.append(True)
        return "yes"
    classify("Q?", "ctx-A", call_raw=fake, cache=cache)
    classify("Q?", "ctx-B", call_raw=fake, cache=cache)
    assert len(calls) == 2, "different context must not share the cache entry"


def test_cache_eviction_by_ttl():
    from enforcement_classifier import _TTLCache
    import time
    c = _TTLCache(ttl_seconds=0.05, max_entries=4)
    c.put("k", True)
    assert c.get("k") is True
    time.sleep(0.08)
    assert c.get("k") is None


def test_propagates_classifier_unavailable():
    from enforcement_classifier import classify, _TTLCache, ClassifierUnavailableError
    cache = _TTLCache()
    def fake(prompt, **kwargs):
        raise ClassifierUnavailableError("simulated")
    with pytest.raises(ClassifierUnavailableError):
        classify("Q?", "", call_raw=fake, cache=cache)
