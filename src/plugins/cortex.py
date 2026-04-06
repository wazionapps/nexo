"""Cognitive Cortex plugin — middleware cognitive layer for NEXO Brain.

Provides structured pre-action reasoning with architectural inhibitory control.
The Cortex does NOT generate answers — it gates, plans, and validates actions.

Activation: event-driven, not on every turn. Only on:
- Tool intent (edit, execute, delegate)
- Ambiguity in user request
- Destructive actions
- Multi-step tasks
- Retry after failure
- Contradictions with known facts

v0.1: Single MCP tool + middleware validation.
"""

import json
import secrets
import time


def _get_db():
    from db import get_db
    return get_db()


def _get_core_rules_for_task(task_type: str) -> list[str]:
    """Get relevant Core Rules for the given task type."""
    conn = _get_db()
    try:
        # Map task type to rule categories
        category_map = {
            "edit": ["integrity", "execution"],
            "execute": ["integrity", "execution", "delegation"],
            "delegate": ["delegation"],
            "analyze": ["execution", "memory"],
            "answer": ["communication"],
        }
        categories = category_map.get(task_type, ["integrity", "execution"])
        placeholders = ",".join("?" * len(categories))

        rows = conn.execute(
            f"SELECT id, rule FROM core_rules WHERE category IN ({placeholders}) AND is_active = 1 AND type = 'blocking' ORDER BY importance DESC LIMIT 5",
            categories
        ).fetchall()
        return [f"{r['id']}: {r['rule']}" for r in rows]
    except Exception:
        return []


def _get_trust_score() -> float:
    """Get current trust score from cognitive.db."""
    try:
        import cognitive
        return cognitive.get_trust_score()
    except Exception:
        return 50.0


def _validate_state(state: dict) -> dict:
    """Validate cognitive state and determine action mode.

    Returns dict with: mode, warnings, injected_rules, blocked_reason
    """
    warnings = []
    mode = "act"  # default: allow action
    blocked_reason = None

    task_type = state.get("task_type", "answer")
    plan = state.get("plan", [])
    unknowns = state.get("unknowns", [])
    evidence = state.get("evidence_refs", [])
    verification = state.get("verification_step", "")
    constraints = state.get("constraints", [])
    goal = state.get("goal", "")

    # === INHIBITION RULES (architectural, not advisory) ===

    # Rule 1: unknowns exist → force ASK mode
    if unknowns:
        mode = "ask"
        blocked_reason = f"Cannot act with {len(unknowns)} unknown(s). Resolve first."
        warnings.append(f"UNKNOWNS: {', '.join(unknowns[:3])}")

    # Rule 2: edit/execute without plan → force PROPOSE
    if task_type in ("edit", "execute", "delegate") and not plan and mode == "act":
        mode = "propose"
        blocked_reason = "No plan defined for action task. Propose plan first."
        warnings.append("MISSING PLAN: define steps before executing")

    # Rule 3: edit/execute without verification → force PROPOSE
    if task_type in ("edit", "execute") and not verification and mode == "act":
        mode = "propose"
        blocked_reason = "No verification step. How will you confirm it worked?"
        warnings.append("MISSING VERIFICATION: define how to verify")

    # Rule 4: execute without evidence → force PROPOSE
    if task_type == "execute" and not evidence and mode == "act":
        mode = "propose"
        blocked_reason = "No evidence supporting this action."
        warnings.append("MISSING EVIDENCE: what supports this action?")

    # Rule 5: no goal → force ASK
    if not goal:
        mode = "ask"
        blocked_reason = "No goal defined."
        warnings.append("NO GOAL: what are you trying to achieve?")

    # === TRUST-BASED ADJUSTMENTS ===
    trust = _get_trust_score()
    if trust < 30 and mode == "act" and task_type in ("edit", "execute"):
        mode = "propose"
        blocked_reason = f"Trust score {trust:.0f}/100 — propose before acting."
        warnings.append(f"LOW TRUST ({trust:.0f}): extra verification required")

    # === INJECT RELEVANT RULES ===
    rules = _get_core_rules_for_task(task_type)

    return {
        "mode": mode,
        "tools_available": _tools_for_mode(mode),
        "warnings": warnings,
        "blocked_reason": blocked_reason,
        "injected_rules": rules,
        "trust_score": round(trust),
    }


def _tools_for_mode(mode: str) -> list[str]:
    """Define which tool categories are available per mode."""
    if mode == "ask":
        return ["read", "search", "ask_user"]
    elif mode == "propose":
        return ["read", "search", "analyze", "propose_plan"]
    else:  # act
        return ["all"]


def _parse_json_list(value) -> list:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def evaluate_cortex_state(state: dict) -> dict:
    """Return structured Cortex evaluation for internal callers."""
    result = _validate_state(state)
    result["check_id"] = f"CTX-{int(time.time())}-{secrets.randbelow(100000)}"
    result["expires_at_epoch"] = int(time.time()) + 1200
    return result


def _log_cortex_activation(goal: str, task_type: str, result: dict):
    try:
        conn = _get_db()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS cortex_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal TEXT,
                task_type TEXT,
                mode TEXT,
                warnings TEXT,
                trust_score INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            )"""
        )
        conn.execute(
            "INSERT INTO cortex_log (goal, task_type, mode, warnings, trust_score) VALUES (?, ?, ?, ?, ?)",
            (
                goal[:200],
                task_type,
                result["mode"],
                json.dumps(result["warnings"]),
                result["trust_score"],
            ),
        )
        conn.commit()
    except Exception:
        pass


def handle_cortex_check(
    goal: str,
    task_type: str = "answer",
    plan: str = "[]",
    known_facts: str = "[]",
    unknowns: str = "[]",
    constraints: str = "[]",
    evidence_refs: str = "[]",
    verification_step: str = "",
) -> str:
    """Cognitive Cortex pre-action check. Call BEFORE significant actions.

    Validates your reasoning state and determines if you can act, should propose,
    or need to ask for clarification first. Implements architectural inhibitory control.

    WHEN TO CALL:
    - Before editing files or running commands
    - Before delegating to subagents
    - When the task has multiple possible approaches
    - After a failed attempt (before retrying)
    - When user instruction seems to conflict with known facts

    DO NOT CALL for simple chat responses, greetings, or explanations.

    Args:
        goal: What you are trying to achieve (required)
        task_type: One of: answer, analyze, edit, execute, delegate
        plan: JSON array of planned steps (e.g. '["read file", "edit function", "test"]')
        known_facts: JSON array of facts you have (from user, memory, files)
        unknowns: JSON array of things you don't know yet but need
        constraints: JSON array of rules or limitations that apply
        evidence_refs: JSON array of evidence supporting your plan (learnings, user statements, file contents)
        verification_step: How you will verify the action worked

    Returns:
        Mode (ask/propose/act), available tools, warnings, and relevant Core Rules
    """
    state = {
        "goal": goal.strip() if goal else "",
        "task_type": task_type if task_type in ("answer", "analyze", "edit", "execute", "delegate") else "answer",
        "plan": _parse_json_list(plan),
        "known_facts": _parse_json_list(known_facts),
        "unknowns": _parse_json_list(unknowns),
        "constraints": _parse_json_list(constraints),
        "evidence_refs": _parse_json_list(evidence_refs),
        "verification_step": verification_step.strip() if verification_step else "",
    }

    result = evaluate_cortex_state(state)

    # Format response
    lines = [
        f"CORTEX CHECK — mode: {result['mode'].upper()}",
        f"Trust: {result['trust_score']}/100",
        f"Check ID: {result['check_id']}",
        f"Valid until epoch: {result['expires_at_epoch']}",
    ]

    if result["mode"] == "act":
        lines.append("CLEARED: You may proceed with the action.")
    elif result["mode"] == "propose":
        lines.append(f"PROPOSE ONLY: {result['blocked_reason']}")
        lines.append("Show the user your plan and get approval before executing.")
    elif result["mode"] == "ask":
        lines.append(f"ASK FIRST: {result['blocked_reason']}")
        lines.append("Gather the missing information before proceeding.")

    if result["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        for w in result["warnings"]:
            lines.append(f"  - {w}")

    if result["injected_rules"]:
        lines.append("")
        lines.append("Applicable Core Rules:")
        for r in result["injected_rules"]:
            lines.append(f"  - {r}")

    lines.append("")
    lines.append(f"Tools available: {', '.join(result['tools_available'])}")

    _log_cortex_activation(goal, task_type, result)

    return "\n".join(lines)


def handle_cortex_stats(days: int = 7) -> str:
    """View Cortex activation statistics — how often it activates, modes, warnings.

    Args:
        days: Period to analyze (default 7)
    """
    conn = _get_db()
    try:
        conn.execute("SELECT 1 FROM cortex_log LIMIT 1")
    except Exception:
        return "No Cortex data yet. The Cortex activates on significant actions."

    cutoff = f"datetime('now', '-{days} days')"

    total = conn.execute(f"SELECT COUNT(*) FROM cortex_log WHERE created_at >= {cutoff}").fetchone()[0]
    by_mode = conn.execute(
        f"SELECT mode, COUNT(*) as c FROM cortex_log WHERE created_at >= {cutoff} GROUP BY mode ORDER BY c DESC"
    ).fetchall()
    by_type = conn.execute(
        f"SELECT task_type, COUNT(*) as c FROM cortex_log WHERE created_at >= {cutoff} GROUP BY task_type ORDER BY c DESC"
    ).fetchall()

    lines = [
        f"CORTEX STATS — last {days} days",
        f"Total activations: {total}",
        "",
        "By mode:",
    ]
    for r in by_mode:
        pct = (r["c"] / total * 100) if total > 0 else 0
        lines.append(f"  {r['mode']}: {r['c']} ({pct:.0f}%)")

    lines.append("")
    lines.append("By task type:")
    for r in by_type:
        lines.append(f"  {r['task_type']}: {r['c']}")

    # Inhibition rate = % of activations that resulted in ask or propose (not act)
    inhibited = sum(r["c"] for r in by_mode if r["mode"] != "act")
    inhibition_rate = (inhibited / total * 100) if total > 0 else 0
    lines.append(f"\nInhibition rate: {inhibition_rate:.0f}% (target: 30-60%)")

    return "\n".join(lines)


TOOLS = [
    (handle_cortex_check, "nexo_cortex_check", "Cognitive pre-action check. Validates reasoning and determines if you can act, should propose, or need to ask first. Call before significant actions."),
    (handle_cortex_stats, "nexo_cortex_stats", "View Cortex activation statistics — modes, task types, inhibition rate."),
]
