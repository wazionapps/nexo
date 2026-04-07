#!/usr/bin/env python3
"""NEXO Catch-Up — recover missed core cron windows after boot/wake.

Recovery is driven by the explicit manifest contract plus cron_runs.
Legacy .catchup-state.json is now only a fallback for pre-wrapper history.
"""

import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_RUNTIME_ROOT = _SCRIPT_DIR.parent
_runtime_root = Path(os.environ.get("NEXO_CODE", str(_DEFAULT_RUNTIME_ROOT)))
if str(_runtime_root) not in sys.path:
    sys.path.insert(0, str(_runtime_root))

from agent_runner import AutomationBackendUnavailableError, probe_automation_backend, run_automation_prompt
from cron_recovery import catchup_candidates

HOME = Path.home()
NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(HOME / ".nexo")))


def _resolve_claude_cli() -> Path:
    """Find claude CLI: saved path > PATH > common locations."""
    saved = NEXO_HOME / "config" / "claude-cli-path"
    if saved.exists():
        p = Path(saved.read_text().strip())
        if p.exists():
            return p
    import shutil
    found = shutil.which("claude")
    if found:
        return Path(found)
    for candidate in [
        HOME / ".local" / "bin" / "claude",
        HOME / ".npm-global" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
    ]:
        if candidate.exists():
            return candidate
    return HOME / ".local" / "bin" / "claude"  # last resort


CLAUDE_CLI = _resolve_claude_cli()
LOG_DIR = NEXO_HOME / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "catchup.log"
STATE_FILE = NEXO_HOME / "operations" / ".catchup-state.json"
LOCK_FILE = NEXO_HOME / "operations" / ".catchup.lock"

SCRIPTS = NEXO_HOME / "scripts"
WRAPPER = SCRIPTS / "nexo-cron-wrapper.sh"

# Resolve Python: prefer NEXO's venv, then the same Python running this script
def _resolve_python() -> str:
    """Find the best Python to use for subprocess calls."""
    # Check for NEXO_CODE env var pointing to the repo's src/
    nexo_code = os.environ.get("NEXO_CODE", "")
    if nexo_code:
        venv_python = Path(nexo_code).parent / ".venv" / "bin" / "python"
        if venv_python.exists():
            return str(venv_python)
    # Check for venv relative to NEXO_HOME
    venv_python = NEXO_HOME / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    # Fall back to the same Python running this script
    return sys.executable

NEXO_PYTHON = _resolve_python()


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


def _resolve_runtime_command(script_type: str) -> str:
    if script_type == "shell":
        return "/bin/bash"
    if script_type == "node":
        return "node"
    if script_type == "php":
        return "php"
    return NEXO_PYTHON


def _acquire_lock():
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_FILE.open("w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def _heal_personal_schedules() -> dict:
    """Recreate declared personal schedules before catch-up checks missed windows."""
    summary = {"created": 0, "repaired": 0, "invalid": 0, "error": ""}
    try:
        from script_registry import reconcile_personal_scripts

        result = reconcile_personal_scripts(dry_run=False)
        ensured = result.get("ensure_schedules", {})
        summary["created"] = len(ensured.get("created", []))
        summary["repaired"] = len(ensured.get("repaired", []))
        summary["invalid"] = len(ensured.get("invalid", []))
        if summary["created"] or summary["repaired"]:
            log(
                "Repaired declared personal schedules before catch-up: "
                f"{summary['created']} created, {summary['repaired']} repaired."
            )
        if summary["invalid"]:
            log(f"WARNING: {summary['invalid']} declared personal schedules are invalid.")
    except Exception as e:
        summary["error"] = str(e)
        log(f"Personal schedule self-heal skipped: {e}")
    return summary


def run_task(candidate: dict, state: dict) -> bool:
    """Execute a task and update state."""
    name = candidate["cron_id"]
    raw_script = str(candidate.get("script", ""))
    script_candidate = Path(raw_script)
    if script_candidate.is_absolute():
        script_path = script_candidate
    else:
        script_path = SCRIPTS / script_candidate.name
    script_name = script_path.name
    if not script_path.exists():
        log(f"  SKIP {name}: script not found ({script_path})")
        return False

    runtime_cmd = _resolve_runtime_command(candidate.get("type", "python"))
    if WRAPPER.exists():
        command = ["/bin/bash", str(WRAPPER), name, runtime_cmd, str(script_path)]
    else:
        command = [runtime_cmd, str(script_path)]

    log(f"  RUNNING {name}: {script_name}")
    try:
        result = subprocess.run(
            command,
            capture_output=True, text=True, timeout=21600,
            env={**os.environ, "HOME": str(HOME), "NEXO_CATCHUP": "1"}
        )
        if result.returncode == 0:
            log(f"  OK {name} (exit 0)")
            state[name] = datetime.now().isoformat()
            save_state(state)
            return True
        else:
            log(f"  FAIL {name} (exit {result.returncode})")
            if result.stderr:
                log(f"    stderr: {result.stderr[:300]}")
            return False
    except subprocess.TimeoutExpired:
        log(f"  TIMEOUT {name} (21600s)")
        return False
    except Exception as e:
        log(f"  ERROR {name}: {e}")
        return False


def main():
    log("=== NEXO Catch-Up starting (boot/wake) ===")
    lock_handle = _acquire_lock()
    if lock_handle is None:
        log("Catch-Up already running; skipping overlapping invocation.")
        return

    ran = 0
    skipped = 0
    skipped_out_of_window = 0
    try:
        _heal_personal_schedules()
        state = load_state()
        tasks = catchup_candidates()

        for candidate in tasks:
            name = candidate["cron_id"]
            if not candidate.get("missed"):
                skipped += 1
                continue
            if not candidate.get("within_window"):
                skipped_out_of_window += 1
                log(
                    f"  SKIP {name}: missed window is {candidate['age_seconds']}s old "
                    f"(max_catchup_age={candidate['contract']['max_catchup_age']}s)"
                )
                continue
            due_at = candidate["last_due_at"].astimezone().strftime("%Y-%m-%d %H:%M")
            log(f"  {name} — missed scheduled run due at {due_at}, catching up...")
            if run_task(candidate, state):
                ran += 1

        if ran == 0 and skipped_out_of_window == 0:
            log("All tasks up to date, nothing to catch up.")
        elif ran >= 3:
            # Many tasks caught up — ask CLI to assess system state
            _cli_post_catchup_assessment(ran, skipped, state)
        else:
            suffix = f", {skipped_out_of_window} outside recovery window" if skipped_out_of_window else ""
            log(f"Caught up {ran} tasks, {skipped} already current{suffix}.")

        log("=== Catch-Up complete ===")
    finally:
        lock_handle.close()


def _cli_post_catchup_assessment(ran: int, skipped: int, state: dict):
    """When 3+ tasks were missed, use CLI to assess if there are concerns."""
    probe = probe_automation_backend(timeout=30)
    if not probe.get("ok"):
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] "
            f"Automation backend unavailable. Skipping CLI analysis. ({probe.get('reason') or probe.get('stderr') or 'not ready'})"
        )
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
    try:
        result = run_automation_prompt(
            prompt,
            model="opus",
            timeout=21600,
            output_format="text",
            allowed_tools="Read,Write,Edit,Glob,Grep,Bash,mcp__nexo__*",
        )
        if result.returncode == 0:
            log(f"Assessment written to {assessment_file}")
        else:
            log(f"CLI assessment exited {result.returncode}")
    except AutomationBackendUnavailableError as e:
        log(f"CLI assessment skipped: {e}")
    except subprocess.TimeoutExpired:
        log("CLI assessment timed out (90s)")
    except Exception as e:
        log(f"CLI assessment error: {e}")


if __name__ == "__main__":
    main()
