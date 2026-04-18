"""Tests for T4 LLM gate — parity + caching + fail-closed."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from t4_llm_gate import (  # noqa: E402
    PROMPTS,
    build_prompt,
    classify_with_llm,
)


def test_prompts_cover_four_rules():
    assert set(PROMPTS.keys()) == {"R15", "R23e", "R23f", "R23h"}
    for p in PROMPTS.values():
        assert len(p["positives"]) >= 3
        assert len(p["negatives"]) >= 3


def test_build_prompt_includes_instruction_span_examples():
    out = build_prompt("R23e", span="git push --force origin main")
    assert "Decide whether the proposed" in out
    assert "git push --force origin main" in out
    assert 'Answer exactly "yes" or "no".' in out


def test_build_prompt_unknown_rule_none():
    assert build_prompt("R999", span="x") is None


def test_classify_none_classifier_returns_unknown():
    cache = {}
    assert classify_with_llm("R23e", prompt="p", context="c", cache=cache) == "unknown"
    assert cache == {}


def test_classify_yes_path():
    cache = {}
    assert classify_with_llm("R23e", prompt="p", classifier=lambda p, c: "yes", cache=cache) == "yes"


def test_classify_no_path():
    cache = {}
    assert classify_with_llm("R23e", prompt="p", classifier=lambda p, c: False, cache=cache) == "no"


def test_classify_unknown_for_other_output():
    cache = {}
    assert classify_with_llm("R23e", prompt="p", classifier=lambda p, c: "maybe", cache=cache) == "unknown"


def test_classify_fails_closed_on_exception():
    def boom(p, c):
        raise RuntimeError("sdk")

    cache = {}
    assert classify_with_llm("R23e", prompt="p", classifier=boom, cache=cache) == "unknown"


def test_cache_hits_avoid_second_call():
    calls = {"n": 0}

    def cls(p, c):
        calls["n"] += 1
        return "yes"

    cache = {}
    assert classify_with_llm("R23e", prompt="p", classifier=cls, cache=cache) == "yes"
    assert classify_with_llm("R23e", prompt="p", classifier=cls, cache=cache) == "yes"
    assert calls["n"] == 1
