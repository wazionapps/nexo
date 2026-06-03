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
import atexit
import json
import os
import re
import shutil
import signal
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
from constants import AUTOMATION_SUBPROCESS_TIMEOUT
from core_prompts import render_core_prompt
from deep_sleep_retention import prune_deep_sleep_runtime
import paths
try:
    from client_preferences import resolve_user_model as _resolve_user_model
    _USER_MODEL = _resolve_user_model()
except Exception:
    _USER_MODEL = ""


# ─── Paths ────────────────────────────────────────────────────────────────────
CLAUDE_DIR = paths.home()
BRAIN_DIR = paths.brain_dir()
COORD_DIR = paths.coordination_dir()
MEMORY_DIR = paths.memory_dir()
DAEMON_LOGS_DIR = CLAUDE_DIR / "daemon" / "logs"

DAILY_SUMMARIES_DIR = BRAIN_DIR / "daily_summaries"
SESSION_ARCHIVE_DIR = BRAIN_DIR / "session_archive"
COMPRESSED_MEMORIES_DIR = BRAIN_DIR / "compressed_memories"

HEARTBEAT_LOG = COORD_DIR / "heartbeat-log.json"
REFLECTION_LOG = COORD_DIR / "reflection-log.json"
SLEEP_LOG = COORD_DIR / "sleep-log.json"
SLEEP_HEALTH_FILE = COORD_DIR / "sleep-health.json"
LEARNINGS_DUMP_FILE = COORD_DIR / "sleep-learnings-dump.json"
LEARNINGS_CHUNKS_DIR = COORD_DIR / "sleep-learnings-chunks"
MIN_LEARNING_COVERAGE_PCT = 95.0
LEARNING_CHUNK_MAX_CHARS = 50000

MEMORY_MD = paths.memory_dir() / "MEMORY.md"
NEXO_DB = paths.db_path()
CLAUDE_MEM_DB = Path.home() / ".claude-mem" / "claude-mem.db"

LAST_RUN_FILE = COORD_DIR / "sleep-last-run"
LOCK_FILE = COORD_DIR / "sleep.lock"
PROCESS_LOCK = COORD_DIR / "sleep-process.lock"

TODAY = date.today()
NOW = datetime.now()
TIMESTAMP = NOW.strftime("%Y-%m-%d %H:%M")
_PROCESS_LOCK_FD = None
_PROCESS_LOCK_CLEANED = False


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


def _cleanup_process_lock():
    global _PROCESS_LOCK_FD, _PROCESS_LOCK_CLEANED

    if _PROCESS_LOCK_CLEANED:
        return
    _PROCESS_LOCK_CLEANED = True
    try:
        if _PROCESS_LOCK_FD is not None:
            fcntl.flock(_PROCESS_LOCK_FD, fcntl.LOCK_UN)
            _PROCESS_LOCK_FD.close()
    except Exception:
        pass
    finally:
        _PROCESS_LOCK_FD = None
        try:
            PROCESS_LOCK.unlink(missing_ok=True)
        except Exception:
            pass


def _handle_shutdown_signal(signum, _frame):
    log(f"Received shutdown signal {signum}; cleaning sleep process lock.")
    _cleanup_process_lock()
    raise SystemExit(128 + signum)


def _register_process_lock_cleanup(lock_fd):
    global _PROCESS_LOCK_FD, _PROCESS_LOCK_CLEANED

    _PROCESS_LOCK_FD = lock_fd
    _PROCESS_LOCK_CLEANED = False
    atexit.register(_cleanup_process_lock)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)


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


def write_sleep_health(
    run_log: dict,
    state: dict,
    *,
    status: str,
    error: str = "",
    actions: dict | None = None,
) -> dict:
    """Write a small machine-readable health file for startup/briefings."""
    stage_b = run_log.get("stage_b") if isinstance(run_log, dict) else {}
    if not isinstance(stage_b, dict):
        stage_b = {}
    coverage = {}
    if isinstance(actions, dict):
        coverage = actions.get("coverage") or {}
    if not coverage and isinstance(stage_b, dict):
        coverage = stage_b.get("coverage") or {}

    health = {
        "date": str(TODAY),
        "status": status,
        "generated_at": datetime.now().isoformat(),
        "error": error,
        "learnings_total": len(state.get("learnings", []) if isinstance(state, dict) else []),
        "memory_md_lines": state.get("memory_md_lines", 0) if isinstance(state, dict) else 0,
        "old_observations": state.get("claude_mem_old", 0) if isinstance(state, dict) else 0,
        "stage_a": run_log.get("stage_a") if isinstance(run_log, dict) else None,
        "stage_b": stage_b,
        "coverage": coverage,
        "last_run_marked_complete": status == "ok",
    }
    save_json(SLEEP_HEALTH_FILE, health)
    return health


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
        "a9_deep_sleep_deleted": 0,
        "a9_deep_sleep_freed_bytes": 0,
        "a9_deep_sleep_logs_rotated": 0,
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

    # A9: Bound Deep Sleep operational artifacts and logs. This is safe to run
    # daily: incomplete/unanalysed contexts are preserved for retry/debugging.
    try:
        deep_sleep_retention = prune_deep_sleep_runtime(nexo_home=NEXO_HOME, apply=True)
        stats["a9_deep_sleep_deleted"] = int(deep_sleep_retention.get("deleted_count") or 0)
        stats["a9_deep_sleep_freed_bytes"] = int(deep_sleep_retention.get("deleted_bytes") or 0)
        stats["a9_deep_sleep_logs_rotated"] = int(deep_sleep_retention.get("logs_rotated") or 0)
    except Exception as exc:
        stats["a9_deep_sleep_warning"] = exc.__class__.__name__

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


def write_learning_context(state: dict) -> dict:
    """Persist all learnings to files so Stage B never receives a truncated list."""
    learnings = state.get("learnings", []) if isinstance(state, dict) else []
    LEARNINGS_CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

    # Remove stale chunks for today's run before rewriting them.
    today_prefix = f"{TODAY}-chunk-"
    for old_chunk in LEARNINGS_CHUNKS_DIR.glob(f"{today_prefix}*.json"):
        try:
            old_chunk.unlink()
        except Exception:
            pass

    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 2
    for learning in learnings:
        rendered = json.dumps(learning, ensure_ascii=False, separators=(",", ":"))
        if current and current_chars + len(rendered) + 1 > LEARNING_CHUNK_MAX_CHARS:
            chunks.append(current)
            current = []
            current_chars = 2
        current.append(learning)
        current_chars += len(rendered) + 1
    if current or not chunks:
        chunks.append(current)

    chunk_files: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        chunk_path = LEARNINGS_CHUNKS_DIR / f"{TODAY}-chunk-{index:03d}.json"
        save_json(
            chunk_path,
            {
                "date": str(TODAY),
                "chunk_index": index,
                "chunk_count": len(chunks),
                "learnings_total_declared": len(learnings),
                "learnings_in_chunk": len(chunk),
                "learnings": chunk,
            },
        )
        chunk_files.append(str(chunk_path))

    coverage = {
        "learnings_visible_count": len(learnings),
        "learnings_total_declared": len(learnings),
        "coverage_pct": 100.0 if learnings or len(learnings) == 0 else 0.0,
        "source_file": str(LEARNINGS_DUMP_FILE),
        "chunk_count": len(chunk_files),
    }
    save_json(
        LEARNINGS_DUMP_FILE,
        {
            "date": str(TODAY),
            "generated_at": datetime.now().isoformat(),
            "coverage": coverage,
            "chunk_files": chunk_files,
            "learnings": learnings,
        },
    )
    return {
        "dump_file": str(LEARNINGS_DUMP_FILE),
        "chunk_files": chunk_files,
        "coverage": coverage,
    }


def validate_actions_coverage(actions: dict, state: dict) -> tuple[bool, str]:
    """Fail closed when Stage B did not inspect nearly all active learnings."""
    expected_total = len(state.get("learnings", []) if isinstance(state, dict) else [])
    if expected_total == 0:
        return True, "no active learnings"

    coverage = actions.get("coverage") if isinstance(actions, dict) else None
    if not isinstance(coverage, dict):
        return False, "sleep-actions.json is missing coverage metadata"

    try:
        visible_count = int(coverage.get("learnings_visible_count", 0) or 0)
    except Exception:
        visible_count = 0
    try:
        declared_total = int(coverage.get("learnings_total_declared", 0) or 0)
    except Exception:
        declared_total = 0
    try:
        coverage_pct = float(coverage.get("coverage_pct", 0.0) or 0.0)
    except Exception:
        coverage_pct = 0.0

    if declared_total != expected_total:
        return False, f"coverage declared {declared_total} learnings but state has {expected_total}"
    if visible_count < expected_total:
        return False, f"coverage only saw {visible_count}/{expected_total} learnings"
    if coverage_pct < MIN_LEARNING_COVERAGE_PCT:
        return False, f"coverage {coverage_pct:.1f}% is below {MIN_LEARNING_COVERAGE_PCT:.1f}%"
    return True, "coverage ok"


def dream(state: dict) -> dict:
    """The brain dreams — CLI does the intelligent work."""
    learning_context = write_learning_context(state)

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
{{
  "archive_ids": [1, 2, 3],
  "contradiction_pairs": [[4, 5]],
  "stale_ids": [6, 7],
  "coverage": {{
    "learnings_visible_count": {len(state['learnings'])},
    "learnings_total_declared": {len(state['learnings'])},
    "coverage_pct": 100.0,
    "source_file": "{learning_context['dump_file']}",
    "chunk_files_read": {json.dumps(learning_context['chunk_files'], ensure_ascii=False)}
  }}
}}

The wrapper will execute the actual DB operations based on this JSON.
The wrapper will fail closed if coverage is missing or below {MIN_LEARNING_COVERAGE_PCT:.0f}%.

LEARNINGS SOURCE:
Read the full learning list from {learning_context['dump_file']}.
If the file is too large for one read, read every chunk listed below and merge them by ID:
{json.dumps(learning_context['chunk_files'], ensure_ascii=False, indent=2)}

Do not infer duplicates/stale items from a partial list. If you cannot read at least
{MIN_LEARNING_COVERAGE_PCT:.0f}% of the declared learnings, write empty archive/stale arrays
and include coverage.blocking_reason explaining the read failure.""")

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

    prompt = render_core_prompt(
        "sleep",
        learnings_count=len(state["learnings"]),
        memory_md_lines=state["memory_md_lines"],
        preferences_count=len(state["preferences"]),
        feedback_count=state["feedback_count"],
        old_observations_count=state["claude_mem_old"],
        tasks_block=tasks_str,
        sleep_report_file=COORD_DIR / "sleep-report.md",
    )

    log("Stage B: Invoking automation backend — dreaming...")
    try:
        result = run_automation_prompt(
            prompt,
            caller="sleep/nightly",
            timeout=AUTOMATION_SUBPROCESS_TIMEOUT,
            output_format="text",
            allowed_tools="Read,Write,Edit,Glob,Grep,Bash,mcp__nexo__*",
        )

        if result.returncode != 0:
            log(f"Stage B: CLI error ({result.returncode}): {(result.stderr or '')[:300]}")
            return {"error": result.returncode, "learning_context": learning_context}

        log(f"Stage B: Dreaming complete. Output: {len(result.stdout or '')} chars")
        return {
            "ok": True,
            "output_len": len(result.stdout or ""),
            "learning_context": learning_context,
            "coverage": learning_context["coverage"],
        }

    except AutomationBackendUnavailableError as e:
        log(f"Stage B: automation backend unavailable: {e}")
        return {"error": "backend-unavailable", "learning_context": learning_context}
    except subprocess.TimeoutExpired:
        log(f"Stage B: CLI timed out ({AUTOMATION_SUBPROCESS_TIMEOUT}s)")
        return {"error": "timeout", "learning_context": learning_context}
    except Exception as e:
        log(f"Stage B: Exception: {e}")
        return {"error": str(e), "learning_context": learning_context}


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
        _register_process_lock_cleanup(lock_fd)
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
                   "stage_a": None, "stage_b": None, "completed": None,
                   "marked_complete": False}
        sleep_had_errors = False
        sleep_error = ""
        actions = None

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
                sleep_error = str(dream_result["error"])
            else:
                # Stage B2: Execute actions from CLI output
                actions_file = COORD_DIR / "sleep-actions.json"
                if actions_file.exists():
                    try:
                        actions = json.loads(actions_file.read_text())
                        coverage_ok, coverage_reason = validate_actions_coverage(actions, state)
                        run_log["stage_b"]["actions_file"] = str(actions_file)
                        run_log["stage_b"]["coverage_ok"] = coverage_ok
                        run_log["stage_b"]["coverage_reason"] = coverage_reason
                        if not coverage_ok:
                            log(f"Stage B2: Refusing dream actions: {coverage_reason}")
                            sleep_had_errors = True
                            sleep_error = coverage_reason
                        else:
                            execute_dream_actions(actions, state)
                    except Exception as e:
                        log(f"Stage B2: Error executing actions: {e}")
                        sleep_had_errors = True
                        sleep_error = str(e)
                else:
                    sleep_had_errors = True
                    sleep_error = f"missing actions file: {actions_file}"
                    run_log["stage_b"]["error"] = sleep_error
                    log(f"Stage B2: {sleep_error}")
        else:
            log("Brain is clean -- no dreaming needed.")
            run_log["stage_b"] = {"skipped": True}

        # Done
        run_log["completed"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        if sleep_had_errors:
            run_log["status"] = "failed"
            write_sleep_health(run_log, state, status="failed", error=sleep_error, actions=actions)
            append_sleep_log(run_log)
            log("NEXO Sleep v2 failed during Stage B; not marking today complete so the next trigger retries.")
            sys.exit(1)

        mark_complete()
        run_log["marked_complete"] = True
        run_log["status"] = "ok"
        write_sleep_health(run_log, state, status="ok", actions=actions)
        append_sleep_log(run_log)
        log(f"NEXO Sleep v2 complete at {run_log['completed']}")

        # Register for catch-up only if all stages succeeded
        if not sleep_had_errors:
            try:
                state_file = paths.operations_dir() / ".catchup-state.json"
                st = json.loads(state_file.read_text()) if state_file.exists() else {}
                st["sleep"] = datetime.now().isoformat()
                state_file.write_text(json.dumps(st, indent=2))
            except Exception:
                pass

    finally:
        _cleanup_process_lock()


if __name__ == "__main__":
    main()
