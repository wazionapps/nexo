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


def _active_followup_scores() -> dict[str, float]:
    conn = nexo_db.get_db()
    rows = conn.execute(
        """SELECT id, impact_score FROM followups
           WHERE status IN ('PENDING', 'ACTIVE', 'WAITING', 'BLOCKED')"""
    ).fetchall()
    return {str(row["id"]): float(row["impact_score"] or 0.0) for row in rows}


def _reasoning_for(row: dict) -> str:
    factors = row.get("impact_factors") or {}
    if isinstance(factors, str):
        try:
            factors = json.loads(factors)
        except json.JSONDecodeError:
            factors = {}
    if isinstance(factors, dict):
        return str(factors.get("reasoning") or "").strip()
    return ""


def main() -> int:
    log("=== Impact Scorer starting ===")
    nexo_db.init_db()
    previous_scores = _active_followup_scores()
    scored = nexo_db.score_active_followups(limit=500)
    top = [
        {
            "id": row.get("id"),
            "date": row.get("date"),
            "priority": row.get("priority"),
            "impact_score": row.get("impact_score", 0),
            "impact_factors": row.get("impact_factors") or {},
            "impact_reasoning": _reasoning_for(row),
        }
        for row in scored[:5]
    ]
    top_changes = sorted(
        [
            {
                "id": row.get("id"),
                "priority": row.get("priority"),
                "date": row.get("date"),
                "impact_score": row.get("impact_score", 0),
                "previous_score": previous_scores.get(str(row.get("id")), 0.0),
                "delta": round(float(row.get("impact_score") or 0.0) - previous_scores.get(str(row.get("id")), 0.0), 2),
                "impact_reasoning": _reasoning_for(row),
            }
            for row in scored
        ],
        key=lambda item: (abs(float(item["delta"])), float(item["impact_score"] or 0.0)),
        reverse=True,
    )[:5]
    summary = {
        "scored_at": datetime.now().isoformat(timespec="seconds"),
        "scored_count": len(scored),
        "top_followups": top,
        "top_changes": top_changes,
    }
    SUMMARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"Scored {len(scored)} active followups.")
    for item in top:
        log(
            f"Top -> {item['id']} score={item['impact_score']} "
            f"priority={item['priority']} date={item['date'] or '—'} "
            f"because={item['impact_reasoning'] or 'no reasoning'}"
        )
    changed = [item for item in top_changes if abs(float(item["delta"])) >= 1.0]
    if changed:
        log("Strong top-5 changes:")
        for item in changed:
            direction = "+" if float(item["delta"]) >= 0 else ""
            log(
                f"  Δ {item['id']} {direction}{item['delta']:.2f} -> {item['impact_score']:.2f} "
                f"priority={item['priority']} because={item['impact_reasoning'] or 'no reasoning'}"
            )
    else:
        log("Strong top-5 changes: none")
    log("=== Impact Scorer complete ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
