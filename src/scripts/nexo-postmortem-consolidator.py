#!/usr/bin/env python3
"""
NEXO Post-Mortem Consolidator v2 — The brain consolidates memories.

Before: 595 lines of word-overlap at 50% to detect "patterns".
Now: Collects data, passes them to CLI which UNDERSTANDS what it reads.

Runs daily at 23:30 via LaunchAgent. Reads session diaries from today,
passes them to Claude CLI (opus) which decides what deserves permanent memory.

Stage 1 — Data collection (Pure Python):
  Query session diaries, existing feedbacks, history.

Stage 2 — Intelligence (Claude CLI opus):
  Read diaries, understand patterns, decide what to promote.

Stage 3 — Sensory Register + Force analysis (Pure Python):
  Process cognitive events. Kept from v1 — genuinely mechanical.
"""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

HOME = Path.home()
NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(HOME / ".nexo")))

# Add NEXO_HOME to path for cognitive engine (Stage 3)
# Auto-detect: if running from repo (src/scripts/), use src/ as NEXO_CODE
_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent  # src/scripts/ -> src/
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(_repo_src) if (_repo_src / "server.py").exists() else str(NEXO_HOME)))
sys.path.insert(0, str(NEXO_CODE))

NEXO_DB = NEXO_HOME / "data" / "nexo.db"
# Memory directory — adjust to match your project's memory location
MEMORY_DIR = NEXO_HOME / "memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"
HISTORY_FILE = NEXO_HOME / "coordination" / "postmortem-history.json"
CONSOLIDATION_LOG = NEXO_HOME / "logs" / "postmortem-consolidation.log"
CLAUDE_CLI = HOME / ".local" / "bin" / "claude"
SESSION_BUFFER = NEXO_HOME / "brain" / "session_buffer.jsonl"

TODAY = date.today()
TODAY_STR = TODAY.isoformat()


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    CONSOLIDATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(CONSOLIDATION_LOG, "a") as f:
        f.write(line + "\n")


# ─── Stage 1: Data Collection (Pure Python) ─────────────────────────────────

def collect_data() -> dict:
    """Collects all data the CLI will need to decide."""
    data = {
        "date": TODAY_STR,
        "diaries": [],
        "existing_feedbacks": [],
        "history_summary": {},
    }

    if not NEXO_DB.exists():
        return data

    conn = sqlite3.connect(str(NEXO_DB))
    conn.row_factory = sqlite3.Row

    # Today's diaries with self-critique
    rows = conn.execute(
        "SELECT id, session_id, summary, self_critique, user_signals, "
        "mental_state, domain, created_at "
        "FROM session_diary WHERE date(created_at) = ? ORDER BY created_at",
        (TODAY_STR,)
    ).fetchall()
    data["diaries"] = [dict(r) for r in rows]

    conn.close()

    # Existing postmortem feedbacks (nombres, para no duplicar)
    data["existing_feedbacks"] = [
        f.stem for f in MEMORY_DIR.glob("feedback_postmortem_*.md")
    ]

    # History summary
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text())
            data["history_summary"] = {
                "total_permanent_rules": len(history.get("permanent_rules", [])),
                "days_tracked": len(history.get("days", {})),
                "recent_rules": history.get("permanent_rules", [])[-10:],
            }
        except Exception:
            pass

    return data


# ─── Stage 2: Intelligence (Claude CLI opus) ────────────────────────────────

def consolidate_with_cli(data: dict) -> bool:
    """The brain consolidates — CLI decides what to promote."""

    diaries_with_critique = [
        d for d in data["diaries"]
        if d.get("self_critique") and not str(d["self_critique"]).strip().lower().startswith("no self-critique")
    ]

    if not diaries_with_critique:
        log("All sessions clean or trivial. Nothing to consolidate.")
        return True

    # Prepare data for CLI (truncate to avoid exceeding context)
    diaries_json = json.dumps(diaries_with_critique, ensure_ascii=False, indent=1)
    if len(diaries_json) > 12000:
        diaries_json = diaries_json[:12000] + "\n... (truncated)"

    prompt = f"""FIRST: Call nexo_startup(task='nightly postmortem consolidation') to register this session.

You are NEXO's nightly consolidator. Your job is to review the self-critiques
from today and decide which deserve to become permanent rules. Use nexo_learning_add for permanent rules and nexo_followup_create for action items.

DATE: {data['date']}
SESSIONS TODAY: {len(data['diaries'])} total, {len(diaries_with_critique)} with self-critique

DIARIES WITH SELF-CRITIQUE:
{diaries_json}

EXISTING POSTMORTEM FEEDBACKS ({len(data['existing_feedbacks'])}):
{json.dumps(data['existing_feedbacks'][:30], ensure_ascii=False)}

RECENT PERMANENT RULES:
{json.dumps(data['history_summary'].get('recent_rules', []), ensure_ascii=False)}

INSTRUCTIONS:

1. Read each self_critique and understand its MEANING (don't count words).

2. PROMOTE to permanent feedback ONLY IF:
   - A pattern appears in 2+ different sessions of the day (by meaning, not literal text)
   - Or the user explicitly corrected (user_signals contains correction)
   - And the self-critique contains a CONCRETE ACTION that prevents a future error
   - And a similar feedback does NOT already exist in the existing ones

3. DO NOT promote if:
   - It's a negative response ("Nothing happened", "clean session")
   - It's generic without concrete action
   - A feedback covering the same topic already exists

4. For each rule to promote, create the file with Write en {MEMORY_DIR}/:
   Nombre: feedback_postmortem_[slug_descriptivo].md
   Formato:
   ---
   name: [descriptive title]
   description: Behavioral rule extracted from self-critique — recurring pattern
   type: feedback
   ---

   [Clear description of the pattern and rule]

   **Why:** [Why this matters — with evidence from sessions]
   **How to apply:** [When and how to apply this rule]

5. Write the daily summary en $NEXO_HOME/coordination/postmortem-daily.md:
   # Post-Mortem Daily — {data['date']}
   Sessions: X | Self-critiques: Y | Promoted: Z

   ## Today's self-critiques (summary)
   [Lista breve]

   ## Promoted to permanent memory
   [What you promoted and why]

   ## Discarded (and why)
   [What you did NOT promote and the reason]

Execute without asking."""

    log(f"Stage 2: Invoking Claude CLI (opus) with {len(diaries_with_critique)} critiques...")
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE", None)

    try:
        result = subprocess.run(
            [str(CLAUDE_CLI), "-p", prompt, "--model", "opus",
             "--output-format", "text",
             "--allowedTools", "Read,Write,Edit,Glob,Grep,Bash,mcp__nexo__*"],
            capture_output=True, text=True, timeout=10800, env=env
        )

        if result.returncode != 0:
            log(f"Stage 2: CLI error (code {result.returncode}): {(result.stderr or '')[:300]}")
            return False

        log(f"Stage 2: Completed. Output: {len(result.stdout or '')} chars")
        # Log last 500 chars of output for debugging
        if result.stdout:
            log(f"Stage 2 output tail: {result.stdout[-500:]}")
        return True

    except subprocess.TimeoutExpired:
        log("Stage 2: CLI timed out (300s)")
        return False
    except Exception as e:
        log(f"Stage 2: Exception: {e}")
        return False


# ─── Stage 3: Sensory Register + Force Analysis (Pure Python) ───────────────
# Kept from v1 — these are genuinely mechanical (embedding vectors, DB updates)

def process_sensory_register():
    """Sensory Register — Atkinson-Shiffrin Layer 1. Embeds events into STM."""
    log("--- Sensory Register processing ---")

    if not SESSION_BUFFER.exists():
        log("  No session_buffer.jsonl found, skipping")
        return

    today_events = []
    try:
        with open(SESSION_BUFFER) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if event.get("ts", "").startswith(TODAY_STR):
                        today_events.append(event)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log(f"  Error reading session_buffer: {e}")
        return

    if not today_events:
        log("  No events from today")
        return

    log(f"  Found {len(today_events)} events")

    try:
        import cognitive
    except ImportError as e:
        log(f"  Cannot import cognitive: {e}")
        return

    ingested = 0
    for event in today_events:
        source = event.get("source", "")
        if source == "hook-fallback":
            task_str = " ".join(event.get("tasks", []))
            if len(task_str) < 50 or "," in task_str:
                continue

        parts = []
        for key, label in [("tasks", "Tasks"), ("decisions", "Decisions"),
                           ("errors_resolved", "Errors"), ("user_patterns", "the user")]:
            val = event.get(key, [])
            if val:
                parts.append(f"{label}: {'; '.join(str(v) for v in val[:3])}")

        critique = event.get("self_critique", "")
        if critique and "hook-fallback" not in critique:
            parts.append(f"Self-critique: {critique[:200]}")

        content = " | ".join(parts)
        if not content or len(content) < 20:
            continue

        try:
            vec = cognitive.embed(content)
            domain = ""
            lower = content.lower()
            # Add your project keywords for domain detection
            for keyword, dom in [("nexo", "nexo")]:
                if keyword in lower:
                    domain = dom
                    break

            cognitive.ingest_sensory(
                content=content, source_id=f"buffer#{event.get('ts', '')}",
                domain=domain, created_at=event.get("ts", "")
            )
            ingested += 1
        except Exception as e:
            log(f"  Error embedding: {e}")

    log(f"  Ingested {ingested} sensory events into STM")


def analyze_force_events():
    """Analyze --force dissonance resolutions from today."""
    log("--- Force event analysis ---")

    try:
        import cognitive
    except ImportError:
        log("  Cannot import cognitive, skipping")
        return

    db = cognitive._get_db()
    today_forces = db.execute(
        """SELECT memory_id, context, created_at FROM memory_corrections
           WHERE correction_type = 'exception' AND context LIKE '%[FORCE]%'
             AND date(created_at) = ? ORDER BY created_at""",
        (TODAY_STR,)
    ).fetchall()

    if not today_forces:
        log("  No --force events today")
        return

    log(f"  {len(today_forces)} --force events")

    from collections import Counter
    memory_counts = Counter(r["memory_id"] for r in today_forces)
    for mem_id, count in memory_counts.most_common():
        mem = db.execute(
            "SELECT content, strength FROM ltm_memories WHERE id = ?", (mem_id,)
        ).fetchone()
        if not mem:
            continue

        total = db.execute(
            "SELECT COUNT(*) FROM memory_corrections WHERE memory_id = ? AND context LIKE '%[FORCE]%'",
            (mem_id,)
        ).fetchone()[0]

        if total >= 3:
            log(f"  PARADIGM SHIFT: LTM #{mem_id} overridden {total}x → decay to 0.3")
            db.execute(
                "UPDATE ltm_memories SET strength = 0.3, "
                "tags = CASE WHEN tags LIKE '%paradigm_candidate%' THEN tags "
                "ELSE tags || ',paradigm_candidate' END WHERE id = ?",
                (mem_id,)
            )
        elif count >= 2:
            log(f"  WATCH: LTM #{mem_id} overridden {count}x today")

    db.commit()


# ─── Main ────────────────────────────────────────────────────────────────────

def already_ran_today() -> bool:
    """Prevent running twice on the same day."""
    marker = NEXO_HOME / "coordination" / "postmortem-last-run"
    if marker.exists():
        try:
            return marker.read_text().strip() == TODAY_STR
        except Exception:
            return False
    return False


def mark_done():
    marker = NEXO_HOME / "coordination" / "postmortem-last-run"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(TODAY_STR)


def main():
    if already_ran_today():
        log("Already ran today. Skipping.")
        return

    log("=== NEXO Post-Mortem Consolidator v2 starting ===")

    # Stage 1: Collect data
    data = collect_data()
    log(f"Stage 1: {len(data['diaries'])} diaries, {len(data['existing_feedbacks'])} existing feedbacks")

    if not data["diaries"]:
        log("No session diaries today. Nothing to consolidate.")
    else:
        # Stage 2: CLI intelligence
        success = consolidate_with_cli(data)
        if not success:
            log("Stage 2 failed — falling back to skip (no v1 fallback)")

    # Stage 3: Sensory Register (mechanical, kept from v1)
    try:
        process_sensory_register()
    except Exception as e:
        log(f"Sensory register failed: {e}")

    # Stage 3b: Force analysis (mechanical, kept from v1)
    try:
        analyze_force_events()
    except Exception as e:
        log(f"Force analysis failed: {e}")

    # Register successful run
    try:
        state_file = NEXO_HOME / "operations" / ".catchup-state.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        state["postmortem"] = datetime.now().isoformat()
        state_file.write_text(json.dumps(state, indent=2))
    except Exception:
        pass

    mark_done()
    log("=== Consolidation v2 complete ===")


if __name__ == "__main__":
    main()
