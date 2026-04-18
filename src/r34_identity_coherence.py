"""R34 — Identity Coherence across terminals.

Plan Consolidado T5. Fires when the agent emits a message that denies
having done an action ("yo no", "I haven't done that", "not me"…) WITHOUT
having consulted the shared brain in the current turn. The LLM cannot
know what another terminal did without reading ``nexo_recent_context``,
``nexo_session_diary_read``, ``nexo_change_log`` or ``nexo_status``, so
a bare denial is a coherence breach.

Detection strategy (two layers):

  1. Regex pre-filter. Multilingual patterns (ES/EN) catch the obvious
     denials. Pure pattern match has high recall but also high false-
     positive rate (any agent message saying "I haven't done X" today
     would match even when the action is plainly something NEXO has not
     done).
  2. LLM classifier confirmation (shared with T4). When the regex fires
     AND no shared-brain tool has been called this turn, the classifier
     decides whether the message is really a past-tense denial worth
     nudging. Tests use a fake classifier to avoid hitting the SDK.

The rule runs post-message (on agent output), unlike the pre-tool
chain. The engine surfaces the message text through ``notify_agent_output``
and this module decides whether to inject.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, Optional


# Shared-brain tools that count as "you checked before speaking".
SHARED_BRAIN_TOOLS = frozenset({
    "nexo_recent_context",
    "nexo_session_diary_read",
    "nexo_change_log",
    "nexo_change_search",
    "nexo_status",
    "nexo_transcript_recent",
    "nexo_transcript_search",
})


# Regex layer. Deliberately narrow enough to not fire on present-tense
# disagreements ("I don't think so"), but broad enough to catch past-
# tense denials. Patterns are OR-ed in a single alternation so we keep
# the Python/JS parity trivial.
DENIAL_PATTERNS = [
    # Spanish
    re.compile(r"\byo\s+no\s+(he|hice|fui)\b", re.I),
    re.compile(r"\bno\s+he\s+(hecho|escrito|enviado|borrado|creado|cambiado|tocado|modificado)\b", re.I),
    re.compile(r"\bno\s+lo\s+hice\b", re.I),
    re.compile(r"\beso\s+no\s+fui\s+yo\b", re.I),
    # English
    re.compile(r"\bi\s+(didn.?t|didn't|have\s+not|haven.?t|haven't)\s+(do|done|write|send|delete|create|change|touch|modify)", re.I),
    re.compile(r"\bit\s+wasn.?t\s+me\b", re.I),
    re.compile(r"\bnot\s+me\b", re.I),
]


INJECTION_PROMPT = (
    "R34 identity-coherence probe: you just denied having done something "
    "without first consulting the shared brain. Other terminals are also "
    "you. Run one of `nexo_recent_context`, `nexo_session_diary_read`, "
    "`nexo_change_log` or `nexo_status` before asserting what happened "
    "or did not happen — then answer again."
)


def _denial_match(message: str) -> Optional[str]:
    """Return the first matched substring, or None."""
    if not isinstance(message, str) or not message:
        return None
    for pattern in DENIAL_PATTERNS:
        m = pattern.search(message)
        if m:
            return m.group(0)
    return None


def should_inject_r34(
    message: str,
    *,
    recent_tool_names: Iterable[str] | None,
    classifier: Callable[[str, str], bool] | None = None,
) -> tuple[bool, str, str]:
    """Return ``(inject, prompt, matched_text)``.

    Args:
        message: agent output text.
        recent_tool_names: tool names seen in this turn; any match in
            SHARED_BRAIN_TOOLS suppresses the rule.
        classifier: optional LLM classifier. Signature
            ``classifier(question, context) -> bool``. If provided, the
            rule fires only when classifier says "yes". If None, the
            regex match alone fires (test path).
    """
    if not isinstance(message, str) or not message:
        return False, "", ""
    recent = set(recent_tool_names or [])
    if recent & SHARED_BRAIN_TOOLS:
        return False, "", ""
    matched = _denial_match(message)
    if matched is None:
        return False, "", ""
    if classifier is None:
        return True, INJECTION_PROMPT, matched
    # LLM disambiguation — reuses T4 infra. The engine passes a lambda
    # that calls enforcement_classifier.classify under the hood.
    question = (
        "Is this message a past-tense denial of an action that might "
        "actually have been performed in another terminal of the same "
        "shared NEXO Brain? Answer yes or no."
    )
    try:
        verdict = bool(classifier(question, message))
    except Exception:
        # Fail-closed: if the classifier errors, do not inject (avoids
        # noisy false positives on regex-only matches when the LLM is
        # unavailable).
        return False, "", matched
    if not verdict:
        return False, "", matched
    return True, INJECTION_PROMPT, matched


__all__ = [
    "DENIAL_PATTERNS",
    "INJECTION_PROMPT",
    "SHARED_BRAIN_TOOLS",
    "should_inject_r34",
]
