#!/usr/bin/env python3
"""
NEXO Sleep System v2 — The brain dreams.

Before: 834 lines with word-overlap "intelligence" for learning consolidation.
Now: Stage A (mechanical cleanup) stays pure Python. Stage B (dreaming) uses
the configured automation backend to understand, deduplicate, and prune with real intelligence.

Triggered hourly via LaunchAgent. Runs ONCE per day, first time Mac is awake.
If interrupted (power loss, crash), resumes on next trigger.

Stage A — Housekeeping (Python pure):
  Delete old logs, rotate files, trim JSON. No intelligence needed.

Stage B — Dreaming (automation backend):
  Review learnings for duplicates and contradictions with UNDERSTANDING.
  Prune MEMORY.md if over limit. Clean preferences. Compress old observations.
  One CLI call that does what 500 lines of word-overlap couldn't.
"""

import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(_repo_src) if (_repo_src / "server.py").exists() else str(NEXO_HOME)))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

from agent_runner import AutomationBackendUnavailableError, run_automation_prompt
try:
    from client_preferences import resolve_user_model as _resolve_user_model
    _USER_MODEL = _resolve_user_model()
except Exception:
    _USER_MODEL = ""


# ─── Paths ────────────────────────────────────────────────────────────────────
CLAUDE_DIR = NEXO_HOME
BRAIN_DIR = CLAUDE_DIR / "brain"
COORD_DIR = CLAUDE_DIR / "coordination"
MEMORY_DIR = CLAUDE_DIR / "memory"
DAEMON_LOGS_DIR = CLAUDE_DIR / "daemon" / "logs"

DAILY_SUMMARIES_DIR = BRAIN_DIR / "daily_summaries"
SESSION_ARCHIVE_DIR = BRAIN_DIR / "session_archive"
COMPRESSED_MEMORIES_DIR = BRAIN_DIR / "compressed_memories"

HEARTBEAT_LOG = COORD_DIR / "heartbeat-log.json"
REFLECTION_LOG = COORD_DIR / "reflection-log.json"
SLEEP_LOG = COORD_DIR / "sleep-log.json"

MEMORY_MD = NEXO_HOME / "memory" / "MEMORY.md"
NEXO_DB = NEXO_HOME / "data" / "nexo.db"
CLAUDE_MEM_DB = Path.home() / ".claude-mem" / "claude-mem.db"
def _resolve_claude_cli() -> Path:
    """Find claude CLI: saved path > PATH > common locations."""
    saved = NEXO_HOME / "config" / "claude-cli-path"
    if saved.exists():
        p = Path(saved.read_text().strip())
        if p.exists():
            return p
    found = shutil.which("claude")
    if found:
        return Path(found)
    for candidate in [
        Path.home() / ".local" / "bin" / "claude",
        Path.home() / ".npm-global" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
    ]:
        if candidate.exists():
            return candidate
    return Path.home() / ".local" / "bin" / "claude"

CLAUDE_CLI = _resolve_claude_cli()

LAST_RUN_FILE = COORD_DIR / "sleep-last-run"
LOCK_FILE = COORD_DIR / "sleep.lock"
PROCESS_LOCK = COORD_DIR / "sleep-process.lock"

TODAY = date.today()
NOW = datetime.now()
TIMESTAMP = NOW.strftime("%Y-%m-%d %H:%M")


# ─── Run-once & resume logic (unchanged from v1) ──────────────────────────────

def already_ran_today() -> bool:
    if not LAST_RUN_FILE.exists():
        return False
    try:
        return LAST_RUN_FILE.read_text().strip() == str(TODAY)
    except Exception:
        return False


def was_interrupted() -> bool:
    if not LOCK_FILE.exists():
        return False
    try:
        lock_data = json.loads(LOCK_FILE.read_text())
        if lock_data.get("date") != str(TODAY):
            LOCK_FILE.unlink()
            return False
        lock_pid = lock_data.get("pid")
        if lock_pid:
            try:
                os.kill(lock_pid, 0)
                log(f"Another instance running (PID {lock_pid}). Exiting.")
                return False
            except ProcessLookupError:
                log(f"Interrupted run (phase: {lock_data.get('phase', '?')}). Resuming.")
                return True
            except PermissionError:
                return False
        LOCK_FILE.unlink()
        return False
    except Exception:
        LOCK_FILE.unlink(missing_ok=True)
        return False


def get_interrupted_phase() -> str:
    try:
        return json.loads(LOCK_FILE.read_text()).get("phase", "stage_a")
    except Exception:
        return "stage_a"


def set_lock(phase: str):
    save_json(LOCK_FILE, {"date": str(TODAY), "phase": phase, "started": TIMESTAMP, "pid": os.getpid()})


def mark_complete():
    LAST_RUN_FILE.write_text(str(TODAY))
    LOCK_FILE.unlink(missing_ok=True)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{ts}] {msg}")


def load_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return default if default is not None else {}


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def parse_date_from_stem(stem: str):
    m = re.search(r'(\d{4}-\d{2}-\d{2})', stem)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            return None
    return None


def append_sleep_log(entry: dict):
    entries = load_json(SLEEP_LOG, [])
    if not isinstance(entries, list):
        entries = []
    entries.append(entry)
    if len(entries) > 90:
        entries = entries[-90:]
    save_json(SLEEP_LOG, entries)


# ─── Stage A: Mechanical cleanup (UNCHANGED from v1) ─────────────────────────

def stage_a_cleanup() -> dict:
    """Pure Python cleanup. No LLM calls."""
    stats = {
        "a1_daily_summaries_deleted": 0,
        "a2_session_archives_deleted": 0,
        "a3_logs_rotated": 0,
        "a4_compressed_memories_deleted": 0,
        "a5_heartbeat_trimmed": False,
        "a6_reflection_trimmed": False,
        "a7_daemon_logs_deleted": 0,
    }

    # A1: Delete daily_summaries/*.md >90 days
    cutoff_90 = TODAY - timedelta(days=90)
    if DAILY_SUMMARIES_DIR.exists():
        for f in DAILY_SUMMARIES_DIR.glob("*.md"):
            d = parse_date_from_stem(f.stem)
            if d and d < cutoff_90:
                try:
                    f.unlink()
                    stats["a1_daily_summaries_deleted"] += 1
                except Exception:
                    pass

    # A2: Delete session_archive/*.jsonl >30 days
    cutoff_30 = TODAY - timedelta(days=30)
    if SESSION_ARCHIVE_DIR.exists():
        for f in SESSION_ARCHIVE_DIR.glob("*.jsonl"):
            d = parse_date_from_stem(f.stem)
            if d and d < cutoff_30:
                try:
                    f.unlink()
                    stats["a2_session_archives_deleted"] += 1
                except Exception:
                    pass

    # A3: Rotate coordination/*-stdout.log if >5MB
    if COORD_DIR.exists():
        for f in COORD_DIR.glob("*-stdout.log"):
            try:
                if f.stat().st_size > 5 * 1024 * 1024:
                    lines = f.read_text().splitlines()
                    keep = lines[-500:]
                    f.write_text("\n".join(keep) + "\n")
                    stats["a3_logs_rotated"] += 1
            except Exception:
                pass

    # A4: Delete compressed_memories/week_*.md >180 days
    cutoff_180 = TODAY - timedelta(days=180)
    if COMPRESSED_MEMORIES_DIR.exists():
        for f in COMPRESSED_MEMORIES_DIR.glob("week_*.md"):
            d = parse_date_from_stem(f.stem)
            if d and d < cutoff_180:
                try:
                    f.unlink()
                    stats["a4_compressed_memories_deleted"] += 1
                except Exception:
                    pass

    # A5: Trim heartbeat-log.json to 200 entries
    if HEARTBEAT_LOG.exists():
        try:
            data = load_json(HEARTBEAT_LOG, [])
            if isinstance(data, list) and len(data) > 200:
                save_json(HEARTBEAT_LOG, data[-200:])
                stats["a5_heartbeat_trimmed"] = True
        except Exception:
            pass

    # A6: Trim reflection-log.json to 60 entries
    if REFLECTION_LOG.exists():
        try:
            data = load_json(REFLECTION_LOG, [])
            if isinstance(data, list) and len(data) > 60:
                save_json(REFLECTION_LOG, data[-60:])
                stats["a6_reflection_trimmed"] = True
        except Exception:
            pass

    # A7: Delete daemon/logs/ dirs >14 days
    cutoff_14 = TODAY - timedelta(days=14)
    if DAEMON_LOGS_DIR.exists():
        for d_path in sorted(DAEMON_LOGS_DIR.iterdir()):
            if not d_path.is_dir():
                continue
            d = parse_date_from_stem(d_path.name)
            if d and d < cutoff_14:
                try:
                    shutil.rmtree(d_path)
                    stats["a7_daemon_logs_deleted"] += 1
                except Exception:
                    pass

    # A8: Delete cortex/logs/*.log >7 days, truncate launchd >5MB
    cutoff_7 = TODAY - timedelta(days=7)
    cortex_logs = NEXO_HOME / "cortex" / "logs"
    if cortex_logs.exists():
        for f in cortex_logs.glob("*.log"):
            if f.name.startswith("launchd-"):
                try:
                    if f.stat().st_size > 5 * 1024 * 1024:
                        lines = f.read_text().splitlines()
                        f.write_text("\n".join(lines[-500:]) + "\n")
                        stats["a3_logs_rotated"] += 1
                except Exception:
                    pass
                continue
            d = parse_date_from_stem(f.stem)
            if d and d < cutoff_7:
                try:
                    f.unlink()
                except Exception:
                    pass

    return stats


# ─── Stage B: Dreaming (automation backend) ─────────────────────────────────

def collect_brain_state() -> dict:
    """Collect all data the CLI needs to dream."""
    state = {"learnings": [], "preferences": [], "memory_md_lines": 0,
             "claude_mem_old": 0, "feedback_count": 0}

    if NEXO_DB.exists():
        try:
            conn = sqlite3.connect(str(NEXO_DB))
            conn.row_factory = sqlite3.Row

            # Learnings
            rows = conn.execute(
                "SELECT id, title, content, category, created_at FROM learnings "
                "WHERE status='active' ORDER BY id"
            ).fetchall()
            state["learnings"] = [dict(r) for r in rows]

            # Preferences
            rows = conn.execute("SELECT key, value, category, updated_at FROM preferences").fetchall()
            state["preferences"] = [dict(r) for r in rows]

            conn.close()
        except Exception as e:
            log(f"DB error: {e}")

    # MEMORY.md
    if MEMORY_MD.exists():
        state["memory_md_lines"] = len(MEMORY_MD.read_text().splitlines())

    # claude-mem.db old observations
    if CLAUDE_MEM_DB.exists():
        try:
            cutoff = int((datetime.now() - timedelta(days=60)).timestamp() * 1000)
            conn = sqlite3.connect(str(CLAUDE_MEM_DB))
            state["claude_mem_old"] = conn.execute(
                "SELECT COUNT(*) FROM observations WHERE created_at_epoch < ?", (cutoff,)
            ).fetchone()[0]
            conn.close()
        except Exception:
            pass

    # Feedback count
    state["feedback_count"] = len(list(MEMORY_MD.parent.glob("feedback_*.md")))

    return state


def should_dream(state: dict) -> bool:
    """Check if there's enough to justify a CLI call."""
    return (
        len(state["learnings"]) > 10
        or state["memory_md_lines"] > 170
        or len(state["preferences"]) > 5
        or state["claude_mem_old"] > 500
    )


def dream(state: dict) -> dict:
    """The brain dreams — CLI does the intelligent work."""

    # Truncate learnings JSON if too large
    learnings_json = json.dumps(state["learnings"], ensure_ascii=False, indent=1)
    if len(learnings_json) > 15000:
        learnings_json = learnings_json[:15000] + "\n... (truncated)"

    tasks = []

    tasks.append(f"""TASK 1: LEARNING CONSOLIDATION ({len(state['learnings'])} active)
Review these learnings and identify:
a) DUPLICATES: learnings that say the same thing differently.
b) CONTRADICTIONS: learnings that contradict each other.
c) STALE: learnings about bugs/issues fixed >60 days ago that are never referenced.

Write your findings to {COORD_DIR}/sleep-report.md with sections:
- "## Duplicates to archive" — list learning IDs to archive and why
- "## Contradictions" — pairs of conflicting learnings
- "## Stale candidates" — IDs of learnings that may be obsolete

Also write a machine-readable file {COORD_DIR}/sleep-actions.json:
{{"archive_ids": [1, 2, 3], "contradiction_pairs": [[4, 5]], "stale_ids": [6, 7]}}

The wrapper will execute the actual DB operations based on this JSON.

LEARNINGS:
{learnings_json}""")

    if state["memory_md_lines"] > 170:
        tasks.append(f"""TASK 2: MEMORY.MD COMPRESSION ({state['memory_md_lines']} lines, limit 200)
File: {MEMORY_MD}
Read it, compress resolved incidents >21 days, merge duplicates.
NEVER delete: credentials, legal entity info, CRITICAL rules, infrastructure.
Target: <180 lines.""")

    if len(state["preferences"]) > 5:
        tasks.append(f"""TASK 3: PREFERENCES CLEANUP ({len(state['preferences'])} entries)
Review the preferences and identify duplicate keys.
Add to sleep-actions.json: "duplicate_preference_keys": ["key1", "key2", ...]
The wrapper will handle the actual DB cleanup safely.""")

    if state["claude_mem_old"] > 500:
        tasks.append(f"""TASK 4: OLD OBSERVATIONS ({state['claude_mem_old']} entries >60d)
Note in sleep-report.md that old observations should be cleaned.
Add to sleep-actions.json: "clean_old_observations": true
The wrapper will handle the actual DB cleanup safely.""")

    tasks_str = "\n\n".join(tasks)

    prompt = f"""FIRST: Call nexo_startup(task='deep-sleep nightly maintenance') to register this session.

You are NEXO Sleep — the nightly brain maintenance process.
Like a human brain during sleep: consolidate important memories, discard noise,
detect conflicts, prepare state for tomorrow.
Use nexo_learning_add, nexo_followup_create, nexo_session_diary_write and other MCP tools directly.

BRAIN STATE:
- {len(state['learnings'])} active learnings
- {state['memory_md_lines']} lines in MEMORY.md (limit: 200)
- {len(state['preferences'])} preferences
- {state['feedback_count']} feedback files
- {state['claude_mem_old']} old observations (>60d)

{tasks_str}

ABSOLUTE RULES:
- NEVER delete legal entity info (LLC, SLU, EIN, NIF, project)
- NEVER delete credentials, tokens, API keys, secrets
- NEVER delete rules marked CRITICAL or MAX PRIORITY
- NEVER delete infrastructure info (servers, repos, deploys)
- When in doubt, DON'T delete

Write a summary to {COORD_DIR}/sleep-report.md when done.
Execute without asking."""

    log("Stage B: Invoking automation backend — dreaming...")
    try:
        result = run_automation_prompt(
            prompt,
            model=_USER_MODEL or "opus",
            timeout=21600,
            output_format="text",
            allowed_tools="Read,Write,Edit,Glob,Grep,Bash,mcp__nexo__*",
        )

        if result.returncode != 0:
            log(f"Stage B: CLI error ({result.returncode}): {(result.stderr or '')[:300]}")
            return {"error": result.returncode}

        log(f"Stage B: Dreaming complete. Output: {len(result.stdout or '')} chars")
        return {"ok": True, "output_len": len(result.stdout or "")}

    except AutomationBackendUnavailableError as e:
        log(f"Stage B: automation backend unavailable: {e}")
        return {"error": "backend-unavailable"}
    except subprocess.TimeoutExpired:
        log("Stage B: CLI timed out (600s)")
        return {"error": "timeout"}
    except Exception as e:
        log(f"Stage B: Exception: {e}")
        return {"error": str(e)}


def execute_dream_actions(actions: dict, state: dict):
    """Execute the DB actions decided by CLI, safely in Python."""
    log("Stage B2: Executing dream actions...")

    # Archive duplicate/stale learnings
    archive_ids = actions.get("archive_ids", []) + actions.get("stale_ids", [])
    if archive_ids and NEXO_DB.exists():
        try:
            conn = sqlite3.connect(str(NEXO_DB))
            for lid in archive_ids:
                if isinstance(lid, int):
                    conn.execute(
                        "UPDATE learnings SET status='archived' WHERE id=? AND status='active'",
                        (lid,)
                    )
            conn.commit()
            conn.close()
            log(f"  Archived {len(archive_ids)} learnings: {archive_ids}")
        except Exception as e:
            log(f"  Error archiving learnings: {e}")

    # Clean duplicate preferences
    dup_keys = actions.get("duplicate_preference_keys", [])
    if dup_keys and NEXO_DB.exists():
        try:
            conn = sqlite3.connect(str(NEXO_DB))
            for key in dup_keys:
                if isinstance(key, str):
                    # Keep newest, delete older duplicates
                    conn.execute(
                        "DELETE FROM preferences WHERE key = ? AND rowid NOT IN "
                        "(SELECT rowid FROM preferences WHERE key = ? ORDER BY updated_at DESC LIMIT 1)",
                        (key, key)
                    )
            conn.commit()
            conn.close()
            log(f"  Cleaned {len(dup_keys)} duplicate preference keys")
        except Exception as e:
            log(f"  Error cleaning preferences: {e}")

    # Clean old observations
    if actions.get("clean_old_observations") and CLAUDE_MEM_DB.exists():
        try:
            cutoff_ms = int((datetime.now() - timedelta(days=60)).timestamp() * 1000)
            conn = sqlite3.connect(str(CLAUDE_MEM_DB))
            deleted = conn.execute(
                "DELETE FROM observations WHERE created_at_epoch < ? "
                "AND discovery_tokens < 300 "
                "AND id NOT IN (SELECT id FROM observations WHERE "
                "title LIKE '%CRITICO%' OR title LIKE '%credential%' "
                "OR title LIKE '%token%' OR title LIKE '%API%' "
                "OR title LIKE '%LLC%' OR title LIKE '%SLU%') "
                "LIMIT 200",
                (cutoff_ms,)
            ).rowcount
            conn.execute(
                "DELETE FROM observations_fts WHERE rowid NOT IN "
                "(SELECT id FROM observations)"
            )
            conn.execute("VACUUM")
            conn.commit()
            conn.close()
            log(f"  Cleaned {deleted} old observations")
        except Exception as e:
            log(f"  Error cleaning observations: {e}")

    log("Stage B2: Actions complete.")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    log("=" * 60)
    log("NEXO Sleep System v2 starting")

    # Process lock
    try:
        lock_fd = open(PROCESS_LOCK, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
    except (IOError, OSError):
        log("Another sleep instance running. Exiting.")
        sys.exit(0)

    try:
        if already_ran_today():
            log("Already ran today. Exiting.")
            sys.exit(0)

        start_phase = "stage_a"
        if was_interrupted():
            start_phase = get_interrupted_phase()

        run_log = {"date": str(TODAY), "started": TIMESTAMP,
                   "stage_a": None, "stage_b": None, "completed": None}
        sleep_had_errors = False

        # Stage A: Housekeeping (mechanical)
        if start_phase == "stage_a":
            set_lock("stage_a")
            log("─── Stage A: Housekeeping ───")
            run_log["stage_a"] = stage_a_cleanup()

        # Stage B: Dreaming (intelligent)
        set_lock("stage_b")
        log("─── Stage B: Dreaming ───")
        state = collect_brain_state()

        if should_dream(state):
            log(f"Brain state: {len(state['learnings'])} learnings, "
                f"{state['memory_md_lines']} MEMORY lines, "
                f"{state['claude_mem_old']} old observations")
            dream_result = dream(state)
            run_log["stage_b"] = dream_result

            if "error" in dream_result:
                log(f"Stage B: Dreaming failed ({dream_result['error']}). "
                    "Stage A cleanup completed successfully. Not marking catchup to allow retry.")
                sleep_had_errors = True
            else:
                # Stage B2: Execute actions from CLI output
                actions_file = COORD_DIR / "sleep-actions.json"
                if actions_file.exists():
                    try:
                        actions = json.loads(actions_file.read_text())
                        execute_dream_actions(actions, state)
                    except Exception as e:
                        log(f"Stage B2: Error executing actions: {e}")
        else:
            log("Brain is clean -- no dreaming needed.")
            run_log["stage_b"] = {"skipped": True}

        # Done
        run_log["completed"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        mark_complete()
        append_sleep_log(run_log)
        log(f"NEXO Sleep v2 complete at {run_log['completed']}")

        # Register for catch-up only if all stages succeeded
        if not sleep_had_errors:
            try:
                state_file = NEXO_HOME / "operations" / ".catchup-state.json"
                st = json.loads(state_file.read_text()) if state_file.exists() else {}
                st["sleep"] = datetime.now().isoformat()
                state_file.write_text(json.dumps(st, indent=2))
            except Exception:
                pass

    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
            PROCESS_LOCK.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
