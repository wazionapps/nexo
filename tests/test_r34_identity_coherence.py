"""Tests for R34 — Plan Consolidado T5."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from r34_identity_coherence import (  # noqa: E402
    DENIAL_PATTERNS,
    INJECTION_PROMPT,
    SHARED_BRAIN_TOOLS,
    should_inject_r34,
)


def test_shared_brain_tools_set():
    assert "nexo_recent_context" in SHARED_BRAIN_TOOLS
    assert "nexo_change_log" in SHARED_BRAIN_TOOLS
    assert "nexo_transcript_search" in SHARED_BRAIN_TOOLS


@pytest.mark.parametrize("phrase", [
    "yo no he hecho eso",
    "yo no lo hice",
    "eso no fui yo",
    "no he borrado nada",
    "I haven't done that",
    "I didn't do that",
    "it wasn't me",
    "not me",
])
def test_denial_phrases_match_regex(phrase):
    inject, _, matched = should_inject_r34(phrase, recent_tool_names=[])
    assert inject is True, f"did not match: {phrase!r}"
    assert matched


def test_shared_brain_tool_suppresses_injection():
    for tool in SHARED_BRAIN_TOOLS:
        inject, _, _ = should_inject_r34(
            "yo no he hecho eso", recent_tool_names=[tool]
        )
        assert inject is False


def test_non_denial_message_does_not_fire():
    inject, _, _ = should_inject_r34(
        "lo hice esta mañana y está commiteado", recent_tool_names=[]
    )
    assert inject is False


def test_classifier_false_suppresses():
    inject, _, _ = should_inject_r34(
        "yo no he hecho eso",
        recent_tool_names=[],
        classifier=lambda q, msg: False,
    )
    assert inject is False


def test_classifier_true_allows():
    inject, _, _ = should_inject_r34(
        "yo no he hecho eso",
        recent_tool_names=[],
        classifier=lambda q, msg: True,
    )
    assert inject is True


def test_classifier_exception_fails_closed():
    def boom(q, msg):
        raise RuntimeError("sdk down")

    inject, _, _ = should_inject_r34(
        "yo no he hecho eso", recent_tool_names=[], classifier=boom
    )
    assert inject is False


def test_empty_or_non_string_input_safe():
    inject, _, _ = should_inject_r34("", recent_tool_names=[])
    assert inject is False
    inject, _, _ = should_inject_r34(None, recent_tool_names=[])  # type: ignore[arg-type]
    assert inject is False


def test_prompt_parity_template():
    expected = (
        "R34 identity-coherence probe: you just denied having done something "
        "without first consulting the shared brain. Other terminals are also "
        "you. Run one of `nexo_recent_context`, `nexo_session_diary_read`, "
        "`nexo_change_log` or `nexo_status` before asserting what happened "
        "or did not happen — then answer again."
    )
    assert INJECTION_PROMPT == expected
