"""Skills plugin — reusable procedures, executable skills, and feedback loops."""

from __future__ import annotations

import json

from db import (
    create_skill,
    delete_skill,
    get_skill,
    get_skill_stats,
    list_skills,
    match_skills,
    merge_skills,
    record_skill_usage,
    search_skills,
    update_skill,
)
from skills_runtime import (
    apply_skill,
    approve_skill_execution,
    compose_skills,
    detect_skill_coactivation_candidates,
    get_featured_skill_summaries,
    list_evolution_candidates,
    materialize_outcome_pattern_skill,
    promote_skill,
    review_skill_outcomes,
    retire_skill,
    sync_skills,
    test_skill,
)


def handle_skill_create(
    id: str,
    name: str,
    description: str = "",
    level: str = "draft",
    tags: str = "[]",
    trigger_patterns: str = "[]",
    source_sessions: str = "[]",
    linked_learnings: str = "[]",
    file_path: str = "",
    mode: str = "",
    source_kind: str = "personal",
    execution_level: str = "none",
    approval_required: bool = False,
    params_schema: str = "{}",
    command_template: str = "{}",
    executable_entry: str = "",
) -> str:
    if not id.startswith("SK-"):
        return "ERROR: Skill ID must start with 'SK-' (e.g., SK-DEPLOY-CHROME-EXT)"
    if get_skill(id):
        return f"ERROR: Skill {id} already exists. Use nexo_skill_update to modify."

    result = create_skill(
        skill_id=id,
        name=name,
        description=description,
        level=level,
        tags=tags,
        trigger_patterns=trigger_patterns,
        source_sessions=source_sessions,
        linked_learnings=linked_learnings,
        file_path=file_path,
        mode=mode,
        source_kind=source_kind,
        execution_level=execution_level,
        approval_required=approval_required,
        params_schema=params_schema,
        command_template=command_template,
        executable_entry=executable_entry,
    )
    if "error" in result:
        return f"ERROR: {result['error']}"

    return (
        f"Skill {id} created ({result['level']}, {result.get('mode', 'guide')}, trust={result.get('trust_score', 50)}).\n"
        f"  Name: {name}\n"
        f"  Source: {result.get('source_kind', source_kind)}\n"
        f"  Execution: {result.get('execution_level', execution_level)}"
    )


def handle_skill_match(task: str, level: str = "") -> str:
    matches = match_skills(task, level=level)
    if not matches:
        return f"No skills found for: '{task}'"

    lines = [f"SKILLS MATCHED ({len(matches)}) for '{task}':"]
    for match in matches:
        match_method = match.pop("_match", "unknown")
        lines.append(
            f"  [{match['id']}] {match['name']} ({match['level']}, {match.get('mode', 'guide')}, "
            f"{match.get('source_kind', 'personal')}, trust={match['trust_score']}, used={match['use_count']}x) "
            f"via {match_method}"
        )
        lines.append(f"    {match['description'][:140]}")
    return "\n".join(lines)


def handle_skill_get(id: str) -> str:
    skill = get_skill(id)
    if not skill:
        return f"ERROR: Skill {id} not found."

    lines = [
        f"SKILL: {skill['id']}",
        f"  Name: {skill['name']}",
        f"  Description: {skill['description']}",
        f"  Level: {skill['level']}",
        f"  Mode: {skill.get('mode', 'guide')}",
        f"  Source: {skill.get('source_kind', 'personal')}",
        f"  Trust: {skill['trust_score']}",
        f"  Execution level: {skill.get('execution_level', 'none')}",
        f"  Approval required: {bool(skill.get('approval_required', 0))}",
        f"  Approved at: {skill.get('approved_at') or 'no'}",
        f"  Definition: {skill.get('definition_path') or '(none)'}",
        f"  File: {skill.get('file_path') or '(none)'}",
        f"  Params schema: {skill.get('params_schema', '{}')}",
        f"  Triggers: {skill['trigger_patterns']}",
        f"  Stats: {skill['use_count']} uses, {skill['success_count']} success, {skill['fail_count']} fail",
    ]
    return "\n".join(lines)


def handle_skill_result(id: str, success: bool = True, context: str = "", notes: str = "") -> str:
    result = record_skill_usage(skill_id=id, success=success, context=context, notes=notes)
    if "error" in result:
        return f"ERROR: {result['error']}"

    promotion = result.get("_promotion")
    msg = f"Skill {id} usage recorded: {'SUCCESS' if success else 'FAILURE'} (trust={result['trust_score']})"
    if promotion:
        msg += f"\n  ⚡ PROMOTION: {promotion}"
    return msg


def handle_skill_list(level: str = "", tag: str = "", source_kind: str = "") -> str:
    skills = list_skills(level=level, tag=tag, source_kind=source_kind)
    if not skills:
        return "No skills found."

    lines = [f"SKILLS ({len(skills)}):"]
    for skill in skills:
        lines.append(
            f"  [{skill['id']}] {skill['name']} ({skill['level']}, {skill.get('mode', 'guide')}, "
            f"{skill.get('source_kind', 'personal')}, trust={skill['trust_score']}, used={skill['use_count']}x)"
        )
    return "\n".join(lines)


def handle_skill_merge(id1: str, id2: str, keep_id: str = "") -> str:
    result = merge_skills(id1, id2, keep_id=keep_id)
    if "error" in result:
        return f"ERROR: {result['error']}"
    return (
        f"Skills merged. Kept {result['id']}, deleted {result['_merged_from']}.\n"
        f"  Trust: {result['trust_score']}, Uses: {result['use_count']}"
    )


def handle_skill_stats() -> str:
    stats = get_skill_stats()
    return (
        "SKILL STATS:\n"
        f"  Total: {stats['total']}\n"
        f"  By level: {', '.join(f'{k}={v}' for k, v in sorted(stats['by_level'].items()))}\n"
        f"  Avg trust: {stats['avg_trust']}\n"
        f"  Total uses: {stats['total_uses']} (success rate: {stats['success_rate']}%)\n"
        f"  Uses last 7d: {stats['uses_last_7d']}\n"
        f"  Reuse rate: {stats['skill_reuse_rate']}\n"
        f"  Outcome-backed skills: {stats['outcome_backed_skills']} (avg success: {stats['outcome_backed_success_rate']}%)\n"
        f"  Promoted from evidence: {stats['promoted_from_evidence_count']}\n"
        f"  Retired for poor outcomes: {stats['retired_for_poor_outcomes_count']}"
    )


def handle_skill_apply(id: str, params: str = "{}", mode: str = "auto", dry_run: bool = False, context: str = "") -> str:
    return json.dumps(apply_skill(id, params=params, mode=mode, dry_run=dry_run, context=context), ensure_ascii=False)


def handle_skill_test(id: str, params: str = "{}", mode: str = "auto", context: str = "") -> str:
    return json.dumps(test_skill(id, params=params, mode=mode, context=context), ensure_ascii=False)


def handle_skill_approve(id: str, execution_level: str = "", approved_by: str = "") -> str:
    result = approve_skill_execution(id, execution_level=execution_level, approved_by=approved_by)
    if "error" in result:
        return f"ERROR: {result['error']}"
    return (
        f"Skill {id} approved.\n"
        f"  Execution level: {result.get('execution_level', 'none')}\n"
        f"  Approved at: {result.get('approved_at', '')}\n"
        f"  Approved by: {result.get('approved_by', '')}"
    )


def handle_skill_sync() -> str:
    result = sync_skills()
    return json.dumps(result, ensure_ascii=False)


def handle_skill_featured(limit: int = 5) -> str:
    return json.dumps(get_featured_skill_summaries(limit=limit), ensure_ascii=False)


def handle_skill_evolution_candidates() -> str:
    return json.dumps(list_evolution_candidates(), ensure_ascii=False)


def handle_skill_compose_candidates(
    min_co_occurrence: int = 3,
    min_success_rate: float = 0.6,
    limit: int = 20,
) -> str:
    """List Voyager-style composition candidates from skill_usage co-activation.

    Closes Fase 5 item 5. Detects pairs of skills that fire together
    in the same session repeatedly and could become a single composite.
    Returns the candidates sorted by co-occurrence count, with a
    suggested deterministic skill_id (SK-COMPOSE-<a>+<b>) that
    nexo_skill_compose can take as input.

    Args:
        min_co_occurrence: minimum sessions where both skills fired (default 3).
        min_success_rate: minimum joint success rate (default 0.6).
        limit: max candidates to return (default 20).
    """
    return json.dumps(
        detect_skill_coactivation_candidates(
            min_co_occurrence=int(min_co_occurrence),
            min_success_rate=float(min_success_rate),
            limit=int(limit),
        ),
        ensure_ascii=False,
        indent=2,
    )


def handle_skill_seed_from_outcome_pattern(pattern_key: str) -> str:
    return json.dumps(materialize_outcome_pattern_skill(pattern_key), ensure_ascii=False)


def handle_skill_outcome_review(id: str, auto_apply: bool = False) -> str:
    return json.dumps(review_skill_outcomes(id, auto_apply=auto_apply), ensure_ascii=False)


def handle_skill_promote(id: str, target_level: str = "published", reason: str = "") -> str:
    return json.dumps(promote_skill(id, target_level=target_level, reason=reason), ensure_ascii=False)


def handle_skill_retire(id: str, replacement_id: str = "", reason: str = "") -> str:
    return json.dumps(retire_skill(id, replacement_id=replacement_id, reason=reason), ensure_ascii=False)


def handle_skill_compose(
    new_id: str,
    name: str,
    component_ids: str = "[]",
    description: str = "",
    level: str = "draft",
    mode: str = "guide",
    tags: str = "[]",
    trigger_patterns: str = "[]",
) -> str:
    try:
        component_list = json.loads(component_ids) if str(component_ids or "").strip().startswith("[") else [
            item.strip() for item in str(component_ids or "").split(",") if item.strip()
        ]
    except json.JSONDecodeError:
        component_list = []
    try:
        tags_list = json.loads(tags) if str(tags or "").strip().startswith("[") else [
            item.strip() for item in str(tags or "").split(",") if item.strip()
        ]
    except json.JSONDecodeError:
        tags_list = []
    try:
        trigger_list = json.loads(trigger_patterns) if str(trigger_patterns or "").strip().startswith("[") else [
            item.strip() for item in str(trigger_patterns or "").split(",") if item.strip()
        ]
    except json.JSONDecodeError:
        trigger_list = []
    return json.dumps(
        compose_skills(
            new_skill_id=new_id,
            name=name,
            component_ids=component_list,
            description=description,
            level=level,
            mode=mode,
            tags=tags_list,
            trigger_patterns=trigger_list,
        ),
        ensure_ascii=False,
    )


TOOLS = [
    (handle_skill_create, "nexo_skill_create",
     "Create a new skill with guide/execute/hybrid metadata, triggers, params schema, and execution level."),
    (handle_skill_match, "nexo_skill_match",
     "Find skills matching a task description. Call before multi-step tasks."),
    (handle_skill_get, "nexo_skill_get",
     "Get a skill's full details, including execution metadata and approval state."),
    (handle_skill_result, "nexo_skill_result",
     "Record the result of using a skill. Updates trust and promotions."),
    (handle_skill_list, "nexo_skill_list",
     "List skills, optionally filtered by level, tag, or source kind."),
    (handle_skill_merge, "nexo_skill_merge",
     "Merge two similar skills into one."),
    (handle_skill_stats, "nexo_skill_stats",
     "Show aggregate skill statistics."),
    (handle_skill_apply, "nexo_skill_apply",
     "Apply a skill in guide, execute, or hybrid mode. Execution goes through the stable nexo scripts runtime."),
    (handle_skill_test, "nexo_skill_test",
     "Test a skill through the canonical runtime in dry-run mode before wider use."),
    (handle_skill_approve, "nexo_skill_approve",
     "Approve a local/remote executable skill so it can run."),
    (handle_skill_sync, "nexo_skill_sync",
     "Sync filesystem skill definitions from personal/core/community directories into SQLite."),
    (handle_skill_featured, "nexo_skill_featured",
     "Return featured published/stable skills for startup discovery."),
    (handle_skill_evolution_candidates, "nexo_skill_evolution_candidates",
     "Return candidates for skill improvement or text-to-script evolution."),
    (handle_skill_seed_from_outcome_pattern, "nexo_skill_seed_from_outcome_pattern",
     "Materialize a draft skill candidate from a repeated successful outcome pattern with linked evidence."),
    (handle_skill_outcome_review, "nexo_skill_outcome_review",
     "Review whether a skill should be promoted, deprioritized, or retired based on sustained outcome evidence."),
    (handle_skill_promote, "nexo_skill_promote",
     "Promote a skill to a stronger published/stable lifecycle stage."),
    (handle_skill_retire, "nexo_skill_retire",
     "Retire a skill cleanly so it leaves the active lifecycle."),
    (handle_skill_compose, "nexo_skill_compose",
     "Compose multiple existing skills into one higher-level reusable skill."),
    (handle_skill_compose_candidates, "nexo_skill_compose_candidates",
     "Voyager-style detection: list skill pairs that fired together in 3+ sessions with 60%+ joint success and could become a single composite (closes Fase 5 item 5)."),
]
