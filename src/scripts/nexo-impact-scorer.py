#!/usr/bin/env python3
from __future__ import annotations
"""NEXO Impact Scorer — recalculate followup impact scores for real queues."""

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

LOG_FILE = NEXO_HOME / "logs" / "impact-scorer.log"
SUMMARY_FILE = NEXO_HOME / "coordination" / "impact-scorer-summary.json"


def log(message: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as handle:
        handle.write(line + "\n")


def main() -> int:
    log("=== Impact Scorer starting ===")
    nexo_db.init_db()
    scored = nexo_db.score_active_followups(limit=500)
    top = [
        {
            "id": row.get("id"),
            "date": row.get("date"),
            "priority": row.get("priority"),
            "impact_score": row.get("impact_score", 0),
        }
        for row in scored[:5]
    ]
    summary = {
        "scored_at": datetime.now().isoformat(timespec="seconds"),
        "scored_count": len(scored),
        "top_followups": top,
    }
    SUMMARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"Scored {len(scored)} active followups.")
    for item in top:
        log(
            f"Top -> {item['id']} score={item['impact_score']} "
            f"priority={item['priority']} date={item['date'] or '—'}"
        )
    log("=== Impact Scorer complete ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
