"""Skills plugin — reusable procedures extracted from complex tasks.

Skills are procedural knowledge (step-by-step how-tos) vs learnings which are
declarative (don't do X). Created automatically by Deep Sleep or manually.

Pipeline: trace → draft → published, fully autonomous.
Trust score with decay controls quality — no human approval gates.
"""

import json
from db import (
    create_skill, get_skill, list_skills, search_skills,
    update_skill, delete_skill,
    record_skill_usage, match_skills, merge_skills, get_skill_stats,
)


def handle_skill_create(
    id: str,
    name: str,
    description: str = '',
    level: str = 'draft',
    tags: str = '[]',
    trigger_patterns: str = '[]',
    source_sessions: str = '[]',
    linked_learnings: str = '[]',
    file_path: str = '',
) -> str:
    """Create a new skill (reusable procedure).

    Skills are procedural knowledge — step-by-step instructions for complex tasks.
    Created by Deep Sleep (auto-extraction) or manually during sessions.

    Pipeline levels: trace → draft → published → archived.
    Promotion is automatic: 2+ successful uses in distinct contexts → published.

    Args:
        id: Unique ID starting with 'SK-' (e.g., SK-DEPLOY-CHROME-EXT).
        name: Human-readable name (e.g., 'Deploy Chrome Extension').
        description: What this skill does (1-2 sentences).
        level: Starting level — trace, draft (default), published, archived.
        tags: JSON array of tags for discovery (e.g., '["chrome", "extension", "deploy"]').
        trigger_patterns: JSON array of phrases that should trigger this skill
                         (e.g., '["deploy extension", "publish chrome"]').
        source_sessions: JSON array of diary IDs where this skill was observed.
        linked_learnings: JSON array of learning IDs related to this skill.
        file_path: Path to the .md file with full procedure (if stored as file).
    """
    if not id.startswith('SK-'):
        return "ERROR: Skill ID must start with 'SK-' (e.g., SK-DEPLOY-CHROME-EXT)"

    existing = get_skill(id)
    if existing:
        return f"ERROR: Skill {id} already exists. Use nexo_skill_update to modify."

    result = create_skill(
        skill_id=id, name=name, description=description, level=level,
        tags=tags, trigger_patterns=trigger_patterns,
        source_sessions=source_sessions, linked_learnings=linked_learnings,
        file_path=file_path,
    )
    if "error" in result:
        return f"ERROR: {result['error']}"

    return (
        f"Skill {id} created ({level}, trust={result.get('trust_score', 50)}).\n"
        f"  Name: {name}\n"
        f"  Tags: {tags}\n"
        f"  Triggers: {trigger_patterns}"
    )


def handle_skill_match(task: str, level: str = '') -> str:
    """Find skills matching a task description. Call BEFORE starting multi-step tasks.

    Searches by: FTS5 relevance, trigger pattern matching, tag keyword overlap.
    Returns top-3 matches sorted by trust score.

    Args:
        task: Description of what you're about to do (e.g., 'deploy chrome extension to CWS').
        level: Filter by level (optional). Default: draft + published.
    """
    matches = match_skills(task, level=level)
    if not matches:
        return f"No skills found for: '{task}'"

    lines = [f"SKILLS MATCHED ({len(matches)}) for '{task}':"]
    for m in matches:
        match_method = m.pop('_match', 'unknown')
        fp = f" → {m['file_path']}" if m.get('file_path') else ""
        lines.append(
            f"  [{m['id']}] {m['name']} ({m['level']}, trust={m['trust_score']}, "
            f"used={m['use_count']}x) via {match_method}{fp}\n"
            f"    {m['description'][:120]}"
        )
        try:
            triggers = json.loads(m.get('trigger_patterns', '[]'))
            if triggers:
                lines.append(f"    Triggers: {', '.join(triggers[:5])}")
        except (json.JSONDecodeError, TypeError):
            pass
    return "\n".join(lines)


def handle_skill_get(id: str) -> str:
    """Get a skill's full details including usage history.

    Args:
        id: Skill ID (e.g., SK-DEPLOY-CHROME-EXT).
    """
    skill = get_skill(id)
    if not skill:
        return f"ERROR: Skill {id} not found."

    from db import get_db
    conn = get_db()
    recent_uses = conn.execute(
        "SELECT * FROM skill_usage WHERE skill_id = ? ORDER BY created_at DESC LIMIT 5",
        (id,),
    ).fetchall()

    lines = [
        f"SKILL: {skill['id']}",
        f"  Name: {skill['name']}",
        f"  Description: {skill['description']}",
        f"  Level: {skill['level']}",
        f"  Trust: {skill['trust_score']}",
        f"  File: {skill['file_path'] or '(none)'}",
        f"  Tags: {skill['tags']}",
        f"  Triggers: {skill['trigger_patterns']}",
        f"  Source sessions: {skill['source_sessions']}",
        f"  Linked learnings: {skill['linked_learnings']}",
        f"  Stats: {skill['use_count']} uses, {skill['success_count']} success, {skill['fail_count']} fail",
        f"  Created: {skill['created_at']}",
        f"  Last used: {skill['last_used_at'] or 'never'}",
    ]

    if recent_uses:
        lines.append("\n  RECENT USAGE:")
        for u in recent_uses:
            u = dict(u)
            status = "✓" if u['success'] else "✗"
            lines.append(f"    {status} {u['created_at']} — {u['context'][:60] or '(no context)'}")
            if u.get('notes'):
                lines.append(f"      Notes: {u['notes'][:80]}")

    return "\n".join(lines)


def handle_skill_result(id: str, success: bool = True, context: str = '', notes: str = '') -> str:
    """Record the result of using a skill. Auto-promotes/degrades based on trust rules.

    Call this AFTER following a skill's procedure to record whether it worked.
    - Success: trust +5. After 2+ successes in distinct contexts: draft → published.
    - Failure: trust -10. If trust < 20: → archived.

    Args:
        id: Skill ID.
        success: Whether the skill's procedure worked correctly.
        context: What task you were doing (used for distinct-context promotion).
        notes: Additional notes (especially useful for failures — what went wrong).
    """
    result = record_skill_usage(skill_id=id, success=success, context=context, notes=notes)
    if "error" in result:
        return f"ERROR: {result['error']}"

    promotion = result.pop('_promotion', None)
    status = "SUCCESS" if success else "FAILURE"
    msg = f"Skill {id} usage recorded: {status} (trust={result['trust_score']})"
    if promotion:
        msg += f"\n  ⚡ PROMOTION: {promotion}"
    return msg


def handle_skill_list(level: str = '', tag: str = '') -> str:
    """List all skills, optionally filtered by level or tag.

    Args:
        level: Filter by level — trace, draft, published, archived.
        tag: Filter by tag (e.g., 'chrome', 'deploy', 'shopify').
    """
    skills = list_skills(level=level, tag=tag)
    if not skills:
        filters = []
        if level: filters.append(f"level={level}")
        if tag: filters.append(f"tag={tag}")
        return f"No skills found{' (' + ', '.join(filters) + ')' if filters else ''}."

    lines = [f"SKILLS ({len(skills)}):"]
    for s in skills:
        fp = f" → {s['file_path']}" if s.get('file_path') else ""
        used = f", last={s['last_used_at'][:10]}" if s.get('last_used_at') else ""
        lines.append(
            f"  [{s['id']}] {s['name']} ({s['level']}, trust={s['trust_score']}, "
            f"used={s['use_count']}x{used}){fp}"
        )
    return "\n".join(lines)


def handle_skill_merge(id1: str, id2: str, keep_id: str = '') -> str:
    """Merge two similar skills into one. Combines tags, triggers, usage history.

    The survivor keeps the higher trust score and all combined metadata.
    The donor is deleted.

    Args:
        id1: First skill ID.
        id2: Second skill ID.
        keep_id: Which one to keep (default: higher trust score).
    """
    result = merge_skills(id1, id2, keep_id=keep_id)
    if "error" in result:
        return f"ERROR: {result['error']}"

    merged_from = result.pop('_merged_from', '?')
    return (
        f"Skills merged. Kept {result['id']}, deleted {merged_from}.\n"
        f"  Trust: {result['trust_score']}, Uses: {result['use_count']}, "
        f"Tags: {result['tags']}"
    )


def handle_skill_stats() -> str:
    """Show aggregate skill statistics: total count, by level, avg trust, usage rates."""
    stats = get_skill_stats()
    levels = stats.get('by_level', {})
    lines = [
        "SKILL STATS:",
        f"  Total: {stats['total']}",
        f"  By level: {', '.join(f'{k}={v}' for k, v in sorted(levels.items()))}",
        f"  Avg trust: {stats['avg_trust']}",
        f"  Total uses: {stats['total_uses']} (success rate: {stats['success_rate']}%)",
        f"  Uses last 7d: {stats['uses_last_7d']}",
    ]
    return "\n".join(lines)


# Plugin registration — TOOLS array consumed by plugin_loader.py
TOOLS = [
    (handle_skill_create, "nexo_skill_create",
     "Create a new skill (reusable procedure). Skills are step-by-step instructions for complex tasks. "
     "Auto-promoted from draft→published after 2+ successful uses. ID must start with 'SK-'."),

    (handle_skill_match, "nexo_skill_match",
     "Find skills matching a task description. Call BEFORE starting multi-step tasks "
     "to check if a reusable procedure exists. Returns top-3 matches by trust score."),

    (handle_skill_get, "nexo_skill_get",
     "Get a skill's full details including procedure, tags, triggers, and usage history."),

    (handle_skill_result, "nexo_skill_result",
     "Record the result of using a skill (success/failure). Auto-promotes draft→published "
     "after 2+ successes, auto-archives if trust drops below 20."),

    (handle_skill_list, "nexo_skill_list",
     "List all skills, optionally filtered by level (trace/draft/published/archived) or tag."),

    (handle_skill_merge, "nexo_skill_merge",
     "Merge two similar skills into one. Combines tags, triggers, and usage history. "
     "Survivor keeps the higher trust score."),

    (handle_skill_stats, "nexo_skill_stats",
     "Show aggregate skill statistics: count by level, average trust, usage rates."),
]
