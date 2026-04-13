#!/usr/bin/env python3
"""
NEXO Synthesis Engine v2 — Daily intelligence brief.

Before: ~400 lines of Python concatenating SQL results into markdown sections.
Now: Collects raw data, passes to the configured automation backend which synthesizes
with real understanding of what matters for tomorrow.

Runs daily at 06:00 via LaunchAgent.
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
_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(_repo_src) if (_repo_src / "server.py").exists() else str(NEXO_HOME)))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

from agent_runner import AutomationBackendUnavailableError, run_automation_prompt

CLAUDE_DIR = NEXO_HOME
COORD_DIR = CLAUDE_DIR / "coordination"
NEXO_DB = NEXO_HOME / "data" / "nexo.db"
OUTPUT_FILE = COORD_DIR / "daily-synthesis.md"
LAST_RUN_FILE = COORD_DIR / "synthesis-last-run"
LOCK_FILE = COORD_DIR / "synthesis.lock"
def _resolve_claude_cli() -> Path:
    """Find claude CLI: saved path > PATH > common locations."""
    import shutil as _shutil
    saved = NEXO_HOME / "config" / "claude-cli-path"
    if saved.exists():
        p = Path(saved.read_text().strip())
        if p.exists():
            return p
    found = _shutil.which("claude")
    if found:
        return Path(found)
    for candidate in [
        HOME / ".local" / "bin" / "claude",
        HOME / ".npm-global" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
    ]:
        if candidate.exists():
            return candidate
    return HOME / ".local" / "bin" / "claude"

CLAUDE_CLI = _resolve_claude_cli()

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


def _table_columns(table_name: str) -> set[str]:
    if not NEXO_DB.exists():
        return set()
    try:
        conn = sqlite3.connect(str(NEXO_DB))
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        conn.close()
        return {str(row[1]) for row in rows}
    except Exception:
        return set()


def _parse_json_field(value):
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return value if isinstance(value, dict) else {}


def _impact_reasoning(row: dict) -> str:
    factors = _parse_json_field(row.get("impact_factors"))
    return str(factors.get("reasoning") or "").strip()


def _load_json_summary(path: Path, *, actionable) -> tuple[dict | None, str | None]:
    if not path.exists():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, str(exc)
    if not isinstance(payload, dict):
        return None, "summary payload is not a JSON object"
    if not actionable(payload):
        return None, None
    return payload, None


def _load_coordination_summary(filename: str, *, actionable) -> tuple[dict | None, str | None]:
    return _load_json_summary(COORD_DIR / filename, actionable=actionable)


def _update_summary_actionable(payload: dict) -> bool:
    if any(payload.get(key) for key in ("error", "updated", "deferred_reason", "git_update", "npm_notice")):
        return True
    for action in payload.get("actions") or []:
        if str(action).startswith("personal-schedules-"):
            return True
    for message in payload.get("client_bootstrap_updates") or []:
        if "already current" not in str(message).lower():
            return True
    return False


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

    # Overdue reminders (schema: description, date, status uppercase)
    data["overdue_reminders"] = safe_query(
        "SELECT id, description, date FROM reminders "
        "WHERE status='PENDING' AND date <= ? ORDER BY date",
        (TODAY_STR,)
    )

    # Pending followups (schema: description, date, status uppercase)
    followup_columns = _table_columns("followups")
    if "impact_score" in followup_columns:
        impact_factors_sql = ", impact_factors" if "impact_factors" in followup_columns else ""
        followup_select = (
            "SELECT id, description, date, priority, impact_score"
            f"{impact_factors_sql} FROM followups "
        )
        followup_order = (
            "ORDER BY "
            "CASE WHEN COALESCE(impact_score, 0) > 0 THEN 0 ELSE 1 END ASC, "
            "COALESCE(impact_score, 0) DESC, "
            "CASE WHEN date IS NULL OR date = '' THEN 1 ELSE 0 END ASC, "
            "date ASC"
        )
    else:
        followup_select = "SELECT id, description, date FROM followups "
        followup_order = "ORDER BY date"
    data["pending_followups"] = safe_query(
        f"{followup_select} WHERE status='PENDING' {followup_order}"
    )
    for row in data["pending_followups"]:
        if "impact_factors" in row:
            row["impact_factors"] = _parse_json_field(row.get("impact_factors"))
            row["impact_reasoning"] = _impact_reasoning(row)

    impact_summary_file = COORD_DIR / "impact-scorer-summary.json"
    if impact_summary_file.exists():
        try:
            data["impact_queue_summary"] = json.loads(impact_summary_file.read_text(encoding="utf-8"))
        except Exception as exc:
            data["impact_queue_summary_error"] = str(exc)

    followup_hygiene_summary, followup_hygiene_error = _load_coordination_summary(
        "followup-hygiene-summary.json",
        actionable=lambda payload: any(
            int(payload.get(key, 0) or 0) > 0
            for key in ("dirty_normalized", "stale_count", "orphan_count")
        ),
    )
    if followup_hygiene_summary is not None:
        data["followup_hygiene_summary"] = followup_hygiene_summary
    elif followup_hygiene_error:
        data["followup_hygiene_summary_error"] = followup_hygiene_error

    outcome_checker_summary, outcome_checker_error = _load_coordination_summary(
        "outcome-checker-summary.json",
        actionable=lambda payload: (
            any(
                int(payload.get(key, 0) or 0) > 0
                for key in ("checked", "met", "missed", "pending", "errors")
            )
            or bool(payload.get("ids"))
            or bool(((payload.get("auto_promoted_patterns") or {}).get("promoted") or []))
        ),
    )
    if outcome_checker_summary is not None:
        data["outcome_checker_summary"] = outcome_checker_summary
    elif outcome_checker_error:
        data["outcome_checker_summary_error"] = outcome_checker_error

    update_summary, update_summary_error = _load_json_summary(
        NEXO_HOME / "logs" / "update-last-summary.json",
        actionable=_update_summary_actionable,
    )
    if update_summary is not None:
        data["update_summary"] = update_summary
    elif update_summary_error:
        data["update_summary_error"] = update_summary_error

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

    prompt = f"""FIRST: Call nexo_startup(task='daily synthesis') to register this session.

You are NEXO's synthesis engine. Write the daily intelligence brief for tomorrow's
startup. This file is read by NEXO at the beginning of each session to understand
what happened today and what to focus on tomorrow. Use nexo_learning_add and nexo_followup_create if you discover actionable items.

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

    log("Invoking automation backend for synthesis...")
    try:
        result = run_automation_prompt(
            prompt,
            model="opus",
            timeout=21600,
            output_format="text",
            allowed_tools="Read,Write,Edit,Glob,Grep,Bash,mcp__nexo__*",
        )

        if result.returncode != 0:
            log(f"CLI error ({result.returncode}): {(result.stderr or '')[:300]}")
            return False

        log(f"Synthesis complete. Output: {len(result.stdout or '')} chars")
        return True

    except AutomationBackendUnavailableError as e:
        log(f"Automation backend unavailable: {e}")
        return False
    except subprocess.TimeoutExpired:
        log("CLI timed out (180s)")
        return False
    except Exception as e:
        log(f"Exception: {e}")
        return False


def fallback_synthesis(data: dict):
    """Write a basic synthesis from raw data when CLI is unavailable."""
    log("Fallback: writing basic synthesis from raw data...")
    lines = [f"# NEXO Daily Synthesis -- {TODAY_STR}", "",
             "*(Generated by fallback -- CLI was unavailable)*", ""]

    if data.get("learnings"):
        lines.append("## Errors & Learnings")
        for l in data["learnings"][:10]:
            lines.append(f"- [{l.get('category', 'general')}] {l.get('title', 'untitled')}")
        lines.append("")

    if data.get("decisions"):
        lines.append("## Decisions Made")
        for d in data["decisions"][:10]:
            lines.append(f"- [{d.get('domain', 'general')}] {d.get('decision', '')[:120]}")
        lines.append("")

    if data.get("changes"):
        lines.append("## Changes Deployed")
        for c in data["changes"][:10]:
            lines.append(f"- {c.get('what_changed', '')[:120]}")
        lines.append("")

    if data.get("overdue_reminders"):
        lines.append("## Overdue Reminders")
        for r in data["overdue_reminders"][:10]:
            lines.append(f"- #{r.get('id', '?')} {r.get('description', '')} (due {r.get('date', '?')})")
        lines.append("")

    if data.get("pending_followups"):
        lines.append("## Pending Followups")
        for f in data["pending_followups"][:10]:
            impact = float(f.get("impact_score") or 0.0)
            impact_tag = f" [impact {impact:.1f}]" if impact > 0 else ""
            because = _impact_reasoning(f)
            because_tag = f" — {because}" if because else ""
            lines.append(
                f"- #{f.get('id', '?')} {f.get('description', '')} "
                f"(due {f.get('date', '?')}){impact_tag}{because_tag}"
            )
        lines.append("")

    impact_summary = data.get("impact_queue_summary") or {}
    if impact_summary.get("top_changes"):
        lines.append("## Queue Changes By Impact")
        for item in impact_summary.get("top_changes", [])[:5]:
            delta = float(item.get("delta") or 0.0)
            if abs(delta) < 1.0:
                continue
            direction = "+" if delta >= 0 else ""
            lines.append(
                f"- #{item.get('id', '?')} {direction}{delta:.1f} -> {float(item.get('impact_score') or 0.0):.1f}"
                f" ({item.get('impact_reasoning') or 'score recalculated'})"
            )
        lines.append("")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text("\n".join(lines))
    log(f"Fallback synthesis written to {OUTPUT_FILE}")


def main():
    if not should_run():
        log(f"Already ran today ({TODAY_STR}). Skipping.")
        return

    lock_fd = acquire_lock()
    try:
        log(f"=== NEXO Synthesis v2 -- {TODAY_STR} ===")

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
            log("Synthesis CLI failed -- writing fallback synthesis.")
            fallback_synthesis(data)
            mark_done()

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
