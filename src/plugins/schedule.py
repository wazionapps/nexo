"""NEXO Schedule — Cron execution history and status tools."""

from db import cron_runs_recent, cron_runs_summary


def handle_schedule_status(hours: int = 24, cron_id: str = '') -> str:
    """Show cron execution status — what ran, what failed, durations.

    Args:
        hours: How far back to look (default 24h).
        cron_id: Filter to a specific cron (optional). E.g. 'deep-sleep', 'immune'.
    """
    if cron_id:
        runs = cron_runs_recent(hours, cron_id)
        if not runs:
            return f"No runs for '{cron_id}' in the last {hours}h."
        lines = [f"CRON RUNS — {cron_id} (last {hours}h): {len(runs)} executions"]
        for r in runs:
            status = "✅" if r.get("exit_code") == 0 else "❌"
            dur = f"{r['duration_secs']:.0f}s" if r.get("duration_secs") else "running"
            summary = f" — {r['summary'][:100]}" if r.get("summary") else ""
            error = f" ERROR: {r['error'][:100]}" if r.get("error") else ""
            lines.append(f"  {status} {r['started_at']} ({dur}){summary}{error}")
        return "\n".join(lines)

    # Summary view — one line per cron
    summary = cron_runs_summary(hours)
    if not summary:
        return f"No cron executions recorded in the last {hours}h."

    lines = [f"CRON STATUS (last {hours}h):"]
    for s in summary:
        status = "✅" if s.get("last_exit_code") == 0 else "❌"
        rate = f"{s['succeeded']}/{s['total_runs']}"
        dur = f"{s['avg_duration']:.0f}s avg" if s.get("avg_duration") else ""
        summary_txt = f" — {s['last_summary'][:80]}" if s.get("last_summary") else ""
        lines.append(f"  {status} {s['cron_id']}: {rate} OK, {dur}{summary_txt}")

    return "\n".join(lines)


TOOLS = [
    (handle_schedule_status, "nexo_schedule_status",
     "Show cron execution status: what ran overnight, what failed, durations. "
     "Use at startup to give the user a quick health overview of autonomous processes."),
]
