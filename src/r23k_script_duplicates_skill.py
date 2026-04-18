"""R23k — personal script creation duplicates an existing skill.

Pure decision module. Part of Fase D2 (soft).

Before nexo_personal_script_create, the engine runs a silent
skill_match against the script description. If any skill has
similarity > 0.75, R23k warns — suggesting the operator reuse or
extend the skill instead of cloning it as a one-off personal script.
"""
from __future__ import annotations


INJECTION_PROMPT_TEMPLATE = (
    "R23k script duplicates skill: the personal script '{script}' "
    "overlaps an existing skill '{skill}' (similarity={score}). "
    "Prefer `nexo_skill_apply skill_id={skill_id}` or extend the "
    "skill instead of cloning as a personal script — skills are "
    "shared and evolve with the brain."
)


def should_inject_r23k(
    tool_name: str,
    tool_input,
    *,
    skill_matches: list[dict] | None,
    threshold: float = 0.75,
) -> tuple[bool, str]:
    if tool_name != "nexo_personal_script_create":
        return False, ""
    if not isinstance(tool_input, dict):
        return False, ""
    script_name = str(tool_input.get("name") or tool_input.get("script_name") or "script")
    best = None
    best_score = -1.0
    for match in skill_matches or []:
        try:
            score = float(match.get("score") or match.get("similarity") or 0.0)
        except (TypeError, ValueError):
            continue
        if score > best_score:
            best_score = score
            best = match
    if not best or best_score < threshold:
        return False, ""
    # Format score to match JS twin (two-decimal). The template stays
    # byte-for-byte identical with lib/r23k-script-duplicates-skill.js;
    # both engines format `score` before interpolation.
    prompt = INJECTION_PROMPT_TEMPLATE.format(
        script=script_name,
        skill=best.get("title") or best.get("name") or "?",
        score=f"{int(best_score*100 + 0.5)/100:.2f}",  # half-up to match JS .toFixed(2) (parity)
        skill_id=best.get("id") or best.get("skill_id") or "?",
    )
    return True, prompt
