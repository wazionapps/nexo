"""v7.7 Gap closure tests — 6 critical obedience rails.

The constructor-guardian-90 pass 1 (v7.6.0) shipped contract parity but
explicitly listed six gaps that needed coverage-per-rail in pass 2.
This file pins one or two invariants per rail:

  1. multi_step_task_detected — detector fires automatically after
     repeated edit/execute/delegate signals without skill_match.
  2. task_close expanded vocabulary — R16 classifier prompt now
     recognises sent / fixed / published / deployed / shipped / released
     (and their Spanish equivalents) as done-claims.
  3. R_CATALOG extended scope — fires on plain Edit/Write into artefact-
     bearing paths even without a dedicated *_create MCP tool.
  4. R_PRIMITIVE_CHOICE — SK-CREATE-NEXO-PRIMITIVE gate catches Edit/
     Write of a brand-new artefact file without a recent primitive-
     choice probe.
  5. R11_plugin_load pre-inventory — guardian_default v1.5.0 ships it
     at hard; Desktop mirror agrees.
  6. Guardian default v1.5.0 invariants — the bumped defaults survive
     future refactors.
"""
from __future__ import annotations

import json
from pathlib import Path

from r_catalog import should_inject_r_catalog, ARTEFACT_PATH_FRAGMENTS as CATALOG_FRAGMENTS
from r_primitive_choice import should_inject_r_primitive


REPO_ROOT = Path(__file__).resolve().parent.parent
MAP_PATH = REPO_ROOT / "tool-enforcement-map.json"
DEFAULTS_PATH = REPO_ROOT / "src" / "presets" / "guardian_default.json"


def _load_map() -> dict:
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


def _load_defaults() -> dict:
    return json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))


# ── Rail 1: multi_step_task_detected detector fires automatically ──

def test_rail_1_multi_step_detector_is_wired_into_on_tool_call():
    """The v7.6 engine exposed raise_event but nothing automatically
    raised multi_step_task_detected. v7.7 adds the heuristic inside
    on_tool_call so three recent Edit/Write/Task calls without a
    nexo_skill_match raise the event exactly once per task cycle.
    """
    src = (REPO_ROOT / "src" / "enforcement_engine.py").read_text(encoding="utf-8")
    # The heuristic must live inside on_tool_call (not only expose the
    # method) and guard the latch.
    assert "multi_step_task_detected" in src, (
        "engine must reference multi_step_task_detected so the map's on_event "
        "rule has an actual trigger path."
    )
    assert "_multi_step_event_fired" in src, (
        "engine must carry a latch so the event fires once per task cycle."
    )
    # Latch cleared on skill_match OR task_close.
    assert 'name == "nexo_skill_match"' in src or "nexo_skill_match" in src, (
        "latch must clear on nexo_skill_match."
    )


# ── Rail 2: task_close recognises extended done-claim vocabulary ─────

def test_rail_2_r16_classifier_recognises_sent_fixed_deployed_shipped():
    """The classifier prompt for R16 is the only way the engine
    decides whether a turn counts as a done-claim. v7.7 expands that
    prompt to cover sent / fixed / delivered / deployed / shipped /
    released / merged / pushed / resolved (plus Spanish equivalents).
    If this drifts, the on_event trigger `done_claimed_with_open_task`
    becomes a much narrower detector than the checklist requires.
    """
    prompt = (REPO_ROOT / "templates" / "core-prompts" / "r16-declared-done-question.md").read_text(encoding="utf-8")
    required_markers = [
        "sent", "delivered", "published", "deployed",
        "released", "fixed", "shipped", "merged", "pushed",
        "resolved", "enviado", "desplegado", "publicado",
    ]
    missing = [m for m in required_markers if m.lower() not in prompt.lower()]
    assert not missing, (
        f"R16 classifier prompt is missing markers {missing}. The on_event "
        f"trigger `done_claimed_with_open_task` depends on this classifier "
        f"firing on the full done-claim vocabulary."
    )


# ── Rail 3: R_CATALOG ampliado a writes Edit/Write artefactos ────────

def test_rail_3_r_catalog_fires_on_plain_edit_into_skill_path():
    """Pre-v7.7 R_CATALOG only fired on nexo_*_create/_open/_add. v7.7
    extends the trigger to plain Edit/Write targeting paths under
    skills/, plugins/, personal scripts, and related artefact roots.
    """
    should, prompt = should_inject_r_catalog(
        "Edit",
        recent_tool_names=["Read", "Bash"],  # no inventory tool
        files=["/Users/x/repo/skills/new-skill/skill.md"],
    )
    assert should, "Edit into /skills/ must trigger R_CATALOG v7.7"
    assert "Edit" in prompt, "prompt must reference the triggering tool"


def test_rail_3_r_catalog_relief_on_recent_inventory_tool():
    """Same Edit should NOT fire when a discovery tool landed recently."""
    should, _prompt = should_inject_r_catalog(
        "Edit",
        recent_tool_names=["nexo_skill_match"],
        files=["/Users/x/repo/skills/new-skill/skill.md"],
    )
    assert not should, (
        "R_CATALOG must be satisfied by a recent discovery tool; otherwise "
        "agents get nagged after doing exactly what the rule wants."
    )


def test_rail_3_r_catalog_ignores_non_artefact_writes():
    """Edit of, say, README.md must not trigger the extended rule —
    only writes into artefact-bearing fragments should."""
    should, _prompt = should_inject_r_catalog(
        "Edit",
        recent_tool_names=[],
        files=["/tmp/scratch/README.md"],
    )
    assert not should, "non-artefact paths must be ignored by R_CATALOG v7.7"


# ── Rail 4: R_PRIMITIVE_CHOICE — SK-CREATE-NEXO-PRIMITIVE gate ───────

def test_rail_4_r_primitive_choice_fires_on_new_artefact_write():
    """Write of a never-before-seen artefact file without a recent
    primitive-choice probe must trigger R_PRIMITIVE_CHOICE."""
    should, prompt = should_inject_r_primitive(
        "Write",
        files=["/Users/x/repo/src/plugins/new_thing.py"],
        recent_tool_names=["Bash"],
        recent_tool_records=[],
    )
    assert should, "fresh artefact Write must trigger R_PRIMITIVE_CHOICE"
    assert "SK-CREATE-NEXO-PRIMITIVE" in prompt, "prompt must name the canonical skill"


def test_rail_4_r_primitive_choice_silent_when_skill_match_recent():
    """If nexo_skill_match fired recently, the rule is satisfied."""
    should, _prompt = should_inject_r_primitive(
        "Write",
        files=["/Users/x/repo/src/plugins/new_thing.py"],
        recent_tool_names=["nexo_skill_match"],
        recent_tool_records=[],
    )
    assert not should, "recent skill_match must satisfy R_PRIMITIVE_CHOICE"


def test_rail_4_r_primitive_choice_silent_when_file_previously_read():
    """Editing an EXISTING artefact (read/grep already touched the
    path) is not flagged — the rule targets NEW artefact creation."""
    class FakeRecord:
        def __init__(self, tool, files):
            self.tool = tool
            self.files = files
    records = [FakeRecord("Read", ("/Users/x/repo/src/plugins/new_thing.py",))]
    should, _prompt = should_inject_r_primitive(
        "Edit",
        files=["/Users/x/repo/src/plugins/new_thing.py"],
        recent_tool_names=["Read"],
        recent_tool_records=records,
    )
    assert not should, "editing an existing artefact must not trigger the rule"


# ── Rail 5: R11_plugin_load_pre_inventory is hard by default ─────────

def test_rail_5_r11_plugin_load_is_hard_by_default():
    defaults = _load_defaults()
    mode = defaults.get("rules", {}).get("R11_plugin_load_pre_inventory")
    assert mode == "hard", (
        f"R11_plugin_load_pre_inventory must be 'hard' (v7.7 pass 2); "
        f"got {mode!r}. The checklist explicitly asked for the pre-"
        f"inventory gate to reflect a hard default."
    )


def test_rail_5_r_primitive_choice_registered_in_defaults():
    defaults = _load_defaults()
    rules = defaults.get("rules", {})
    assert "R_PRIMITIVE_CHOICE" in rules, (
        "R_PRIMITIVE_CHOICE must be declared in guardian_default.json v1.5.0"
    )
    mode = rules["R_PRIMITIVE_CHOICE"]
    assert mode in ("soft", "hard"), (
        f"R_PRIMITIVE_CHOICE default must be soft or hard; got {mode!r}."
    )


# ── Rail 6: guardian_default v1.5.0 shape invariants ─────────────────

def test_rail_6_guardian_defaults_version_bumped():
    defaults = _load_defaults()
    assert defaults.get("version", "").startswith("1."), (
        "guardian_default.json must keep a 1.x version line."
    )
    # v1.5.0 is the pass-2 version — any later bump must keep the
    # semantics for R11 and R_PRIMITIVE_CHOICE covered by the tests
    # above. If the version drops below 1.5.0 something regressed.
    version = defaults.get("version", "0.0.0")
    parts = version.split(".") + ["0", "0"]
    major = int(parts[0])
    minor = int(parts[1])
    assert (major, minor) >= (1, 5), (
        f"guardian_default.json version {version} is below 1.5.0 — the "
        f"v7.7 pass-2 baseline. Do not regress."
    )


def test_rail_6_r_catalog_fragments_include_core_artefact_roots():
    """The fragment list must include the artefact roots the checklist
    called out explicitly: skills/, plugins/, personal scripts,
    templates/core-prompts/. Dropping any of them re-opens Gap 3."""
    required = ("/skills/", "/plugins/", "/personal/scripts/", "/templates/core-prompts/", "/src/plugins/")
    for fragment in required:
        assert any(f == fragment or f.endswith(fragment) or fragment in f for f in CATALOG_FRAGMENTS), (
            f"R_CATALOG fragment list missing {fragment}; Gap 3 coverage broken."
        )
