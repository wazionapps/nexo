import json


def test_unknowns_block_reason_allows_investigation_but_not_mutation():
    from plugins.cortex import evaluate_cortex_state

    result = evaluate_cortex_state(
        {
            "goal": "Inspect a runtime issue before patching it",
            "task_type": "edit",
            "unknowns": ["which config file is authoritative", "whether runtime drift still exists"],
            "plan": ["inspect", "compare", "patch"],
            "verification_step": "run targeted verification",
            "evidence_refs": ["ops note"],
        }
    )

    assert result["mode"] == "ask"
    assert "Investigation and reading are allowed" in result["blocked_reason"]
    assert "do not mutate anything" in result["blocked_reason"]


def test_answer_cortex_injects_product_and_bootstrap_core_rules():
    from plugins.cortex import evaluate_cortex_state

    result = evaluate_cortex_state(
        {
            "goal": "Answer whether NEXO can create videos",
            "task_type": "answer",
            "unknowns": [],
            "plan": [],
            "verification_step": "",
            "evidence_refs": ["core rules registry"],
        }
    )

    rules = "\n".join(result["injected_rules"])
    assert "C1: Execute, don't narrate" in rules
    assert "PC1: Context before asking" in rules
    assert "PC8: Do not invent product capabilities" in rules
    assert "PC28: Check real capability before denying" in rules
    assert "MEMORY_AUTHORITY: Brain, calibration and profile are authoritative" in rules
    assert "IDENTITY_CONTINUITY: NEXO presents one continuous operational identity" in rules


def test_edit_cortex_injects_product_architecture_core_rules():
    from plugins.cortex import evaluate_cortex_state

    result = evaluate_cortex_state(
        {
            "goal": "Patch Brain core rules injection",
            "task_type": "edit",
            "unknowns": [],
            "plan": ["read code", "patch selector", "test"],
            "verification_step": "run targeted pytest",
            "evidence_refs": ["user approval", "repo code"],
        }
    )

    rules = "\n".join(result["injected_rules"])
    assert "E1: Understand the full system before writing a line" in rules
    assert "PC18: No parallel architecture before reviewing existing pieces" in rules
    assert "PC24: Read what NEXO already wrote before acting on the same topic" in rules
    assert "PC32: Reuse prior work before researching from zero" in rules
    assert "RUNTIME_CORE_PROTECTED: Installed runtime core is protected" in rules
