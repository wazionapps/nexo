#!/usr/bin/env python3
"""
NEXO Daily Self-Audit v2

Stage A — Mechanical checks (Python pure, unchanged):
  18 checks: overdue reminders, disk space, DB size, stale sessions, guard stats,
  cognitive health, snapshot drift, etc. All pure queries, no intelligence needed.

Stage B — Interpretation (automation backend):
  Takes the raw findings from Stage A and UNDERSTANDS them:
  - Groups related findings
  - Identifies root causes
  - Prioritizes what actually matters
  - Suggests specific actions
  - Writes actionable summary

Runs via launchd at 7:00 AM daily.
"""
import json
import hashlib
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
# Auto-detect: if running from repo (src/scripts/), use src/ as NEXO_CODE
_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent  # src/scripts/ -> src/
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(_repo_src) if (_repo_src / "server.py").exists() else str(NEXO_HOME)))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

from agent_runner import AutomationBackendUnavailableError, run_automation_prompt

LOG_DIR = NEXO_HOME / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_HISTORY_DIR = LOG_DIR / "self-audit"
AUDIT_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "self-audit.log"
NEXO_DB = NEXO_HOME / "data" / "nexo.db"
# Configure your main project repo to check for uncommitted changes (optional)
PROJECT_REPO_DIR = None  # e.g., Path.home() / "projects" / "my-repo"
HASH_REGISTRY = NEXO_HOME / "scripts" / ".watchdog-hashes"
SNAPSHOT_GOLDEN = NEXO_HOME / "snapshots" / "golden" / "files" / "claude"
RUNTIME_PREFLIGHT_SUMMARY = LOG_DIR / "runtime-preflight-summary.json"
WATCHDOG_SMOKE_SUMMARY = LOG_DIR / "watchdog-smoke-summary.json"
RESTORE_LOG = LOG_DIR / "snapshot-restores.log"
CORTEX_LOG_DIR = NEXO_HOME / "brain" / "logs"
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
        Path.home() / ".local" / "bin" / "claude",
        Path.home() / ".npm-global" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
    ]:
        if candidate.exists():
            return candidate
    return Path.home() / ".local" / "bin" / "claude"

CLAUDE_CLI = _resolve_claude_cli()

findings = []


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def finding(severity, area, msg):
    findings.append({"severity": severity, "area": area, "msg": msg})
    log(f"  [{severity}] {area}: {msg}")


def _parse_iso_dt(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _area_summary_from_daily_summaries(summaries: list[dict]) -> tuple[list[dict], list[str]]:
    per_area: dict[str, dict] = {}
    area_days: dict[str, set[str]] = {}
    for item in summaries:
        day = str(item.get("date_label") or item.get("timestamp") or "")[:10]
        for finding_item in item.get("findings", []):
            area = str(finding_item.get("area") or "unknown").strip() or "unknown"
            severity = str(finding_item.get("severity") or "INFO").strip().upper()
            bucket = per_area.setdefault(area, {"area": area, "count": 0, "error": 0, "warn": 0, "info": 0})
            bucket["count"] += 1
            if severity == "ERROR":
                bucket["error"] += 1
            elif severity == "WARN":
                bucket["warn"] += 1
            else:
                bucket["info"] += 1
            if day:
                area_days.setdefault(area, set()).add(day)
    top_areas = sorted(
        per_area.values(),
        key=lambda item: (-item["count"], -item["error"], item["area"]),
    )[:10]
    repeated = sorted(area for area, days in area_days.items() if len(days) >= 2)
    return top_areas, repeated


def _load_recent_daily_summaries(reference_dt: datetime, window_days: int) -> list[dict]:
    summaries: list[dict] = []
    cutoff = reference_dt - timedelta(days=window_days - 1)
    for path in sorted(AUDIT_HISTORY_DIR.glob("*-daily-summary.json")):
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        ts = _parse_iso_dt(payload.get("timestamp"))
        if not ts:
            continue
        if ts.date() < cutoff.date() or ts.date() > reference_dt.date():
            continue
        summaries.append(payload)
    summaries.sort(key=lambda item: str(item.get("timestamp") or ""))
    return summaries


def write_horizon_summaries(summary_payload: dict, *, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    daily_payload = dict(summary_payload)
    daily_payload.setdefault("date_label", now.strftime("%Y-%m-%d"))
    daily_file = AUDIT_HISTORY_DIR / f"{daily_payload['date_label']}-daily-summary.json"
    daily_file.write_text(json.dumps(daily_payload, indent=2))

    outputs = {
        "daily_file": str(daily_file),
        "weekly_file": "",
        "weekly_latest": "",
        "monthly_file": "",
        "monthly_latest": "",
    }
    for kind, window_days in (("weekly", 7), ("monthly", 30)):
        recent = _load_recent_daily_summaries(now, window_days)
        total_counts = {"error": 0, "warn": 0, "info": 0}
        for item in recent:
            counts = item.get("counts") or {}
            for key in total_counts:
                total_counts[key] += int(counts.get(key) or 0)
        top_areas, repeated_areas = _area_summary_from_daily_summaries(recent)
        if kind == "weekly":
            year, week, _ = now.isocalendar()
            label = f"{year}-W{week:02d}"
        else:
            label = now.strftime("%Y-%m")
        rollup = {
            "timestamp": now.isoformat(),
            "label": label,
            "horizon": kind,
            "window_days": window_days,
            "source_daily_summaries": len(recent),
            "days": [item.get("date_label") for item in recent if item.get("date_label")],
            "counts": total_counts,
            "top_areas": top_areas,
            "repeated_areas": repeated_areas,
        }
        dated_file = AUDIT_HISTORY_DIR / f"{label}-{kind}-summary.json"
        latest_file = LOG_DIR / f"self-audit-{kind}-summary.json"
        dated_file.write_text(json.dumps(rollup, indent=2))
        latest_file.write_text(json.dumps(rollup, indent=2))
        outputs[f"{kind}_file"] = str(dated_file)
        outputs[f"{kind}_latest"] = str(latest_file)
    return outputs


def _protocol_debt_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='protocol_debt'"
    ).fetchone()
    return bool(row)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _ensure_protocol_debt(conn: sqlite3.Connection, *, debt_type: str, severity: str, evidence: str) -> bool:
    existing = conn.execute(
        """SELECT id
           FROM protocol_debt
           WHERE status = 'open' AND debt_type = ? AND evidence = ?
           LIMIT 1""",
        (debt_type, evidence),
    ).fetchone()
    if existing:
        return False
    conn.execute(
        """INSERT INTO protocol_debt (session_id, task_id, debt_type, severity, evidence)
           VALUES ('', '', ?, ?, ?)""",
        (debt_type, severity, evidence),
    )
    return True


def _ensure_followup(conn: sqlite3.Connection, *, prefix: str, description: str,
                     verification: str, reasoning: str, priority: str = "high") -> str:
    if not _table_exists(conn, "followups"):
        return ""
    followup_id = f"NF-{prefix}-{hashlib.sha1(description.encode('utf-8')).hexdigest()[:8].upper()}"
    existing = conn.execute(
        """SELECT id FROM followups
           WHERE status NOT LIKE 'COMPLETED%'
             AND status NOT IN ('DELETED','archived','blocked','waiting')
             AND description = ?
           LIMIT 1""",
        (description,),
    ).fetchone()
    if existing:
        return str(existing["id"])
    now_epoch = int(datetime.now().timestamp())
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(followups)").fetchall()}
    existing_id_row = conn.execute(
        "SELECT id, status FROM followups WHERE id = ? LIMIT 1",
        (followup_id,),
    ).fetchone()
    if existing_id_row:
        update_fields = {
            "description": description,
            "verification": verification,
            "reasoning": reasoning,
            "updated_at": now_epoch,
        }
        if "priority" in columns:
            update_fields["priority"] = priority
        closed_status = str(existing_id_row["status"] or "").upper()
        if closed_status.startswith("COMPLETED") or closed_status in {"DELETED", "ARCHIVED", "BLOCKED", "WAITING"}:
            update_fields["status"] = "PENDING"
        ordered_updates = [name for name in update_fields.keys() if name in columns]
        if ordered_updates:
            assignments = ", ".join(f"{name} = ?" for name in ordered_updates)
            conn.execute(
                f"UPDATE followups SET {assignments} WHERE id = ?",
                [update_fields[name] for name in ordered_updates] + [followup_id],
            )
        return followup_id

    values = {
        "id": followup_id,
        "date": "",
        "description": description,
        "verification": verification,
        "status": "PENDING",
        "reasoning": reasoning,
        "recurrence": None,
        "created_at": now_epoch,
        "updated_at": now_epoch,
    }
    if "priority" in columns:
        values["priority"] = priority

    ordered_columns = [name for name in values.keys() if name in columns]
    placeholders = ", ".join("?" for _ in ordered_columns)
    conn.execute(
        f"INSERT INTO followups ({', '.join(ordered_columns)}) VALUES ({placeholders})",
        [values[name] for name in ordered_columns],
    )
    return followup_id


TOPIC_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "about", "after",
    "before", "again", "need", "needs", "task", "tasks", "work", "working",
    "continue", "continuing", "review", "check", "checks", "make", "making",
    "fix", "fixes", "build", "create", "created", "update", "updates", "ship",
    "prepare", "finish", "open", "another", "around", "must",
}


def _topic_signature(text: str) -> str:
    tokens = [
        token for token in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(token) >= 3 and token not in TOPIC_STOPWORDS
    ]
    return " ".join(tokens[:2])


REPAIR_KEYWORDS = {
    "fix", "fixed", "bug", "bugs", "regression", "regressions", "repair", "repaired",
    "correct", "corrected", "correction", "typo", "hotfix", "patch", "patched",
    "resolve", "resolved", "failure", "error", "issue", "broken", "broke",
}


def _split_changed_files(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            value = json.loads(text)
        except Exception:
            value = []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
    parts = re.split(r"[\n,;]+", text)
    return [part.strip() for part in parts if part.strip()]


def _looks_like_repair_change(text: str) -> bool:
    tokens = {token for token in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(token) >= 3}
    return bool(tokens & REPAIR_KEYWORDS)


def _parse_mixed_datetime(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _learning_matches_change(row: sqlite3.Row, files: list[str], change_text: str, created_at: datetime | None) -> bool:
    learning_text = " ".join(
        str(row[key] or "")
        for key in ("title", "content", "reasoning", "prevention")
        if key in row.keys()
    )
    applies_to = str(row["applies_to"] or "").strip() if "applies_to" in row.keys() else ""
    if files and applies_to:
        applies_tokens = {item for item in _split_changed_files(applies_to)}
        if any(file_path in applies_tokens or Path(file_path).name in applies_to for file_path in files):
            return True
    change_signature = _topic_signature(change_text)
    learning_signature = _topic_signature(learning_text)
    if change_signature and learning_signature and change_signature == learning_signature:
        return True
    if change_signature and change_signature in learning_text.lower():
        return True

    updated_at = _parse_mixed_datetime(row["updated_at"] if "updated_at" in row.keys() else None)
    if created_at and updated_at:
        delta = updated_at - created_at
        if timedelta(hours=-1) <= delta <= timedelta(days=3):
            return True
    return False


def _attempt_repair_learning_auto_capture(row: sqlite3.Row) -> dict:
    try:
        from tools_learnings import find_conflicting_active_learning, handle_learning_add
    except Exception as exc:
        return {"ok": False, "error": f"learning runtime unavailable: {exc}"}

    files = _split_changed_files(str(row["files"] or ""))
    title_seed = str(row["what_changed"] or row["why"] or "").strip() or f"Repair change #{row['id']}"
    title = title_seed[:120]
    content_parts = [
        str(row["what_changed"] or "").strip(),
        str(row["why"] or "").strip(),
    ]
    if files:
        content_parts.append(f"Affected files: {', '.join(files[:5])}")
    content = " ".join(part for part in content_parts if part).strip()
    if not content:
        content = f"Repair-oriented change log entry #{row['id']} required a canonical learning."
    applies_to = ",".join(files)
    conflicting = find_conflicting_active_learning(
        category="nexo-ops",
        title=title,
        content=content,
        applies_to=applies_to,
    )
    supersedes_id = int(conflicting["id"]) if conflicting else 0
    response = handle_learning_add(
        category="nexo-ops",
        title=title,
        content=content,
        reasoning=f"Auto-captured by daily self-audit from repair change #{row['id']}.",
        prevention="Review the canonical repair learning before touching the affected file again." if applies_to else "",
        applies_to=applies_to,
        priority="high",
        supersedes_id=supersedes_id,
    )
    match = re.search(r"Learning #(\d+)", response)
    if match and "ERROR:" not in response:
        return {
            "ok": True,
            "learning_id": int(match.group(1)),
            "response": response,
        }
    return {"ok": False, "error": response}


# ═══════════════════════════════════════════════════════════════════════════════
# Stage A: Mechanical checks (UNCHANGED from v1 — all 18 checks)
# ═══════════════════════════════════════════════════════════════════════════════

def check_overdue_reminders():
    if not NEXO_DB.exists():
        return
    conn = sqlite3.connect(str(NEXO_DB))
    today = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT description, date FROM reminders WHERE status='PENDING' AND date < ? AND date != '' ORDER BY date",
        (today,)
    ).fetchall()
    conn.close()
    if rows:
        finding("WARN", "reminders", f"{len(rows)} overdue: {', '.join(r[0][:40] for r in rows[:5])}")


def check_overdue_followups():
    if not NEXO_DB.exists():
        return
    conn = sqlite3.connect(str(NEXO_DB))
    today = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT description, date FROM followups WHERE status='PENDING' AND date < ? AND date != '' ORDER BY date",
        (today,)
    ).fetchall()
    conn.close()
    if rows:
        finding("WARN", "followups", f"{len(rows)} overdue: {', '.join(r[0][:40] for r in rows[:5])}")


def check_uncommitted_changes():
    if not PROJECT_REPO_DIR or not PROJECT_REPO_DIR.exists():
        return
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(PROJECT_REPO_DIR), capture_output=True, text=True
    )
    lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
    if len(lines) > 10:
        finding("WARN", "git", f"{len(lines)} uncommitted changes in project repo")


def check_cron_errors():
    if not NEXO_DB.exists():
        return
    conn = sqlite3.connect(str(NEXO_DB))
    yesterday = (datetime.now() - timedelta(days=1)).isoformat()
    rows = conn.execute(
        "SELECT category, title FROM learnings WHERE category='cron_error' AND created_at > ? ORDER BY created_at DESC",
        (yesterday,)
    ).fetchall()
    conn.close()
    if rows:
        finding("ERROR", "crons", f"{len(rows)} cron errors in last 24h")


def check_evolution_health():
    # Check brain/ (canonical) first, fall back to cortex/ (legacy)
    obj_file = NEXO_HOME / "brain" / "evolution-objective.json"
    if not obj_file.exists():
        obj_file = NEXO_HOME / "cortex" / "evolution-objective.json"
    if not obj_file.exists():
        return
    obj = json.loads(obj_file.read_text())
    failures = obj.get("consecutive_failures", 0)
    if failures >= 2:
        finding("WARN", "evolution", f"{failures} consecutive failures — circuit breaker at 3")
    if not obj.get("evolution_enabled", True):
        finding("ERROR", "evolution", f"Evolution DISABLED: {obj.get('disabled_reason', 'unknown')}")


def check_disk_space():
    result = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
    for line in result.stdout.strip().split("\n")[1:]:
        parts = line.split()
        if len(parts) >= 5:
            usage_pct = int(parts[4].replace("%", ""))
            if usage_pct > 90:
                finding("ERROR", "disk", f"Root disk at {usage_pct}% capacity")
            elif usage_pct > 80:
                finding("WARN", "disk", f"Root disk at {usage_pct}% capacity")


def check_db_size():
    if NEXO_DB.exists():
        size_mb = NEXO_DB.stat().st_size / (1024 * 1024)
        if size_mb > 100:
            finding("WARN", "database", f"nexo.db is {size_mb:.1f} MB — consider cleanup")


def check_stale_sessions():
    if not NEXO_DB.exists():
        return
    conn = sqlite3.connect(str(NEXO_DB))
    cutoff = (datetime.now() - timedelta(hours=2)).timestamp()
    day_ago = (datetime.now() - timedelta(days=1)).timestamp()
    rows = conn.execute(
        "SELECT sid, task FROM sessions WHERE last_update_epoch < ? AND last_update_epoch > ?",
        (cutoff, day_ago)
    ).fetchall()
    conn.close()
    if rows:
        finding("INFO", "sessions", f"{len(rows)} stale sessions (no heartbeat >2h)")


def check_repetition_rate():
    if not NEXO_DB.exists():
        return
    conn = sqlite3.connect(str(NEXO_DB))
    cutoff_epoch = (datetime.now() - timedelta(days=3)).timestamp()
    cutoff_3d = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    new_learnings = conn.execute(
        "SELECT COUNT(*) FROM learnings WHERE created_at > ?", (cutoff_epoch,)
    ).fetchone()[0]
    repetitions = conn.execute(
        "SELECT COUNT(*) FROM error_repetitions WHERE created_at > ?", (cutoff_3d,)
    ).fetchone()[0]
    conn.close()
    if new_learnings > 0:
        rate = repetitions / new_learnings
        if rate > 0.30:
            finding("ERROR", "guard", f"Repetition rate {rate:.0%} ({repetitions}/{new_learnings})")
        elif rate > 0.20:
            finding("WARN", "guard", f"Repetition rate {rate:.0%} ({repetitions}/{new_learnings})")


def check_unused_learnings():
    if not NEXO_DB.exists():
        return
    conn = sqlite3.connect(str(NEXO_DB))
    cutoff_epoch = (datetime.now() - timedelta(days=7)).timestamp()
    old_learnings = conn.execute(
        "SELECT COUNT(*) FROM learnings WHERE created_at < ?", (cutoff_epoch,)
    ).fetchone()[0]
    total_checks = conn.execute("SELECT COUNT(*) FROM guard_checks").fetchone()[0]
    conn.close()
    if total_checks == 0 and old_learnings > 10:
        finding("WARN", "guard", f"Guard never used — {old_learnings} learnings idle")


def check_memory_reviews():
    if not NEXO_DB.exists():
        return
    conn = sqlite3.connect(str(NEXO_DB))
    now_epoch = datetime.now().timestamp()
    now_iso = datetime.now().isoformat(timespec="seconds")
    try:
        due_learnings = conn.execute(
            "SELECT COUNT(*) FROM learnings WHERE review_due_at IS NOT NULL AND status != 'superseded' AND review_due_at <= ?",
            (now_epoch,)
        ).fetchone()[0]
        due_decisions = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE review_due_at IS NOT NULL AND status != 'reviewed' AND review_due_at <= ?",
            (now_iso,)
        ).fetchone()[0]
    except sqlite3.OperationalError:
        conn.close()
        return
    conn.close()
    total = due_learnings + due_decisions
    if total >= 10:
        finding("WARN", "memory", f"{total} reviews due ({due_decisions} decisions, {due_learnings} learnings)")
    elif total > 0:
        finding("INFO", "memory", f"{total} reviews due")


def check_learning_contradictions():
    if not NEXO_DB.exists():
        return
    conn = sqlite3.connect(str(NEXO_DB))
    conn.row_factory = sqlite3.Row
    if not _table_exists(conn, "learnings"):
        conn.close()
        return

    from tools_learnings import _applies_overlap, _looks_contradictory

    rows = conn.execute(
        """SELECT id, title, content, applies_to
           FROM learnings
           WHERE status = 'active' AND COALESCE(applies_to, '') != ''
           ORDER BY updated_at DESC, id DESC
           LIMIT 200"""
    ).fetchall()
    contradictions: list[tuple[sqlite3.Row, sqlite3.Row]] = []
    for index, left in enumerate(rows):
        for right in rows[index + 1:]:
            if not _applies_overlap(left["applies_to"], right["applies_to"]):
                continue
            if not _looks_contradictory(
                f"{left['title']} {left['content']}",
                f"{right['title']} {right['content']}",
            ):
                continue
            contradictions.append((left, right))

    if contradictions:
        finding("ERROR", "contradictions", f"{len(contradictions)} contradictory active learning pair(s)")
        for left, right in contradictions[:5]:
            description = (
                f"Resolve contradictory active learnings #{left['id']} and #{right['id']} "
                f"for {left['applies_to'] or right['applies_to']}"
            )
            reasoning = (
                "Daily self-audit found two active canonical rules that contradict each other. "
                "One rule must be superseded or reconciled before the next edit repeats the error."
            )
            _ensure_followup(
                conn,
                prefix="CONTRADICTION",
                description=description,
                verification="One canonical learning remains active and the conflicting rule is superseded or archived",
                reasoning=reasoning,
                priority="critical",
            )
        conn.commit()
    conn.close()


def check_error_memory_loop():
    if not NEXO_DB.exists():
        return
    conn = sqlite3.connect(str(NEXO_DB))
    conn.row_factory = sqlite3.Row
    if not _table_exists(conn, "protocol_tasks"):
        conn.close()
        return

    rows = conn.execute(
        """SELECT task_id, goal, area, files, status, learning_id
           FROM protocol_tasks
           WHERE status IN ('failed', 'blocked')
             AND (learning_id IS NULL OR learning_id = 0)
             AND opened_at >= datetime('now', '-30 days')
           ORDER BY opened_at DESC"""
    ).fetchall()

    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        files = str(row["files"] or "").strip()
        signature = files if files and files != "[]" else (row["area"] or row["goal"] or "general")
        grouped.setdefault(signature[:220], []).append(row)

    repeated = {signature: items for signature, items in grouped.items() if len(items) >= 2}
    if repeated:
        finding("WARN", "prevention", f"{len(repeated)} repeated failure cluster(s) still lack canonical prevention learnings")
        for signature, items in list(repeated.items())[:5]:
            description = (
                f"Mine a canonical prevention learning from repeated failed/blocked protocol tasks around {signature}"
            )
            reasoning = (
                f"Daily self-audit found {len(items)} failed/blocked protocol tasks without a linked learning. "
                "Turn the repeated failure into a prevention rule before it repeats again."
            )
            _ensure_followup(
                conn,
                prefix="PREVENTION",
                description=description,
                verification="Canonical prevention learning captured and linked to the repeated failure pattern",
                reasoning=reasoning,
                priority="high",
            )
        conn.commit()
    conn.close()


def check_repair_changes_missing_learning_capture():
    if not NEXO_DB.exists():
        return
    conn = sqlite3.connect(str(NEXO_DB))
    conn.row_factory = sqlite3.Row
    if not _table_exists(conn, "change_log") or not _table_exists(conn, "learnings"):
        conn.close()
        return

    learning_rows = conn.execute(
        """SELECT *
           FROM learnings
           WHERE COALESCE(status, 'active') != 'deleted'
           ORDER BY updated_at DESC, created_at DESC
           LIMIT 300"""
    ).fetchall()
    if not learning_rows:
        learning_rows = []

    rows = conn.execute(
        """SELECT id, files, what_changed, why, created_at
           FROM change_log
           WHERE created_at >= datetime('now', '-14 days')
           ORDER BY created_at DESC
           LIMIT 200"""
    ).fetchall()
    missing: list[sqlite3.Row] = []
    for row in rows:
        change_text = f"{row['what_changed'] or ''} {row['why'] or ''}".strip()
        if not _looks_like_repair_change(change_text):
            continue
        files = _split_changed_files(str(row["files"] or ""))
        created_at = _parse_mixed_datetime(row["created_at"])
        if any(_learning_matches_change(learning, files, change_text, created_at) for learning in learning_rows):
            continue
        missing.append(row)

    if missing:
        auto_captured = 0
        unresolved: list[sqlite3.Row] = []
        for row in missing:
            captured = _attempt_repair_learning_auto_capture(row)
            if captured.get("ok"):
                auto_captured += 1
                continue
            unresolved.append(row)

        if unresolved:
            finding(
                "WARN",
                "learning-capture",
                f"{len(unresolved)} repair/logged fix change(s) still lack linked learnings "
                f"after {auto_captured} self-audit auto-capture(s)",
            )
        else:
            finding(
                "INFO",
                "learning-capture",
                f"Self-audit auto-captured {auto_captured} missing repair learning(s)",
            )

        for row in unresolved[:5]:
            files = _split_changed_files(str(row["files"] or ""))
            target = files[0] if files else str(row["what_changed"] or "recent repair")[:120]
            evidence = (
                f"Repair-oriented change log entry #{row['id']} on {target} has no nearby linked learning capture."
            )
            _ensure_protocol_debt(
                conn,
                debt_type="repair_change_without_learning_capture",
                severity="warn",
                evidence=evidence,
            )
            _ensure_followup(
                conn,
                prefix="LEARNCAP",
                description=f"Capture canonical learning for repair change touching {target}",
                verification="A learning exists with applies_to/topic linked to the repair change",
                reasoning="Daily self-audit found a repair/fix change log entry with no durable learning attached.",
                priority="high",
            )
        conn.commit()
    conn.close()


def check_unformalized_mentions():
    if not NEXO_DB.exists():
        return
    conn = sqlite3.connect(str(NEXO_DB))
    conn.row_factory = sqlite3.Row
    if not _table_exists(conn, "protocol_tasks"):
        conn.close()
        return

    rows = conn.execute(
        """SELECT goal, area, learning_id, followup_id
           FROM protocol_tasks
           WHERE opened_at >= datetime('now', '-30 days')
             AND COALESCE(goal, '') != ''
           ORDER BY opened_at DESC"""
    ).fetchall()
    if not rows:
        conn.close()
        return

    formalized_topics: set[str] = set()
    if _table_exists(conn, "workflow_goals"):
        goal_rows = conn.execute(
            """SELECT title, objective
               FROM workflow_goals
               WHERE status NOT IN ('abandoned', 'cancelled')"""
        ).fetchall()
        for row in goal_rows:
            for candidate in (row["title"], row["objective"]):
                signature = _topic_signature(str(candidate or ""))
                if signature:
                    formalized_topics.add(signature)

    repeated: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        if row["learning_id"] or str(row["followup_id"] or "").strip():
            continue
        signature = _topic_signature(str(row["goal"] or ""))
        if not signature or signature in formalized_topics:
            continue
        area = str(row["area"] or "general").strip() or "general"
        repeated.setdefault((area, signature), []).append(row)

    loose_topics = {
        key: items
        for key, items in repeated.items()
        if len(items) >= 2
    }
    if loose_topics:
        finding("WARN", "formalization", f"{len(loose_topics)} repeated topic(s) keep being mentioned without durable formalization")
        for (area, signature), items in list(loose_topics.items())[:5]:
            sample_goal = str(items[0]["goal"] or "").strip()[:120]
            description = (
                f"Formalize repeated unresolved theme in {area}: '{sample_goal}' "
                f"appears {len(items)} times without a durable goal, followup, or learning."
            )
            reasoning = (
                "Daily self-audit found the same theme recurring across protocol tasks without being "
                "converted into a workflow goal, followup, or learning. Formalize it before it keeps resurfacing."
            )
            _ensure_followup(
                conn,
                prefix="FORMALIZE",
                description=description,
                verification="Theme converted into a durable goal, followup, or canonical learning",
                reasoning=reasoning,
                priority="high",
            )
        conn.commit()
    conn.close()


def check_automation_opportunities():
    if not NEXO_DB.exists():
        return
    conn = sqlite3.connect(str(NEXO_DB))
    conn.row_factory = sqlite3.Row
    if not _table_exists(conn, "protocol_tasks"):
        conn.close()
        return

    rows = conn.execute(
        """SELECT goal, area, files
           FROM protocol_tasks
           WHERE status = 'done'
             AND closed_at >= datetime('now', '-30 days')
           ORDER BY closed_at DESC"""
    ).fetchall()
    if not rows:
        conn.close()
        return

    grouped: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        signature = str(row["files"] or "").strip() or _topic_signature(str(row["goal"] or ""))
        if not signature:
            continue
        area = str(row["area"] or "general").strip() or "general"
        grouped.setdefault((area, signature[:220]), []).append(row)

    repeated = {
        key: items
        for key, items in grouped.items()
        if len(items) >= 3
    }
    if repeated:
        finding("INFO", "opportunities", f"{len(repeated)} repeated manual pattern(s) are good candidates for skills/scripts")
        for (area, signature), items in list(repeated.items())[:5]:
            sample_goal = str(items[0]["goal"] or "").strip()[:120]
            description = (
                f"Extract a reusable automation for repeated {area} work around '{sample_goal}' "
                f"(seen {len(items)} successful protocol tasks in 30 days)."
            )
            reasoning = (
                "Daily self-audit found repeated successful manual work. Convert it into a skill, script, "
                "or reusable workflow before it keeps consuming operator time."
            )
            _ensure_followup(
                conn,
                prefix="OPPORTUNITY",
                description=description,
                verification="A reusable skill, script, or workflow now covers the repeated manual pattern",
                reasoning=reasoning,
                priority="medium",
            )
        conn.commit()
    conn.close()


def check_state_watchers():
    try:
        import importlib
        import db as _db
        import state_watchers_runtime as _state_watchers_runtime
    except Exception as exc:
        finding("WARN", "watchers", f"state watchers runtime unavailable: {exc}")
        return
    importlib.reload(_db)
    runtime = importlib.reload(_state_watchers_runtime)
    summary = runtime.run_state_watchers(persist=True)
    counts = summary.get("counts") or {}
    if int(counts.get("critical") or 0) > 0:
        finding("ERROR", "watchers", f"{counts.get('critical')} critical state watcher(s)")
    elif int(counts.get("degraded") or 0) > 0:
        finding("WARN", "watchers", f"{counts.get('degraded')} degraded state watcher(s)")
    elif int(summary.get("watcher_count") or 0) > 0:
        finding("INFO", "watchers", f"{summary.get('watcher_count')} state watcher(s) healthy")


def check_memory_quality_scores():
    if not NEXO_DB.exists():
        return
    conn = sqlite3.connect(str(NEXO_DB))
    conn.row_factory = sqlite3.Row
    if not _table_exists(conn, "learnings"):
        conn.close()
        return
    try:
        from tools_learnings import score_learning_quality
    except Exception:
        conn.close()
        return

    rows = conn.execute(
        """SELECT *
           FROM learnings
           WHERE status = 'active'
           ORDER BY updated_at DESC, id DESC
           LIMIT 200"""
    ).fetchall()
    if not rows:
        conn.close()
        return

    normalized = [dict(row) for row in rows]
    scored = [(row, score_learning_quality(row, conn)) for row in normalized]
    weak = [(row, quality) for row, quality in scored if quality["score"] < 60]
    fragile_conditioned = [
        (row, quality)
        for row, quality in weak
        if str(row.get("applies_to") or "").strip()
    ]
    if weak:
        finding("WARN", "memory-quality", f"{len(weak)} active learning(s) have low quality scores")
        if fragile_conditioned:
            sample = fragile_conditioned[0][0]
            description = (
                f"Refresh low-quality conditioned learnings; first weak rule is #{sample['id']} "
                f"for {sample['applies_to']}"
            )
        else:
            sample = weak[0][0]
            description = f"Refresh low-quality learnings; first weak rule is #{sample['id']} {sample['title']}"
        _ensure_followup(
            conn,
            prefix="MEMQ",
            description=description,
            verification="Weak active learnings refreshed with stronger reasoning/prevention/applies_to coverage",
            reasoning="Daily self-audit found active learnings with weak quality scores that may mislead retrieval or guard.",
            priority="high" if fragile_conditioned else "medium",
        )
        conn.commit()
    conn.close()


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_watchdog_registry():
    if not HASH_REGISTRY.exists():
        return
    text = HASH_REGISTRY.read_text(errors="ignore")
    forbidden = ["CLAUDE.md", "AGENTS.md", "server.py", "plugin_loader.py"]
    bad = [name for name in forbidden if name in text]
    if bad:
        finding("ERROR", "watchdog", f"mutable files still protected: {', '.join(bad)}")


def check_snapshot_sync():
    pairs = [
        (NEXO_CODE / "db" / "__init__.py", SNAPSHOT_GOLDEN / "db" / "__init__.py"),
        (NEXO_CODE / "evolution_cycle.py", SNAPSHOT_GOLDEN / "evolution_cycle.py"),
    ]
    drift = [live.name for live, snap in pairs
             if not live.exists() or not snap.exists() or _sha256(live) != _sha256(snap)]
    if drift:
        finding("WARN", "snapshots", f"golden snapshot drift: {', '.join(drift)}")


def check_restore_activity():
    if not RESTORE_LOG.exists():
        return
    cutoff_day = datetime.now() - timedelta(days=1)
    current_hour_prefix = datetime.now().strftime("%Y-%m-%d %H")
    recent_day = 0
    recent_hour = 0
    for line in RESTORE_LOG.read_text(errors="ignore").splitlines():
        if not line.startswith("[") or "/.codex/memories/nexo-" in line:
            continue
        try:
            ts = datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if ts >= cutoff_day:
            recent_day += 1
        if line[1:14] == current_hour_prefix:
            recent_hour += 1
    if recent_hour > 2:
        finding("ERROR", "restore", f"{recent_hour} restores in last hour")
    elif recent_day > 5:
        finding("WARN", "restore", f"{recent_day} restores in last 24h")


def check_bad_responses():
    if not CORTEX_LOG_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=1)
    bad = [p for p in CORTEX_LOG_DIR.glob("bad-response-*.json")
           if datetime.fromtimestamp(p.stat().st_mtime) >= cutoff]
    if bad:
        finding("WARN", "cortex", f"{len(bad)} bad model responses in last 24h")


def check_runtime_preflight():
    if not RUNTIME_PREFLIGHT_SUMMARY.exists():
        return
    data = json.loads(RUNTIME_PREFLIGHT_SUMMARY.read_text())
    ts = data.get("timestamp")
    try:
        when = datetime.fromisoformat(ts)
    except Exception:
        return
    if when < datetime.now() - timedelta(days=1):
        finding("WARN", "preflight", "runtime preflight older than 24h")
    if not data.get("ok", False):
        finding("ERROR", "preflight", "runtime preflight failing")


def run_watchdog_smoke():
    """Run the watchdog smoke test so its summary is fresh before we check it."""
    smoke_script = Path(__file__).resolve().parent / "nexo-watchdog-smoke.py"
    if not smoke_script.exists():
        finding("WARN", "watchdog", f"smoke script not found at {smoke_script}")
        return
    try:
        result = subprocess.run(
            [sys.executable, str(smoke_script)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            finding("WARN", "watchdog", f"smoke test exited {result.returncode}")
    except subprocess.TimeoutExpired:
        finding("ERROR", "watchdog", "smoke test timed out (60s)")
    except Exception as e:
        finding("WARN", "watchdog", f"smoke test failed: {e}")


def check_watchdog_smoke():
    if not WATCHDOG_SMOKE_SUMMARY.exists():
        return
    data = json.loads(WATCHDOG_SMOKE_SUMMARY.read_text())
    ts = data.get("timestamp")
    try:
        when = datetime.fromisoformat(ts)
    except Exception:
        return
    if when < datetime.now() - timedelta(days=1):
        finding("WARN", "watchdog", "watchdog smoke older than 24h")
    if not data.get("ok", False):
        finding("ERROR", "watchdog", "watchdog smoke failing")


def check_cognitive_health():
    cognitive_db = NEXO_HOME / "data" / "cognitive.db"
    if not cognitive_db.exists():
        finding("WARN", "cognitive", "cognitive.db not found")
        return

    conn = sqlite3.connect(str(cognitive_db))
    stm_count = conn.execute("SELECT COUNT(*) FROM stm_memories WHERE promoted_to_ltm = 0").fetchone()[0]
    ltm_active = conn.execute("SELECT COUNT(*) FROM ltm_memories WHERE is_dormant = 0").fetchone()[0]
    ltm_dormant = conn.execute("SELECT COUNT(*) FROM ltm_memories WHERE is_dormant = 1").fetchone()[0]
    avg_stm_str = conn.execute("SELECT AVG(strength) FROM stm_memories WHERE promoted_to_ltm = 0").fetchone()[0] or 0.0
    sensory_count = conn.execute("SELECT COUNT(*) FROM stm_memories WHERE source_type = 'sensory' AND promoted_to_ltm = 0").fetchone()[0]
    conn.close()

    size_mb = cognitive_db.stat().st_size / (1024 * 1024)
    finding("INFO", "cognitive", f"STM: {stm_count} (sensory: {sensory_count}) | LTM: {ltm_active} active, {ltm_dormant} dormant | {size_mb:.1f} MB")

    if avg_stm_str < 0.3 and stm_count > 20:
        finding("WARN", "cognitive", f"STM average strength very low ({avg_stm_str:.2f})")

    # Metrics
    try:
        sys.path.insert(0, str(NEXO_CODE))
        import cognitive as cog
        metrics = cog.get_metrics(days=7)
        if metrics["total_retrievals"] > 0:
            finding("INFO", "cognitive-metrics",
                    f"7d: {metrics['total_retrievals']} retrievals, relevance={metrics['retrieval_relevance_pct']}%")
            if metrics["retrieval_relevance_pct"] < 50 and metrics["total_retrievals"] >= 5:
                finding("ERROR", "cognitive-metrics", f"Relevance critically low: {metrics['retrieval_relevance_pct']}%")

        repeats = cog.check_repeat_errors()
        if repeats["new_count"] > 0 and repeats["repeat_rate_pct"] > 30:
            finding("WARN", "cognitive-metrics", f"Repeat rate {repeats['repeat_rate_pct']}% > 30%")

        # Save metrics
        metrics_file = LOG_DIR / "cognitive-metrics.json"
        metrics_file.write_text(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "retrieval": metrics,
            "repeats": {k: v for k, v in repeats.items() if k != "duplicates"},
        }, indent=2))

        # Track history for phase triggers
        history_file = LOG_DIR / "cognitive-metrics-history.json"
        try:
            history = json.loads(history_file.read_text()) if history_file.exists() else []
        except Exception:
            history = []
        m1 = cog.get_metrics(days=1)
        if m1["total_retrievals"] > 0:
            history.append({"date": datetime.now().strftime("%Y-%m-%d"),
                            "relevance": m1["retrieval_relevance_pct"],
                            "retrievals": m1["total_retrievals"]})
            history = history[-60:]
            history_file.write_text(json.dumps(history, indent=2))

    except Exception as e:
        finding("WARN", "cognitive-metrics", f"Metrics failed: {e}")

    # Weekly GC on Sundays
    if datetime.now().weekday() == 6:
        try:
            sys.path.insert(0, str(NEXO_CODE))
            import cognitive as cog
            gc_stm = cog.gc_stm()
            gc_sensory = cog.gc_sensory(max_age_hours=48)
            gc_ltm = cog.gc_ltm_dormant(min_age_days=30)
            if gc_stm + gc_sensory + gc_ltm > 0:
                finding("INFO", "cognitive", f"Weekly GC: {gc_stm} STM + {gc_sensory} sensory + {gc_ltm} dormant")
        except Exception as e:
            finding("WARN", "cognitive", f"Weekly GC failed: {e}")


def check_codex_conditioned_file_discipline():
    try:
        from doctor.providers.runtime import _recent_codex_conditioned_file_discipline_status
    except Exception as e:
        finding("WARN", "codex-discipline", f"Codex discipline audit unavailable: {e}")
        return

    audit = _recent_codex_conditioned_file_discipline_status()
    if not audit.get("conditioned_rules"):
        return

    read_violations = int(audit.get("read_without_protocol") or 0)
    write_without_protocol = int(audit.get("write_without_protocol") or 0)
    write_without_guard_ack = int(audit.get("write_without_guard_ack") or 0)
    delete_without_protocol = int(audit.get("delete_without_protocol") or 0)
    delete_without_guard_ack = int(audit.get("delete_without_guard_ack") or 0)
    total_violations = (
        read_violations
        + write_without_protocol
        + write_without_guard_ack
        + delete_without_protocol
        + delete_without_guard_ack
    )
    if total_violations <= 0:
        return

    created_debts = 0
    if NEXO_DB.exists():
        conn = sqlite3.connect(str(NEXO_DB))
        if _protocol_debt_table_exists(conn):
            debt_type_map = {
                "read_without_protocol": ("codex_conditioned_read_without_protocol", "warn"),
                "write_without_protocol": ("codex_conditioned_write_without_protocol", "error"),
                "write_without_guard_ack": ("codex_conditioned_write_without_guard_ack", "error"),
                "delete_without_protocol": ("codex_conditioned_delete_without_protocol", "error"),
                "delete_without_guard_ack": ("codex_conditioned_delete_without_guard_ack", "error"),
            }
            for sample in audit.get("samples", []):
                debt_info = debt_type_map.get(sample.get("kind"))
                if not debt_info:
                    continue
                debt_type, severity = debt_info
                evidence = (
                    "Codex conditioned-file transcript audit: "
                    f"{sample.get('kind')} {sample.get('file')} via {sample.get('tool')} "
                    f"in {sample.get('session_file')}"
                )
                if _ensure_protocol_debt(conn, debt_type=debt_type, severity=severity, evidence=evidence):
                    created_debts += 1
            conn.commit()
        conn.close()

    severity = "ERROR" if (write_without_protocol or write_without_guard_ack) else "WARN"
    message = (
        "Codex conditioned-file discipline drift: "
        f"{read_violations} read(s) without protocol/guard, "
        f"{write_without_protocol} write(s) without protocol, "
        f"{write_without_guard_ack} write(s) without guard ack, "
        f"{delete_without_protocol} delete(s) without protocol, "
        f"{delete_without_guard_ack} delete(s) without guard ack"
    )
    if created_debts:
        message += f" | opened {created_debts} protocol debt item(s)"
    finding(severity, "codex-discipline", message)


def check_codex_startup_discipline():
    try:
        from doctor.providers.runtime import _recent_codex_session_parity_status
    except Exception as e:
        finding("WARN", "codex-startup", f"Codex startup audit unavailable: {e}")
        return

    audit = _recent_codex_session_parity_status()
    if not audit.get("files"):
        return

    samples = audit.get("samples", [])
    missing_startup = [sample for sample in samples if not sample.get("startup")]
    missing_heartbeat = [sample for sample in samples if sample.get("startup") and not sample.get("heartbeat")]
    missing_bootstrap = [
        sample for sample in samples
        if sample.get("startup") and sample.get("heartbeat") and not sample.get("bootstrap")
    ]
    if not missing_startup and not missing_heartbeat and not missing_bootstrap:
        return

    created_debts = 0
    if NEXO_DB.exists():
        conn = sqlite3.connect(str(NEXO_DB))
        if _protocol_debt_table_exists(conn):
            for sample in samples:
                debt_type = ""
                severity = "warn"
                if not sample.get("startup"):
                    debt_type = "codex_session_missing_startup"
                    severity = "error"
                elif not sample.get("heartbeat"):
                    debt_type = "codex_session_missing_heartbeat"
                elif not sample.get("bootstrap"):
                    debt_type = "codex_session_missing_bootstrap"
                if not debt_type:
                    continue
                evidence = (
                    "Codex session parity audit: "
                    f"{debt_type} in {sample.get('file')} "
                    f"(origin={sample.get('origin') or 'unknown'})"
                )
                if _ensure_protocol_debt(conn, debt_type=debt_type, severity=severity, evidence=evidence):
                    created_debts += 1
            conn.commit()
        conn.close()

    severity = "ERROR" if missing_startup else "WARN"
    message = (
        "Codex startup discipline drift: "
        f"{len(missing_bootstrap)} session(s) missing bootstrap marker, "
        f"{len(missing_startup)} missing startup, "
        f"{len(missing_heartbeat)} missing heartbeat"
    )
    if created_debts:
        message += f" | opened {created_debts} protocol debt item(s)"
    finding(severity, "codex-startup", message)


# ═══════════════════════════════════════════════════════════════════════════════
# Stage B: Interpretation (automation backend) — NEW in v2
# ═══════════════════════════════════════════════════════════════════════════════

def interpret_findings(raw_findings: list) -> bool:
    """CLI interprets the raw findings with real understanding."""

    errors = [f for f in raw_findings if f["severity"] == "ERROR"]
    warns = [f for f in raw_findings if f["severity"] == "WARN"]

    # Don't invoke CLI if everything is clean
    if not errors and not warns:
        log("Stage B: All clean, no interpretation needed.")
        return True

    findings_json = json.dumps(raw_findings, ensure_ascii=False, indent=1)

    prompt = f"""FIRST: Call nexo_startup(task='daily self-audit') to register this session.

You are NEXO's morning self-audit interpreter. The mechanical checks found
{len(errors)} errors and {len(warns)} warnings. Your job is to UNDERSTAND what's
actually wrong, not just list findings. Use nexo_learning_add for new findings and nexo_followup_create for action items.

RAW FINDINGS:
{findings_json}

Write an actionable audit report to {LOG_DIR}/self-audit-interpreted.md:

# NEXO Self-Audit — {datetime.now().strftime('%Y-%m-%d')}

## Critical (needs immediate action)
[Group related findings, identify ROOT CAUSE, suggest specific fix]

## Warnings (should address today)
[Same: group, root cause, specific action]

## Observations
[Trends, things getting worse, things improving]

## Recommended Actions (priority order)
1. [Most important action with specific command/steps]
2. ...

Be specific. "Fix the DB" is useless. "Archive learnings >90 days in category X
via sqlite3 nexo.db 'UPDATE...'" is useful.

Also write the machine-readable summary to {LOG_DIR}/self-audit-summary.json.

    Execute without asking."""

    log("Stage B: Invoking automation backend for interpretation...")
    try:
        result = run_automation_prompt(
            prompt,
            model="opus",
            timeout=21600,
            output_format="text",
            allowed_tools="Read,Write,Edit,Glob,Grep,Bash,mcp__nexo__*",
        )

        if result.returncode != 0:
            log(f"Stage B: CLI error ({result.returncode})")
            return False

        log(f"Stage B: Interpretation complete ({len(result.stdout or '')} chars)")
        return True

    except AutomationBackendUnavailableError as e:
        log(f"Stage B: automation backend unavailable: {e}")
        return False
    except subprocess.TimeoutExpired:
        log("Stage B: CLI timed out")
        return False
    except Exception as e:
        log(f"Stage B: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    log("=" * 60)
    log("NEXO Daily Self-Audit v2 starting")

    # Stage A: Run all mechanical checks (unchanged)
    check_overdue_reminders()
    check_overdue_followups()
    check_uncommitted_changes()
    check_cron_errors()
    check_evolution_health()
    check_disk_space()
    check_db_size()
    check_stale_sessions()
    check_repetition_rate()
    check_unused_learnings()
    check_memory_reviews()
    check_learning_contradictions()
    check_error_memory_loop()
    check_repair_changes_missing_learning_capture()
    check_unformalized_mentions()
    check_automation_opportunities()
    check_state_watchers()
    check_memory_quality_scores()
    check_codex_startup_discipline()
    check_codex_conditioned_file_discipline()
    check_watchdog_registry()
    check_snapshot_sync()
    check_restore_activity()
    check_bad_responses()
    check_runtime_preflight()
    run_watchdog_smoke()
    check_watchdog_smoke()
    check_cognitive_health()

    errors = sum(1 for f in findings if f["severity"] == "ERROR")
    warns = sum(1 for f in findings if f["severity"] == "WARN")
    infos = sum(1 for f in findings if f["severity"] == "INFO")
    log(f"Stage A complete: {errors} errors, {warns} warnings, {infos} info")

    # Write raw summary (backward compatible) + horizon rollups
    summary_payload = {
        "timestamp": datetime.now().isoformat(),
        "findings": findings,
        "counts": {"error": errors, "warn": warns, "info": infos},
        "date_label": datetime.now().strftime("%Y-%m-%d"),
    }
    summary_file = LOG_DIR / "self-audit-summary.json"
    summary_file.write_text(json.dumps(summary_payload, indent=2))
    write_horizon_summaries(summary_payload)

    # Stage B: CLI interpretation (graceful fallback if CLI unavailable)
    cli_ok = interpret_findings(findings)
    if not cli_ok:
        log("Stage B: CLI unavailable or failed. Stage A results saved to self-audit-summary.json.")

    # Register for catch-up
    try:
        state_file = NEXO_HOME / "operations" / ".catchup-state.json"
        st = json.loads(state_file.read_text()) if state_file.exists() else {}
        st["self-audit"] = datetime.now().isoformat()
        state_file.write_text(json.dumps(st, indent=2))
    except Exception:
        pass

    if errors or warns:
        log(
            f"Self-audit completed with findings: {errors} errors, {warns} warnings, {infos} info. "
            f"Summary written to {summary_file}."
        )
    else:
        log(
            f"Self-audit completed cleanly: {errors} errors, {warns} warnings, {infos} info. "
            f"Summary written to {summary_file}."
        )

    log("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
