from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_long_running_and_batch_closure_guardrails_are_in_bootstraps_and_server_prompt():
    targets = [
        "templates/core-prompts/server-mcp-instructions.md",
        "templates/CODEX.AGENTS.md.template",
        "templates/CLAUDE.md.template",
    ]

    for relative in targets:
        text = _read(relative)
        lower = text.lower()
        assert "long-running" in text or "long opaque" in text or "long-running work" in text
        assert "what closed since" in text
        assert "what remains" in text
        assert "threshold" in text
        assert "GREEN" in text
        assert "OPEN" in text
        assert "explaining" in lower or "explanation" in lower
        assert "byte-for-byte diff" in text or "direct source evidence" in text


def test_first_response_and_change_ledger_guardrails_are_in_bootstraps_and_server_prompt():
    targets = [
        "templates/core-prompts/server-mcp-instructions.md",
        "templates/CODEX.AGENTS.md.template",
        "templates/CLAUDE.md.template",
    ]

    for relative in targets:
        text = _read(relative)
        lower = text.lower()
        assert "conclusion plus" in lower
        assert "next action" in lower
        assert "cost" in lower
        assert "risk" in lower
        assert "decision" in lower
        assert "2-3 short sentences" in text
        assert "open optional question" in lower
        assert "change ledger" in lower or "nexo_change_log" in text
        assert "commits" in lower
        assert "deploys" in lower
        assert "queue" in lower or "enqueue" in lower
        assert "what changed" in lower
        assert "impact" in lower
        assert "pending items" in lower
        assert "evidence" in lower
        assert "repo/runtime/production state" in lower


def test_core_rules_registry_contains_matching_product_generic_guardrails():
    payload = json.loads(_read("src/rules/core-rules.json"))
    rules = {
        rule["id"]: rule
        for section in payload["categories"].values()
        for rule in section["rules"]
    }

    assert payload["_meta"]["total_rules"] == len(rules)
    assert payload["_meta"]["blocking"] == sum(1 for rule in rules.values() if rule.get("type") == "blocking")
    assert "PC34" in rules
    assert "PC35" in rules
    assert "PC36" in rules
    assert "PC37" in rules
    assert "PC38" in rules
    assert "operator" not in rules["PC34"]["why"].lower()
    assert "operator" not in rules["PC35"]["why"].lower()
    assert "operator" not in rules["PC36"]["why"].lower()
    assert "operator" not in rules["PC37"]["why"].lower()
    assert "operator" not in rules["PC38"]["why"].lower()
    assert "what closed since the last update" in rules["PC34"]["why"]
    assert "GREEN" in rules["PC35"]["why"]
    assert "OPEN" in rules["PC35"]["why"]
    assert "cost, risk, or decision impact" in rules["PC36"]["why"]
    assert "change_log" in rules["PC37"]["why"]
    assert "what changed, impact, pending items, evidence, and repo/runtime/production state" in rules["PC38"]["why"]
