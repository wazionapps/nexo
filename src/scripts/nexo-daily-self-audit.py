#!/usr/bin/env python3
"""
NEXO Daily Self-Audit
Proactively scans for common issues before they become problems.
Runs via launchd at 7:00 AM daily. Results saved to ~/claude/logs/self-audit.log
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

LOG_DIR = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))) / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "self-audit.log"
NEXO_DB = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))) / "nexo.db"
# Project directory for git checks (user configurable)
HASH_REGISTRY = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))) / "scripts" / ".watchdog-hashes"
SNAPSHOT_GOLDEN = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))) / "snapshots" / "golden" / "files" / "claude"
RUNTIME_PREFLIGHT_SUMMARY = LOG_DIR / "runtime-preflight-summary.json"
WATCHDOG_SMOKE_SUMMARY = LOG_DIR / "watchdog-smoke-summary.json"
RESTORE_LOG = LOG_DIR / "snapshot-restores.log"
CORTEX_LOG_DIR = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))) / "cortex" / "logs"

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


def check_uncommitted_changes():
    if not WAZION_DIR.exists():
        return
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(WAZION_DIR), capture_output=True, text=True
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
    obj_file = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))) / "cortex" / "evolution-objective.json"
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── Check 12: Watchdog registry sanity ──────────────────────────────────
def check_watchdog_registry():
    if not HASH_REGISTRY.exists():
        finding("WARN", "watchdog", "hash registry missing")
        return
    text = HASH_REGISTRY.read_text(errors="ignore")
    forbidden = ["CLAUDE.md", "db.py", "server.py", "plugin_loader.py", "cortex-wrapper.py"]
    bad = [name for name in forbidden if name in text]
    if bad:
        finding("ERROR", "watchdog", f"mutable files still protected by watchdog: {', '.join(bad)}")


# ── Check 13: Snapshot drift on protected recovery files ────────────────
def check_snapshot_sync():
    pairs = [
        (Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))) / "db.py", SNAPSHOT_GOLDEN / "nexo-mcp" / "db.py"),
        (Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))) / "cortex" / "cortex-wrapper.py", SNAPSHOT_GOLDEN / "cortex" / "cortex-wrapper.py"),
        (Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))) / "cortex" / "evolution_cycle.py", SNAPSHOT_GOLDEN / "cortex" / "evolution_cycle.py"),
    ]
    drift = []
    for live, snap in pairs:
        if not live.exists() or not snap.exists():
            drift.append(live.name)
            continue
        if _sha256(live) != _sha256(snap):
            drift.append(live.name)
    if drift:
        finding("WARN", "snapshots", f"golden snapshot drift: {', '.join(drift)}")


# ── Check 14: Recent restore activity ───────────────────────────────────
def check_restore_activity():
    if not RESTORE_LOG.exists():
        return
    cutoff_day = datetime.now() - timedelta(days=1)
    current_hour_prefix = datetime.now().strftime("%Y-%m-%d %H")
    recent_day = 0
    recent_hour = 0
    for line in RESTORE_LOG.read_text(errors="ignore").splitlines():
        if not line.startswith("["):
            continue
        if "/.codex/memories/nexo-" in line:
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
        finding("ERROR", "restore", f"{recent_hour} snapshot restores in last hour")
    elif recent_day > 5:
        finding("WARN", "restore", f"{recent_day} snapshot restores in last 24h")
    elif recent_day > 0:
        finding("INFO", "restore", f"{recent_day} snapshot restores in last 24h (historical activity)")


# ── Check 15: Bad model responses ───────────────────────────────────────
def check_bad_responses():
    if not CORTEX_LOG_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=1)
    bad = [
        p for p in CORTEX_LOG_DIR.glob("bad-response-*.json")
        if datetime.fromtimestamp(p.stat().st_mtime) >= cutoff
    ]
    if bad:
        finding("WARN", "cortex", f"{len(bad)} bad model responses in last 24h")


# ── Check 16: Runtime preflight freshness ───────────────────────────────
def check_runtime_preflight():
    if not RUNTIME_PREFLIGHT_SUMMARY.exists():
        finding("WARN", "preflight", "runtime preflight summary missing")
        return
    data = json.loads(RUNTIME_PREFLIGHT_SUMMARY.read_text())
    ts = data.get("timestamp")
    try:
        when = datetime.fromisoformat(ts)
    except Exception:
        finding("WARN", "preflight", "runtime preflight timestamp invalid")
        return
    if when < datetime.now() - timedelta(days=1):
        finding("WARN", "preflight", "runtime preflight older than 24h")
    if not data.get("ok", False):
        finding("ERROR", "preflight", "runtime preflight failing")


# ── Check 17: Watchdog smoke freshness ──────────────────────────────────
def check_watchdog_smoke():
    if not WATCHDOG_SMOKE_SUMMARY.exists():
        finding("WARN", "watchdog", "watchdog smoke summary missing")
        return
    data = json.loads(WATCHDOG_SMOKE_SUMMARY.read_text())
    ts = data.get("timestamp")
    try:
        when = datetime.fromisoformat(ts)
    except Exception:
        finding("WARN", "watchdog", "watchdog smoke timestamp invalid")
        return
    if when < datetime.now() - timedelta(days=1):
        finding("WARN", "watchdog", "watchdog smoke older than 24h")
    if not data.get("ok", False):
        finding("ERROR", "watchdog", "watchdog smoke failing")


# ── Check 18: Cognitive memory health ────────────────────────────────
def check_cognitive_health():
    """Check cognitive.db health and run weekly GC on Sundays."""
    cognitive_db = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))) / "cognitive.db"
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
        sys.path.insert(0, str(Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))))
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
                        f"Retrieval relevance {metrics['retrieval_relevance_pct']}% < 70% — consider switching to multilingual model (spec 13.3)")

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

    # Phase triggers monitoring (spec section 10)
    try:
        sys.path.insert(0, str(Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))))
        import cognitive as cog

        db_cog = cog._get_db()

        # v2.0: Procedural memory — trigger: >50 procedural change_logs
        procedural_markers = ['1.', '2.', '3.', 'step ', 'Step ', 'then ', 'first ', 'First ', '→', '->', 'SSH', 'scp', 'git commit', 'deploy']
        changes = db_cog.execute('SELECT content FROM ltm_memories WHERE source_type = "change"').fetchall()
        procedural_count = sum(1 for r in changes if sum(1 for m in procedural_markers if m in r[0]) >= 2)
        if procedural_count >= 50:
            finding("WARN", "cognitive-phase", f"v2.0 TRIGGER MET: {procedural_count} procedural memories (>50). Implement Store 4 (memoria procedimental).")

        # v2.1: MEMORY.md reduction — trigger: RAG relevance >80% for 30 days
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
                finding("WARN", "cognitive-phase", "v2.1 TRIGGER MET: RAG relevance >80% for 30 consecutive days. Reduce MEMORY.md to ~20 lines.")

        # v2.2: Dashboard — trigger: 30 days of metrics
        if len(history) >= 30:
            finding("INFO", "cognitive-phase", f"v2.2 TRIGGER MET: {len(history)} days of metrics accumulated. Implement HTML dashboard.")

        # v3.0: Clustering — trigger: LTM >1000
        ltm_count = db_cog.execute('SELECT COUNT(*) FROM ltm_memories WHERE is_dormant = 0').fetchone()[0]
        if ltm_count >= 1000:
            finding("WARN", "cognitive-phase", f"v3.0 TRIGGER MET: {ltm_count} LTM vectors (>1000). Implement K-means clustering.")

        # v1.4: Multilingual — already checked in metrics section above

    except Exception as e:
        finding("WARN", "cognitive-phase", f"Phase trigger check failed: {e}")

    # Weekly GC on Sundays
    if datetime.now().weekday() == 6:
        log("  Running weekly cognitive GC (Sunday)...")
        try:
            sys.path.insert(0, str(Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))))
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
    check_watchdog_registry()
    check_snapshot_sync()
    check_restore_activity()
    check_bad_responses()
    check_runtime_preflight()
    check_watchdog_smoke()
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
        _state_file = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))) / "operations" / ".catchup-state.json"
        _state = _json.loads(_state_file.read_text()) if _state_file.exists() else {}
        _state["self-audit"] = datetime.now().isoformat()
        _state_file.write_text(_json.dumps(_state, indent=2))
    except Exception:
        pass

    log("=" * 60)
    return 1 if errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
