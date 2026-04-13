"""Evolution plugin — NEXO self-improvement tools for interactive sessions."""

import json
import os
from pathlib import Path
from db import get_latest_metrics, get_evolution_history, update_evolution_log_status, get_db


CANONICAL_DIMENSIONS = {
    "episodic_memory": "Episodic Memory",
    "autonomy": "Autonomy",
    "proactivity": "Proactivity",
    "self_improvement": "Self-improvement",
    "agi": "AGI",
}


def _resolve_objective_file() -> Path:
    nexo_home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
    for candidate in (
        nexo_home / "brain" / "evolution-objective.json",
        nexo_home / "cortex" / "evolution-objective.json",
    ):
        if candidate.exists():
            return candidate
    return nexo_home / "brain" / "evolution-objective.json"


def _load_objective() -> dict:
    try:
        raw = json.loads(_resolve_objective_file().read_text())
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def handle_evolution_status() -> str:
    """Show current NEXO dimension scores and recent trend."""
    metrics = get_latest_metrics()
    objective = _load_objective()
    objective_dims = objective.get("dimensions", {}) if isinstance(objective.get("dimensions"), dict) else {}

    from user_context import get_context
    lines = [f"{get_context().assistant_name} EVOLUTION STATUS:"]
    has_output = False
    for key, label in CANONICAL_DIMENSIONS.items():
        m = metrics.get(key)
        if m:
            score = m["score"]
            delta = m["delta"]
            bar = "█" * (score // 5) + "░" * (20 - score // 5)
            delta_str = f" (+{delta})" if delta > 0 else f" ({delta})" if delta < 0 else " (=)"
            target = ""
            if isinstance(objective_dims.get(key), dict) and objective_dims[key].get("target") is not None:
                target = f" / target {int(objective_dims[key].get('target', 0) or 0)}%"
            lines.append(f"  {label:<20} {bar} {score}%{delta_str}{target}")
            has_output = True
            continue

        objective_entry = objective_dims.get(key)
        if isinstance(objective_entry, dict):
            score = int(objective_entry.get("current", 0) or 0)
            target = int(objective_entry.get("target", 0) or 0)
            bar = "█" * (score // 5) + "░" * (20 - score // 5)
            lines.append(f"  {label:<20} {bar} {score}% (objective fallback, target {target}%)")
            has_output = True

    if not has_output:
        return "No evolution metrics recorded."

    if not metrics:
        lines.append("  Note: no persisted evolution_metrics rows yet; showing objective fallback.")
    if objective.get("last_evolution"):
        lines.append(f"  Last evolution: {objective['last_evolution']}")
    return "\n".join(lines)


def handle_evolution_history(limit: int = 10) -> str:
    """Show past evolution cycles and their outcomes.

    Args:
        limit: Number of entries to return (default 10)
    """
    history = get_evolution_history(limit)
    if not history:
        return "No evolution history."

    lines = [f"EVOLUTION HISTORY ({len(history)} entries):"]
    for h in history:
        status_icon = {
            "applied": "✓",
            "rolled_back": "↺",
            "blocked": "⛔",
            "proposed": "?",
            "pending_review": "…",
            "accepted": "✓✓",
            "rejected": "✗✗",
        }.get(h["status"], "·")
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
    nexo_home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
    # Check brain/ (canonical) first, fall back to cortex/ (legacy)
    obj_file = nexo_home / "brain" / "evolution-objective.json"
    if not obj_file.exists():
        obj_file = nexo_home / "cortex" / "evolution-objective.json"
    if not obj_file.exists():
        return "ERROR: evolution-objective.json not found. Run the installer or create one in ~/.nexo/brain/"
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
        notes: Optional notes from user
    """
    update_evolution_log_status(log_id, "accepted",
                                test_result=f"Approved by user. {notes}".strip())
    return f"Proposal #{log_id} APPROVED. Will be applied in next Evolution cycle."


def handle_evolution_reject(log_id: int, reason: str = '') -> str:
    """Reject a pending Evolution proposal.

    Args:
        log_id: Evolution log entry ID to reject
        reason: Why this proposal was rejected
    """
    update_evolution_log_status(log_id, "rejected",
                                test_result=f"Rejected: {reason}" if reason else "Rejected by user")
    return f"Proposal #{log_id} REJECTED. Reason: {reason or 'no reason given'}"


TOOLS = [
    (handle_evolution_status, "nexo_evolution_status",
     "Show current NEXO dimension scores (episodic memory, autonomy, proactivity, self-improvement, AGI)"),
    (handle_evolution_history, "nexo_evolution_history",
     "Show past evolution cycles, proposals, and their outcomes"),
    (handle_evolution_propose, "nexo_evolution_propose",
     "Manually trigger an evolution analysis outside weekly schedule"),
    (handle_evolution_approve, "nexo_evolution_approve",
     "Approve a pending Evolution proposal (user only)"),
    (handle_evolution_reject, "nexo_evolution_reject",
     "Reject a pending Evolution proposal with reason"),
]
