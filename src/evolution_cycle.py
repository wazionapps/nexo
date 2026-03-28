"""NEXO Evolution Cycle — Self-improvement via Opus API.

Runs weekly after DMN. Analyzes patterns, proposes improvements.
v1: observe-only (all proposals logged as 'proposed' for the owner to review).
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

NEXO_DB = Path.home() / "claude" / "nexo-mcp" / "nexo.db"
CORTEX_DIR = Path(__file__).parent
CLAUDE_DIR = Path.home() / "claude"
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
    """Build the prompt for the Opus Evolution cycle."""
    if PROMPT_FILE.exists():
        template = PROMPT_FILE.read_text()
    else:
        template = "You are NEXO Evolution. Analyze the data and propose improvements."

    prompt = template + "\n\n## WEEKLY DATA\n\n"
    prompt += f"### Learnings ({len(week_data.get('learnings', []))} this week)\n"
    for l in week_data.get("learnings", [])[:30]:
        prompt += f"- [{l['category']}] {l['title']}: {str(l['content'])[:150]}\n"

    prompt += f"\n### Decisions ({len(week_data.get('decisions', []))} this week)\n"
    for d in week_data.get("decisions", [])[:15]:
        outcome = f" → {str(d['outcome'])[:80]}" if d.get("outcome") else " → no outcome yet"
        prompt += f"- [{d['domain']}] {str(d['decision'])[:150]}{outcome}\n"

    prompt += f"\n### Changes ({len(week_data.get('changes', []))} this week)\n"
    for c in week_data.get("changes", [])[:20]:
        prompt += f"- {str(c['files'])[:60]}: {str(c['what_changed'])[:100]}\n"

    prompt += f"\n### Session Diaries ({len(week_data.get('diaries', []))} this week)\n"
    for s in week_data.get("diaries", [])[:10]:
        prompt += f"- [{s.get('domain','')}] {str(s['summary'])[:150]}\n"
        if s.get("user_signals"):
            prompt += f"  the owner: {str(s['user_signals'])[:100]}\n"

    prompt += "\n### Current Dimension Scores\n"
    for dim, m in week_data.get("current_metrics", {}).items():
        prompt += f"- {dim}: {m['score']}% (delta: {m.get('delta', 0)})\n"

    prompt += f"\n### Evolution History ({len(week_data.get('evolution_history', []))} entries)\n"
    for h in week_data.get("evolution_history", [])[:10]:
        prompt += f"- #{h['id']} [{h['status']}] {str(h['proposal'])[:100]}\n"

    prompt += f"\n### Objective\n{json.dumps(objective, indent=2)}\n"

    # Guard stats — error prevention effectiveness
    try:
        guard_conn = sqlite3.connect(str(NEXO_DB), timeout=10)
        cutoff_7d = (date.today() - timedelta(days=7)).isoformat()
        cutoff_epoch_7d = time.time() - 7 * 86400

        total_reps = guard_conn.execute(
            "SELECT COUNT(*) FROM error_repetitions WHERE created_at > ?", (cutoff_7d,)
        ).fetchone()[0]
        new_learnings_7d = guard_conn.execute(
            "SELECT COUNT(*) FROM learnings WHERE created_at > ?", (cutoff_epoch_7d,)
        ).fetchone()[0]
        rep_rate = round(total_reps / new_learnings_7d, 2) if new_learnings_7d > 0 else 0.0
        guard_checks = guard_conn.execute(
            "SELECT COUNT(*) FROM guard_checks WHERE created_at > ?", (cutoff_7d,)
        ).fetchone()[0]

        top_areas = guard_conn.execute(
            "SELECT area, COUNT(*) as cnt FROM error_repetitions WHERE created_at > ? GROUP BY area ORDER BY cnt DESC LIMIT 5",
            (cutoff_7d,)
        ).fetchall()

        most_ignored = guard_conn.execute(
            "SELECT original_learning_id, COUNT(*) as cnt FROM error_repetitions "
            "GROUP BY original_learning_id HAVING cnt >= 3 ORDER BY cnt DESC LIMIT 5"
        ).fetchall()

        guard_conn.close()

        prompt += "\n### Guard Stats (Error Prevention)\n"
        prompt += f"- Repetition rate: {rep_rate:.0%} (target: <15%)\n"
        prompt += f"- Guard checks this week: {guard_checks} (target: >5/session)\n"
        prompt += f"- New learnings: {new_learnings_7d}, Repetitions: {total_reps}\n"
        if top_areas:
            prompt += "- Top problem areas: " + ", ".join(f"{r[0]}({r[1]})" for r in top_areas) + "\n"
        if most_ignored:
            prompt += "- Most ignored learnings (3+ repeats): " + ", ".join(f"#{r[0]}({r[1]}x)" for r in most_ignored) + "\n"
        prompt += "- Propose more aggressive rules for areas with high repetition rate.\n"
    except Exception:
        pass

    # Infrastructure inventory — so Opus knows what exists before proposing changes
    inventory_script = Path.home() / "claude" / "scripts" / "nexo-infra-inventory.sh"
    if inventory_script.exists():
        try:
            result = subprocess.run(
                ["bash", str(inventory_script)],
                capture_output=True, text=True, timeout=10
            )
            if result.stdout.strip():
                prompt += f"\n### Infrastructure Inventory (hooks, scripts, memory, crons)\n"
                prompt += "Before proposing any change, check this inventory to avoid duplicating existing infrastructure.\n"
                prompt += f"```json\n{result.stdout.strip()}\n```\n"
        except Exception:
            pass

    return prompt


def max_auto_changes(total_evolutions: int) -> int:
    """Progressive trust: 1 for first 4 cycles, 2 for next 4, then 3."""
    if total_evolutions < 4:
        return 1
    elif total_evolutions < 8:
        return 2
    return 3
