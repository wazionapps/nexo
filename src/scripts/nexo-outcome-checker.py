#!/usr/bin/env python3
from __future__ import annotations
"""NEXO Outcome Checker — daily verification of pending tracked outcomes."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(_repo_src) if (_repo_src / "server.py").exists() else str(NEXO_HOME)))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

import db as nexo_db

LOG_FILE = NEXO_HOME / "logs" / "outcome-checker.log"
SUMMARY_FILE = NEXO_HOME / "coordination" / "outcome-checker-summary.json"


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

    summary = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "checked": checked,
        "met": met,
        "missed": missed,
        "pending": pending,
        "errors": errors,
        "ids": checked_ids,
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
