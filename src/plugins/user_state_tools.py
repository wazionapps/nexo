"""User-state modeling tools."""

import user_state_model


def handle_user_state(days: int = 7, persist: bool = True) -> str:
    snapshot = user_state_model.build_user_state(days=days, persist=persist)
    lines = [
        f"USER STATE: {snapshot['state_label'].upper()} (confidence={snapshot['confidence']})",
        f"Trust: {snapshot['trust_score']}/100",
        f"Guidance: {snapshot['guidance']}",
        "Signals:",
    ]
    for key, value in snapshot["signals"].items():
        lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def handle_user_state_history(limit: int = 20) -> str:
    items = user_state_model.list_user_state_snapshots(limit=limit)
    if not items:
        return "No user-state snapshots yet."
    lines = [f"USER STATE HISTORY — {len(items)} snapshot(s):", ""]
    for item in items:
        lines.append(f"  {item['created_at']} — {item['state_label']} ({item['confidence']})")
    return "\n".join(lines)


def handle_user_state_stats(days: int = 30) -> str:
    stats = user_state_model.user_state_stats(days=days)
    return (
        f"USER STATE STATS — {days}d\n"
        f"  snapshots: {stats['snapshots']}\n"
        f"  backend: {stats['backend']}\n"
        f"  by_state: {stats['by_state']}"
    )


TOOLS = [
    (handle_user_state, "nexo_user_state", "Compute a richer, inspectable user-state snapshot from trust, corrections, sentiment, and hot context."),
    (handle_user_state_history, "nexo_user_state_history", "List recent user-state snapshots."),
    (handle_user_state_stats, "nexo_user_state_stats", "Aggregate user-state snapshots by label."),
]
