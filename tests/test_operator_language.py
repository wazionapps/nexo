from __future__ import annotations

import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def test_normalize_operator_language_prefers_primary_tag():
    import operator_language

    assert operator_language.normalize_operator_language("es-ES") == "es"
    assert operator_language.normalize_operator_language("zh_CN") == "zh"
    assert operator_language.normalize_operator_language("") == ""


def test_append_operator_language_contract_is_idempotent():
    import operator_language

    prompt = "Run nexo_stop now."
    once = operator_language.append_operator_language_contract(prompt, "es")
    twice = operator_language.append_operator_language_contract(once, "es")

    assert "Spanish (es)" in once
    assert twice == once


def test_describe_operator_language_covers_desktop_language_options():
    import operator_language

    assert operator_language.describe_operator_language("gl") == "Galician (gl)"
    assert operator_language.describe_operator_language("eu") == "Basque (eu)"


def test_agent_runner_language_contract_applies_to_operator_facing_callers(monkeypatch):
    import agent_runner
    import operator_language

    operator_language.build_operator_language_contract.cache_clear()
    monkeypatch.setattr(operator_language, "load_operator_language", lambda: "es")

    prompt = agent_runner._apply_operator_language_contract(
        "Write the daily report.",
        caller="sleep/nightly",
    )

    assert "Write the daily report." in prompt
    assert "CRITICAL LANGUAGE CONTRACT" in prompt
    assert "Spanish (es)" in prompt

    operator_language.build_operator_language_contract.cache_clear()


def test_agent_runner_language_contract_skips_machine_only_callers():
    import agent_runner

    assert agent_runner._apply_operator_language_contract(
        "Reply exactly OK.",
        caller="automation_probe",
    ) == "Reply exactly OK."
    assert agent_runner._apply_operator_language_contract(
        "{}",
        caller="check_context",
    ) == "{}"
    assert agent_runner._apply_operator_language_contract(
        "{}",
        caller="learning_validator",
    ) == "{}"


def test_deep_sleep_callers_are_language_contracted():
    import agent_runner

    assert agent_runner._should_apply_operator_language_contract("deep-sleep/extract")
    assert agent_runner._should_apply_operator_language_contract("deep-sleep/synthesize")
