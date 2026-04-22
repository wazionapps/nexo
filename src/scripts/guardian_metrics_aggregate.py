#!/usr/bin/env python3
"""guardian_metrics_aggregate — Plan Consolidado 0.25.

Reads ``~/.nexo/logs/guardian-telemetry.ndjson`` (produced per-enqueue
by ``guardian_telemetry.log_event``) and the latest drift baseline under
``~/.nexo/reports/drift-baseline-*.json``, and writes a rolling aggregate
of KPIs to ``~/.nexo/logs/guardian-metrics.ndjson``.

KPIs (Plan Consolidado 0.25):
  - capture_rate                           (injected / triggered)
  - core_rule_violations_per_session       (R13/R14/R16/R25/R30 injects / #sessions)
  - declared_done_without_evidence_ratio   (R16 hard hits / sessions)
  - false_positive_correction_rate         (events tagged fp / events total)
  - avg_minutes_between_guard_check_failures

Pure reader: never writes inside the telemetry file. ``NEXO_HOME`` is
honoured so tests isolate via tmp_path.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


CORE_RULE_IDS = {
    "R13_pre_edit_guard",
    "R14_correction_learning",
    "R16_declared_done",
    "R25_nora_maria_read_only",
    "R30_pre_done_evidence_system_prompt",
}


def _nexo_home() -> Path:
    env = os.environ.get("NEXO_HOME")
    if env:
        return Path(env)
    return Path.home() / ".nexo"


def _read_ndjson(path: Path) -> Iterable[dict]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _read_latest_drift_baseline(home: Path) -> dict | None:
    reports_dir = home / "reports"
    if not reports_dir.is_dir():
        return None
    candidates = sorted(reports_dir.glob("drift-baseline-*.json"))
    if not candidates:
        return None
    try:
        return json.loads(candidates[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def aggregate(home: Path | None = None) -> dict:
    home = home if home is not None else _nexo_home()
    telemetry_path = home / "logs" / "guardian-telemetry.ndjson"
    events = list(_read_ndjson(telemetry_path))

    by_rule: dict[str, dict[str, int]] = {}
    sessions: set[str] = set()
    core_violations = 0
    r16_hard = 0
    fp_count = 0
    guard_check_failure_ts: list[float] = []

    for ev in events:
        rid = str(ev.get("rule_id") or ev.get("rule") or "").strip() or "unknown"
        event = str(ev.get("event") or "").strip()
        mode = str(ev.get("mode") or "").strip().lower()
        session_id = str(ev.get("session_id") or ev.get("sid") or "").strip()
        if session_id:
            sessions.add(session_id)
        bucket = by_rule.setdefault(rid, {"triggered": 0, "injected": 0, "fp": 0})
        if event in ("trigger", "fire"):
            bucket["triggered"] += 1
        elif event in ("enqueue", "inject"):
            bucket["injected"] += 1
            bucket["triggered"] += 1
            if rid in CORE_RULE_IDS:
                core_violations += 1
            if rid == "R16_declared_done" and mode == "hard":
                r16_hard += 1
        if ev.get("fp") is True:
            bucket["fp"] += 1
            fp_count += 1
        if event == "guard_check_failed":
            try:
                guard_check_failure_ts.append(float(ev.get("ts") or 0))
            except (TypeError, ValueError):
                pass

    total_triggered = sum(b["triggered"] for b in by_rule.values()) or 0
    total_injected = sum(b["injected"] for b in by_rule.values()) or 0
    capture_rate = (total_injected / total_triggered) if total_triggered else 0.0

    guard_check_failure_ts.sort()
    deltas_min = [
        (b - a) / 60.0
        for a, b in zip(guard_check_failure_ts, guard_check_failure_ts[1:])
        if (b - a) > 0
    ]
    avg_min_between = statistics.mean(deltas_min) if deltas_min else None

    n_sessions = len(sessions) or 1

    baseline = _read_latest_drift_baseline(home)

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "nexo_home": str(home),
        "events_read": len(events),
        "sessions_seen": len(sessions),
        "capture_rate": round(capture_rate, 4),
        "core_rule_violations_per_session": round(core_violations / n_sessions, 4),
        "declared_done_without_evidence_ratio": round(r16_hard / n_sessions, 4),
        "false_positive_correction_rate": round(
            (fp_count / total_triggered) if total_triggered else 0.0, 4
        ),
        "avg_minutes_between_guard_check_failures": avg_min_between,
        "per_rule": {
            rid: {
                "triggered": b["triggered"],
                "injected": b["injected"],
                "fp": b["fp"],
                "baseline_hits": (baseline or {}).get("rule_counts", {}).get(rid, 0),
            }
            for rid, b in by_rule.items()
        },
        "drift_baseline_source": (
            str(sorted((home / "reports").glob("drift-baseline-*.json"))[-1])
            if baseline else None
        ),
    }


def write_metrics(result: dict, *, home: Path | None = None) -> Path:
    home = home if home is not None else _nexo_home()
    logs_dir = home / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "guardian-metrics.ndjson"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result, ensure_ascii=False) + "\n")
    return path


def main(argv: list[str] | None = None) -> int:
    result = aggregate()
    path = write_metrics(result)
    print(
        f"guardian_metrics_aggregate: {result['events_read']} events across "
        f"{result['sessions_seen']} sessions → appended to {path}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
