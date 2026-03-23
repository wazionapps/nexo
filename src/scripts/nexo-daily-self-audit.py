#!/usr/bin/env python3
"""
NEXO Daily Self-Audit
Proactively scans for common issues before they become problems.
Runs via launchd at 7:00 AM daily. Results saved to NEXO_HOME/logs/self-audit.log
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))

LOG_DIR = NEXO_HOME / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "self-audit.log"
NEXO_DB = NEXO_HOME / "nexo.db"
# Optional: project directory for git checks — set via env var
PROJECT_DIR_STR = os.environ.get("NEXO_PROJECT_DIR", "")
PROJECT_DIR = Path(PROJECT_DIR_STR) if PROJECT_DIR_STR else None
HASH_REGISTRY = NEXO_HOME / "scripts" / ".watchdog-hashes"
CORTEX_LOG_DIR = NEXO_HOME / "cortex" / "logs"

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


# ── Check 1: Overdue reminders ──────────────────────────────────────────
def check_overdue_reminders():
    if not NEXO_DB.exists():
        return
    conn = sqlite3.connect(str(NEXO_DB))
    today = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT description, date FROM reminders WHERE status='PENDIENTE' AND date < ? AND date != '' ORDER BY date",
        (today,)
    ).fetchall()
    conn.close()
    if rows:
        finding("WARN", "reminders", f"{len(rows)} overdue: {', '.join(r[0][:40] for r in rows[:5])}")


# ── Check 2: Overdue followups ──────────────────────────────────────────
def check_overdue_followups():
    if not NEXO_DB.exists():
        return
    conn = sqlite3.connect(str(NEXO_DB))
    today = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT description, date FROM followups WHERE status='PENDIENTE' AND date < ? AND date != '' ORDER BY date",
        (today,)
    ).fetchall()
    conn.close()
    if rows:
        finding("WARN", "followups", f"{len(rows)} overdue: {', '.join(r[0][:40] for r in rows[:5])}")


# ── Check 3: Git uncommitted changes in project dir ─────────────────────────
def check_uncommitted_changes():
    if not PROJECT_DIR or not PROJECT_DIR.exists():
        return
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(PROJECT_DIR), capture_output=True, text=True
    )
    lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
    if len(lines) > 10:
        finding("WARN", "git", f"{len(lines)} uncommitted changes in project repo")


# ── Check 4: Cron error logs (last 24h) ────────────────────────────────
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


# ── Check 5: Evolution failures ─────────────────────────────────────────
def check_evolution_health():
    obj_file = NEXO_HOME / "cortex" / "evolution-objective.json"
    if not obj_file.exists():
        return
    obj = json.loads(obj_file.read_text())
    failures = obj.get("consecutive_failures", 0)
    if failures >= 2:
        finding("WARN", "evolution", f"{failures} consecutive failures — circuit breaker at 3")
    if not obj.get("evolution_enabled", True):
        finding("ERROR", "evolution", f"Evolution DISABLED: {obj.get('disabled_reason', 'unknown')}")


# ── Check 6: Disk space ────────────────────────────────────────────────
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


# ── Check 7: NEXO DB size ──────────────────────────────────────────────
def check_db_size():
    if NEXO_DB.exists():
        size_mb = NEXO_DB.stat().st_size / (1024 * 1024)
        if size_mb > 100:
            finding("WARN", "database", f"nexo.db is {size_mb:.1f} MB — consider cleanup")


# ── Check 8: Stale sessions ────────────────────────────────────────────
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


# ── Check 9: Error repetition rate (Guard) ─────────────────────────────
def check_repetition_rate():
    """Alert if >30% of learnings in last 3 days are repetitions."""
    if not NEXO_DB.exists():
        return
    conn = sqlite3.connect(str(NEXO_DB))
    cutoff_3d = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_epoch = (datetime.now() - timedelta(days=3)).timestamp()

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
            finding("ERROR", "guard", f"Repetition rate {rate:.0%} over last 3 days ({repetitions}/{new_learnings}) — exceeds 30% threshold")
        elif rate > 0.20:
            finding("WARN", "guard", f"Repetition rate {rate:.0%} over last 3 days ({repetitions}/{new_learnings})")


# ── Check 10: Unused learnings ─────────────────────────────────────────
def check_unused_learnings():
    """Find learnings >7 days old never returned by guard_check."""
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
        finding("WARN", "guard", f"Guard never used — {old_learnings} learnings sitting idle. Call nexo_guard_check before edits.")
    elif total_checks > 0 and total_checks < 5:
        finding("INFO", "guard", f"Only {total_checks} guard checks performed — aim for >5 per session")


# ── Check 11: Memory reviews due ────────────────────────────────────────
def check_memory_reviews():
    """Alert when decisions/learnings are due for review."""
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

    total_due = due_learnings + due_decisions
    if total_due >= 10:
        finding("WARN", "memory", f"{total_due} memory reviews due ({due_decisions} decisions, {due_learnings} learnings)")
    elif total_due > 0:
        finding("INFO", "memory", f"{total_due} memory reviews due ({due_decisions} decisions, {due_learnings} learnings)")


# ── Check 12: Cognitive memory health ────────────────────────────────
def check_cognitive_health():
    """Check cognitive.db health and run weekly GC on Sundays."""
    cognitive_db = NEXO_HOME / "cognitive.db"
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
    finding("INFO", "cognitive", f"STM: {stm_count} (sensory: {sensory_count}) | LTM: {ltm_active} active, {ltm_dormant} dormant | {size_mb:.1f} MB | avg STM strength: {avg_stm_str:.2f}")

    if avg_stm_str < 0.3 and stm_count > 20:
        finding("WARN", "cognitive", f"STM average strength very low ({avg_stm_str:.2f}) — memories decaying without access")

    # Metrics report (spec section 9)
    try:
        import cognitive as cog

        metrics = cog.get_metrics(days=7)
        if metrics["total_retrievals"] > 0:
            finding("INFO", "cognitive-metrics",
                    f"7d: {metrics['total_retrievals']} retrievals, "
                    f"relevance={metrics['retrieval_relevance_pct']}%, "
                    f"avg_score={metrics['avg_top_score']}, "
                    f"{metrics['retrievals_per_day']}/day")

            if metrics["needs_multilingual"]:
                finding("WARN", "cognitive-metrics",
                        f"Retrieval relevance {metrics['retrieval_relevance_pct']}% < 70% — consider switching to multilingual model")

            if metrics["retrieval_relevance_pct"] < 50 and metrics["total_retrievals"] >= 5:
                finding("ERROR", "cognitive-metrics",
                        f"Retrieval relevance critically low: {metrics['retrieval_relevance_pct']}%")

        # Repeat error rate
        repeats = cog.check_repeat_errors()
        if repeats["new_count"] > 0:
            finding("INFO", "cognitive-metrics",
                    f"Repeat errors: {repeats['duplicate_count']}/{repeats['new_count']} "
                    f"({repeats['repeat_rate_pct']}%) — target <10%")
            if repeats["repeat_rate_pct"] > 30:
                finding("WARN", "cognitive-metrics",
                        f"Repeat error rate {repeats['repeat_rate_pct']}% exceeds 30% threshold")

        # Write metrics to file for dashboard/tracking
        metrics_file = LOG_DIR / "cognitive-metrics.json"
        metrics_file.write_text(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "retrieval": metrics,
            "repeats": {k: v for k, v in repeats.items() if k != "duplicates"},
        }, indent=2))
    except Exception as e:
        finding("WARN", "cognitive-metrics", f"Metrics collection failed: {e}")

    # Phase triggers monitoring
    try:
        import cognitive as cog

        db_cog = cog._get_db()

        # v2.0: Procedural memory — trigger: >50 procedural change_logs
        procedural_markers = ['1.', '2.', '3.', 'step ', 'Step ', 'then ', 'first ', 'First ', '→', '->', 'git commit', 'deploy']
        changes = db_cog.execute('SELECT content FROM ltm_memories WHERE source_type = "change"').fetchall()
        procedural_count = sum(1 for r in changes if sum(1 for m in procedural_markers if m in r[0]) >= 2)
        if procedural_count >= 50:
            finding("WARN", "cognitive-phase", f"v2.0 TRIGGER MET: {procedural_count} procedural memories (>50). Implement Store 4 (procedural memory).")

        # v2.1: MEMORY reduction — trigger: RAG relevance >80% for 30 days
        metrics_file = LOG_DIR / "cognitive-metrics-history.json"
        try:
            history = json.loads(metrics_file.read_text()) if metrics_file.exists() else []
        except Exception:
            history = []

        # Append today's metrics
        m = cog.get_metrics(days=1)
        if m["total_retrievals"] > 0:
            history.append({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "relevance": m["retrieval_relevance_pct"],
                "retrievals": m["total_retrievals"],
            })
            # Keep last 60 days
            history = history[-60:]
            metrics_file.write_text(json.dumps(history, indent=2))

        # Check if last 30 entries all have relevance >80%
        if len(history) >= 30:
            last_30 = history[-30:]
            all_above_80 = all(h["relevance"] >= 80.0 for h in last_30)
            if all_above_80:
                finding("WARN", "cognitive-phase", "v2.1 TRIGGER MET: RAG relevance >80% for 30 consecutive days. Consider reducing static memory files.")

        # v2.2: Dashboard — trigger: 30 days of metrics
        if len(history) >= 30:
            finding("INFO", "cognitive-phase", f"v2.2 TRIGGER MET: {len(history)} days of metrics accumulated. Implement HTML dashboard.")

        # v3.0: Clustering — trigger: LTM >1000
        ltm_count = db_cog.execute('SELECT COUNT(*) FROM ltm_memories WHERE is_dormant = 0').fetchone()[0]
        if ltm_count >= 1000:
            finding("WARN", "cognitive-phase", f"v3.0 TRIGGER MET: {ltm_count} LTM vectors (>1000). Implement K-means clustering.")

    except Exception as e:
        finding("WARN", "cognitive-phase", f"Phase trigger check failed: {e}")

    # Weekly GC on Sundays
    if datetime.now().weekday() == 6:
        log("  Running weekly cognitive GC (Sunday)...")
        try:
            import cognitive as cog

            # 1. Delete STM with strength < 0.1 and > 30 days
            gc_stm = cog.gc_stm()

            # 2. GC sensory > 48h (should already be cleaned by postmortem, but safety net)
            gc_sensory = cog.gc_sensory(max_age_hours=48)

            # 3. Delete dormant LTM with strength < 0.1 and > 30 days
            gc_ltm = cog.gc_ltm_dormant(min_age_days=30)

            log(f"  Weekly GC results: STM removed={gc_stm}, sensory removed={gc_sensory}, LTM dormant removed={gc_ltm}")
            if gc_stm + gc_sensory + gc_ltm > 0:
                finding("INFO", "cognitive", f"Weekly GC cleaned: {gc_stm} STM + {gc_sensory} sensory + {gc_ltm} dormant LTM")
        except Exception as e:
            finding("WARN", "cognitive", f"Weekly GC failed: {e}")


# ── Main ────────────────────────────────────────────────────────────────
def main():
    # Ensure cognitive module is importable
    src_dir = NEXO_HOME / "src"
    if src_dir.exists() and str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    log("=" * 60)
    log("NEXO Daily Self-Audit starting")

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
    check_cognitive_health()

    errors = sum(1 for f in findings if f["severity"] == "ERROR")
    warns = sum(1 for f in findings if f["severity"] == "WARN")
    infos = sum(1 for f in findings if f["severity"] == "INFO")

    log(f"Audit complete: {errors} errors, {warns} warnings, {infos} info")

    # Write summary for NEXO startup to read
    summary_file = LOG_DIR / "self-audit-summary.json"
    summary_file.write_text(json.dumps({
        "timestamp": datetime.now().isoformat(),
        "findings": findings,
        "counts": {"error": errors, "warn": warns, "info": infos}
    }, indent=2))

    # Register successful run for catch-up
    try:
        import json as _json
        _state_file = NEXO_HOME / "operations" / ".catchup-state.json"
        _state = _json.loads(_state_file.read_text()) if _state_file.exists() else {}
        _state["self-audit"] = datetime.now().isoformat()
        _state_file.write_text(_json.dumps(_state, indent=2))
    except Exception:
        pass

    log("=" * 60)
    return 1 if errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
