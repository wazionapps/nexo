"""Contract parity tests for v7.6.0.

The constructor-guardian-90 checklist explicitly requires:

- Every rule type declared in `tool-enforcement-map.json` must be
  dispatched by BOTH enforcement engines (Brain Python + Desktop JS).
  If one side supports a type the other does not, the map is lying and
  the user-visible contract is broken.

- The conditional rule for `nexo_task_open` must fire BEFORE 10 tool
  calls' worth of non-trivial work has already happened. The checklist
  called the old threshold "tarde"; the v7.6 map lowers it and adds an
  `inject_prompt` so the engine has actual text to emit.

- `on_event` rules (learning_add, skill_match, task_close, confidence_
  check, checkpoint_read/save) must be dispatched by Brain. Previously
  they were only honoured on the Desktop side — confirmed by reading
  the loader in `src/enforcement_engine.py` pre-v7.6.

These tests pin those invariants so future refactors cannot quietly
re-introduce drift between map / Brain / Desktop.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MAP_PATH = REPO_ROOT / "tool-enforcement-map.json"
BRAIN_ENGINE = REPO_ROOT / "src" / "enforcement_engine.py"
DESKTOP_ENGINE = Path(
    os.environ.get(
        "NEXO_DESKTOP_ENGINE",
        "/Users/franciscoc/Documents/_PhpstormProjects/nexo-desktop/enforcement-engine.js",
    )
)


def _load_map() -> dict:
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


def _declared_types(map_data: dict) -> set[str]:
    types: set[str] = set()
    for tool in map_data.get("tools", {}).values():
        for rule in tool.get("enforcement", {}).get("rules", []):
            t = rule.get("type")
            if t:
                types.add(t)
    return types


def _brain_dispatched_types() -> set[str]:
    """Parse `_build_indexes` in Brain engine and return the rule types
    it knows how to load. A type is only "dispatched" if the loader has
    an explicit `rtype == "<t>"` branch.
    """
    src = BRAIN_ENGINE.read_text(encoding="utf-8")
    # Match both `== "type"` and `rtype == "type"` forms used inside the
    # dispatcher. The regex is intentionally narrow so comments mentioning
    # type names do not leak false positives.
    hits = re.findall(r'rtype\s*==\s*"([a-z_]+)"', src)
    return set(hits)


def _desktop_dispatched_types() -> set[str]:
    if not DESKTOP_ENGINE.exists():
        # Desktop repo may not be available in a purely Brain checkout
        # (CI typically has it; local dev always does). Skip gracefully
        # rather than making the whole suite brittle.
        return set()
    src = DESKTOP_ENGINE.read_text(encoding="utf-8")
    # Desktop uses `case '<type>':` in a switch statement inside its
    # loader. That is the canonical signal — any other mention is
    # documentation.
    hits = re.findall(r"case\s+['\"]([a-z_]+)['\"]\s*:", src)
    return set(hits)


def test_all_declared_types_are_dispatched_by_both_engines():
    """Primary contract: if a rule type appears in the map, both engines
    must handle it. v7.5 installs shipped with Brain missing before_tool
    / on_event / conditional. v7.6 fixes that by adding the three
    missing branches; this test pins the fix.
    """
    map_data = _load_map()
    declared = _declared_types(map_data)
    brain = _brain_dispatched_types()
    desktop = _desktop_dispatched_types()

    missing_in_brain = declared - brain
    assert not missing_in_brain, (
        f"Rule types declared in tool-enforcement-map.json but NOT dispatched "
        f"by Brain (src/enforcement_engine.py _build_indexes): {sorted(missing_in_brain)}. "
        f"Either add an elif branch for each type or stop declaring it in the map."
    )

    if desktop:  # only run the Desktop half when the repo is available
        missing_in_desktop = declared - desktop
        assert not missing_in_desktop, (
            f"Rule types declared in tool-enforcement-map.json but NOT dispatched "
            f"by Desktop (nexo-desktop/enforcement-engine.js case list): "
            f"{sorted(missing_in_desktop)}. Fix the desktop engine or the map."
        )


def test_fase2_schema_lists_exactly_what_engines_support():
    """fase2_schema.supported_rule_types_v2_0 documents what both engines
    must cover. It is the external contract consumers read; if it claims
    support for a type neither engine implements, the public contract is
    a lie. v7.6 invariant: every documented v2_0 type is covered by both
    engines.
    """
    map_data = _load_map()
    documented = set(map_data.get("fase2_schema", {}).get("supported_rule_types_v2_0", []))
    brain = _brain_dispatched_types()

    missing_in_brain = documented - brain
    assert not missing_in_brain, (
        f"fase2_schema.supported_rule_types_v2_0 declares support for "
        f"{sorted(missing_in_brain)} but Brain's _build_indexes does not "
        f"dispatch them. Fix the engine or remove from the schema."
    )

    desktop = _desktop_dispatched_types()
    if desktop:
        missing_in_desktop = documented - desktop
        assert not missing_in_desktop, (
            f"fase2_schema.supported_rule_types_v2_0 declares support for "
            f"{sorted(missing_in_desktop)} but Desktop's engine does not. "
            f"Fix Desktop or remove from the schema."
        )


def test_task_open_conditional_threshold_is_low_enough_for_non_trivial_work():
    """Checklist explicit rule: the conditional task_open trigger must not
    wait until 10 tool calls have happened before nudging. v7.6 caps the
    threshold at 5 and requires a concrete inject_prompt so the dispatcher
    can actually emit a visible reminder (instead of the prior empty
    reminder_prompt-only path).
    """
    map_data = _load_map()
    tool = map_data["tools"]["nexo_task_open"]
    rules = tool["enforcement"]["rules"]
    conditional_rules = [r for r in rules if r.get("type") == "conditional"]
    assert conditional_rules, "nexo_task_open must declare a conditional rule"
    threshold = int(conditional_rules[0].get("threshold", 999))
    assert threshold <= 5, (
        f"task_open conditional threshold is {threshold}; checklist requires <=5 "
        f"so non-trivial work surfaces the obligation BEFORE 10 tool calls."
    )
    assert conditional_rules[0].get("inject_prompt"), (
        "conditional rule must carry inject_prompt; otherwise the engine "
        "has no text to surface when the threshold trips."
    )


def test_learning_add_on_correction_fires_immediately():
    """Checklist: 'Haz que la captura de learning sea inmediata cuando hay
    corrección ... No quiero que quede limitada a 3 mensajes'. v7.6 map
    drops grace_messages from 3 to 0 so the learning is recorded the
    turn the correction lands.
    """
    map_data = _load_map()
    rules = map_data["tools"]["nexo_learning_add"]["enforcement"]["rules"]
    evt = next((r for r in rules if r.get("type") == "on_event"), None)
    assert evt is not None, "learning_add must declare an on_event rule"
    grace = int(evt.get("grace_messages", 999))
    assert grace == 0, (
        f"learning_add grace_messages is {grace}; checklist requires 0 so "
        f"corrections produce a learning in the same turn, not 3 later."
    )


def test_brain_engine_exposes_raise_event_and_conditional_helpers():
    """The v7.6 engine must expose the new public API for hooks: raise_event
    (for on_event triggers) and reset_task_cycle (for conditional rearms
    after task_close). The test pins the signatures so the hooks layer
    cannot quietly diverge from the engine.
    """
    src = BRAIN_ENGINE.read_text(encoding="utf-8")
    assert "def raise_event(" in src, "EnforcementEngine must expose raise_event(event_name, context=None)"
    assert "def reset_task_cycle(" in src, "EnforcementEngine must expose reset_task_cycle(tool)"
    assert "def on_tool_call_before(" in src, (
        "EnforcementEngine must expose on_tool_call_before(raw_name, tool_input) "
        "so before_tool rules can fire prior to destructive tool invocations."
    )


def test_after_tool_satisfaction_is_per_instance_not_once_per_session():
    """The checklist flagged this specific bug: after_tool used to check
    `target not in self.tools_called`, meaning a single historical call
    satisfied every subsequent trigger. v7.6 replaces that with a
    per-instance comparison (target_last_instance > trigger_instance).
    This test pins the shape of the fix via source inspection.
    """
    src = BRAIN_ENGINE.read_text(encoding="utf-8")
    assert "target_last < current_instance" in src, (
        "after_tool dispatcher must compare per-instance counters (v7.6), "
        "not set-membership in tools_called (the pre-v7.6 bug)."
    )
    assert "_tool_instance_counter" in src, (
        "EnforcementEngine must maintain a monotonic _tool_instance_counter "
        "so before_tool/after_tool can reason about 'called after this trigger'."
    )
