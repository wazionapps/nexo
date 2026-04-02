#!/usr/bin/env python3
"""
NEXO Evolution — Standalone weekly runner with real execution.
Cron: 0 3 * * 0  (Sundays 3:00 AM)

Runs independently of Cortex. Calls Opus API directly to analyze
the past week and generate improvement proposals.

AUTO proposals are executed: snapshot → apply → validate → commit/rollback.
PROPOSE proposals are logged for the user's review.
"""

import json
import os
import py_compile
import sqlite3
import subprocess
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
# Auto-detect: if running from repo (src/scripts/), use src/ as NEXO_CODE
_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent  # src/scripts/ -> src/
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(_repo_src) if (_repo_src / "server.py").exists() else str(NEXO_HOME)))

# ── Paths ────────────────────────────────────────────────────────────────
CLAUDE_DIR = NEXO_HOME
NEXO_DB = CLAUDE_DIR / "data" / "nexo.db"
LOG_DIR = CLAUDE_DIR / "logs"
SNAPSHOTS_DIR = CLAUDE_DIR / "snapshots"
SANDBOX_DIR = CLAUDE_DIR / "sandbox" / "workspace"
MAX_CONSECUTIVE_FAILURES = 3
MAX_SNAPSHOTS = 8

# ── Safe zones for AUTO execution ────────────────────────────────────────
# "review" mode (owner): broader zones, but nothing executes without approval
# "auto" mode (public users): restricted to user scripts and plugins ONLY
AUTO_SAFE_PREFIXES = [
    str(CLAUDE_DIR / "scripts") + "/",
    str(CLAUDE_DIR / "brain") + "/",
    str(NEXO_CODE / "plugins") + "/",
    str(CLAUDE_DIR / "logs") + "/",
    str(CLAUDE_DIR / "coordination") + "/",
]

# Public mode: only user-created scripts — NEVER core, cortex, or plugins
AUTO_SAFE_PREFIXES_PUBLIC = [
    str(CLAUDE_DIR / "scripts") + "/",
]

# ── Immutable files — NEVER touch (applies to ALL modes) ────────────────
IMMUTABLE_FILES = {
    "db.py", "server.py", "plugin_loader.py", "nexo-watchdog.sh",
    "cortex-wrapper.py", "CLAUDE.md", "personality.md",
    "user-profile.md", "evolution_cycle.py",
    # Core cognitive engine — never auto-modified
    "cognitive.py", "knowledge_graph.py", "storage_router.py",
    # Core tools — never auto-modified
    "tools_sessions.py", "tools_coordination.py", "tools_reminders.py",
    "tools_reminders_crud.py", "tools_learnings.py", "tools_credentials.py",
    "tools_task_history.py", "tools_menu.py",
}

# ── Claude CLI path ──────────────────────────────────────────────────────
CLAUDE_CLI = Path.home() / ".local" / "bin" / "claude"

# ── Logging ──────────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "evolution.log"


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ── Import from evolution_cycle.py (lives in NEXO_CODE, i.e. src/) ──────
sys.path.insert(0, str(NEXO_CODE))
from evolution_cycle import (
    load_objective, save_objective, get_week_data, build_evolution_prompt,
    dry_run_restore_test, max_auto_changes, create_snapshot
)


# ── Consecutive failure tracking ─────────────────────────────────────────
def get_consecutive_failures() -> int:
    obj = load_objective()
    return obj.get("consecutive_failures", 0)


def set_consecutive_failures(count: int):
    obj = load_objective()
    obj["consecutive_failures"] = count
    save_objective(obj)


# ── Claude CLI call ──────────────────────────────────────────────────────
CLI_TIMEOUT = 21600  # 3h safety net (prevents zombie processes)


def verify_claude_cli() -> bool:
    """Check Claude CLI is available and authenticated."""
    if not CLAUDE_CLI.exists():
        return False
    try:
        result = subprocess.run(
            [str(CLAUDE_CLI), "-p", "reply OK", "--output-format", "text"],
            capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0
    except Exception:
        return False


def call_claude_cli(prompt: str) -> str:
    """Call claude -p prompt --model opus via subprocess. Returns stdout text."""
    env = os.environ.copy()
    env["NEXO_HEADLESS"] = "1"  # Skip stop hook post-mortem
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE", None)

    result = subprocess.run(
        [str(CLAUDE_CLI), "-p", prompt, "--model", "opus",
         "--output-format", "text",
         "--allowedTools", "Read,Write,Edit,Glob,Grep,Bash,mcp__nexo__*"],
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI exited {result.returncode}: {result.stderr[:500]}")
    return result.stdout


# ── File safety validation ───────────────────────────────────────────────
def is_safe_path(filepath: str, mode: str = "auto") -> bool:
    """Check if a file path is within safe zones and not immutable.
    mode='auto' (public): restricted to scripts/ and plugins/ only.
    mode='review' (owner): broader zones but nothing executes without approval anyway.
    """
    expanded = str(Path(filepath).expanduser().resolve())
    filename = Path(expanded).name

    if filename in IMMUTABLE_FILES:
        return False

    prefixes = AUTO_SAFE_PREFIXES if mode == "review" else AUTO_SAFE_PREFIXES_PUBLIC
    for prefix in prefixes:
        resolved_prefix = str(Path(prefix).expanduser().resolve())
        if expanded.startswith(resolved_prefix):
            return True

    return False


def validate_syntax(filepath: str) -> tuple[bool, str]:
    """Basic syntax validation for known file types."""
    path = Path(filepath)
    ext = path.suffix

    if ext == ".py":
        try:
            py_compile.compile(str(path), doraise=True)
            return True, "Python syntax OK"
        except Exception as e:
            return False, f"Validation error: {e}"

    elif ext == ".sh":
        try:
            result = subprocess.run(
                ["bash", "-n", str(path)],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return True, "Bash syntax OK"
            return False, f"Bash syntax error: {result.stderr[:200]}"
        except Exception as e:
            return False, f"Validation error: {e}"

    elif ext == ".json":
        try:
            json.loads(Path(filepath).read_text())
            return True, "JSON valid"
        except Exception as e:
            return False, f"JSON error: {e}"

    elif ext == ".md":
        return True, "Markdown (no validation needed)"

    return True, f"No validator for {ext} (accepted)"


# ── Apply a single change operation ──────────────────────────────────────
def apply_change(change: dict) -> tuple[bool, str]:
    """Apply a single file change operation. Returns (success, message)."""
    filepath = str(Path(change["file"]).expanduser())
    operation = change.get("operation", "")
    content = change.get("content", "")

    if not is_safe_path(filepath):
        return False, f"BLOCKED: {filepath} is outside safe zones or immutable"

    try:
        if operation == "create":
            if Path(filepath).exists():
                return False, f"BLOCKED: {filepath} already exists (create requires new file)"
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            Path(filepath).write_text(content)
            # Make scripts executable
            if filepath.endswith(".sh") or filepath.endswith(".py"):
                os.chmod(filepath, 0o755)
            return True, f"Created {filepath}"

        elif operation == "replace":
            search = change.get("search", "")
            if not search:
                return False, "BLOCKED: replace operation requires 'search' field"
            if not Path(filepath).exists():
                return False, f"BLOCKED: {filepath} does not exist"
            original = Path(filepath).read_text()
            count = original.count(search)
            if count == 0:
                return False, f"BLOCKED: search text not found in {filepath}"
            if count > 1:
                return False, f"BLOCKED: search text matches {count} times (must be unique)"
            new_content = original.replace(search, content, 1)
            Path(filepath).write_text(new_content)
            return True, f"Replaced in {filepath}"

        elif operation == "append":
            if not Path(filepath).exists():
                return False, f"BLOCKED: {filepath} does not exist"
            with open(filepath, "a") as f:
                f.write(content)
            return True, f"Appended to {filepath}"

        else:
            return False, f"BLOCKED: unknown operation '{operation}'"

    except Exception as e:
        return False, f"ERROR: {e}"


# ── Execute AUTO proposals ───────────────────────────────────────────────
def execute_auto_proposal(proposal: dict, cycle_num: int, conn: sqlite3.Connection) -> dict:
    """Execute an AUTO proposal with snapshot/apply/validate/rollback."""
    changes = proposal.get("changes", [])
    if not changes:
        return {"status": "skipped", "reason": "No changes array in proposal"}

    # Validate all paths first
    for change in changes:
        filepath = str(Path(change["file"]).expanduser())
        if not is_safe_path(filepath):
            return {"status": "blocked", "reason": f"Unsafe path: {filepath}"}

    # Collect files to snapshot (existing files only)
    files_to_backup = []
    for change in changes:
        filepath = str(Path(change["file"]).expanduser())
        if Path(filepath).exists():
            files_to_backup.append(filepath)

    # Create snapshot
    snapshot_ref = None
    if files_to_backup:
        snapshot_ref = create_snapshot(files_to_backup)
        log(f"  Snapshot created: {snapshot_ref}")

    # Apply changes
    applied_files = []
    all_results = []
    try:
        for change in changes:
            success, msg = apply_change(change)
            all_results.append(msg)
            log(f"    {msg}")
            if not success:
                raise RuntimeError(f"Change failed: {msg}")
            filepath = str(Path(change["file"]).expanduser())
            applied_files.append(filepath)

        # Validate all modified/created files
        for filepath in applied_files:
            valid, vmsg = validate_syntax(filepath)
            all_results.append(vmsg)
            log(f"    Validate: {vmsg}")
            if not valid:
                raise RuntimeError(f"Validation failed: {vmsg}")

        return {
            "status": "applied",
            "snapshot_ref": snapshot_ref,
            "files_changed": applied_files,
            "test_result": "; ".join(all_results),
        }

    except RuntimeError as e:
        # Rollback
        log(f"  ROLLBACK: {e}")
        if snapshot_ref:
            try:
                restore_script = CLAUDE_DIR / "scripts" / "nexo-snapshot-restore.sh"
                subprocess.run(
                    [str(restore_script), snapshot_ref],
                    capture_output=True, timeout=15, check=True
                )
                log(f"  Restored from snapshot {snapshot_ref}")
            except Exception as re:
                log(f"  CRITICAL: Restore failed: {re}")
        else:
            # Remove created files that didn't exist before
            for filepath in applied_files:
                if filepath not in files_to_backup:
                    Path(filepath).unlink(missing_ok=True)
                    log(f"  Removed created file: {filepath}")

        return {
            "status": "failed",
            "snapshot_ref": snapshot_ref,
            "files_changed": [],
            "test_result": f"ROLLBACK: {e}; " + "; ".join(all_results),
        }


# ── Review followup for owner mode ──────────────────────────────────────
def _create_review_followup(conn: sqlite3.Connection, cycle_num: int,
                            items: list[dict], analysis: str):
    """Create a followup summarizing Evolution proposals for owner review."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    followup_id = f"NF-EVO-C{cycle_num}"

    public_items = [i for i in items if i.get("scope") == "public"]
    local_items = [i for i in items if i.get("scope") != "public"]

    lines = [f"Evolution Cycle #{cycle_num} — {len(items)} proposals to review."]
    lines.append(f"Analysis: {analysis[:200]}")
    lines.append("")

    if public_items:
        lines.append(f"FOR EVERYONE ({len(public_items)}):")
        for i, item in enumerate(public_items, 1):
            lines.append(f"  {i}. [{item['dimension']}] {item['action'][:120]}")
            lines.append(f"     Why: {item['reasoning'][:100]}")
        lines.append("")

    if local_items:
        lines.append(f"FOR YOU ONLY ({len(local_items)}):")
        for i, item in enumerate(local_items, 1):
            lines.append(f"  {i}. [{item['dimension']}] {item['action'][:120]}")
            lines.append(f"     Why: {item['reasoning'][:100]}")

    description = "\n".join(lines)

    try:
        now_epoch = datetime.now().timestamp()
        conn.execute(
            "INSERT OR REPLACE INTO followups (id, description, date, status, verification, created_at, updated_at) "
            "VALUES (?, ?, ?, 'pending', ?, ?, ?)",
            (followup_id, description, tomorrow,
             f"SELECT * FROM evolution_log WHERE cycle_number={cycle_num}",
             now_epoch, now_epoch)
        )
        conn.commit()
        log(f"  Followup {followup_id} created for {tomorrow}")
    except Exception as e:
        log(f"  WARN: Failed to create followup: {e}")


# ── Main run ─────────────────────────────────────────────────────────────
def run():
    log("=" * 60)
    log("NEXO Evolution cycle starting (standalone, v2 — real execution)")

    # Check objective
    objective = load_objective()
    if not objective:
        log("ERROR: No evolution-objective.json found")
        sys.exit(1)
    if not objective.get("evolution_enabled", True):
        log(f"Evolution DISABLED: {objective.get('disabled_reason', 'unknown')}")
        return

    # Circuit breaker: consecutive failures
    failures = get_consecutive_failures()
    if failures >= MAX_CONSECUTIVE_FAILURES:
        log(f"CIRCUIT BREAKER: {failures} consecutive failures. Disabling evolution.")
        objective["evolution_enabled"] = False
        objective["disabled_reason"] = f"Circuit breaker: {failures} consecutive failures at {datetime.now().isoformat()}"
        save_objective(objective)
        return

    # Dry-run restore test
    log("Running restore dry-run test...")
    if not dry_run_restore_test():
        log("CRITICAL: Restore test failed — aborting")
        set_consecutive_failures(failures + 1)
        sys.exit(1)
    log("Restore test PASSED")

    # Gather data
    log("Gathering week data from nexo.db...")
    week_data = get_week_data(str(NEXO_DB))
    log(f"  Learnings: {len(week_data.get('learnings', []))}")
    log(f"  Decisions: {len(week_data.get('decisions', []))}")
    log(f"  Changes: {len(week_data.get('changes', []))}")
    log(f"  Diaries: {len(week_data.get('diaries', []))}")

    # Build prompt
    prompt = build_evolution_prompt(week_data, objective)
    log(f"Prompt built: {len(prompt)} chars")

    # Verify Claude CLI is authenticated before calling
    if not verify_claude_cli():
        log("Claude CLI not available or not authenticated. Skipping evolution run.")
        return

    # Call Opus via claude -p
    log("Calling claude -p --model opus...")
    try:
        raw_response = call_claude_cli(prompt)
    except Exception as e:
        log(f"claude CLI call failed: {e}")
        set_consecutive_failures(failures + 1)
        return

    log(f"Response received: {len(raw_response)} chars")

    # Parse JSON
    try:
        text = raw_response
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        response = json.loads(text.strip())
    except Exception as e:
        log(f"JSON parse failed: {e}")
        log(f"Raw (first 500): {raw_response[:500]}")
        set_consecutive_failures(failures + 1)
        return

    # Reset consecutive failures on successful parse
    set_consecutive_failures(0)

    log(f"Analysis: {response.get('analysis', 'N/A')[:200]}")

    # Log patterns
    for p in response.get("patterns", []):
        log(f"  Pattern [{p.get('type', '?')}]: {p.get('description', '')[:100]} (freq: {p.get('frequency', '?')})")

    # Process proposals
    proposals = response.get("proposals", [])
    cycle_num = objective.get("total_evolutions", 0) + 1
    max_auto = max_auto_changes(objective.get("total_evolutions", 0))
    auto_count = 0
    auto_applied = 0
    evolution_mode = objective.get("evolution_mode", "auto")  # "auto" (public) or "review" (owner)

    conn = sqlite3.connect(str(NEXO_DB), timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")

    # In "review" mode: log everything as pending_review, create followup
    # In "auto" mode: execute AUTO proposals, log PROPOSE as proposed
    review_items = []

    for p in proposals:
        classification = p.get("classification", "propose")
        dimension = p.get("dimension", "other")
        action = p.get("action", "")
        reasoning = p.get("reasoning", "")
        scope = p.get("scope", "local")  # "public" or "local"

        if evolution_mode == "review":
            # Owner mode: nothing executes, everything queued for review
            log(f"  QUEUED [{scope}]: {action[:80]}")
            conn.execute(
                "INSERT INTO evolution_log (cycle_number, dimension, proposal, classification, "
                "reasoning, status) VALUES (?, ?, ?, ?, ?, ?)",
                (cycle_num, dimension, action, classification, reasoning, "pending_review")
            )
            review_items.append({
                "dimension": dimension,
                "action": action,
                "reasoning": reasoning,
                "scope": scope,
                "classification": classification,
            })

        elif classification == "auto" and auto_count < max_auto:
            # Public mode: execute AUTO proposals
            auto_count += 1
            log(f"  AUTO #{auto_count}/{max_auto}: {action[:80]}")

            result = execute_auto_proposal(p, cycle_num, conn)
            status = result["status"]

            conn.execute(
                "INSERT INTO evolution_log (cycle_number, dimension, proposal, classification, "
                "reasoning, status, files_changed, snapshot_ref, test_result) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cycle_num, dimension, action, "auto", reasoning, status,
                 json.dumps(result.get("files_changed", [])),
                 result.get("snapshot_ref", ""),
                 result.get("test_result", ""))
            )

            if status == "applied":
                auto_applied += 1
                log(f"    APPLIED successfully")
            elif status == "blocked":
                log(f"    BLOCKED: {result.get('test_result', '')}")
            elif status == "skipped":
                log(f"    SKIPPED: {result.get('reason', '')}")
            else:
                log(f"    FAILED: {result.get('test_result', '')[:100]}")

        else:
            # PROPOSE or over auto limit
            if classification == "auto" and auto_count >= max_auto:
                log(f"  AUTO→PROPOSE (over limit {max_auto}): {action[:80]}")
                classification = "propose"
            else:
                log(f"  PROPOSE: {action[:80]}")

            conn.execute(
                "INSERT INTO evolution_log (cycle_number, dimension, proposal, classification, "
                "reasoning, status) VALUES (?, ?, ?, ?, ?, ?)",
                (cycle_num, dimension, action, classification, reasoning, "proposed")
            )

    conn.commit()

    # In review mode: create followup for owner
    if evolution_mode == "review" and review_items:
        _create_review_followup(conn, cycle_num, review_items, response.get("analysis", ""))

    # Update metrics
    scores = response.get("dimension_scores", {})
    evidence = response.get("score_evidence", {})
    current = week_data.get("current_metrics", {})

    for dim, score in scores.items():
        if isinstance(score, (int, float)) and 0 <= score <= 100:
            prev = current.get(dim, {}).get("score", 0)
            delta = int(score) - prev
            conn.execute(
                "INSERT INTO evolution_metrics (dimension, score, evidence, delta) VALUES (?, ?, ?, ?)",
                (dim, int(score), json.dumps(evidence.get(dim, "")), delta)
            )

    conn.commit()
    conn.close()

    # Update objective
    objective["last_evolution"] = str(date.today())
    objective["total_evolutions"] = cycle_num
    objective["total_proposals_made"] = objective.get("total_proposals_made", 0) + len(proposals)
    objective["total_auto_applied"] = objective.get("total_auto_applied", 0) + auto_applied
    for dim, score in scores.items():
        if dim in objective.get("dimensions", {}) and isinstance(score, (int, float)):
            objective["dimensions"][dim]["current"] = int(score)

    objective.setdefault("history", []).insert(0, {
        "cycle": cycle_num,
        "date": str(date.today()),
        "proposals": len(proposals),
        "auto_count": auto_count,
        "auto_applied": auto_applied,
        "analysis": response.get("analysis", "")[:200]
    })
    objective["history"] = objective["history"][:12]

    save_objective(objective)

    log(f"Evolution cycle #{cycle_num} COMPLETE: {len(proposals)} proposals "
        f"({auto_count} auto, {auto_applied} applied, "
        f"{len(proposals) - auto_count} propose)")
    log("=" * 60)


def _update_catchup_state():
    """Register successful run for catch-up."""
    try:
        import json as _json
        from pathlib import Path as _Path

        _state_file = NEXO_HOME / "operations" / ".catchup-state.json"
        _state = _json.loads(_state_file.read_text()) if _state_file.exists() else {}
        _state["evolution"] = datetime.now().isoformat()
        _state_file.write_text(_json.dumps(_state, indent=2))
    except Exception:
        pass


if __name__ == "__main__":
    try:
        run()
        _update_catchup_state()
    except Exception as e:
        log(f"FATAL: {e}")
        import traceback
        log(traceback.format_exc())
        sys.exit(1)
