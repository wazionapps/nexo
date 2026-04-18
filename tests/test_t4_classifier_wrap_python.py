"""Plan Consolidado T4.2-T4.6 — Python twin of the Desktop T4 wrap test.

Scenarios:
  1. classify_with_llm returns "yes" → caller proceeds.
  2. classify_with_llm returns "no"  → caller skips.
  3. classifier unavailable (None)    → caller falls back to regex-only behaviour.
  4. classifier raises                → fail-closed to "unknown".
"""

from __future__ import annotations


def test_gate_returns_unknown_when_no_classifier():
    from t4_llm_gate import classify_with_llm

    verdict = classify_with_llm(
        "R15",
        prompt="Is this a project turn?",
        context="",
        classifier=None,
    )
    assert verdict == "unknown"


def test_gate_uses_classifier_yes():
    from t4_llm_gate import classify_with_llm

    calls: list[tuple[str, str]] = []

    def fake(prompt: str, context: str) -> bool:
        calls.append((prompt, context))
        return True

    verdict = classify_with_llm(
        "R23e",
        prompt="Is `git push --force origin main` hitting a protected branch?",
        context="",
        classifier=fake,
        cache={},
    )
    assert verdict == "yes"
    assert len(calls) == 1


def test_gate_uses_classifier_no():
    from t4_llm_gate import classify_with_llm

    verdict = classify_with_llm(
        "R23f",
        prompt="Is `DELETE FROM tmp_scratch` unscoped against production?",
        context="",
        classifier=lambda p, c: False,
        cache={},
    )
    assert verdict == "no"


def test_gate_cache_replays_without_calling_classifier_again():
    from t4_llm_gate import classify_with_llm

    counter = {"n": 0}

    def fake(prompt: str, context: str) -> bool:
        counter["n"] += 1
        return True

    cache: dict = {}
    for _ in range(3):
        classify_with_llm(
            "R23h",
            prompt="prompt",
            context="ctx",
            classifier=fake,
            cache=cache,
        )
    assert counter["n"] == 1


def test_gate_fail_closed_when_classifier_raises():
    from t4_llm_gate import classify_with_llm

    def boom(prompt: str, context: str):
        raise RuntimeError("rate limited")

    verdict = classify_with_llm(
        "R15",
        prompt="prompt",
        context="",
        classifier=boom,
        cache={},
    )
    assert verdict == "unknown"


def test_build_prompt_for_each_wrapped_rule():
    from t4_llm_gate import build_prompt

    for rid in ("R15", "R23e", "R23f", "R23h"):
        prompt = build_prompt(rid, span="sample", context="")
        assert prompt
        assert "Answer exactly" in prompt
        assert "yes" in prompt and "no" in prompt


def test_auditor_h1_tristate_unknown_does_not_suppress_rule():
    """Regression — Auditor 1 found that enforcement_classifier.classify
    conflated "classifier said no" with "classifier response
    unparseable after retries", which would silently suppress destructive
    rules on flaky backends.

    With tristate=True, a conservative-parse-fallback now returns
    "unknown" and `classify_with_llm` must NOT treat it as "no".
    """
    from enforcement_classifier import classify, _TTLCache
    from t4_llm_gate import classify_with_llm

    def unparseable(*_args, **_kwargs):
        # Simulates the backend returning something the yes/no parser
        # can't match. classify() will try twice then fall back.
        return "something unparseable"

    tristate_cache = _TTLCache()

    def tristate_classifier(q, ctx):
        return classify(
            q,
            ctx,
            call_raw=unparseable,
            cache=tristate_cache,
            tristate=True,
        )

    verdict = classify_with_llm(
        "R23e",
        prompt="Is this a destructive force-push?",
        context="",
        classifier=tristate_classifier,
        cache={},
    )
    assert verdict == "unknown", (
        "unparseable classifier response must not silently suppress "
        "destructive rules — verdict must be unknown, not no"
    )


def test_classify_legacy_bool_fallback_still_false():
    """tristate=False keeps the legacy bool contract intact for existing
    rule callers (R14/R16/R17/R20)."""
    from enforcement_classifier import classify, _TTLCache

    def unparseable(*_args, **_kwargs):
        return "???"

    res = classify(
        "Is this a correction?",
        "",
        call_raw=unparseable,
        cache=_TTLCache(),
    )
    assert res is False  # legacy conservative fallback unchanged
