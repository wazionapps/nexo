#!/usr/bin/env python3
from __future__ import annotations
"""NEXO Outcome Checker — daily verification of pending tracked outcomes."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from paths import coordination_dir, logs_dir

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(_repo_src) if (_repo_src / "server.py").exists() else str(NEXO_HOME)))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

import db as nexo_db

LOG_FILE = logs_dir() / "outcome-checker.log"
SUMMARY_FILE = coordination_dir() / "outcome-checker-summary.json"


def log(message: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as handle:
        handle.write(line + "\n")


def main() -> int:
    log("=== Outcome Checker starting ===")
    nexo_db.init_db()

    due_rows = nexo_db.pending_outcomes_due(limit=500)
    if not due_rows:
        summary = {
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "checked": 0,
            "met": 0,
            "missed": 0,
            "pending": 0,
            "errors": 0,
            "ids": [],
        }
        SUMMARY_FILE.parent.mkdir(parents=True, exist_ok=True)
        SUMMARY_FILE.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        log("No pending due outcomes.")
        log("=== Outcome Checker complete ===")
        return 0

    checked = met = missed = pending = errors = 0
    checked_ids: list[int] = []
    for row in due_rows:
        checked += 1
        checked_ids.append(int(row["id"]))
        result = nexo_db.evaluate_outcome(int(row["id"]), create_learning_on_miss=True)
        if "error" in result:
            errors += 1
            log(f"ERROR outcome #{row['id']}: {result['error']}")
            continue
        status = result.get("status", "pending")
        if status == "met":
            met += 1
        elif status == "missed":
            missed += 1
        else:
            pending += 1
        log(
            f"Outcome #{row['id']} -> {status} "
            f"(action={result.get('action_type')}:{result.get('action_id') or '—'}, "
            f"deadline={result.get('deadline')})"
        )

    # Phase 2 item 2: after closing outcomes, attempt to promote any
    # outcome pattern that just crossed the suggested-skill threshold to a
    # real draft skill. The helper is idempotent and capped at
    # max_promotions per run, so this is safe to call on every cycle.
    promotion_summary: dict = {"promoted": [], "skipped": [], "errors": [], "scanned": 0}
    try:
        from skills_runtime import auto_promote_outcome_patterns_to_skills
        promotion_summary = auto_promote_outcome_patterns_to_skills(
            min_success_rate=0.8,
            max_promotions=3,
        )
        if promotion_summary.get("promoted"):
            log(
                f"Auto-promoted {len(promotion_summary['promoted'])} outcome pattern(s) "
                f"to skill draft(s) (scanned={promotion_summary.get('scanned', 0)})"
            )
            for entry in promotion_summary["promoted"]:
                log(
                    f"  -> {entry.get('pattern_key')} -> skill {entry.get('skill_id')} "
                    f"(success_rate={entry.get('success_rate')}, created={entry.get('created')})"
                )
        elif promotion_summary.get("scanned"):
            log(
                f"Outcome pattern auto-promote: scanned {promotion_summary['scanned']}, "
                f"none qualified (skipped={len(promotion_summary['skipped'])})"
            )
    except Exception as e:
        log(f"WARN: outcome pattern auto-promote raised: {e}")

    summary = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "checked": checked,
        "met": met,
        "missed": missed,
        "pending": pending,
        "errors": errors,
        "ids": checked_ids,
        "auto_promoted_patterns": promotion_summary,
    }
    SUMMARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(
        f"Summary: checked={checked} met={met} missed={missed} "
        f"pending={pending} errors={errors}"
    )
    log("=== Outcome Checker complete ===")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
