"""Evolution plugin — NEXO self-improvement tools for interactive sessions."""

from db import get_latest_metrics, get_evolution_history, update_evolution_log_status, get_db


def handle_evolution_status() -> str:
    """Show current NEXO dimension scores and recent trend."""
    metrics = get_latest_metrics()
    if not metrics:
        return "Sin métricas de evolución registradas."

    BARS = {
        "episodic_memory": "Memoria Episódica",
        "autonomy": "Autonomía",
        "proactivity": "Proactividad",
        "self_improvement": "Auto-mejora",
        "agi": "AGI",
    }

    lines = ["NEXO EVOLUTION STATUS:"]
    for key, label in BARS.items():
        m = metrics.get(key)
        if m:
            score = m["score"]
            delta = m["delta"]
            bar = "█" * (score // 5) + "░" * (20 - score // 5)
            delta_str = f" (+{delta})" if delta > 0 else f" ({delta})" if delta < 0 else " (=)"
            lines.append(f"  {label:<20} {bar} {score}%{delta_str}")
    return "\n".join(lines)


def handle_evolution_history(limit: int = 10) -> str:
    """Show past evolution cycles and their outcomes.

    Args:
        limit: Number of entries to return (default 10)
    """
    history = get_evolution_history(limit)
    if not history:
        return "Sin historial de evolución."

    lines = [f"EVOLUTION HISTORY ({len(history)} entries):"]
    for h in history:
        status_icon = {"applied": "✓", "rolled_back": "✗", "proposed": "?",
                       "accepted": "✓✓", "rejected": "✗✗"}.get(h["status"], "·")
        lines.append(f"  {status_icon} #{h['id']} [{h['classification']}] {h['dimension']}")
        lines.append(f"    {h['proposal'][:100]}")
        if h.get("test_result"):
            lines.append(f"    Test: {h['test_result'][:80]}")
        if h.get("impact"):
            lines.append(f"    Impact: {h['impact']:+d}")
    return "\n".join(lines)


def handle_evolution_propose() -> str:
    """Manually trigger an evolution analysis outside the weekly schedule.
    This sets a flag that the Cortex wrapper reads on the next cycle.
    """
    import json
    from pathlib import Path
    obj_file = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))) / "cortex" / "evolution-objective.json"
    if not obj_file.exists():
        return "ERROR: evolution-objective.json not found"
    try:
        obj = json.loads(obj_file.read_text())
        if not obj.get("evolution_enabled", True):
            return f"Evolution is DISABLED: {obj.get('disabled_reason', 'unknown')}"
        obj["force_next_cycle"] = True
        obj_file.write_text(json.dumps(obj, indent=2, ensure_ascii=False))
        return "Evolution cycle queued. Will run on next Cortex cycle (within ~10 min)."
    except Exception as e:
        return f"ERROR: {e}"


def handle_evolution_approve(log_id: int, notes: str = '') -> str:
    """Approve a pending Evolution proposal.

    Args:
        log_id: Evolution log entry ID to approve
        notes: Optional notes from the user
    """
    update_evolution_log_status(log_id, "accepted",
                                test_result=f"Approved by the user. {notes}".strip())
    return f"Proposal #{log_id} APPROVED. Will be applied in next Evolution cycle."


def handle_evolution_reject(log_id: int, reason: str = '') -> str:
    """Reject a pending Evolution proposal.

    Args:
        log_id: Evolution log entry ID to reject
        reason: Why this proposal was rejected
    """
    update_evolution_log_status(log_id, "rejected",
                                test_result=f"Rejected: {reason}" if reason else "Rejected by the user")
    return f"Proposal #{log_id} REJECTED. Reason: {reason or 'no reason given'}"


TOOLS = [
    (handle_evolution_status, "nexo_evolution_status",
     "Show current NEXO dimension scores (episodic memory, autonomy, proactivity, self-improvement, AGI)"),
    (handle_evolution_history, "nexo_evolution_history",
     "Show past evolution cycles, proposals, and their outcomes"),
    (handle_evolution_propose, "nexo_evolution_propose",
     "Manually trigger an evolution analysis outside weekly schedule"),
    (handle_evolution_approve, "nexo_evolution_approve",
     "Approve a pending Evolution proposal (the user only)"),
    (handle_evolution_reject, "nexo_evolution_reject",
     "Reject a pending Evolution proposal with reason"),
]
