"""NEXO DB — Task History and Frequencies."""
import time


def _get_db():
    from db import get_db
    return get_db()


def _now_epoch():
    return time.time()


def log_task(task_num: str, task_name: str, notes: str = '', reasoning: str = '') -> dict:
    """Log a task execution with optional reasoning."""
    conn = _get_db()
    now = _now_epoch()
    cursor = conn.execute(
        "INSERT INTO task_history (task_num, task_name, executed_at, notes, reasoning) "
        "VALUES (?, ?, ?, ?, ?)",
        (task_num, task_name, now, notes, reasoning)
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM task_history WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return dict(row)


def list_task_history(task_num: str = None, days: int = 30) -> list[dict]:
    """List task execution history, optionally filtered by task_num."""
    conn = _get_db()
    cutoff = _now_epoch() - (days * 86400)
    if task_num:
        rows = conn.execute(
            "SELECT * FROM task_history WHERE task_num = ? AND executed_at >= ? "
            "ORDER BY executed_at DESC",
            (task_num, cutoff)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM task_history WHERE executed_at >= ? "
            "ORDER BY executed_at DESC",
            (cutoff,)
        ).fetchall()
    return [dict(r) for r in rows]


def set_task_frequency(task_num: str, task_name: str,
                       frequency_days: int, description: str = '') -> dict:
    """Set or update the expected frequency for a task."""
    conn = _get_db()
    conn.execute(
        "INSERT OR REPLACE INTO task_frequencies (task_num, task_name, frequency_days, description) "
        "VALUES (?, ?, ?, ?)",
        (task_num, task_name, frequency_days, description)
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM task_frequencies WHERE task_num = ?", (task_num,)
    ).fetchone()
    return dict(row)


def get_overdue_tasks() -> list[dict]:
    """Get tasks where last execution exceeds the configured frequency."""
    conn = _get_db()
    freqs = conn.execute("SELECT * FROM task_frequencies").fetchall()
    now = _now_epoch()
    overdue = []
    for f in freqs:
        last = conn.execute(
            "SELECT MAX(executed_at) as last_exec FROM task_history WHERE task_num = ?",
            (f["task_num"],)
        ).fetchone()
        last_exec = last["last_exec"] if last and last["last_exec"] else None
        threshold = f["frequency_days"] * 86400
        if last_exec is None or (now - last_exec) > threshold:
            days_ago = round((now - last_exec) / 86400, 1) if last_exec else None
            overdue.append({
                "task_num": f["task_num"],
                "task_name": f["task_name"],
                "frequency_days": f["frequency_days"],
                "last_executed": last_exec,
                "days_since_last": days_ago,
                "description": f["description"]
            })
    return overdue


def get_task_frequencies() -> list[dict]:
    """Get all configured task frequencies."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM task_frequencies ORDER BY task_num ASC"
    ).fetchall()
    return [dict(r) for r in rows]
