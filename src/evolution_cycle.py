"""NEXO Evolution Cycle — Self-improvement via Opus API.

Runs weekly after DMN. Analyzes patterns, proposes improvements.
v1: observe-only (all proposals logged as 'proposed' for the user to review).
v1.1 (future): sandbox execution of auto-approved changes.
"""

import json
import os
import paths
import shutil
import subprocess
import sqlite3
import time
from datetime import datetime, date, timedelta
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(NEXO_HOME)))
NEXO_DB = paths.db_path()
SANDBOX_DIR = NEXO_HOME / "sandbox" / "workspace"
SNAPSHOTS_DIR = paths.snapshots_dir()
RESTORE_LOG = paths.logs_dir() / "snapshot-restores.log"

# Evolution config: brain/ (canonical) > cortex/ (legacy) > NEXO_CODE (dev)
def _resolve_evolution_file(name: str) -> Path:
    for candidate in [paths.brain_dir() / name, NEXO_HOME / "cortex" / name, NEXO_CODE / name]:
        if candidate.exists():
            return candidate
    return paths.brain_dir() / name  # default canonical path

OBJECTIVE_FILE = _resolve_evolution_file("evolution-objective.json")
PROMPT_FILE = _resolve_evolution_file("evolution-prompt.md")

MAX_SNAPSHOTS = 8


def _normalize_dimensions(raw: dict | None) -> dict:
    normalized = {}
    for key, value in (raw or {}).items():
        canonical_key = "agi" if key == "agi_readiness" else key
        if isinstance(value, dict):
            normalized[canonical_key] = {
                "current": int(value.get("current", 0) or 0),
                "target": int(value.get("target", 0) or 0),
            }
        else:
            normalized[canonical_key] = {
                "current": 0,
                "target": int(value or 0),
            }
    return normalized


def normalize_objective(obj: dict | None) -> dict:
    """Upgrade legacy objective files to the canonical schema."""
    source = dict(obj or {})

    if "evolution_mode" in source:
        mode = str(source.get("evolution_mode") or "auto").strip().lower()
        if mode in {"public", "public_core", "contributor", "draft_prs"}:
            mode = "public_core"
    else:
        legacy_mode = str(source.get("review_mode") or "").strip().lower()
        if legacy_mode in {"manual", "review"}:
            mode = "review"
        elif legacy_mode in {"managed", "hybrid", "owner", "core"}:
            mode = "managed"
        elif legacy_mode in {"public", "public_core", "contributor", "draft_prs"}:
            mode = "public_core"
        else:
            mode = "auto"

    if mode not in {"auto", "review", "managed", "public_core"}:
        mode = "auto"

    dimensions = source.get("dimensions")
    if not isinstance(dimensions, dict) or not dimensions:
        dimensions = _normalize_dimensions(source.get("dimension_targets"))
    else:
        dimensions = _normalize_dimensions(dimensions)

    defaults = {
        "episodic_memory": {"current": 0, "target": 90},
        "autonomy": {"current": 0, "target": 80},
        "proactivity": {"current": 0, "target": 70},
        "self_improvement": {"current": 0, "target": 60},
        "agi": {"current": 0, "target": 20},
    }
    merged_dimensions = dict(defaults)
    merged_dimensions.update(dimensions)

    normalized = dict(source)
    normalized["evolution_mode"] = mode
    normalized["dimensions"] = merged_dimensions
    normalized["total_evolutions"] = int(source.get("total_evolutions", source.get("cycles_completed", 0)) or 0)
    normalized["last_evolution"] = source.get("last_evolution", source.get("last_cycle"))
    normalized["total_proposals_made"] = int(source.get("total_proposals_made", 0) or 0)
    normalized["total_auto_applied"] = int(source.get("total_auto_applied", 0) or 0)
    normalized["consecutive_failures"] = int(source.get("consecutive_failures", 0) or 0)
    normalized["history"] = source.get("history", []) if isinstance(source.get("history"), list) else []
    normalized["evolution_enabled"] = bool(source.get("evolution_enabled", True))
    normalized.pop("review_mode", None)
    normalized.pop("dimension_targets", None)
    normalized.pop("cycles_completed", None)
    normalized.pop("last_cycle", None)
    return normalized


def load_objective() -> dict:
    if OBJECTIVE_FILE.exists():
        return normalize_objective(json.loads(OBJECTIVE_FILE.read_text()))
    return normalize_objective({})


def save_objective(obj: dict):
    OBJECTIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    OBJECTIVE_FILE.write_text(json.dumps(normalize_objective(obj), indent=2, ensure_ascii=False))


def get_week_data(db_path: str) -> dict:
    """Gather last 7 days of learnings, decisions, changes, diaries."""
    conn = sqlite3.connect(db_path, timeout=10)
    try:
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

        return data
    finally:
        conn.close()


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
            if os.path.abspath(str(fp)) == os.path.abspath(str(dest)):
                continue  # Skip: source and destination are the same file
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

    # Find restore script: NEXO_CODE/scripts/ first, then NEXO_HOME/scripts/
    _nexo_code = Path(os.environ.get("NEXO_CODE", ""))
    restore_script = None
    for candidate in [_nexo_code / "scripts" / "nexo-snapshot-restore.sh",
                      paths.core_scripts_dir() / "nexo-snapshot-restore.sh"]:
        if candidate.exists():
            restore_script = candidate
            break
    if not restore_script:
        test_file.unlink(missing_ok=True)
        return False  # No restore script available

    try:
        subprocess.run(
            [str(restore_script), snap_dir],
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

    objective_dims = normalize_objective(objective).get("dimensions", {})
    current_scores = {
        dim: int(m["score"])
        for dim, m in week_data.get("current_metrics", {}).items()
        if isinstance(m, dict) and isinstance(m.get("score"), (int, float))
    }
    if not current_scores:
        current_scores = {
            dim: int((payload or {}).get("current", 0) or 0)
            for dim, payload in objective_dims.items()
            if isinstance(payload, dict)
        }

    # Summary stats only — CLI will dig deeper with tools
    stats = {
        "learnings_this_week": len(week_data.get("learnings", [])),
        "decisions_this_week": len(week_data.get("decisions", [])),
        "changes_this_week": len(week_data.get("changes", [])),
        "diaries_this_week": len(week_data.get("diaries", [])),
        "evolution_history": len(week_data.get("evolution_history", [])),
        "current_scores": current_scores,
    }

    mode = normalize_objective(objective).get("evolution_mode", "auto")
    total = objective.get("total_evolutions", 0)
    max_auto = max_auto_changes(total)
    if mode == "review":
        mode_desc = "review-only, nothing executes automatically"
        safe_zones = "~/.nexo/scripts/, ~/.nexo/plugins/, ~/.nexo/brain/"
        immutable_files = "db.py, server.py, plugin_loader.py, storage_router.py, cognitive.py, knowledge_graph.py, tools_*.py, nexo-watchdog.sh, evolution_cycle.py, CLAUDE.md, AGENTS.md"
    elif mode == "managed":
        mode_desc = f"owner-managed, max {max_auto} auto-applied changes with rollback and followups"
        safe_zones = "~/.nexo/scripts/, ~/.nexo/plugins/, ~/.nexo/brain/, NEXO_CODE/src, repo bin/docs/templates/tests"
        immutable_files = "db.py, server.py, plugin_loader.py, storage_router.py, nexo-watchdog.sh, evolution_cycle.py, CLAUDE.md, AGENTS.md, personality.md, user-profile.md"
    elif mode == "public_core":
        mode_desc = "public core contribution via isolated checkout and Draft PR"
        safe_zones = "isolated public repo checkout only"
        immutable_files = "personal runtime, ~/.nexo/**, local DBs/logs, CLAUDE.md, AGENTS.md, user-profile.md"
    else:
        mode_desc = f"public auto, max {max_auto} auto-applied changes in personal safe zones"
        safe_zones = "~/.nexo/scripts/, ~/.nexo/plugins/"
        immutable_files = "db.py, server.py, plugin_loader.py, storage_router.py, cognitive.py, knowledge_graph.py, tools_*.py, nexo-watchdog.sh, evolution_cycle.py, CLAUDE.md, AGENTS.md"

    prompt = f"""You are NEXO Evolution — the weekly self-improvement cycle.

YOUR JOB: Analyze the past week and propose concrete improvements to NEXO's codebase.

WEEK SUMMARY:
- {stats['learnings_this_week']} new learnings
- {stats['decisions_this_week']} decisions made
- {stats['changes_this_week']} code changes deployed
- {stats['diaries_this_week']} session diaries
- {stats['evolution_history']} past evolution proposals
- Current scores: {json.dumps(stats['current_scores'])}

MODE: {mode} ({mode_desc})
CYCLE: #{total + 1}

INVESTIGATE using these tools:
1. Bash: sqlite3 {NEXO_DB} "SELECT category, title FROM learnings WHERE created_at > {time.time() - 7*86400} ORDER BY created_at DESC LIMIT 30"
2. Bash: sqlite3 {NEXO_DB} "SELECT area, COUNT(*) as cnt FROM error_repetitions GROUP BY area ORDER BY cnt DESC LIMIT 10"
3. Read ~/.nexo/coordination/daily-synthesis.md — today's context
4. Read ~/.nexo/coordination/postmortem-daily.md — self-critique patterns
5. Read ~/.nexo/logs/self-audit-summary.json — system health
6. Glob ~/.nexo/scripts/*.py — existing scripts
7. Glob ~/.nexo/plugins/*.py — existing plugins

LOOK FOR:
- Repeated errors that guard isn't preventing
- Scripts or processes that are failing or underperforming
- Missing functionality that session diaries keep asking for
- Redundant code or config that could be simplified
- Patterns in self-critique that suggest systemic issues

SAFETY:
- Safe zones for this mode: {safe_zones}
- IMMUTABLE files (never touch in this mode): {immutable_files}
- Every change needs: what file, what to change, why, risk, how to verify
- AUTO changes must be deterministic. If the edit is ambiguous, risky, or needs human taste, mark it as "propose".
- In managed mode, failed AUTO changes will be rolled back automatically and turned into followups with evidence.

OUTPUT FORMAT (JSON):
{{
  "analysis": "one paragraph summary of what you found",
  "dimension_scores": {{
    "episodic_memory": 0,
    "autonomy": 0,
    "proactivity": 0,
    "self_improvement": 0,
    "agi": 0
  }},
  "score_evidence": {{
    "episodic_memory": "why this score changed or stayed flat",
    "autonomy": "why this score changed or stayed flat",
    "proactivity": "why this score changed or stayed flat",
    "self_improvement": "why this score changed or stayed flat",
    "agi": "why this score changed or stayed flat"
  }},
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

Always include all five canonical keys in `dimension_scores` and `score_evidence`.
Scores must be integers in the 0-100 range and reflect the current week, not targets.
Max 3 proposals. Quality over quantity. If nothing needs improving, say so."""

    return prompt


def build_public_contribution_prompt(
    *,
    repo_root: str,
    cycle_number: int,
    queued_candidate: dict | None = None,
) -> str:
    """Prompt for the public-core contributor mode.

    This prompt must never rely on private runtime state. It should inspect only
    the isolated public repo checkout, make one coherent improvement, and end
    by returning machine-readable summary JSON.
    """

    queued_section = ""
    if queued_candidate:
        queued_files = "\n".join(
            f"- {path}" for path in (queued_candidate.get("files_changed") or [])[:20]
        ) or "- (no files recorded)"
        queued_source = str((queued_candidate.get("metadata") or {}).get("source") or "managed-runtime")
        queued_section = f"""

PRIORITY PUBLIC-PORT QUEUE ITEM:
- Source: {queued_source}
- Title: {str(queued_candidate.get("title") or "").strip()}
- Why it matters: {str(queued_candidate.get("reasoning") or "").strip()}
- Files originally touched:
{queued_files}

This item was already fixed or detected outside the public contribution runner.
Before inventing another improvement, verify whether the public repository still
needs the same change and port it if necessary. If the repo is already correct,
make the smallest validating change that captures the same gap.
"""

    return f"""You are NEXO Public Evolution.

You are running inside an isolated checkout of the public NEXO repository.
Your job is to make one technically coherent improvement to the public core and
prepare it for a Draft PR.

STRICT RULES:
- Work only inside this repository checkout: {repo_root}
- You may modify only public core surfaces: src/, bin/, tests/, templates/, hooks/, migrations/, .claude-plugin/
- Do not read or use ~/.nexo, local DBs, personal scripts, emails, logs, prompts, secrets, or any user-identifying paths
- Do not push, open PRs, or change git remotes yourself
- Do not touch README, website, gh-pages, changelog, or release metadata in this mode
- Focus on one concrete improvement only
- Run validation for the files you touched

What to do:
1. Inspect the repo and find a real, self-contained improvement in reliability, install/update behavior, cron recovery, diagnostics, hooks, tests, or other core infrastructure.
2. Implement the change directly in this checkout.
3. Run the smallest relevant validation commands.
4. Return ONLY valid JSON with this shape:

{{
  "title": "type: short title",
  "problem": "what was wrong",
  "summary": "what you changed",
  "tests": ["command 1", "command 2"],
  "risks": ["risk 1", "risk 2"]
}}

Cycle: #{cycle_number}
Quality over quantity. One strong improvement is better than three weak ones.
{queued_section}
"""


def build_public_pr_review_prompt(
    *,
    pr_number: int,
    title: str,
    author: str,
    url: str,
    body: str,
    files: list[str],
    diff_text: str,
) -> str:
    """Prompt for peer-reviewing another public evolution PR.

    This is used only when this machine already has its own Draft PR open, so
    Evolution can still add value without opening a second PR.
    """

    rendered_files = "\n".join(f"- {path}" for path in files[:40]) if files else "- (no file list provided)"
    trimmed_diff = (diff_text or "").strip()
    if len(trimmed_diff) > 80000:
        trimmed_diff = trimmed_diff[:80000] + "\n\n[diff truncated by NEXO]"

    return f"""You are NEXO Public Evolution Review.

You are reviewing another opt-in public evolution PR. You must NOT merge, rebase,
push, or edit the PR. Your only job is to decide whether it deserves an approval
or whether it should receive a review comment without approval.

STRICT RULES:
- Review only this PR:
  - Number: #{pr_number}
  - Author: {author}
  - URL: {url}
- Base the review only on the provided title, body, file list, and diff
- Do not assume hidden context
- If confidence is not strong, choose `comment`, not `approve`
- If the diff is too incomplete, too risky, or too ambiguous, choose `skip`
- Never suggest merge authority; maintainers decide that later
- Keep the review concise, technical, and useful

PR TITLE:
{title}

PR BODY:
{body or "(empty)"}

FILES CHANGED:
{rendered_files}

DIFF:
```diff
{trimmed_diff or "(empty diff)"}
```

Return ONLY valid JSON:
{{
  "decision": "approve|comment|skip",
  "summary": "one-line verdict",
  "body": "the exact markdown text to post as the review body"
}}
"""


def max_auto_changes(total_evolutions: int) -> int:
    """Progressive trust: 1 for first 4 cycles, 2 for next 4, then 3."""
    if total_evolutions < 4:
        return 1
    elif total_evolutions < 8:
        return 2
    return 3
