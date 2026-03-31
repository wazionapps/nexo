"""NEXO Evolution Cycle — Self-improvement via Opus API.

Runs weekly after DMN. Analyzes patterns, proposes improvements.
v1: observe-only (all proposals logged as 'proposed' for the user to review).
v1.1 (future): sandbox execution of auto-approved changes.
"""

import json
import os
import shutil
import subprocess
import sqlite3
import time
from datetime import datetime, date, timedelta
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_DB = NEXO_HOME / "data" / "nexo.db"
CORTEX_DIR = Path(__file__).parent
CLAUDE_DIR = Path.home() / ".nexo"
SANDBOX_DIR = CLAUDE_DIR / "sandbox" / "workspace"
SNAPSHOTS_DIR = CLAUDE_DIR / "snapshots"
OBJECTIVE_FILE = CORTEX_DIR / "evolution-objective.json"
PROMPT_FILE = CORTEX_DIR / "evolution-prompt.md"
RESTORE_LOG = CLAUDE_DIR / "logs" / "snapshot-restores.log"

MAX_SNAPSHOTS = 8


def load_objective() -> dict:
    if OBJECTIVE_FILE.exists():
        return json.loads(OBJECTIVE_FILE.read_text())
    return {}


def save_objective(obj: dict):
    OBJECTIVE_FILE.write_text(json.dumps(obj, indent=2, ensure_ascii=False))


def get_week_data(db_path: str) -> dict:
    """Gather last 7 days of learnings, decisions, changes, diaries."""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    cutoff_epoch = time.time() - 7 * 86400
    cutoff_date = (date.today() - timedelta(days=7)).isoformat()

    data = {}

    rows = conn.execute(
        "SELECT category, title, content FROM learnings WHERE created_at > ? ORDER BY created_at DESC LIMIT 50",
        (cutoff_epoch,)
    ).fetchall()
    data["learnings"] = [dict(r) for r in rows]

    rows = conn.execute(
        "SELECT domain, decision, alternatives, based_on, confidence, outcome FROM decisions "
        "WHERE created_at > ? ORDER BY created_at DESC LIMIT 20",
        (cutoff_date,)
    ).fetchall()
    data["decisions"] = [dict(r) for r in rows]

    rows = conn.execute(
        "SELECT files, what_changed, why, affects, risks FROM change_log "
        "WHERE created_at > ? ORDER BY created_at DESC LIMIT 30",
        (cutoff_date,)
    ).fetchall()
    data["changes"] = [dict(r) for r in rows]

    rows = conn.execute(
        "SELECT summary, decisions as diary_decisions, pending, mental_state, domain, user_signals "
        "FROM session_diary WHERE created_at > ? ORDER BY created_at DESC LIMIT 20",
        (cutoff_date,)
    ).fetchall()
    data["diaries"] = [dict(r) for r in rows]

    rows = conn.execute(
        "SELECT * FROM evolution_log ORDER BY id DESC LIMIT 20"
    ).fetchall()
    data["evolution_history"] = [dict(r) for r in rows]

    rows = conn.execute(
        "SELECT dimension, score, delta, measured_at FROM evolution_metrics "
        "WHERE id IN (SELECT MAX(id) FROM evolution_metrics GROUP BY dimension)"
    ).fetchall()
    data["current_metrics"] = {r["dimension"]: dict(r) for r in rows}

    conn.close()
    return data


def create_snapshot(files_to_backup: list) -> str:
    """Create a snapshot of specific files before modification."""
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M")
    snap_dir = SNAPSHOTS_DIR / ts
    files_dir = snap_dir / "files"

    manifest = {
        "created_at": datetime.now().isoformat(),
        "files": [],
        "reason": "evolution_cycle"
    }

    for filepath in files_to_backup:
        fp = Path(filepath).expanduser()
        if fp.exists():
            rel = str(fp).replace(str(Path.home()) + "/", "")
            dest = files_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fp, dest)
            manifest["files"].append(rel)

    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    latest = SNAPSHOTS_DIR / "latest"
    if latest.is_symlink():
        latest.unlink()
    latest.symlink_to(snap_dir)

    _cleanup_snapshots()
    return str(snap_dir)


def _cleanup_snapshots():
    """Remove old snapshots, keeping MAX_SNAPSHOTS most recent + golden."""
    if not SNAPSHOTS_DIR.exists():
        return
    snaps = sorted(
        [d for d in SNAPSHOTS_DIR.iterdir()
         if d.is_dir() and d.name not in ("latest", "golden")],
        key=lambda d: d.stat().st_mtime,
        reverse=True
    )
    for old in snaps[MAX_SNAPSHOTS:]:
        shutil.rmtree(old)


def dry_run_restore_test() -> bool:
    """Test that snapshot+restore works before making real changes."""
    test_file = SANDBOX_DIR / "restore-test.txt"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("original_content")

    snap_dir = create_snapshot([str(test_file)])

    test_file.write_text("modified_content")

    try:
        subprocess.run(
            [str(CLAUDE_DIR / "scripts" / "nexo-snapshot-restore.sh"), snap_dir],
            capture_output=True, timeout=10, check=True
        )
        content = test_file.read_text()
        test_file.unlink(missing_ok=True)
        # Clean up test snapshot
        snap_path = Path(snap_dir)
        if snap_path.exists():
            shutil.rmtree(snap_path)
        return content == "original_content"
    except Exception:
        test_file.unlink(missing_ok=True)
        return False


def build_evolution_prompt(week_data: dict, objective: dict) -> str:
    """Build a SHORT prompt — CLI investigates on its own using tools."""

    # Summary stats only — CLI will dig deeper with tools
    stats = {
        "learnings_this_week": len(week_data.get("learnings", [])),
        "decisions_this_week": len(week_data.get("decisions", [])),
        "changes_this_week": len(week_data.get("changes", [])),
        "diaries_this_week": len(week_data.get("diaries", [])),
        "evolution_history": len(week_data.get("evolution_history", [])),
        "current_scores": {dim: m["score"] for dim, m in week_data.get("current_metrics", {}).items()},
    }

    mode = objective.get("evolution_mode", "auto")
    total = objective.get("total_evolutions", 0)
    max_auto = max_auto_changes(total)

    prompt = f"""You are NEXO Evolution — the weekly self-improvement cycle.

YOUR JOB: Analyze the past week and propose concrete improvements to NEXO's codebase.

WEEK SUMMARY:
- {stats['learnings_this_week']} new learnings
- {stats['decisions_this_week']} decisions made
- {stats['changes_this_week']} code changes deployed
- {stats['diaries_this_week']} session diaries
- {stats['evolution_history']} past evolution proposals
- Current scores: {json.dumps(stats['current_scores'])}

MODE: {mode} ({"proposals only, owner reviews" if mode == "review" else f"max {max_auto} auto-applied changes"})
CYCLE: #{total + 1}

INVESTIGATE using these tools:
1. Bash: sqlite3 {NEXO_DB} "SELECT category, title FROM learnings WHERE created_at > {time.time() - 7*86400} ORDER BY created_at DESC LIMIT 30"
2. Bash: sqlite3 {NEXO_DB} "SELECT area, COUNT(*) as cnt FROM error_repetitions GROUP BY area ORDER BY cnt DESC LIMIT 10"
3. Read ~/.nexo/coordination/daily-synthesis.md — today's context
4. Read ~/.nexo/coordination/postmortem-daily.md — self-critique patterns
5. Read ~/.nexo/logs/self-audit-summary.json — system health
6. Glob ~/.nexo/scripts/*.py — existing scripts
7. Glob ~/.nexo/nexo-mcp/plugins/*.py — existing plugins

LOOK FOR:
- Repeated errors that guard isn't preventing
- Scripts or processes that are failing or underperforming
- Missing functionality that session diaries keep asking for
- Redundant code or config that could be simplified
- Patterns in self-critique that suggest systemic issues

SAFETY:
- Safe zones for auto changes: ~/.nexo/scripts/, ~/.nexo/nexo-mcp/plugins/, ~/.nexo/cortex/
- IMMUTABLE files (never touch): db.py, server.py, plugin_loader.py, cognitive.py, CLAUDE.md
- Every change needs: what file, what to change, why, risk, how to verify

OUTPUT FORMAT (JSON):
{{
  "analysis": "one paragraph summary of what you found",
  "patterns": [{{"type": "...", "description": "...", "frequency": "..."}}],
  "proposals": [
    {{
      "classification": "auto" or "propose",
      "dimension": "reliability|proactivity|efficiency|safety|learning",
      "action": "what to do",
      "reasoning": "why",
      "scope": "local",
      "changes": [{{"file": "path", "operation": "create|replace|append", "search": "text to find", "content": "new text"}}]
    }}
  ]
}}

Max 3 proposals. Quality over quantity. If nothing needs improving, say so."""

    return prompt


def max_auto_changes(total_evolutions: int) -> int:
    """Progressive trust: 1 for first 4 cycles, 2 for next 4, then 3."""
    if total_evolutions < 4:
        return 1
    elif total_evolutions < 8:
        return 2
    return 3
