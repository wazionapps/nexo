"""Tests for src/r_catalog.py — Plan Consolidado 0.X.2."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from r_catalog import (  # noqa: E402
    DISCOVERY_TOOLS,
    INJECTION_PROMPT,
    should_inject_r_catalog,
)


def test_discovery_tools_set_is_the_canonical_six():
    assert DISCOVERY_TOOLS == frozenset({
        "nexo_system_catalog",
        "nexo_tool_explain",
        "nexo_skill_match",
        "nexo_skill_list",
        "nexo_learning_search",
        "nexo_guard_check",
    })


def test_non_trigger_tool_never_injects():
    inject, _ = should_inject_r_catalog("nexo_heartbeat", recent_tool_names=[])
    assert inject is False


def test_create_without_any_discovery_injects():
    inject, prompt = should_inject_r_catalog("nexo_followup_create", recent_tool_names=[])
    assert inject is True
    assert "nexo_followup_create" in prompt
    assert prompt.startswith("R-CATALOG pre-create probe")


@pytest.mark.parametrize("discovery", sorted(DISCOVERY_TOOLS))
def test_any_discovery_tool_suppresses_injection(discovery):
    inject, _ = should_inject_r_catalog("nexo_learning_add", recent_tool_names=[discovery])
    assert inject is False


def test_open_trigger_also_detected():
    inject, _ = should_inject_r_catalog("nexo_workflow_open", recent_tool_names=[])
    assert inject is True


def test_add_trigger_also_detected():
    inject, _ = should_inject_r_catalog("nexo_learning_add", recent_tool_names=[])
    assert inject is True


def test_non_string_input_is_safe():
    inject, _ = should_inject_r_catalog(None, recent_tool_names=None)  # type: ignore[arg-type]
    assert inject is False


def test_discovery_tool_invoking_itself_is_not_triggered():
    inject, _ = should_inject_r_catalog("nexo_skill_list", recent_tool_names=[])
    assert inject is False


def test_prompt_template_parity_with_js_twin():
    expected = (
        "R-CATALOG pre-create probe: about to call {tool} without having "
        "consulted the live inventory in this turn. Run one of "
        "`nexo_system_catalog(query=...)`, `nexo_skill_match`, "
        "`nexo_tool_explain`, `nexo_learning_search` or `nexo_guard_check` "
        "first to avoid duplicating an existing artefact."
    )
    assert INJECTION_PROMPT == expected
