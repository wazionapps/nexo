#!/usr/bin/env python3
"""
NEXO Catch-Up — Runs at Mac boot to execute any missed scheduled tasks.

When the Mac was asleep/off during scheduled times, launchd does NOT retry
missed StartCalendarInterval jobs. This script detects what was missed and
runs them in the correct order.

Scheduled tasks (ordered by intended run time):
  03:00 — cognitive-decay (Ebbinghaus decay + STM→LTM promotion)
  03:00 — evolution (weekly, Sundays only)
  04:00 — sleep (session cleanup)
  07:00 — self-audit (health checks + weekly cognitive GC on Sundays)
  23:30 — postmortem (consolidation + sensory register)

Logic: For each task, check if its last successful run was before the
most recent scheduled time. If so, run it now.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

CLAUDE_CLI = Path.home() / ".local" / "bin" / "claude"

HOME = Path.home()
NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
LOG_DIR = NEXO_HOME / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "catchup.log"
STATE_FILE = NEXO_HOME / "operations" / ".catchup-state.json"

PYTHON_BREW = "/opt/homebrew/bin/python3"
PYTHON_SYS = "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"
SCRIPTS = NEXO_HOME / "scripts"


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def last_scheduled_time(hour: int, minute: int, weekday: int = None) -> datetime:
    """Calculate the most recent time this task should have run."""
    now = datetime.now()
    today_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if weekday is not None:
        # Weekly task — find the most recent matching weekday
        days_since = (now.weekday() - weekday) % 7
        target = now - timedelta(days=days_since)
        target = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target > now:
            target -= timedelta(weeks=1)
        return target

    # Daily task
    if today_at <= now:
        return today_at
    else:
        return today_at - timedelta(days=1)


def should_run(task_name: str, hour: int, minute: int, state: dict, weekday: int = None) -> bool:
    """Check if task needs catch-up: last run was before last scheduled time."""
    last_run_str = state.get(task_name)
    last_scheduled = last_scheduled_time(hour, minute, weekday)

    if not last_run_str:
        # Never ran — should run
        return True

    try:
        last_run = datetime.fromisoformat(last_run_str)
    except ValueError:
        return True

    return last_run < last_scheduled


def run_task(name: str, python: str, script: str, state: dict) -> bool:
    """Execute a task and update state."""
    script_path = str(SCRIPTS / script)
    if not Path(script_path).exists():
        log(f"  SKIP {name}: script not found ({script_path})")
        return False

    log(f"  RUNNING {name}: {script}")
    try:
        result = subprocess.run(
            [python, script_path],
            capture_output=True, text=True, timeout=300,
            env={**os.environ, "HOME": str(HOME), "NEXO_CATCHUP": "1"}
        )
        if result.returncode == 0:
            log(f"  OK {name} (exit 0)")
        else:
            log(f"  WARN {name} (exit {result.returncode})")
            if result.stderr:
                log(f"    stderr: {result.stderr[:300]}")
        state[name] = datetime.now().isoformat()
        save_state(state)
        return True
    except subprocess.TimeoutExpired:
        log(f"  TIMEOUT {name} (300s)")
        return False
    except Exception as e:
        log(f"  ERROR {name}: {e}")
        return False


def main():
    log("=== NEXO Catch-Up starting (boot/wake) ===")
    state = load_state()

    # Define tasks in execution order (matching their intended schedule order)
    # Auto-update check FIRST
    update_script = SCRIPTS / "nexo-auto-update.py"
    if update_script.exists():
        log("Checking for NEXO updates...")
        try:
            subprocess.run(
                [PYTHON_BREW if os.path.exists(PYTHON_BREW) else PYTHON_SYS, str(update_script)],
                capture_output=True, text=True, timeout=60,
                env={**os.environ, "HOME": str(HOME), "NEXO_HOME": str(NEXO_HOME)}
            )
        except Exception as e:
            log(f"  Update check failed: {e}")

    tasks = [
        # (name, hour, minute, python, script, weekday)
        ("cognitive-decay", 3, 0, PYTHON_BREW, "nexo-cognitive-decay.py", None),
        ("evolution", 3, 0, PYTHON_SYS, "nexo-evolution-run.py", 6),  # Sunday = 6
        ("sleep", 4, 0, PYTHON_SYS, "nexo-sleep.py", None),
        ("self-audit", 7, 0, PYTHON_SYS, "nexo-daily-self-audit.py", None),
        ("github-monitor", 8, 0, PYTHON_BREW, "nexo-github-monitor.py", None),
        ("postmortem", 23, 30, PYTHON_BREW, "nexo-postmortem-consolidator.py", None),
    ]

    ran = 0
    skipped = 0
    for name, hour, minute, python, script, weekday in tasks:
        if should_run(name, hour, minute, state, weekday):
            log(f"  {name} — missed scheduled run, catching up...")
            if run_task(name, python, script, state):
                ran += 1
        else:
            skipped += 1

    if ran == 0:
        log("All tasks up to date, nothing to catch up.")
    elif ran >= 3:
        # Many tasks caught up — ask CLI to assess system state
        _cli_post_catchup_assessment(ran, skipped, state)
    else:
        log(f"Caught up {ran} tasks, {skipped} already current.")

    log("=== Catch-Up complete ===")


def _cli_post_catchup_assessment(ran: int, skipped: int, state: dict):
    """When 3+ tasks were missed, use CLI to assess if there are concerns."""
    if not CLAUDE_CLI.exists():
        log(f"Caught up {ran} tasks, {skipped} already current. (CLI unavailable for assessment)")
        return

    assessment_file = LOG_DIR / "catchup-assessment.md"
    state_summary = json.dumps(state, indent=2, default=str)

    prompt = f"""You are the NEXO Catch-Up system. The Mac was off/asleep and {ran} scheduled tasks just ran as catch-up ({skipped} were already current).

Task run state (timestamps of last successful runs):
{state_summary}

Assess:
1. How long was the system likely offline? (compare timestamps to now)
2. Are there any tasks that depend on each other where order matters?
3. Any tasks that may have produced stale results because they ran late?
4. Should any task be re-run at its normal time today?

Write a brief assessment (max 20 lines) to: {assessment_file}

Format:
## Catch-Up Assessment — {datetime.now().strftime('%Y-%m-%d %H:%M')}
- Offline duration: ~Xh
- Tasks caught up: {ran}
- Concerns: ...
- Recommendation: ..."""

    log(f"Caught up {ran} tasks — running CLI assessment...")
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE", None)

    try:
        result = subprocess.run(
            [str(CLAUDE_CLI), "-p", prompt, "--model", "opus",
             "--allowedTools", "Read,Write,Edit,Glob,Grep"],
            capture_output=True, text=True, timeout=90, env=env
        )
        if result.returncode == 0:
            log(f"Assessment written to {assessment_file}")
        else:
            log(f"CLI assessment exited {result.returncode}")
    except subprocess.TimeoutExpired:
        log("CLI assessment timed out (90s)")
    except Exception as e:
        log(f"CLI assessment error: {e}")


if __name__ == "__main__":
    main()
