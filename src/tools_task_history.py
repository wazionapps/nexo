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
    return f"Tarea {task_num} ({task_name}) registrada."


def handle_task_list(task_num: str = '', days: int = 30) -> str:
    """Show execution history for all tasks or a specific task number."""
    results = list_task_history(task_num if task_num else None, days)
    if not results:
        scope = f"tarea {task_num}" if task_num else "ninguna tarea"
        return f"HISTORIAL: Sin ejecuciones de {scope} en los últimos {days} días."
    lines = [f"HISTORIAL ({len(results)} ejecuciones, {days}d):"]
    for r in results:
        date_str = _epoch_to_date(r["executed_at"])
        notes_str = f": {r['notes']}" if r.get("notes") else ""
        lines.append(f"  {date_str} — Tarea {r['task_num']} ({r['task_name']}){notes_str}")
    return "\n".join(lines)


def handle_task_frequency() -> str:
    """Report tasks that are overdue based on their configured frequency."""
    overdue = get_overdue_tasks()
    if not overdue:
        return "Todas las tareas al día."
    lines = ["TAREAS VENCIDAS:"]
    for t in overdue:
        days_since = t.get("days_since_last")
        if days_since is not None:
            since_str = f"última hace {days_since:.1f} días"
        else:
            since_str = "nunca ejecutada"
        lines.append(
            f"  Tarea {t['task_num']} ({t['task_name']}): "
            f"{since_str}, frecuencia cada {t['frequency_days']} días"
        )
    return "\n".join(lines)
