#!/usr/bin/env python3
import os
"""
NEXO Followup Hygiene — Weekly cleanup of followup/reminder statuses.

Runs Sundays via LaunchAgent (or manually). Tasks:
1. Normalize dirty statuses (COMPLETED YYYY-MM-DD -> COMPLETED)
2. Escalate PENDING followups >14 days without updates to needs_decision
3. Generate summary of orphaned/forgotten followups for synthesis

No CLI needed — this is pure mechanical cleanup.
"""

import json
import sqlite3
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(_repo_src) if (_repo_src / "server.py").exists() else str(NEXO_HOME)))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

import db as nexo_db
import paths

NEXO_DB = paths.db_path()
COORD_DIR = paths.coordination_dir()
LOG_FILE = paths.logs_dir() / "followup-hygiene.log"

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
        dirty_followups = conn.execute(
            "SELECT id, status FROM followups WHERE status LIKE 'COMPLETED %'"
        ).fetchall()
        for row in dirty_followups:
            nexo_db.update_followup(
                str(row["id"]),
                status="COMPLETED",
                history_actor="followup-hygiene",
                history_event="normalized",
                history_note=f"Weekly hygiene normalized dirty status from {row['status']} to COMPLETED.",
            )
        log(f"Normalized {dirty_f} dirty followup statuses")

    if dirty_r > 0:
        dirty_reminders = conn.execute(
            "SELECT id, status FROM reminders WHERE status LIKE 'COMPLETED %'"
        ).fetchall()
        for row in dirty_reminders:
            nexo_db.update_reminder(
                str(row["id"]),
                status="COMPLETED",
                history_actor="followup-hygiene",
                history_event="normalized",
                history_note=f"Weekly hygiene normalized dirty status from {row['status']} to COMPLETED.",
            )
        log(f"Normalized {dirty_r} dirty reminder statuses")

    # 2. Escalate stale followups (PENDING >14 days, no updates)
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    updated_cutoff = datetime.now().timestamp() - (14 * 24 * 60 * 60)
    stale = conn.execute(
        "SELECT id, description, date, updated_at FROM followups "
        "WHERE status NOT LIKE 'COMPLETED%' "
        "AND UPPER(COALESCE(status, '')) NOT IN ('DELETED','ARCHIVED','BLOCKED','WAITING','WAITING_EXTERNAL','NEEDS_DECISION','WAITING_USER','PARKED','EXPIRED','DONE') "
        "AND date != '' AND date < ? "
        "AND (updated_at IS NULL OR updated_at = '' OR updated_at < ?) "
        "ORDER BY date",
        (cutoff, updated_cutoff)
    ).fetchall()

    escalated_stale = []
    if stale:
        log(f"Escalating {len(stale)} stale followups (>14 days overdue, no recent update):")
        for s in stale[:10]:
            log(f"  {s['id']}: {s['description'][:60]} (due: {s['date']})")
        for s in stale:
            result = nexo_db.update_followup(
                str(s["id"]),
                status="needs_decision",
                date=TODAY,
                history_actor="followup-hygiene",
                history_event="stale_triage",
                history_note=(
                    "Weekly hygiene escalated this old due followup to needs_decision "
                    "instead of leaving it in the executable briefing indefinitely."
                ),
            )
            if not result.get("error"):
                escalated_stale.append(str(s["id"]))

    # 3. Orphaned followups (no date, no recent update)
    orphans = conn.execute(
        "SELECT id, description FROM followups "
        "WHERE status NOT LIKE 'COMPLETED%' "
        "AND UPPER(COALESCE(status, '')) NOT IN ('DELETED','ARCHIVED','BLOCKED','WAITING','WAITING_EXTERNAL','NEEDS_DECISION','WAITING_USER','PARKED','EXPIRED','DONE') "
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
        "stale_escalated_count": len(escalated_stale),
        "orphan_count": len(orphans) if orphans else 0,
        "stale_ids": [s["id"] for s in stale[:20]] if stale else [],
        "stale_escalated_ids": escalated_stale[:20],
        "orphan_ids": [o["id"] for o in orphans[:20]] if orphans else [],
    }

    summary_file = COORD_DIR / "followup-hygiene-summary.json"
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    summary_file.write_text(json.dumps(summary, indent=2))

    log(f"Summary: {dirty_f + dirty_r} normalized, {len(escalated_stale)} stale escalated, {len(orphans) if orphans else 0} orphans")
    log("=== Followup Hygiene complete ===")


if __name__ == "__main__":
    main()
