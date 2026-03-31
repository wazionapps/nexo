#!/usr/bin/env python3
"""
NEXO Synthesis Engine v2 — Daily intelligence brief.

Before: ~400 lines of Python concatenating SQL results into markdown sections.
Now: Collects raw data, passes to Claude CLI (sonnet) which synthesizes
with real understanding of what matters for tomorrow.

Runs every 2 hours via LaunchAgent. Executes ONCE per day (internal gate).
"""

import fcntl
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

HOME = Path.home()
NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
CLAUDE_DIR = NEXO_HOME
COORD_DIR = CLAUDE_DIR / "coordination"
NEXO_DB = NEXO_HOME / "data" / "nexo.db"
OUTPUT_FILE = COORD_DIR / "daily-synthesis.md"
LAST_RUN_FILE = COORD_DIR / "synthesis-last-run"
LOCK_FILE = COORD_DIR / "synthesis.lock"
CLAUDE_CLI = HOME / ".local" / "bin" / "claude"

TODAY = date.today()
TODAY_STR = TODAY.isoformat()


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def should_run() -> bool:
    if LAST_RUN_FILE.exists():
        return LAST_RUN_FILE.read_text().strip() != TODAY_STR
    return True


def mark_done():
    LAST_RUN_FILE.write_text(TODAY_STR)


def acquire_lock():
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fd
    except BlockingIOError:
        log("Another instance running. Exiting.")
        sys.exit(0)


def release_lock(lock_fd):
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    lock_fd.close()
    LOCK_FILE.unlink(missing_ok=True)


def safe_query(sql: str, params=()) -> list:
    if not NEXO_DB.exists():
        return []
    try:
        conn = sqlite3.connect(str(NEXO_DB))
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.close()
        return rows
    except Exception as e:
        log(f"Query error: {e}")
        return []


def collect_data() -> dict:
    """Collect all raw data for synthesis."""
    data = {"date": TODAY_STR}

    # Today's learnings
    data["learnings"] = safe_query(
        "SELECT category, title, content, reasoning FROM learnings "
        "WHERE date(created_at, 'unixepoch') = ? ORDER BY created_at DESC",
        (TODAY_STR,)
    )

    # Today's decisions
    data["decisions"] = safe_query(
        "SELECT domain, decision, alternatives, based_on, outcome FROM decisions "
        "WHERE date(created_at) = ? ORDER BY created_at DESC",
        (TODAY_STR,)
    )

    # Today's changes
    data["changes"] = safe_query(
        "SELECT files, what_changed, why, affects, risks FROM change_log "
        "WHERE date(created_at) = ? ORDER BY created_at DESC",
        (TODAY_STR,)
    )

    # Session diaries (summaries + mental_state)
    data["diaries"] = safe_query(
        "SELECT summary, self_critique, mental_state, user_signals FROM session_diary "
        "WHERE date(created_at) = ? ORDER BY created_at DESC",
        (TODAY_STR,)
    )

    # Overdue reminders
    data["overdue_reminders"] = safe_query(
        "SELECT id, title, due_date FROM reminders "
        "WHERE status='PENDING' AND due_date <= ? ORDER BY due_date",
        (TODAY_STR,)
    )

    # Pending followups
    data["pending_followups"] = safe_query(
        "SELECT id, title, description, due_date FROM followups "
        "WHERE status='pending' ORDER BY due_date"
    )

    # Guard stats
    data["guard_stats"] = safe_query(
        "SELECT category, COUNT(*) as cnt FROM learnings WHERE status='active' "
        "GROUP BY category ORDER BY cnt DESC LIMIT 10"
    )

    # Postmortem daily (if exists)
    pm_file = COORD_DIR / "postmortem-daily.md"
    if pm_file.exists():
        data["postmortem_summary"] = pm_file.read_text()[:2000]

    return data


def synthesize(data: dict) -> bool:
    """CLI synthesizes the daily brief."""

    data_json = json.dumps(data, ensure_ascii=False, indent=1)
    if len(data_json) > 15000:
        data_json = data_json[:15000] + "\n... (truncated)"

    prompt = f"""You are NEXO's synthesis engine. Write the daily intelligence brief for tomorrow's
startup. This file is read by NEXO at the beginning of each session to understand
what happened today and what to focus on tomorrow.

TODAY'S RAW DATA:
{data_json}

Write the synthesis to {OUTPUT_FILE} with this structure:

# NEXO Daily Synthesis — {TODAY_STR}

## Errors & Learnings
[New learnings from today — what went wrong, what was learned]

## Decisions Made
[Key decisions and their reasoning]

## Changes Deployed
[What was changed in production today]

## the user — Observations
[Patterns in the user's behavior: frustrations, pending decisions, ideas without
deadlines, topics he started but didn't close. This is NEXO's peripheral vision.]

## Weak Points (self-assessment)
[Where NEXO failed or could have done better today — from session diaries]

## Tomorrow's Context
[What the next session needs to know: pending followups, overdue reminders,
in-progress tasks, things to verify]

## Guard Status
[Areas with most learnings — where errors concentrate]

Be concise. Each section 3-8 bullet points max. Focus on what CHANGES BEHAVIOR,
not what merely happened. If a section has nothing, write "Nothing notable."

Execute without asking."""

    log("Invoking Claude CLI (opus) for synthesis...")

    # Verify Claude CLI is authenticated before calling
    try:
        auth_check = subprocess.run(
            [str(CLAUDE_CLI), "--version"],
            capture_output=True, timeout=5
        )
        if auth_check.returncode != 0:
            log("Claude CLI not available or not authenticated. Skipping synthesis.")
            return False
    except Exception:
        log("Claude CLI check failed. Skipping synthesis.")
        return False

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE", None)

    try:
        result = subprocess.run(
            [str(CLAUDE_CLI), "-p", prompt, "--model", "opus",
             "--output-format", "text", "--bare",
             "--allowedTools", "Read,Write,Edit,Glob,Grep"],
            capture_output=True, text=True, timeout=180, env=env
        )

        if result.returncode != 0:
            log(f"CLI error ({result.returncode}): {(result.stderr or '')[:300]}")
            return False

        log(f"Synthesis complete. Output: {len(result.stdout or '')} chars")
        return True

    except subprocess.TimeoutExpired:
        log("CLI timed out (180s)")
        return False
    except Exception as e:
        log(f"Exception: {e}")
        return False


def main():
    if not should_run():
        log(f"Already ran today ({TODAY_STR}). Skipping.")
        return

    lock_fd = acquire_lock()
    try:
        log(f"=== NEXO Synthesis v2 — {TODAY_STR} ===")

        data = collect_data()
        log(f"Collected: {len(data.get('learnings', []))} learnings, "
            f"{len(data.get('decisions', []))} decisions, "
            f"{len(data.get('changes', []))} changes, "
            f"{len(data.get('diaries', []))} diaries")

        success = synthesize(data)

        if success:
            mark_done()
            log("Synthesis v2 complete.")
        else:
            log("Synthesis failed — will retry next trigger.")

        # Register for catch-up
        try:
            state_file = NEXO_HOME / "operations" / ".catchup-state.json"
            st = json.loads(state_file.read_text()) if state_file.exists() else {}
            st["synthesis"] = datetime.now().isoformat()
            state_file.write_text(json.dumps(st, indent=2))
        except Exception:
            pass

    finally:
        release_lock(lock_fd)


if __name__ == "__main__":
    main()
