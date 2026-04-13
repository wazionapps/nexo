#!/usr/bin/env python3
"""NEXO Cortex Cycle — continuous quality validation.

Scheduled every 6 hours by src/crons/manifest.json (id: cortex-cycle).
Closes Fase 2 item 6 of NEXO-AUDIT-2026-04-11.

Until this script existed, Cortex evaluations only ran when an agent
explicitly invoked nexo_cortex_decide / nexo_cortex_check during a task.
There was no continuous loop watching for quality drops, so a degraded
recommendation pattern could persist indefinitely between user reports.

What this script does (idempotent and best-effort):

1. Loads cortex_evaluation_summary for the last 7 days and last 1 day.
2. Persists the snapshot to ~/.nexo/operations/cortex-quality-latest.json
   so dashboards / morning briefings can read fresh metrics without
   re-running the SQL.
3. Detects degradation signals on the 7-day window. The criteria are
   intentionally conservative to avoid false alarms on small samples:
     a. recommendation_accept_rate < 50% AND total_evaluations >= 10
     b. linked_outcome_success_rate < 50% AND linked_outcomes_resolved >= 5
     c. override_success_rate > recommended_success_rate by >= 20pp
        AND linked_outcomes_resolved >= 5
4. Opens (or refreshes) NF-CORTEX-QUALITY-DROP followup with the offending
   metrics when degradation is detected. Idempotent: if a non-PENDING /
   resolved followup of the same id already exists, it is updated in
   place rather than duplicated.
5. Logs every run to ~/.nexo/logs/cortex-cycle.log.

Catchup-friendly: a stale plist firing twice in quick succession is fine.
The quality file is rewritten in place, the followup is upserted, no
mutating side effects beyond those.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent  # src/scripts/ -> src/
NEXO_CODE = Path(
    os.environ.get(
        "NEXO_CODE",
        str(_repo_src) if (_repo_src / "server.py").exists() else str(NEXO_HOME),
    )
)
sys.path.insert(0, str(NEXO_CODE))

OPERATIONS_DIR = NEXO_HOME / "operations"
LOGS_DIR = NEXO_HOME / "logs"
QUALITY_FILE = OPERATIONS_DIR / "cortex-quality-latest.json"
LOG_FILE = LOGS_DIR / "cortex-cycle.log"
FOLLOWUP_ID = "NF-CORTEX-QUALITY-DROP"

ACCEPT_RATE_FLOOR = 50.0
ACCEPT_RATE_MIN_SAMPLE = 10
LINKED_SUCCESS_FLOOR = 50.0
LINKED_MIN_SAMPLE = 5
OVERRIDE_GAP_THRESHOLD = 20.0  # percentage points


def _log(msg: str) -> None:
    """Append a timestamped line to LOG_FILE. Best-effort, never raises."""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().isoformat(timespec="seconds")
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"[{ts}] {msg}\n")
    except Exception:
        pass
    print(msg)


def detect_quality_signals(summary: dict) -> list[dict]:
    """Inspect a cortex_evaluation_summary dict and return degradation signals.

    Each signal is a dict with at least:
        - kind: short identifier (accept_rate / linked_success / override_gap)
        - severity: warn | error
        - message: human-readable explanation
        - metric_value: the failing measurement
        - threshold: the floor it dropped below

    Returns an empty list when nothing is degraded. Pure function so it can
    be unit-tested without touching the DB.
    """
    signals: list[dict] = []
    if not isinstance(summary, dict):
        return signals

    total = int(summary.get("total_evaluations") or 0)
    accept_rate = float(summary.get("recommendation_accept_rate") or 0.0)
    linked_total = int(summary.get("linked_outcomes_total") or 0)
    linked_met = int(summary.get("linked_outcomes_met") or 0)
    linked_missed = int(summary.get("linked_outcomes_missed") or 0)
    linked_pending = int(summary.get("linked_outcomes_pending") or 0)
    linked_resolved = linked_met + linked_missed
    if linked_resolved <= 0 and linked_total > 0:
        # Older callers may omit the met/missed counters; fall back to total minus pending.
        linked_resolved = max(0, linked_total - linked_pending)
    linked_success = float(summary.get("linked_outcome_success_rate") or 0.0)
    recommended_success = float(summary.get("recommended_success_rate") or 0.0)
    override_success = float(summary.get("override_success_rate") or 0.0)

    if total >= ACCEPT_RATE_MIN_SAMPLE and accept_rate < ACCEPT_RATE_FLOOR:
        signals.append({
            "kind": "accept_rate",
            "severity": "warn",
            "metric_value": accept_rate,
            "threshold": ACCEPT_RATE_FLOOR,
            "sample_size": total,
            "message": (
                f"Cortex recommendation accept rate {accept_rate:.1f}% on {total} "
                f"evaluations is below the {ACCEPT_RATE_FLOOR:.0f}% floor. Users "
                "are overriding the recommended choice more often than not."
            ),
        })

    linked_scope = f"{linked_resolved} resolved linked outcomes"
    if linked_pending > 0:
        linked_scope += f" ({linked_total} total, {linked_pending} pending)"

    if linked_resolved >= LINKED_MIN_SAMPLE and linked_success < LINKED_SUCCESS_FLOOR:
        signals.append({
            "kind": "linked_success",
            "severity": "warn",
            "metric_value": linked_success,
            "threshold": LINKED_SUCCESS_FLOOR,
            "sample_size": linked_resolved,
            "message": (
                f"Cortex linked-outcome success rate {linked_success:.1f}% on "
                f"{linked_scope} is below the "
                f"{LINKED_SUCCESS_FLOOR:.0f}% floor."
            ),
        })

    if linked_resolved >= LINKED_MIN_SAMPLE:
        gap = override_success - recommended_success
        if gap >= OVERRIDE_GAP_THRESHOLD:
            signals.append({
                "kind": "override_gap",
                "severity": "error",
                "metric_value": gap,
                "threshold": OVERRIDE_GAP_THRESHOLD,
                "sample_size": linked_resolved,
                "message": (
                    f"Cortex overrides outperform recommendations by {gap:.1f}pp "
                    f"(override {override_success:.1f}% vs recommended "
                    f"{recommended_success:.1f}% on {linked_scope}). The "
                    "recommender is mis-ranking choices."
                ),
            })

    return signals


def _persist_quality_snapshot(window_7d: dict, window_1d: dict, signals: list[dict]) -> None:
    payload = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "window_7d": window_7d,
        "window_1d": window_1d,
        "signals": signals,
        "schema": 1,
    }
    try:
        OPERATIONS_DIR.mkdir(parents=True, exist_ok=True)
        QUALITY_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    except Exception as e:
        _log(f"WARN: failed to persist quality snapshot: {e}")


def _upsert_quality_followup(signals: list[dict]) -> str:
    """Open or refresh NF-CORTEX-QUALITY-DROP. Returns the action taken.

    Idempotent: if the followup already exists in PENDING status it is
    updated in place rather than duplicated. If it exists but was already
    resolved, a fresh row is inserted with the same id (REPLACE) so the
    new degradation pattern is visible.
    """
    try:
        from db import complete_followup, get_followup, get_db
    except Exception as e:
        _log(f"WARN: cannot import db helpers: {e}")
        return "skipped_no_db"

    try:
        existing = get_followup(FOLLOWUP_ID)
    except Exception as e:
        _log(f"WARN: get_followup raised: {e}")
        existing = None

    if not signals:
        if not existing:
            return "no_signal"
        status = str(existing.get("status") or "").upper()
        if status.startswith("COMPLETED") or status in {"DELETED", "ARCHIVED", "BLOCKED", "WAITING", "CANCELLED"}:
            return "no_signal"
        try:
            complete_followup(
                FOLLOWUP_ID,
                result=(
                    "Auto-resolved by cortex-cycle: no active degradation signals in the "
                    "current 7d window."
                ),
            )
        except Exception as e:
            _log(f"WARN: failed to close followup: {e}")
            return "failed_close"
        return "closed"

    summary_lines = ["Cortex continuous validation found quality degradation:"]
    for sig in signals:
        summary_lines.append(
            f"- [{sig['severity'].upper()}] {sig['kind']}: {sig['message']}"
        )
    summary_lines.append("")
    summary_lines.append(
        "Investigate cortex_evaluations recent rows, review goal profiles, "
        "and consider tightening or relaxing the recommender heuristics."
    )
    description = "\n".join(summary_lines)
    verification = (
        "SELECT id, goal, recommended_choice, selected_choice, selection_source, "
        "created_at FROM cortex_evaluations ORDER BY id DESC LIMIT 20"
    )
    now_epoch = datetime.now().timestamp()

    try:
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO followups (id, description, date, status, "
            "verification, created_at, updated_at, priority) "
            "VALUES (?, ?, NULL, 'PENDING', ?, ?, ?, 'high')",
            (FOLLOWUP_ID, description, verification, now_epoch, now_epoch),
        )
        try:
            conn.commit()
        except Exception:
            pass
    except Exception as e:
        _log(f"WARN: failed to upsert followup: {e}")
        return "failed"

    return "refreshed" if existing else "opened"


def run() -> int:
    """Main cron entry point. Returns 0 on success, 2 on partial failure."""
    try:
        from db import cortex_evaluation_summary
    except Exception as e:
        _log(f"FATAL: cannot import cortex_evaluation_summary: {e}")
        return 2

    try:
        window_7d = cortex_evaluation_summary(days=7)
    except Exception as e:
        _log(f"FATAL: cortex_evaluation_summary(7d) raised: {e}")
        return 2

    try:
        window_1d = cortex_evaluation_summary(days=1)
    except Exception as e:
        _log(f"WARN: cortex_evaluation_summary(1d) raised: {e}")
        window_1d = {"days": 1, "total_evaluations": 0, "error": str(e)}

    signals = detect_quality_signals(window_7d)
    _persist_quality_snapshot(window_7d, window_1d, signals)

    total = int(window_7d.get("total_evaluations") or 0)
    accept_rate = float(window_7d.get("recommendation_accept_rate") or 0.0)
    if total == 0:
        _log("Cortex cycle: no evaluations in 7d window — nothing to validate")
    else:
        _log(
            f"Cortex cycle: {total} evaluations in 7d, accept_rate={accept_rate:.1f}%, "
            f"signals={len(signals)}"
        )

    action = _upsert_quality_followup(signals)
    if signals or action not in {"no_signal"}:
        _log(f"Cortex cycle: followup {FOLLOWUP_ID} {action} ({len(signals)} signal(s))")

    return 0


if __name__ == "__main__":
    sys.exit(run())
