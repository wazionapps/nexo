#!/usr/bin/env python3
"""
NEXO Followup Hygiene — Weekly cleanup of followup/reminder statuses.

Runs Sundays via LaunchAgent (or manually). Tasks:
1. Normalize dirty statuses (COMPLETED YYYY-MM-DD → COMPLETED)
2. Flag PENDING followups >14 days without updates as STALE
3. Generate summary of orphaned/forgotten followups for synthesis

No CLI needed — this is pure mechanical cleanup.
"""

import json
import sqlite3
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

NEXO_DB = Path.home() / ".nexo" / "nexo-mcp" / "nexo.db"
COORD_DIR = Path.home() / ".nexo" / "coordination"
LOG_FILE = Path.home() / ".nexo" / "logs" / "followup-hygiene.log"

TODAY = date.today().isoformat()


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def main():
    log("=== Followup Hygiene starting ===")

    if not NEXO_DB.exists():
        log("nexo.db not found")
        return

    conn = sqlite3.connect(str(NEXO_DB))
    conn.row_factory = sqlite3.Row

    # 1. Normalize dirty statuses
    dirty_f = conn.execute("SELECT COUNT(*) FROM followups WHERE status LIKE 'COMPLETED %'").fetchone()[0]
    dirty_r = conn.execute("SELECT COUNT(*) FROM reminders WHERE status LIKE 'COMPLETED %'").fetchone()[0]

    if dirty_f > 0:
        conn.execute("UPDATE followups SET status='COMPLETED' WHERE status LIKE 'COMPLETED %'")
        log(f"Normalized {dirty_f} dirty followup statuses")

    if dirty_r > 0:
        conn.execute("UPDATE reminders SET status='COMPLETED' WHERE status LIKE 'COMPLETED %'")
        log(f"Normalized {dirty_r} dirty reminder statuses")

    # 2. Flag stale followups (PENDING >14 days, no updates)
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    stale = conn.execute(
        "SELECT id, description, date, updated_at FROM followups "
        "WHERE status NOT LIKE 'COMPLETED%' AND status NOT LIKE 'COMPLETED%' "
        "AND date != '' AND date < ? "
        "ORDER BY date",
        (cutoff,)
    ).fetchall()

    if stale:
        log(f"Found {len(stale)} stale followups (>14 days overdue):")
        for s in stale[:10]:
            log(f"  {s['id']}: {s['description'][:60]} (due: {s['date']})")

    # 3. Orphaned followups (no date, no recent update)
    orphans = conn.execute(
        "SELECT id, description FROM followups "
        "WHERE status NOT LIKE 'COMPLETED%' AND status NOT LIKE 'COMPLETED%' "
        "AND (date IS NULL OR date = '') "
        "ORDER BY id"
    ).fetchall()

    if orphans:
        log(f"Found {len(orphans)} orphaned followups (no date):")
        for o in orphans[:10]:
            log(f"  {o['id']}: {o['description'][:60]}")

    conn.commit()
    conn.close()

    # 4. Write summary for synthesis
    summary = {
        "date": TODAY,
        "dirty_normalized": dirty_f + dirty_r,
        "stale_count": len(stale) if stale else 0,
        "orphan_count": len(orphans) if orphans else 0,
        "stale_ids": [s["id"] for s in stale[:20]] if stale else [],
        "orphan_ids": [o["id"] for o in orphans[:20]] if orphans else [],
    }

    summary_file = COORD_DIR / "followup-hygiene-summary.json"
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    summary_file.write_text(json.dumps(summary, indent=2))

    log(f"Summary: {dirty_f + dirty_r} normalized, {len(stale) if stale else 0} stale, {len(orphans) if orphans else 0} orphans")
    log("=== Followup Hygiene complete ===")


if __name__ == "__main__":
    main()
