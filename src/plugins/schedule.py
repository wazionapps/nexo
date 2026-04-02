"""NEXO Schedule — Cron execution history, status, and management tools."""

import json
import os
import platform
import subprocess
from pathlib import Path

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


def handle_schedule_add(cron_id: str, script: str, schedule: str = '',
                        interval_seconds: int = 0, description: str = '',
                        script_type: str = 'python') -> str:
    """Add a new personal cron job. Generates and installs the LaunchAgent (macOS) or systemd timer (Linux).

    Args:
        cron_id: Unique ID for this cron (e.g. 'my-backup', 'report-daily'). Must be lowercase with hyphens.
        script: Path to the script to run (absolute or relative to NEXO_HOME/scripts/).
        schedule: Time-based schedule as 'HH:MM' (daily) or 'HH:MM:weekday' (e.g. '08:00:1' for Monday 8AM). Mutually exclusive with interval_seconds.
        interval_seconds: Run every N seconds (e.g. 300 for every 5 min). Mutually exclusive with schedule.
        description: What this cron does (for logs and status).
        script_type: 'python' (default) or 'shell'.
    """
    if not cron_id or not script:
        return "ERROR: cron_id and script are required."
    if not schedule and not interval_seconds:
        return "ERROR: either schedule (e.g. '08:00') or interval_seconds (e.g. 300) is required."

    nexo_home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
    script_path = Path(script)
    if not script_path.is_absolute():
        script_path = nexo_home / "scripts" / script
    if not script_path.exists():
        return f"ERROR: script not found: {script_path}"

    wrapper_path = nexo_home / "scripts" / "nexo-cron-wrapper.sh"
    if not wrapper_path.exists():
        return f"ERROR: wrapper not found at {wrapper_path}. Run crons/sync.py first."

    system = platform.system()

    if system == "Darwin":
        return _add_launchagent(cron_id, str(script_path), str(wrapper_path),
                                schedule, interval_seconds, description, script_type, nexo_home)
    elif system == "Linux":
        return _add_systemd_timer(cron_id, str(script_path), str(wrapper_path),
                                  schedule, interval_seconds, description, script_type, nexo_home)
    else:
        return f"ERROR: unsupported platform: {system}"


def _add_launchagent(cron_id, script_path, wrapper_path, schedule, interval_seconds,
                     description, script_type, nexo_home):
    """Create and load a macOS LaunchAgent."""
    import plistlib

    label = f"com.nexo.{cron_id}"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"

    if plist_path.exists():
        return f"ERROR: cron '{cron_id}' already exists at {plist_path}. Use a different ID or remove it first."

    python_bin = "/opt/homebrew/bin/python3"
    for p in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]:
        if Path(p).exists():
            python_bin = p
            break

    if script_type == "shell":
        program_args = ["/bin/bash", wrapper_path, cron_id, "/bin/bash", script_path]
    else:
        program_args = ["/bin/bash", wrapper_path, cron_id, python_bin, script_path]

    plist = {
        "Label": label,
        "ProgramArguments": program_args,
        "StandardOutPath": str(nexo_home / "logs" / f"{cron_id}-stdout.log"),
        "StandardErrorPath": str(nexo_home / "logs" / f"{cron_id}-stderr.log"),
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            "NEXO_HOME": str(nexo_home),
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + str(Path.home() / ".local/bin"),
        },
    }

    if interval_seconds:
        plist["StartInterval"] = interval_seconds
    elif schedule:
        parts = schedule.split(":")
        cal = {"Hour": int(parts[0]), "Minute": int(parts[1])}
        if len(parts) > 2:
            cal["Weekday"] = int(parts[2])
        plist["StartCalendarInterval"] = cal

    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)

    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)], capture_output=True)

    return f"Cron '{cron_id}' installed at {plist_path} and loaded.{' Schedule: ' + schedule if schedule else f' Interval: {interval_seconds}s'}"


def _add_systemd_timer(cron_id, script_path, wrapper_path, schedule, interval_seconds,
                       description, script_type, nexo_home):
    """Create and enable a systemd user timer (Linux)."""
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)

    python_bin = "/usr/bin/python3"
    for p in ["/usr/bin/python3", "/usr/local/bin/python3"]:
        if Path(p).exists():
            python_bin = p
            break

    if script_type == "shell":
        exec_cmd = f"/bin/bash {wrapper_path} {cron_id} /bin/bash {script_path}"
    else:
        exec_cmd = f"/bin/bash {wrapper_path} {cron_id} {python_bin} {script_path}"

    # Service unit
    service_content = f"""[Unit]
Description=NEXO: {description or cron_id}

[Service]
Type=oneshot
ExecStart={exec_cmd}
Environment=NEXO_HOME={nexo_home}
Environment=HOME={Path.home()}
"""
    service_path = unit_dir / f"nexo-{cron_id}.service"
    service_path.write_text(service_content)

    # Timer unit
    if interval_seconds:
        timer_spec = f"OnUnitActiveSec={interval_seconds}s\nOnBootSec=60s"
    elif schedule:
        parts = schedule.split(":")
        hour, minute = int(parts[0]), int(parts[1])
        if len(parts) > 2:
            days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            day = days[int(parts[2])]
            timer_spec = f"OnCalendar={day} *-*-* {hour:02d}:{minute:02d}:00"
        else:
            timer_spec = f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00"
    else:
        return "ERROR: no schedule or interval"

    timer_content = f"""[Unit]
Description=NEXO timer: {description or cron_id}

[Timer]
{timer_spec}
Persistent=true

[Install]
WantedBy=timers.target
"""
    timer_path = unit_dir / f"nexo-{cron_id}.timer"
    timer_path.write_text(timer_content)

    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", f"nexo-{cron_id}.timer"], capture_output=True)

    return f"Cron '{cron_id}' installed as systemd timer and enabled. Service: {service_path}, Timer: {timer_path}"


TOOLS = [
    (handle_schedule_status, "nexo_schedule_status",
     "Show cron execution status: what ran overnight, what failed, durations. "
     "Use at startup to give the user a quick health overview of autonomous processes."),

    (handle_schedule_add, "nexo_schedule_add",
     "Add a new personal cron job. Creates LaunchAgent (macOS) or systemd timer (Linux) "
     "automatically, wrapped with execution tracking."),
]
