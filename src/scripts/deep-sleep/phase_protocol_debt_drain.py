#!/usr/bin/env python3
"""Deep Sleep phase: auto-drain stale ``protocol_debt`` rows.

Block K G2 (Francisco 2026-04-22): the debt table accumulates
``unacknowledged_guard_blocking`` + ``missing_cortex_evaluation`` rows
faster than the operator can resolve them by hand (20 open in 48h,
0 auto-resolved). This phase runs nightly and classifies every
``resolved_at IS NULL`` row into three buckets:

  - ``stale``: older than STALE_AGE_DAYS (default 7) *and* the
    referenced ``task_id`` either does not exist any more or is
    closed. Auto-resolved with a ``deep_sleep/stale_auto_drain``
    resolution note so the morning briefing still shows a clean
    audit trail.
  - ``still_valid``: the referenced task is still active. Left
    untouched — the operator will resolve it alongside the task.
  - ``requires_user``: newer than STALE_AGE_DAYS, or referenced task
    is unknown. Emitted as a ``morning_briefing_item`` so the operator
    sees a consolidated list instead of discovering them one by one.

Design invariants:

  - Idempotent: re-running on the same day re-emits the same set of
    auto-resolves without double-writing (``resolved_at IS NULL`` filter
    skips rows already drained).
  - Read-modify-write wrapped in ``BEGIN IMMEDIATE`` so a concurrent
    operator writer cannot race-overwrite ``resolved_at``.
  - Backup-safe: writes an audit JSON to
    ``runtime/operations/deep-sleep/$DATE-protocol-debt-drain.json``
    before (and regardless of) mutation so we can always inspect what
    was drained.

Environment:
    NEXO_HOME (optional) — root of the NEXO installation.
    NEXO_DEBT_DRAIN_STALE_DAYS (optional) — override stale cutoff.
    NEXO_DEBT_DRAIN_DRY_RUN=1 — classify + emit JSON but do not write.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parents[2])))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

import paths  # noqa: E402  (sys.path tweaked above)

DEFAULT_STALE_AGE_DAYS = 7
AUTO_DRAIN_NOTE = "deep_sleep/stale_auto_drain"


def _stale_cutoff_days() -> int:
    raw = os.environ.get("NEXO_DEBT_DRAIN_STALE_DAYS", "").strip()
    if not raw:
        return DEFAULT_STALE_AGE_DAYS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_STALE_AGE_DAYS
    return max(1, value)


def _resolve_db_path() -> Path:
    try:
        return paths.db_path()
    except Exception:
        return NEXO_HOME / "runtime" / "data" / "nexo.db"


def _resolve_ops_dir() -> Path:
    try:
        return paths.operations_dir() / "deep-sleep"
    except Exception:
        return NEXO_HOME / "runtime" / "operations" / "deep-sleep"


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    # Try the three shapes SQLite's ``datetime('now')`` + direct ISO
    # formatters commonly produce. strptime itself rejects trailing noise,
    # so there is no need to pre-truncate the input (earlier revisions did
    # and silently dropped the seconds because ``len('%Y-%m-%d %H:%M:%S')``
    # is smaller than the rendered value).
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except Exception:
            continue
    # Some rows drop fractional seconds or timezone — try a lenient fallback
    # before giving up so we do not over-eagerly bucket them as
    # requires_user.
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _task_is_open(conn: sqlite3.Connection, task_id: str) -> bool | None:
    """Return True if the task is open, False if closed, None if unknown."""
    if not task_id:
        return None
    try:
        row = conn.execute(
            "SELECT status FROM protocol_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    status = str(row[0] or "").strip().lower()
    # ``open`` is the canonical live state. Everything else (``closed``,
    # ``cancelled``, ``completed``, …) means the task is no longer pinning
    # the debt.
    return status == "open"


def classify_debt(
    *,
    created_at: str,
    task_id: str,
    now: datetime,
    task_open: bool | None,
    stale_age_days: int,
) -> str:
    """Return one of ``stale`` / ``still_valid`` / ``requires_user``.

    Pure function — easy to unit-test without an open DB. Rules:

      - Task known to be open → ``still_valid`` regardless of age (the
        operator will resolve it together with the task).
      - Task closed OR task_id absent/unknown AND debt older than
        ``stale_age_days`` → ``stale`` (auto-drainable).
      - Anything else → ``requires_user`` (surface in briefing).
    """
    if task_open is True:
        return "still_valid"
    created_dt = _parse_ts(created_at)
    if created_dt is None:
        # Unparseable timestamp: best to surface for the operator rather
        # than silently discard.
        return "requires_user"
    age = now - created_dt
    if age < timedelta(days=stale_age_days):
        return "requires_user"
    # Old enough + task closed/unknown → safe to drain.
    return "stale"


def run(
    *,
    db_path: Path | None = None,
    ops_dir: Path | None = None,
    stale_age_days: int | None = None,
    dry_run: bool | None = None,
    now: datetime | None = None,
) -> dict:
    db_path = db_path or _resolve_db_path()
    ops_dir = ops_dir or _resolve_ops_dir()
    stale_age_days = stale_age_days if stale_age_days is not None else _stale_cutoff_days()
    if dry_run is None:
        dry_run = os.environ.get("NEXO_DEBT_DRAIN_DRY_RUN", "").strip() == "1"
    now = now or datetime.utcnow()

    report: dict = {
        "db_path": str(db_path),
        "stale_age_days": stale_age_days,
        "dry_run": bool(dry_run),
        "ran_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "totals": {"stale": 0, "still_valid": 0, "requires_user": 0},
        "drained_ids": [],
        "requires_user_summary": [],
    }

    if not db_path.exists():
        report["error"] = "db_path_missing"
        return report

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id, session_id, task_id, debt_type, severity, evidence, created_at "
            "FROM protocol_debt WHERE resolved_at IS NULL"
        ).fetchall()
        by_severity_type: dict[tuple[str, str], int] = {}
        for row in rows:
            task_open = _task_is_open(conn, str(row["task_id"] or ""))
            bucket = classify_debt(
                created_at=str(row["created_at"] or ""),
                task_id=str(row["task_id"] or ""),
                now=now,
                task_open=task_open,
                stale_age_days=stale_age_days,
            )
            report["totals"][bucket] = report["totals"].get(bucket, 0) + 1
            if bucket == "stale":
                report["drained_ids"].append(int(row["id"]))
                if not dry_run:
                    conn.execute(
                        "UPDATE protocol_debt SET status = 'resolved', resolved_at = ?, resolution = ? "
                        "WHERE id = ? AND resolved_at IS NULL",
                        (
                            now.strftime("%Y-%m-%d %H:%M:%S"),
                            AUTO_DRAIN_NOTE,
                            int(row["id"]),
                        ),
                    )
            elif bucket == "requires_user":
                # Track by (severity, debt_type) so the morning briefing
                # can split ERROR vs WARN buckets dynamically — without
                # this split, freshly-introduced ERROR debt classes stay
                # invisible until someone hand-edits the whitelist.
                severity = str(row["severity"] or "warn").strip().lower() or "warn"
                debt_type = str(row["debt_type"] or "")
                key = (severity, debt_type)
                by_severity_type[key] = by_severity_type.get(key, 0) + 1
        # Consolidate requires_user into a per-severity, per-type summary
        # so the morning briefing stays short even when the backlog is
        # long, while still surfacing ALL error classes (not a fixed top-4).
        report["requires_user_summary"] = [
            {"severity": severity, "debt_type": debt_type, "count": count}
            for (severity, debt_type), count in sorted(
                by_severity_type.items(),
                key=lambda item: (item[0][0] != "error", -item[1]),
            )
        ]
        # Aggregate by severity so consumers can report
        # ``ERROR=N (a=x, b=y), WARN=M`` without re-bucketing.
        report["requires_user_by_severity"] = {}
        for entry in report["requires_user_summary"]:
            sev = entry["severity"]
            stat = report["requires_user_by_severity"].setdefault(
                sev, {"total": 0, "by_type": []}
            )
            stat["total"] += int(entry["count"])
            stat["by_type"].append(
                {"debt_type": entry["debt_type"], "count": int(entry["count"])}
            )
        if dry_run:
            conn.execute("ROLLBACK")
        else:
            conn.execute("COMMIT")
    except Exception as exc:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()

    # Persist the audit JSON even when drained_ids is empty so the daily
    # Deep Sleep surface always has a file to reference.
    try:
        ops_dir.mkdir(parents=True, exist_ok=True)
        audit_path = ops_dir / f"{now.strftime('%Y-%m-%d')}-protocol-debt-drain.json"
        audit_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        report["audit_path"] = str(audit_path)
    except Exception as exc:
        report["audit_write_error"] = f"{type(exc).__name__}: {exc}"

    return report


def main(argv: list[str] | None = None) -> int:
    report = run()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if "error" not in report else 1


if __name__ == "__main__":
    sys.exit(main())
