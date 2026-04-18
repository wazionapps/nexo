#!/usr/bin/env python3
"""Deep Sleep phase — Fase F (Plan Consolidado F.3).

Reads `~/.nexo/logs/guardian-telemetry.ndjson` + recent correction
signals, produces the Fase F summary report at
`~/.nexo/reports/guardian-fase-f-<date>.json`.

Invoked by nexo-sleep.py after the existing Deep Sleep phases land.
Downstream phases may read the JSON output to propose default-mode
shifts (F.4) or new-rule shadow candidates (F.6).
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


_DEFAULT_RUNTIME_ROOT = Path(__file__).resolve().parents[1]
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(_DEFAULT_RUNTIME_ROOT)))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

from fase_f_loops import (  # noqa: E402
    load_telemetry_events,
    aggregate_per_rule,
    group_false_positives,
    collect_false_negative_candidates,
)

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
LOG_PATH = NEXO_HOME / "logs" / "guardian-telemetry.ndjson"
REPORT_DIR = NEXO_HOME / "reports"
NEXO_DB = NEXO_HOME / "data" / "nexo.db"


def _recent_corrections_from_db(window_seconds: int = 30 * 86400) -> list[dict]:
    if not NEXO_DB.exists():
        return []
    try:
        conn = sqlite3.connect(NEXO_DB)
        conn.row_factory = sqlite3.Row
        cutoff = time.time() - window_seconds
        try:
            rows = conn.execute(
                "SELECT rowid, ts, sentiment, signals FROM sentiment_log "
                "WHERE sentiment = 'negative' AND ts >= ?",
                (cutoff,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        out: list[dict] = []
        for r in rows:
            fingerprint = ""
            sig = r["signals"] or ""
            if sig:
                fingerprint = "sig:" + ",".join(sorted(set(sig.split(",")))[:3])
            out.append({
                "ts": r["ts"],
                "fingerprint": fingerprint,
                "source": "sentiment_log",
                "row_id": r["rowid"],
            })
        return out
    except Exception:
        return []


def build_report() -> dict:
    events = load_telemetry_events(LOG_PATH)
    per_rule = aggregate_per_rule(events)
    fp_groups = group_false_positives(events)
    corrections = _recent_corrections_from_db()
    fn_candidates = collect_false_negative_candidates(corrections, events)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "telemetry_events_read": len(events),
        "per_rule": per_rule,
        "false_positive_groups": fp_groups,
        "false_negative_candidates": fn_candidates,
    }


def write_report(report: dict | None = None) -> Path:
    if report is None:
        report = build_report()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = REPORT_DIR / f"guardian-fase-f-{stamp}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> int:
    report = build_report()
    path = write_report(report)
    print(json.dumps({
        "phase": "guardian_analysis",
        "report_path": str(path),
        "events_read": report["telemetry_events_read"],
        "rules_covered": len(report["per_rule"]),
        "fp_groups": len(report["false_positive_groups"]),
        "fn_candidates": len(report["false_negative_candidates"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
