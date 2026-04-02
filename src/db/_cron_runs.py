"""NEXO DB — Cron execution history."""
from db._core import get_db


def cron_run_start(cron_id: str) -> int:
    """Record a cron starting. Returns the run ID."""
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO cron_runs (cron_id) VALUES (?)", (cron_id,)
    )
    conn.commit()
    return cursor.lastrowid


def cron_run_end(run_id: int, exit_code: int, summary: str = '', error: str = ''):
    """Record a cron finishing."""
    conn = get_db()
    conn.execute(
        """UPDATE cron_runs
           SET ended_at = datetime('now'),
               exit_code = ?,
               summary = ?,
               error = ?,
               duration_secs = ROUND((julianday(datetime('now')) - julianday(started_at)) * 86400, 1)
           WHERE id = ?""",
        (exit_code, summary[:500], error[:500], run_id)
    )
    conn.commit()


def cron_runs_recent(hours: int = 24, cron_id: str = '') -> list[dict]:
    """Get recent cron executions."""
    conn = get_db()
    if cron_id:
        rows = conn.execute(
            """SELECT * FROM cron_runs
               WHERE cron_id = ? AND started_at >= datetime('now', ?)
               ORDER BY started_at DESC""",
            (cron_id, f"-{hours} hours")
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM cron_runs
               WHERE started_at >= datetime('now', ?)
               ORDER BY started_at DESC""",
            (f"-{hours} hours",)
        ).fetchall()
    return [dict(r) for r in rows]


def cron_runs_summary(hours: int = 24) -> list[dict]:
    """Get summary per cron: last run, success rate, avg duration."""
    conn = get_db()
    rows = conn.execute(
        """SELECT
               cron_id,
               COUNT(*) as total_runs,
               SUM(CASE WHEN exit_code = 0 THEN 1 ELSE 0 END) as succeeded,
               SUM(CASE WHEN exit_code != 0 OR exit_code IS NULL THEN 1 ELSE 0 END) as failed,
               ROUND(AVG(duration_secs), 1) as avg_duration,
               MAX(started_at) as last_run,
               (SELECT exit_code FROM cron_runs cr2
                WHERE cr2.cron_id = cron_runs.cron_id
                ORDER BY started_at DESC LIMIT 1) as last_exit_code,
               (SELECT summary FROM cron_runs cr3
                WHERE cr3.cron_id = cron_runs.cron_id AND cr3.summary != ''
                ORDER BY started_at DESC LIMIT 1) as last_summary
           FROM cron_runs
           WHERE started_at >= datetime('now', ?)
           GROUP BY cron_id
           ORDER BY last_run DESC""",
        (f"-{hours} hours",)
    ).fetchall()
    return [dict(r) for r in rows]
