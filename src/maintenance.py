"""Opportunistic maintenance — run overdue tasks on MCP startup."""

import time
from datetime import datetime, timezone
from db import get_db


def check_and_run_overdue():
    conn = get_db()
    rows = conn.execute("SELECT task_name, interval_hours, last_run_at FROM maintenance_schedule").fetchall()
    ran = []
    for row in rows:
        task = row["task_name"]
        interval = row["interval_hours"]
        last_run = row["last_run_at"]
        if last_run:
            try:
                last_dt = datetime.strptime(last_run, "%Y-%m-%dT%H:%M:%S")
                hours_since = (datetime.now(timezone.utc).replace(tzinfo=None) - last_dt).total_seconds() / 3600
                if hours_since < interval:
                    continue
            except (ValueError, TypeError):
                pass
        start = time.time()
        try:
            _run_task(task)
            duration_ms = int((time.time() - start) * 1000)
            conn.execute(
                "UPDATE maintenance_schedule SET last_run_at = ?, last_duration_ms = ?, "
                "run_count = run_count + 1 WHERE task_name = ?",
                (datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S"), duration_ms, task))
            conn.commit()
            ran.append({"task": task, "duration_ms": duration_ms})
        except Exception as e:
            ran.append({"task": task, "error": str(e)})
    return ran


def _run_task(task_name: str):
    import cognitive
    if task_name == "cognitive_decay":
        cognitive.apply_decay()
        cognitive.promote_stm_to_ltm()
        cognitive.gc_stm()
    elif task_name == "somatic_decay":
        cognitive.somatic_nightly_decay()
    elif task_name == "somatic_projection":
        cognitive.somatic_project_events()
    elif task_name == "weight_learning":
        try:
            import sys, os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "plugins"))
            from adaptive_mode import learn_weights, prune_adaptive_log
            learn_weights()
            prune_adaptive_log()
        except Exception:
            pass
    elif task_name == "drive_decay":
        from db import decay_drive_signals
        decay_drive_signals()
    elif task_name == "graph_maintenance":
        pass  # Future: orphan cleanup, consolidation
