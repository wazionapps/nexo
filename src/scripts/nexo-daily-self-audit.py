#!/usr/bin/env python3
"""
NEXO Daily Self-Audit v2

Stage A — Mechanical checks (Python pure, unchanged):
  18 checks: overdue reminders, disk space, DB size, stale sessions, guard stats,
  cognitive health, snapshot drift, etc. All pure queries, no intelligence needed.

Stage B — Interpretation (Claude CLI opus):
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

LOG_DIR = NEXO_HOME / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
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
CLAUDE_CLI = Path.home() / ".local" / "bin" / "claude"

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


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_watchdog_registry():
    if not HASH_REGISTRY.exists():
        return
    text = HASH_REGISTRY.read_text(errors="ignore")
    forbidden = ["CLAUDE.md", "server.py", "plugin_loader.py"]
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


# ═══════════════════════════════════════════════════════════════════════════════
# Stage B: Interpretation (Claude CLI opus) — NEW in v2
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

    log("Stage B: Invoking Claude CLI (opus) for interpretation...")
    env = os.environ.copy()
    env["NEXO_HEADLESS"] = "1"  # Skip stop hook post-mortem
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE", None)

    try:
        result = subprocess.run(
            [str(CLAUDE_CLI), "-p", prompt, "--model", "opus",
             "--output-format", "text",
             "--allowedTools", "Read,Write,Edit,Glob,Grep,Bash,mcp__nexo__*"],
            capture_output=True, text=True, timeout=21600, env=env
        )

        if result.returncode != 0:
            log(f"Stage B: CLI error ({result.returncode})")
            return False

        log(f"Stage B: Interpretation complete ({len(result.stdout or '')} chars)")
        return True

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

    # Write raw summary (backward compatible)
    summary_file = LOG_DIR / "self-audit-summary.json"
    summary_file.write_text(json.dumps({
        "timestamp": datetime.now().isoformat(),
        "findings": findings,
        "counts": {"error": errors, "warn": warns, "info": infos}
    }, indent=2))

    # Stage B: CLI interpretation
    interpret_findings(findings)

    # Register for catch-up
    try:
        state_file = NEXO_HOME / "operations" / ".catchup-state.json"
        st = json.loads(state_file.read_text()) if state_file.exists() else {}
        st["self-audit"] = datetime.now().isoformat()
        state_file.write_text(json.dumps(st, indent=2))
    except Exception:
        pass

    log("=" * 60)
    return 1 if errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
