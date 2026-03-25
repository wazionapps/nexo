#!/usr/bin/env python3
"""
NEXO Sleep System — Daily memory cleanup and pruning.

Triggered hourly via LaunchAgent. Runs ONCE per day, first time the Mac is awake.
If interrupted (power loss, crash), resumes on next trigger.

Stage A — Mechanical cleanup (Python pure, always runs):
  A1: Delete daily_summaries >90 days
  A2: Delete session_archive >30 days
  A3: Rotate coordination stdout logs >5MB
  A4: Delete compressed_memories/week_*.md >180 days
  A5: Trim heartbeat-log.json to 200 entries
  A6: Trim reflection-log.json to 60 entries
  A7: Delete daemon/logs/ dirs >14 days

Stage C — Learning Consolidation (Python pure, always runs):
  C1: Duplicate detection (>80% word overlap in titles)
  C2: Age distribution of learnings
  C3: Category health (counts, hottest last 7d, categories >20)
  C4: Contradiction detection (NUNCA pairs in same category)

Stage B — Intelligent pruning (Claude CLI, conditional):
  Only activates if MEMORY.md >170 lines, nexo.db preferences table has >5 rows,
  or claude-mem.db has >500 observations >60 days.
  Uses Claude CLI (sonnet) to compress and prune.

Zero external dependencies beyond stdlib + sqlite3. Claude CLI for Stage B only.
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

# ─── Paths ────────────────────────────────────────────────────────────────────
CLAUDE_DIR = Path.home() / "claude"
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

NEXO_HOME = os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))
MEMORY_MD = Path.home() / ".claude" / "projects" / f"-Users-{os.environ.get('USER', 'user')}" / "memory" / "MEMORY.md"
NEXO_DB = Path(NEXO_HOME) / "nexo.db"
CLAUDE_MEM_DB = Path.home() / ".claude-mem" / "claude-mem.db"
CLAUDE_CLI = Path.home() / ".local" / "bin" / "claude"

LAST_RUN_FILE = COORD_DIR / "sleep-last-run"
LOCK_FILE = COORD_DIR / "sleep.lock"
PROCESS_LOCK = COORD_DIR / "sleep-process.lock"

TODAY = date.today()
NOW = datetime.now()
TIMESTAMP = NOW.strftime("%Y-%m-%d %H:%M")


# ─── Run-once & resume logic ────────────────────────────────────────────────

def already_ran_today() -> bool:
    """Check if sleep already completed today."""
    if not LAST_RUN_FILE.exists():
        return False
    try:
        last_date = LAST_RUN_FILE.read_text().strip()
        return last_date == str(TODAY)
    except Exception:
        return False


def was_interrupted() -> bool:
    """Check if a previous run was interrupted (lock file exists with dead PID)."""
    if not LOCK_FILE.exists():
        return False
    try:
        lock_data = json.loads(LOCK_FILE.read_text())
        lock_date = lock_data.get("date", "")
        if lock_date != str(TODAY):
            LOCK_FILE.unlink()
            return False

        lock_pid = lock_data.get("pid")
        if lock_pid:
            try:
                os.kill(lock_pid, 0)
                log(f"Another instance running (PID {lock_pid}). Exiting.")
                return False
            except ProcessLookupError:
                log(f"Interrupted run detected (phase: {lock_data.get('phase', '?')}, dead PID {lock_pid}). Resuming.")
                return True
            except PermissionError:
                return False
        else:
            LOCK_FILE.unlink()
            return False
    except Exception:
        LOCK_FILE.unlink(missing_ok=True)
        return False


def get_interrupted_phase() -> str:
    """Get which phase was interrupted."""
    try:
        lock_data = json.loads(LOCK_FILE.read_text())
        return lock_data.get("phase", "stage_a")
    except Exception:
        return "stage_a"


def set_lock(phase: str):
    """Set lock file indicating current phase with PID for race detection."""
    save_json(LOCK_FILE, {
        "date": str(TODAY),
        "phase": phase,
        "started": TIMESTAMP,
        "pid": os.getpid()
    })


def mark_complete():
    """Mark today's run as complete."""
    LAST_RUN_FILE.write_text(str(TODAY))
    LOCK_FILE.unlink(missing_ok=True)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def log(msg: str):
    print(f"[{TIMESTAMP}] {msg}")


def load_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text())
    except Exception as e:
        log(f"WARN: Failed to load {path}: {e}")
        return default if default is not None else {}


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def parse_date_from_stem(stem: str) -> date | None:
    """Extract YYYY-MM-DD date from a filename stem."""
    m = re.search(r'(\d{4}-\d{2}-\d{2})', stem)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            return None
    return None


def append_sleep_log(entry: dict):
    """Append entry to sleep-log.json, keeping last 90 entries."""
    entries = load_json(SLEEP_LOG, [])
    if not isinstance(entries, list):
        entries = []
    entries.append(entry)
    # Keep last 90
    if len(entries) > 90:
        entries = entries[-90:]
    save_json(SLEEP_LOG, entries)


# ─── Stage A: Mechanical cleanup ─────────────────────────────────────────────

def stage_a_cleanup() -> dict:
    """
    Pure Python cleanup. No LLM calls.
    Returns stats dict with counts per sub-task.
    """
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
                    log(f"A1: Deleted {f.name} (>{90}d)")
                except Exception as e:
                    log(f"A1: WARN: Could not delete {f.name}: {e}")

    # A2: Delete session_archive/*.jsonl >30 days
    cutoff_30 = TODAY - timedelta(days=30)
    if SESSION_ARCHIVE_DIR.exists():
        for f in SESSION_ARCHIVE_DIR.glob("*.jsonl"):
            d = parse_date_from_stem(f.stem)
            if d and d < cutoff_30:
                try:
                    f.unlink()
                    stats["a2_session_archives_deleted"] += 1
                    log(f"A2: Deleted {f.name} (>{30}d)")
                except Exception as e:
                    log(f"A2: WARN: Could not delete {f.name}: {e}")

    # A3: Rotate coordination/*-stdout.log if >5MB (keep last 500 lines)
    if COORD_DIR.exists():
        for f in COORD_DIR.glob("*-stdout.log"):
            try:
                if f.stat().st_size > 5 * 1024 * 1024:  # >5MB
                    lines = f.read_text().splitlines()
                    keep = lines[-500:] if len(lines) > 500 else lines
                    f.write_text("\n".join(keep) + "\n")
                    stats["a3_logs_rotated"] += 1
                    log(f"A3: Rotated {f.name} ({len(lines)}→{len(keep)} lines)")
            except Exception as e:
                log(f"A3: WARN: Could not rotate {f.name}: {e}")

    # A4: Delete compressed_memories/week_*.md >180 days
    cutoff_180 = TODAY - timedelta(days=180)
    if COMPRESSED_MEMORIES_DIR.exists():
        for f in COMPRESSED_MEMORIES_DIR.glob("week_*.md"):
            d = parse_date_from_stem(f.stem)
            if d and d < cutoff_180:
                try:
                    f.unlink()
                    stats["a4_compressed_memories_deleted"] += 1
                    log(f"A4: Deleted {f.name} (>{180}d)")
                except Exception as e:
                    log(f"A4: WARN: Could not delete {f.name}: {e}")

    # A5: Trim heartbeat-log.json to 200 entries
    if HEARTBEAT_LOG.exists():
        try:
            data = load_json(HEARTBEAT_LOG, [])
            if isinstance(data, list) and len(data) > 200:
                before = len(data)
                data = data[-200:]
                save_json(HEARTBEAT_LOG, data)
                stats["a5_heartbeat_trimmed"] = True
                log(f"A5: Trimmed heartbeat-log.json {before}→200 entries")
        except Exception as e:
            log(f"A5: WARN: {e}")

    # A6: Trim reflection-log.json to 60 entries
    if REFLECTION_LOG.exists():
        try:
            data = load_json(REFLECTION_LOG, [])
            if isinstance(data, list) and len(data) > 60:
                before = len(data)
                data = data[-60:]
                save_json(REFLECTION_LOG, data)
                stats["a6_reflection_trimmed"] = True
                log(f"A6: Trimmed reflection-log.json {before}→60 entries")
        except Exception as e:
            log(f"A6: WARN: {e}")

    # A7: Delete daemon/logs/ dirs >14 days (subdirs named YYYY-MM-DD)
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
                    log(f"A7: Deleted daemon/logs/{d_path.name}/ (>{14}d)")
                except Exception as e:
                    log(f"A7: WARN: Could not delete {d_path.name}: {e}")

    # A8: Delete cortex/logs/*.log >7 days, truncate launchd logs >5MB
    cutoff_7 = TODAY - timedelta(days=7)
    cortex_logs = Path.home() / "claude" / "cortex" / "logs"
    if cortex_logs.exists():
        for f in cortex_logs.glob("*.log"):
            if f.name.startswith("launchd-"):
                try:
                    if f.stat().st_size > 5 * 1024 * 1024:
                        lines = f.read_text().splitlines()
                        keep = lines[-500:] if len(lines) > 500 else lines
                        f.write_text("\n".join(keep) + "\n")
                        stats["a3_logs_rotated"] += 1
                        log(f"A8: Truncated cortex {f.name}")
                except Exception as e:
                    log(f"A8: WARN: {e}")
                continue
            d = parse_date_from_stem(f.stem)
            if d and d < cutoff_7:
                try:
                    f.unlink()
                    log(f"A8: Deleted cortex log {f.name} (>7d)")
                except Exception as e:
                    log(f"A8: WARN: Could not delete {f.name}: {e}")

    return stats


# ─── Stage C: Learning Consolidation ─────────────────────────────────────────

STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas",
    "de", "del", "al", "en", "y", "o", "a", "con", "por", "para",
    "que", "es", "se", "no", "si", "lo", "le", "su", "sus",
    "the", "a", "an", "of", "in", "and", "or", "to", "for", "is",
    "it", "on", "at", "by", "from", "with", "not", "be", "as",
    "this", "that", "are", "was", "were",
}


def _title_words(title: str) -> set:
    """Lowercase, tokenize, remove stopwords from a title."""
    words = re.findall(r'[a-záéíóúüñA-ZÁÉÍÓÚÜÑ\w]+', title.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def _word_overlap(words_a: set, words_b: set) -> float:
    """Jaccard-like overlap: intersection / union."""
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def stage_c_learning_consolidation() -> dict:
    """
    Pure Python analysis of the learnings table in nexo.db.
    Reads only — no deletions.
    Returns stats dict stored under run_log['stage_c'].
    """
    stats = {
        "total_learnings": 0,
        "potential_duplicates": [],       # max 10
        "age_distribution": {"<7d": 0, "7-30d": 0, "30-90d": 0, ">90d": 0},
        "category_counts": {},
        "hottest_category_7d": None,
        "categories_over_20": [],
        "potential_contradictions": [],   # max 5
    }

    if not NEXO_DB.exists():
        log("Stage C: nexo.db not found, skipping.")
        return stats

    try:
        conn = sqlite3.connect(str(NEXO_DB))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='learnings'"
        )
        if not cursor.fetchone():
            log("Stage C: learnings table not found, skipping.")
            conn.close()
            return stats

        cursor.execute(
            "SELECT id, title, content, category, created_at FROM learnings ORDER BY id"
        )
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        log(f"Stage C: DB error: {e}")
        return stats

    if not rows:
        log("Stage C: No learnings found.")
        return stats

    stats["total_learnings"] = len(rows)
    now_dt = datetime.now()
    cutoff_7 = now_dt - timedelta(days=7)
    cutoff_30 = now_dt - timedelta(days=30)
    cutoff_90 = now_dt - timedelta(days=90)

    # Pre-compute per-row data
    parsed = []
    category_7d_counts: dict[str, int] = {}

    for row in rows:
        # Parse created_at (stored as epoch float or ISO string)
        created_dt = None
        raw_ts = row["created_at"]
        if raw_ts:
            # Try epoch first (nexo.db uses epoch floats)
            try:
                ts_float = float(raw_ts)
                if ts_float > 1_000_000_000:  # reasonable epoch
                    created_dt = datetime.fromtimestamp(ts_float)
            except (ValueError, TypeError, OSError):
                pass
            # Fallback to ISO string formats
            if created_dt is None:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                    try:
                        created_dt = datetime.strptime(str(raw_ts)[:19], fmt)
                        break
                    except ValueError:
                        continue

        words = _title_words(row["title"] or "")
        cat = (row["category"] or "uncategorized").strip()

        parsed.append({
            "id": row["id"],
            "title": row["title"] or "",
            "words": words,
            "category": cat,
            "created_dt": created_dt,
        })

        # C2: age distribution
        if created_dt:
            if created_dt >= cutoff_7:
                stats["age_distribution"]["<7d"] += 1
                category_7d_counts[cat] = category_7d_counts.get(cat, 0) + 1
            elif created_dt >= cutoff_30:
                stats["age_distribution"]["7-30d"] += 1
            elif created_dt >= cutoff_90:
                stats["age_distribution"]["30-90d"] += 1
            else:
                stats["age_distribution"][">90d"] += 1
        else:
            # Unknown age → bucket as >90d
            stats["age_distribution"][">90d"] += 1

        # C3: category counts
        stats["category_counts"][cat] = stats["category_counts"].get(cat, 0) + 1

    # C3: hottest category last 7d + categories over 20
    if category_7d_counts:
        stats["hottest_category_7d"] = max(category_7d_counts, key=lambda k: category_7d_counts[k])
    stats["categories_over_20"] = [
        cat for cat, cnt in stats["category_counts"].items() if cnt > 20
    ]

    # C1: Duplicate detection — O(n²) but learnings table is small
    duplicates = []
    for i in range(len(parsed)):
        if len(duplicates) >= 10:
            break
        for j in range(i + 1, len(parsed)):
            if len(duplicates) >= 10:
                break
            overlap = _word_overlap(parsed[i]["words"], parsed[j]["words"])
            if overlap >= 0.80:
                duplicates.append({
                    "id1": parsed[i]["id"],
                    "id2": parsed[j]["id"],
                    "title1": parsed[i]["title"],
                    "title2": parsed[j]["title"],
                    "overlap": round(overlap, 2),
                })
    stats["potential_duplicates"] = duplicates

    # C4: Contradiction detection — NUNCA pairs in same category
    nunca_entries = [p for p in parsed if "nunca" in p["title"].lower()]
    contradictions = []
    for nunca in nunca_entries:
        if len(contradictions) >= 5:
            break
        # Look for same-category entries that don't contain NUNCA
        # and whose remaining words overlap significantly (same subject, opposite stance)
        nunca_words_no_nunca = nunca["words"] - {"nunca"}
        for other in parsed:
            if len(contradictions) >= 5:
                break
            if other["id"] == nunca["id"]:
                continue
            if other["category"] != nunca["category"]:
                continue
            if "nunca" in other["title"].lower():
                continue
            # Check if they share meaningful subject words
            overlap = _word_overlap(nunca_words_no_nunca, other["words"])
            if overlap >= 0.50:
                contradictions.append({
                    "id1": nunca["id"],
                    "id2": other["id"],
                    "title1": nunca["title"],
                    "title2": other["title"],
                })
    stats["potential_contradictions"] = contradictions

    log(f"Stage C: {stats['total_learnings']} learnings analyzed. "
        f"Potential duplicates: {len(duplicates)}. "
        f"Categories over 20: {len(stats['categories_over_20'])}. "
        f"Potential contradictions: {len(contradictions)}.")
    if stats["hottest_category_7d"]:
        log(f"Stage C: Hottest category last 7d: {stats['hottest_category_7d']} "
            f"({category_7d_counts.get(stats['hottest_category_7d'], 0)} new).")

    return stats


# ─── Stage B: Intelligent pruning (Claude CLI) ──────────────────────────────

def check_stage_b_conditions() -> dict:
    """
    Check if Stage B should activate.
    Returns dict with condition results and whether to trigger.
    """
    conditions = {
        "memory_md_lines": 0,
        "memory_md_over_limit": False,
        "preferences_auto_sections": 0,
        "preferences_over_limit": False,
        "claude_mem_old_observations": 0,
        "claude_mem_over_limit": False,
        "should_trigger": False,
    }

    # Check MEMORY.md line count
    if MEMORY_MD.exists():
        try:
            lines = MEMORY_MD.read_text().splitlines()
            conditions["memory_md_lines"] = len(lines)
            conditions["memory_md_over_limit"] = len(lines) > 170
        except Exception as e:
            log(f"Stage B check: WARN reading MEMORY.md: {e}")

    # Check preferences count in SQLite
    if NEXO_DB.exists():
        try:
            conn = sqlite3.connect(str(NEXO_DB))
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM preferences")
            count = cursor.fetchone()[0]
            conn.close()
            conditions["preferences_auto_sections"] = count
            conditions["preferences_over_limit"] = count > 5
        except Exception as e:
            log(f"Stage B check: WARN reading nexo.db preferences: {e}")

    # Check claude-mem.db observations >60 days
    if CLAUDE_MEM_DB.exists():
        try:
            cutoff_epoch = int((datetime.now() - timedelta(days=60)).timestamp() * 1000)
            conn = sqlite3.connect(str(CLAUDE_MEM_DB))
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM observations WHERE created_at_epoch < ?",
                (cutoff_epoch,)
            )
            count = cursor.fetchone()[0]
            conn.close()
            conditions["claude_mem_old_observations"] = count
            conditions["claude_mem_over_limit"] = count > 500
        except Exception as e:
            log(f"Stage B check: WARN reading claude-mem.db: {e}")

    conditions["should_trigger"] = (
        conditions["memory_md_over_limit"]
        or conditions["preferences_over_limit"]
        or conditions["claude_mem_over_limit"]
    )

    return conditions


def build_stage_b_prompt(conditions: dict) -> str:
    """Build the prompt for Claude CLI based on which conditions triggered."""
    tasks = []

    if conditions["memory_md_over_limit"]:
        tasks.append(f"""TAREA 1: MEMORY.md ({conditions['memory_md_lines']} lineas, limite 200)
Archivo: {MEMORY_MD}
Lee con Read tool, comprime incidentes resueltos >21 dias, fusiona duplicados, mantener <180 lineas.
PRESERVA toda la estructura de secciones existente. No elimines secciones enteras.""")

    if conditions["preferences_over_limit"]:
        tasks.append(f"""TAREA 2: preferences en SQLite ({conditions['preferences_auto_sections']} registros)
DB: {NEXO_DB}, tabla: preferences (columnas: key, value, category, updated_at)
Conecta con sqlite3. Elimina preferencias duplicadas (mismo key) manteniendo la mas reciente.
Elimina preferencias con updated_at mas antiguo de 30 dias si hay un duplicado mas reciente.
Reporta cuantos registros eliminaste.""")

    if conditions["claude_mem_over_limit"]:
        tasks.append(f"""TAREA 3: claude-mem observations ({conditions['claude_mem_old_observations']} registros >60d)
DB: {CLAUDE_MEM_DB}
Conecta con sqlite3. Ejecuta:
  DELETE FROM observations WHERE created_at_epoch < {int((datetime.now() - timedelta(days=60)).timestamp() * 1000)}
    AND discovery_tokens < 300
    AND id NOT IN (SELECT id FROM observations WHERE
        title LIKE '%CRITICO%' OR title LIKE '%MAXIMA%'
        OR title LIKE '%credential%' OR title LIKE '%token%' OR title LIKE '%API%'
        OR narrative LIKE '%CRITICO%' OR narrative LIKE '%MAXIMA%')
  LIMIT 200;
Luego: DELETE FROM observations_fts WHERE rowid NOT IN (SELECT id FROM observations);
Luego: VACUUM;
Reporta cuantos registros eliminaste.""")

    if not tasks:
        return ""

    tasks_str = "\n\n".join(tasks)

    return f"""You are NEXO Sleep System. Your job is to PRUNE memory.
You are NOT interactive. Do NOT wait for input. Execute the following tasks and exit.

ABSOLUTE RULES:
- NEVER delete credentials, tokens, account IDs, API endpoints, keys, secrets.
- NEVER delete operational rules marked as "CRITICAL" or "HIGHEST PRIORITY".
- NEVER delete information about infrastructure (servers, repos, deploys).
- You CAN merge redundant sections.
- You CAN remove obsolete technical information (fixed >30 days ago and never referenced since).
- You CAN compress long paragraphs into concise bullets.
- Every line you remove must have a clear reason. When in doubt, do NOT delete.

{tasks_str}

Al terminar, imprime un resumen JSON con las acciones realizadas."""


def run_stage_b(conditions: dict) -> dict:
    """Run Stage B using Claude CLI."""
    prompt = build_stage_b_prompt(conditions)
    if not prompt:
        return {"skipped": True, "reason": "No tasks to run"}

    if not CLAUDE_CLI.exists():
        return {"error": f"Claude CLI not found at {CLAUDE_CLI}"}

    log("Stage B: Invoking Claude CLI (sonnet)...")

    try:
        env = os.environ.copy()
        # Remove env vars that would cause Claude CLI to think it's inside Claude Code
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE", None)

        result = subprocess.run(
            [str(CLAUDE_CLI), "-p", prompt, "--model", "sonnet"],
            capture_output=True,
            text=True,
            timeout=600,
            env=env
        )

        stdout = result.stdout.strip() if result.stdout else ""
        stderr = result.stderr.strip() if result.stderr else ""

        if result.returncode != 0:
            log(f"Stage B: Claude CLI returned code {result.returncode}")
            if stderr:
                log(f"Stage B: stderr: {stderr[:500]}")
            return {
                "returncode": result.returncode,
                "stderr": stderr[:500],
                "stdout": stdout[:500],
            }

        log(f"Stage B: Completed. Output length: {len(stdout)} chars")
        return {
            "returncode": 0,
            "output_length": len(stdout),
            "output_preview": stdout[:800],
        }

    except subprocess.TimeoutExpired:
        log("Stage B: Claude CLI timed out (600s)")
        return {"error": "timeout"}
    except Exception as e:
        log(f"Stage B: Exception: {e}")
        return {"error": str(e)}


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    log("=" * 60)
    log("NEXO Sleep System starting")

    # Process lock via fcntl to prevent concurrent instances
    try:
        lock_fd = open(PROCESS_LOCK, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
    except (IOError, OSError):
        log("Another sleep instance is already running. Exiting.")
        sys.exit(0)

    try:
        # Check if already completed today
        if already_ran_today():
            log("Already ran today. Exiting.")
            sys.exit(0)

        # Determine start phase (for resume after interruption)
        start_phase = "stage_a"
        if was_interrupted():
            start_phase = get_interrupted_phase()
            log(f"Resuming from phase: {start_phase}")

        run_log = {
            "date": str(TODAY),
            "started": TIMESTAMP,
            "stage_a": None,
            "stage_c": None,
            "stage_b_conditions": None,
            "stage_b": None,
            "completed": None,
        }

        # Stage A: Mechanical cleanup
        if start_phase in ("stage_a",):
            set_lock("stage_a")
            log("─── Stage A: Mechanical cleanup ───")
            stage_a_stats = stage_a_cleanup()
            run_log["stage_a"] = stage_a_stats

            total_cleaned = (
                stage_a_stats["a1_daily_summaries_deleted"]
                + stage_a_stats["a2_session_archives_deleted"]
                + stage_a_stats["a3_logs_rotated"]
                + stage_a_stats["a4_compressed_memories_deleted"]
                + stage_a_stats["a7_daemon_logs_deleted"]
            )
            log(f"Stage A complete: {total_cleaned} items cleaned, "
                f"heartbeat trimmed={stage_a_stats['a5_heartbeat_trimmed']}, "
                f"reflection trimmed={stage_a_stats['a6_reflection_trimmed']}")

        # Stage C: Learning Consolidation (always runs, pure Python)
        if start_phase in ("stage_a", "stage_c", "stage_b"):
            set_lock("stage_c")
            log("─── Stage C: Learning Consolidation ───")
            stage_c_stats = stage_c_learning_consolidation()
            run_log["stage_c"] = stage_c_stats

        # Stage B: Intelligent pruning (conditional)
        if start_phase in ("stage_a", "stage_c", "stage_b"):
            set_lock("stage_b")
            log("─── Stage B: Checking conditions ───")
            conditions = check_stage_b_conditions()
            run_log["stage_b_conditions"] = conditions

            log(f"  MEMORY.md: {conditions['memory_md_lines']} lines "
                f"(trigger={conditions['memory_md_over_limit']})")
            log(f"  nexo.db preferences: {conditions['preferences_auto_sections']} rows "
                f"(trigger={conditions['preferences_over_limit']})")
            log(f"  claude-mem old observations: {conditions['claude_mem_old_observations']} "
                f"(trigger={conditions['claude_mem_over_limit']})")

            if conditions["should_trigger"]:
                log("Stage B: Conditions met, running intelligent pruning...")
                stage_b_result = run_stage_b(conditions)
                run_log["stage_b"] = stage_b_result
            else:
                log("Stage B: No conditions met, skipping.")
                run_log["stage_b"] = {"skipped": True, "reason": "No conditions met"}

        # Mark complete
        run_log["completed"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        mark_complete()
        append_sleep_log(run_log)

        log(f"NEXO Sleep complete at {run_log['completed']}")

        # Register successful run for catch-up
        try:
            import json as _json
            _state_file = Path.home() / "claude" / "operations" / ".catchup-state.json"
            _state = _json.loads(_state_file.read_text()) if _state_file.exists() else {}
            _state["sleep"] = datetime.now().isoformat()
            _state_file.write_text(_json.dumps(_state, indent=2))
        except Exception:
            pass

        log("=" * 60)

    finally:
        # Release process lock
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
            PROCESS_LOCK.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
