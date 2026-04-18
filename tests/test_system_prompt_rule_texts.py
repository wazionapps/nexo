"""Plan A.4 — Fase A rule texts (R26-R33 + R34) present in system prompt,
prompt-friendly shape (trigger + expected action)."""

from __future__ import annotations

import re


def _system_prompt() -> str:
    import server  # noqa: F401  — ensures mcp server module loads
    # The prompt is injected as the docstring/body of the mcp server
    # object; we read it directly from the source file so we don't need
    # to spin up a live server during tests.
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "src" / "server.py"
    return path.read_text(encoding="utf-8")


def test_rule_headers_present():
    text = _system_prompt()
    for rid in ("R26", "R27", "R28", "R29", "R30", "R31", "R32", "R33"):
        assert re.search(rf"\*\*{rid} ", text), f"{rid} header not found in system prompt"


def test_r34_identity_coherence_present():
    text = _system_prompt()
    assert "R34" in text or "identity" in text.lower(), (
        "R34 Identity Coherence must be referenced somewhere in the prompt or supporting files"
    )


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
    ]:
        block = re.search(rf"\*\*{rid}.*?(?=\n- \*\*R|$)", text, re.DOTALL)
        assert block, f"{rid} block not located"
        body = block.group(0).lower()
        assert marker in body, f"{rid} body missing marker {marker!r}"
