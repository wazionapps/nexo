"""NEXO Schedule — Cron execution history, status, and management tools."""

from datetime import datetime, timezone
import json
import os
import platform
import re
import subprocess
from pathlib import Path

from db import (
    init_db, cron_runs_recent, cron_runs_summary,
    upsert_personal_script, register_personal_script_schedule,
    get_personal_script_schedule,
)
from script_registry import (
    PERSONAL_SCHEDULE_MANAGED_ENV,
    parse_inline_metadata,
    classify_runtime,
    get_declared_schedule,
)


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
        schedule_meta = get_personal_script_schedule(cron_id) or {}
        lines = [f"CRON RUNS — {cron_id} (last {hours}h): {len(runs)} executions"]
        for r in runs:
            status, detail = _run_status_marker(
                r.get("exit_code"),
                r.get("summary"),
                schedule_meta=schedule_meta,
                started_at=r.get("started_at"),
                ended_at=r.get("ended_at"),
            )
            if schedule_meta.get("schedule_type") == "keep_alive" and r.get("exit_code") is None:
                dur = "daemon active"
            else:
                dur = f"{r['duration_secs']:.0f}s" if r.get("duration_secs") else "running"
            summary = f" — {r['summary'][:100]}" if r.get("summary") else ""
            error = f" ERROR: {r['error'][:100]}" if r.get("error") else ""
            suffix = f" [{detail}]" if detail else ""
            lines.append(f"  {status} {r['started_at']} ({dur}){summary}{error}{suffix}")
        return "\n".join(lines)

    # Summary view — one line per cron
    summary = cron_runs_summary(hours)
    if not summary:
        return f"No cron executions recorded in the last {hours}h."

    lines = [f"CRON STATUS (last {hours}h):"]
    for s in summary:
        schedule_meta = get_personal_script_schedule(s["cron_id"]) or {}
        status, detail = _run_status_marker(
            s.get("last_exit_code"),
            s.get("last_summary"),
            schedule_meta=schedule_meta,
            started_at=s.get("last_run"),
            ended_at=s.get("last_ended_at"),
        )
        if schedule_meta.get("schedule_type") == "keep_alive" and s.get("last_exit_code") is None:
            rate = "daemon active"
        else:
            completed_runs = s.get("completed_runs")
            if completed_runs is None:
                completed_runs = s["total_runs"]
            rate = f"{s['succeeded']}/{completed_runs}"
        dur = f"{s['avg_duration']:.0f}s avg" if s.get("avg_duration") else ""
        summary_txt = f" — {s['last_summary'][:80]}" if s.get("last_summary") else ""
        suffix = f" [{detail}]" if detail else ""
        lines.append(f"  {status} {s['cron_id']}: {rate}, {dur}{summary_txt}{suffix}")

    return "\n".join(lines)


def _summary_has_warning(summary: str = "") -> bool:
    lowered = str(summary or "").strip().lower()
    if not lowered:
        return False
    if re.search(r"\b[1-9]\d*\s+(errors?|warnings?)\b", lowered):
        return True
    if "no warning" in lowered or "without warnings" in lowered:
        return False
    warning_tokens = (
        "warning",
        "warnings",
        "warn:",
        "degraded",
        "partial failure",
        "issues detected",
        "completed with findings",
        "findings detected",
    )
    return any(token in lowered for token in warning_tokens)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_db_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_age(seconds: float) -> str:
    if seconds < 60:
        return "<1m"
    if seconds < 3600:
        return f"{int(round(seconds / 60))}m"
    return f"{seconds / 3600:.1f}h"


def _open_run_stale_after(schedule_meta: dict | None = None) -> int:
    schedule_meta = schedule_meta or {}
    if schedule_meta.get("schedule_type") == "interval":
        try:
            interval = int(schedule_meta.get("interval_seconds") or schedule_meta.get("schedule_value") or 0)
        except (TypeError, ValueError):
            interval = 0
        if interval > 0:
            return max(300, interval * 2)
    if schedule_meta.get("schedule_type") == "calendar":
        return 3600
    return 1800


def _open_run_marker(started_at: str | None, *, schedule_meta: dict | None = None) -> tuple[str, str]:
    started = _parse_db_timestamp(started_at)
    if started is None:
        return "⚠", "open run"
    age_secs = max(0.0, (_now_utc() - started).total_seconds())
    if age_secs <= _open_run_stale_after(schedule_meta):
        return "⏳", f"running {_format_age(age_secs)}"
    return "⚠", f"open run {_format_age(age_secs)}"


def _run_status_marker(
    exit_code,
    summary: str = "",
    *,
    schedule_meta: dict | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
) -> tuple[str, str]:
    schedule_meta = schedule_meta or {}
    if schedule_meta.get("schedule_type") == "keep_alive" and exit_code is None:
        return "🟢", "keep_alive daemon active"
    if exit_code is None:
        return _open_run_marker(started_at, schedule_meta=schedule_meta)
    if exit_code == 0 and _summary_has_warning(summary):
        return "⚠", "exit 0 with warnings"
    if exit_code == 0:
        return "✅", "exit 0"
    return "❌", f"exit {exit_code}"


def handle_schedule_add(cron_id: str, script: str, schedule: str = '',
                        interval_seconds: int = 0, description: str = '',
                        script_type: str = 'auto', keep_alive: bool = False) -> str:
    """Add a new personal cron job. Generates and installs the LaunchAgent (macOS) or systemd timer (Linux).

    Args:
        cron_id: Unique ID for this cron (e.g. 'my-backup', 'report-daily'). Must be lowercase with hyphens.
        script: Path to the script to run (absolute or relative to NEXO_HOME/scripts/).
        schedule: Time-based schedule as 'HH:MM' (daily) or 'HH:MM:weekday' (e.g. '08:00:1' for Monday 8AM). Mutually exclusive with interval_seconds.
        interval_seconds: Run every N seconds (e.g. 300 for every 5 min). Mutually exclusive with schedule/keep_alive.
        description: What this cron does (for logs and status).
        script_type: 'auto' (default), 'python', 'shell', 'node', or 'php'.
        keep_alive: Run as a daemon/keep-alive service instead of a timer.
    """
    if not cron_id or not script:
        return "ERROR: cron_id and script are required."

    nexo_home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
    script_path = Path(script)
    if not script_path.is_absolute():
        script_path = nexo_home / "scripts" / script
    if not script_path.exists():
        return f"ERROR: script not found: {script_path}"

    script_meta = parse_inline_metadata(script_path)
    detected_runtime = classify_runtime(script_path, script_meta)
    declared = get_declared_schedule(script_meta, script_meta.get("name", script_path.stem))
    keep_alive = bool(keep_alive or declared.get("schedule_type") == "keep_alive")

    if sum(bool(value) for value in [schedule, interval_seconds, keep_alive]) != 1:
        return "ERROR: choose exactly one schedule mode: schedule, interval_seconds, or keep_alive."

    script_type = (script_type or "auto").strip().lower()
    if script_type == "auto":
        script_type = detected_runtime if detected_runtime != "unknown" else "python"

    wrapper_path = nexo_home / "scripts" / "nexo-cron-wrapper.sh"
    if not wrapper_path.exists():
        return f"ERROR: wrapper not found at {wrapper_path}. Run crons/sync.py first."

    system = platform.system()

    if system == "Darwin":
        return _add_launchagent(
            cron_id,
            str(script_path),
            str(wrapper_path),
            schedule,
            interval_seconds,
            description or script_meta.get("description", ""),
            script_type,
            nexo_home,
            keep_alive=keep_alive,
            declared=declared,
        )
    elif system == "Linux":
        return _add_systemd_timer(
            cron_id,
            str(script_path),
            str(wrapper_path),
            schedule,
            interval_seconds,
            description or script_meta.get("description", ""),
            script_type,
            nexo_home,
            keep_alive=keep_alive,
            declared=declared,
        )
    else:
        return f"ERROR: unsupported platform: {system}"


def _runtime_command(script_type: str) -> str:
    if script_type == "shell":
        return "/bin/bash"
    if script_type == "node":
        return "node"
    if script_type == "php":
        return "php"

    for p in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]:
        if Path(p).exists():
            return p
    return "python3"


def _register_schedule_metadata(cron_id, script_path, schedule, interval_seconds, description, script_type, label="", plist_path="", keep_alive: bool = False):
    init_db()
    script_meta = parse_inline_metadata(Path(script_path))
    runtime = classify_runtime(Path(script_path), script_meta)
    upsert_personal_script(
        name=script_meta.get("name", Path(script_path).stem),
        path=str(Path(script_path)),
        description=script_meta.get("description", description),
        runtime=runtime,
        metadata=script_meta,
        created_by="schedule:add",
        source="filesystem",
        has_inline_metadata=bool(script_meta),
    )
    if keep_alive:
        schedule_type = "keep_alive"
        schedule_value = "true"
        schedule_label = "keep alive"
    elif interval_seconds:
        schedule_type = "interval"
        schedule_value = str(interval_seconds)
        schedule_label = f"every {interval_seconds}s"
    elif schedule:
        schedule_type = "calendar"
        schedule_value = schedule
        schedule_label = schedule
    else:
        schedule_type = "manual"
        schedule_value = ""
        schedule_label = ""
    register_personal_script_schedule(
        script_path=str(Path(script_path)),
        cron_id=cron_id,
        schedule_type=schedule_type,
        schedule_value=schedule_value,
        schedule_label=schedule_label,
        launchd_label=label,
        plist_path=plist_path,
        description=description,
        enabled=True,
    )


def _add_launchagent(cron_id, script_path, wrapper_path, schedule, interval_seconds,
                     description, script_type, nexo_home, *, keep_alive: bool = False, declared: dict | None = None):
    """Create and load a macOS LaunchAgent."""
    import plistlib

    label = f"com.nexo.{cron_id}"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"

    if plist_path.exists():
        return f"ERROR: cron '{cron_id}' already exists at {plist_path}. Use a different ID or remove it first."

    runtime_cmd = _runtime_command(script_type)
    program_args = ["/bin/bash", wrapper_path, cron_id, runtime_cmd, script_path]

    plist = {
        "Label": label,
        "ProgramArguments": program_args,
        "StandardOutPath": str(nexo_home / "logs" / f"{cron_id}-stdout.log"),
        "StandardErrorPath": str(nexo_home / "logs" / f"{cron_id}-stderr.log"),
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            "NEXO_HOME": str(nexo_home),
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + str(Path.home() / ".local/bin"),
            PERSONAL_SCHEDULE_MANAGED_ENV: "1",
            "NEXO_PERSONAL_CRON_ID": cron_id,
        },
    }

    if keep_alive:
        plist["KeepAlive"] = True
    elif interval_seconds:
        plist["StartInterval"] = interval_seconds
    elif schedule:
        parts = schedule.split(":")
        cal = {"Hour": int(parts[0]), "Minute": int(parts[1])}
        if len(parts) > 2:
            cal["Weekday"] = int(parts[2])
        plist["StartCalendarInterval"] = cal

    declared = declared or {}
    if declared.get("run_on_boot") or (keep_alive and "run_on_boot" not in declared):
        plist["RunAtLoad"] = True

    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)

    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)], capture_output=True)

    _register_schedule_metadata(
        cron_id,
        script_path,
        schedule,
        interval_seconds,
        description,
        script_type,
        label=label,
        plist_path=str(plist_path),
        keep_alive=keep_alive,
    )

    if keep_alive:
        detail = " KeepAlive daemon"
    elif schedule:
        detail = f" Schedule: {schedule}"
    else:
        detail = f" Interval: {interval_seconds}s"
    return f"Cron '{cron_id}' installed at {plist_path} and loaded.{detail}"


def _add_systemd_timer(cron_id, script_path, wrapper_path, schedule, interval_seconds,
                       description, script_type, nexo_home, *, keep_alive: bool = False, declared: dict | None = None):
    """Create and enable a systemd user timer (Linux)."""
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)

    runtime_cmd = _runtime_command(script_type)
    exec_cmd = f"/bin/bash {wrapper_path} {cron_id} {runtime_cmd} {script_path}"

    # Service unit
    service_content = f"""[Unit]
Description=NEXO: {description or cron_id}

[Service]
Type=oneshot
ExecStart={exec_cmd}
Environment=NEXO_HOME={nexo_home}
Environment=HOME={Path.home()}
Environment={PERSONAL_SCHEDULE_MANAGED_ENV}=1
Environment=NEXO_PERSONAL_CRON_ID={cron_id}
"""
    service_path = unit_dir / f"nexo-{cron_id}.service"
    service_path.write_text(service_content)

    declared = declared or {}

    if keep_alive:
        service_content = f"""[Unit]
Description=NEXO daemon: {description or cron_id}

[Service]
Type=simple
ExecStart={exec_cmd}
Restart=always
RestartSec=10
Environment=NEXO_HOME={nexo_home}
Environment=HOME={Path.home()}
Environment={PERSONAL_SCHEDULE_MANAGED_ENV}=1
Environment=NEXO_PERSONAL_CRON_ID={cron_id}

[Install]
WantedBy=default.target
"""
        service_path = unit_dir / f"nexo-{cron_id}.service"
        service_path.write_text(service_content)

        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", f"nexo-{cron_id}.service"], capture_output=True)

        _register_schedule_metadata(
            cron_id,
            script_path,
            schedule,
            interval_seconds,
            description,
            script_type,
            label=f"nexo-{cron_id}",
            plist_path="",
            keep_alive=True,
        )

        return f"Cron '{cron_id}' installed as KeepAlive systemd service and enabled. Service: {service_path}"

    # Timer unit
    if interval_seconds:
        timer_spec = f"OnUnitActiveSec={interval_seconds}s"
        if declared.get("run_on_boot") or not declared.get("required"):
            timer_spec += "\nOnBootSec=60s"
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

    _register_schedule_metadata(
        cron_id,
        script_path,
        schedule,
        interval_seconds,
        description,
        script_type,
        label=f"nexo-{cron_id}",
        plist_path="",
        keep_alive=False,
    )

    return f"Cron '{cron_id}' installed as systemd timer and enabled. Service: {service_path}, Timer: {timer_path}"


TOOLS = [
    (handle_schedule_status, "nexo_schedule_status",
     "Show cron execution status: what ran overnight, what failed, durations. "
     "Use at startup to give the user a quick health overview of autonomous processes."),

    (handle_schedule_add, "nexo_schedule_add",
     "Add a new personal cron job. Creates LaunchAgent (macOS) or systemd timer (Linux) "
     "automatically, wrapped with execution tracking."),
]
