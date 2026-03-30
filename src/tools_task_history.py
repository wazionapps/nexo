"""Task history tools: log executions, list history, report overdue tasks."""

import datetime
from db import log_task, list_task_history, set_task_frequency, get_overdue_tasks, get_task_frequencies


def _epoch_to_date(epoch: float) -> str:
    """Format epoch timestamp as readable date string."""
    return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def handle_task_log(task_num: str, task_name: str, notes: str = '', reasoning: str = '') -> str:
    """Record a task execution in the history log.

    Args:
        task_num: Task number identifier
        task_name: Task name
        notes: Execution notes
        reasoning: WHY this task was executed now — what triggered it, what data informed it
    """
    result = log_task(task_num, task_name, notes, reasoning)
    if "error" in result:
        return f"ERROR: {result['error']}"
    return f"Task {task_num} ({task_name}) logged."


def handle_task_list(task_num: str = '', days: int = 30) -> str:
    """Show execution history for all tasks or a specific task number."""
    results = list_task_history(task_num if task_num else None, days)
    if not results:
        scope = f"task {task_num}" if task_num else "any task"
        return f"HISTORY: No executions of {scope} in recent days."
    lines = [f"HISTORY ({len(results)} executions, {days}d):"]
    for r in results:
        date_str = _epoch_to_date(r["executed_at"])
        notes_str = f": {r['notes']}" if r.get("notes") else ""
        lines.append(f"  {date_str} — Task {r['task_num']} ({r['task_name']}){notes_str}")
    return "\n".join(lines)


def handle_task_frequency() -> str:
    """Report tasks that are overdue based on their configured frequency."""
    overdue = get_overdue_tasks()
    if not overdue:
        return "All tasks up to date."
    lines = ["TAREAS VENCIDAS:"]
    for t in overdue:
        days_since = t.get("days_since_last")
        if days_since is not None:
            since_str = f"last run {days_since:.1f} days ago"
        else:
            since_str = "never executed"
        lines.append(
            f"  Task {t['task_num']} ({t['task_name']}): "
            f"{since_str}, frequency every {t['frequency_days']} days"
        )
    return "\n".join(lines)
