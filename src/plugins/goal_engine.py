"""Goal Engine v1 — explicit optimization profiles that Cortex can resolve and trace."""

from __future__ import annotations

import json

from db import (
    get_db,
    ensure_default_goal_profiles,
    get_goal_profile,
    list_goal_profiles,
    resolve_goal_profile,
    upsert_goal_profile,
)


def handle_goal_profile_set(
    profile_id: str,
    weights: str = "{}",
    goal_labels: str = "[]",
    profile_name: str = "",
    description: str = "",
    scope_type: str = "default",
    scope_value: str = "",
    status: str = "active",
    source: str = "manual",
) -> str:
    """Create or update an explicit goal profile for Goal Engine v1."""
    try:
        profile = upsert_goal_profile(
            profile_id=profile_id,
            profile_name=profile_name,
            description=description,
            scope_type=scope_type,
            scope_value=scope_value,
            goal_labels=json.loads(goal_labels) if str(goal_labels).strip() else [],
            weights=json.loads(weights) if str(weights).strip() else {},
            status=status,
            source=source,
        )
    except json.JSONDecodeError as exc:
        return json.dumps({"ok": False, "error": f"Invalid JSON payload: {exc}"}, ensure_ascii=False, indent=2)
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2)

    return json.dumps({"ok": True, "profile": profile}, ensure_ascii=False, indent=2)


def handle_goal_profile_get(
    profile_id: str = "",
    area: str = "",
    task_type: str = "",
    goal_id: str = "",
) -> str:
    """Get one goal profile directly or resolve the active one for a context."""
    if (profile_id or "").strip():
        profile = get_goal_profile(profile_id)
        if not profile:
            return json.dumps({"ok": False, "error": f"Unknown goal profile: {profile_id}"}, ensure_ascii=False, indent=2)
        return json.dumps({"ok": True, "profile": profile}, ensure_ascii=False, indent=2)
    try:
        profile = resolve_goal_profile(
            area=area,
            task_type=task_type,
            goal_id=goal_id,
        )
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2)
    return json.dumps({"ok": True, "profile": profile}, ensure_ascii=False, indent=2)


def handle_goal_profile_list(scope_type: str = "", status: str = "active", limit: int = 20) -> str:
    """List Goal Engine profiles currently available in the runtime."""
    profiles = list_goal_profiles(scope_type=scope_type, status=status, limit=limit)
    return json.dumps(
        {
            "ok": True,
            "count": len(profiles),
            "profiles": profiles,
        },
        ensure_ascii=False,
        indent=2,
    )


def handle_goal_engine_status() -> str:
    """Report the telemetry readiness of Goal Engine v1 and Cortex v2 inputs."""
    ensure_default_goal_profiles()
    conn = get_db()
    outcomes_total = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
    outcomes_met = conn.execute("SELECT COUNT(*) FROM outcomes WHERE status = 'met'").fetchone()[0]
    outcomes_missed = conn.execute("SELECT COUNT(*) FROM outcomes WHERE status = 'missed'").fetchone()[0]
    evaluations_total = conn.execute("SELECT COUNT(*) FROM cortex_evaluations").fetchone()[0]
    overrides_total = conn.execute("SELECT COUNT(*) FROM cortex_evaluations WHERE selection_source = 'override'").fetchone()[0]
    linked_total = conn.execute(
        "SELECT COUNT(*) FROM cortex_evaluations WHERE linked_outcome_id IS NOT NULL"
    ).fetchone()[0]
    active_profiles = conn.execute("SELECT COUNT(*) FROM goal_profiles WHERE status = 'active'").fetchone()[0]
    active_goals = conn.execute("SELECT COUNT(*) FROM workflow_goals WHERE status = 'active'").fetchone()[0]

    readiness = {
        "has_outcome_history": bool(outcomes_total >= 3),
        "has_cortex_history": bool(evaluations_total >= 3),
        "has_linked_decisions": bool(linked_total >= 1),
        "has_active_profiles": bool(active_profiles >= 1),
        "has_active_goals": bool(active_goals >= 1),
    }
    next_gap = []
    if not readiness["has_outcome_history"]:
        next_gap.append("Necesita acumular outcomes reales antes de optimizar por historico.")
    if not readiness["has_cortex_history"]:
        next_gap.append("Necesita acumular evaluaciones reales del cortex antes de medir Decision Cortex v2.")
    if not readiness["has_linked_decisions"]:
        next_gap.append("Necesita decisiones de impacto enlazadas a outcome para cerrar el loop.")

    return json.dumps(
        {
            "ok": True,
            "telemetry": {
                "outcomes_total": outcomes_total,
                "outcomes_met": outcomes_met,
                "outcomes_missed": outcomes_missed,
                "cortex_evaluations_total": evaluations_total,
                "cortex_overrides_total": overrides_total,
                "cortex_linked_outcomes_total": linked_total,
                "active_goal_profiles": active_profiles,
                "active_workflow_goals": active_goals,
            },
            "readiness": readiness,
            "next_gaps": next_gap,
        },
        ensure_ascii=False,
        indent=2,
    )


TOOLS = [
    (handle_goal_profile_set, "nexo_goal_profile_set", "Create or update a Goal Engine profile with explicit weights and labels."),
    (handle_goal_profile_get, "nexo_goal_profile_get", "Get or resolve the active Goal Engine profile for a context, area, task type, or workflow goal."),
    (handle_goal_profile_list, "nexo_goal_profile_list", "List Goal Engine profiles currently available in the runtime."),
    (handle_goal_engine_status, "nexo_goal_engine_status", "Report Goal Engine telemetry readiness: outcomes, cortex history, linked decisions, and active profiles."),
]
