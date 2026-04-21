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
