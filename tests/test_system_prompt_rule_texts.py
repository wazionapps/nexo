"""Plan A.4 — Fase A rule texts (R26-R33 + R34/R37) present in system prompt,
prompt-friendly shape (trigger + expected action)."""

from __future__ import annotations

import re


def _system_prompt() -> str:
    from core_prompts import render_core_prompt

    return render_core_prompt("server-mcp-instructions", assistant_name="Nero")


def test_rule_headers_present():
    text = _system_prompt()
    for rid in ("R26", "R27", "R28", "R29", "R30", "R31", "R32", "R33", "R37"):
        assert re.search(rf"\*\*{rid} ", text), f"{rid} header not found in system prompt"


def test_r34_identity_coherence_present():
    text = _system_prompt()
    assert "R34" in text or "identity" in text.lower(), (
        "R34 Identity Coherence must be referenced somewhere in the prompt or supporting files"
    )


def test_r37_pre_answer_evidence_present():
    text = _system_prompt()
    assert "R37" in text
    assert "releases, commits, branches" in text
    assert "not verified yet" in text


def test_each_core_rule_has_triggering_situation_and_action():
    """Prompt-friendly rules describe when to trigger + what to do."""
    text = _system_prompt()
    for rid, marker in [
        ("R26", "jargon"),
        ("R27", "2–3"),
        ("R28", "learning_add"),
        ("R29", "execute"),
        ("R30", "evidence"),
        ("R31", "assumption"),
        ("R32", "read-only"),
        ("R33", "system_catalog"),
        ("R37", "evidence"),
    ]:
        block = re.search(rf"\*\*{rid}.*?(?=\n- \*\*R|$)", text, re.DOTALL)
        assert block, f"{rid} block not located"
        body = block.group(0).lower()
        assert marker in body, f"{rid} body missing marker {marker!r}"
