"""Plan Consolidado F.2/F.5/F.6 — Fase F telemetry loops.

Consumes `guardian-telemetry.ndjson` (item 0.18) and produces:
  - per-rule aggregate metrics (F.2)
  - false-positive grouping (F.5)
  - false-negative candidates for new-rule promotion (F.6)

These are pure functions with no side effects on the live runtime —
`src/scripts/phase_guardian_analysis.py` (Deep Sleep phase) calls them
and persists summaries to `~/.nexo/reports/guardian-fase-f-*.json`.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable


DEFAULT_TELEMETRY_PATH = Path.home() / ".nexo" / "logs" / "guardian-telemetry.ndjson"
DEFAULT_FP_GROUP_MIN_OCCURRENCES = 3
DEFAULT_FN_PROMOTION_THRESHOLD = 3
DEFAULT_FN_WINDOW_DAYS = 14


def load_telemetry_events(path: Path | str = DEFAULT_TELEMETRY_PATH) -> list[dict]:
    """Return events persisted by the guardian_engine, oldest-first.

    Ignores lines that fail to parse so a malformed append does not
    blow up Deep Sleep.
    """
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def aggregate_per_rule(events: Iterable[dict]) -> dict[str, dict]:
    """F.2 — produce the per-rule metrics table.

    Each rule_id gets:
      - trigger_count
      - injection_count
      - followed_through_count (engine observed the agent then called
        the gating tool within the dedup window)
      - false_positive_count (operator flagged the injection as noise)
      - avg_response_latency (ms between injection emit and agent act)
    """
    agg: dict[str, dict] = defaultdict(lambda: {
        "trigger_count": 0,
        "injection_count": 0,
        "followed_through_count": 0,
        "false_positive_count": 0,
        "latencies_ms": [],
    })
    for e in events:
        rid = e.get("rule_id") or ""
        if not rid:
            continue
        etype = e.get("event") or e.get("type") or "injection"
        bucket = agg[rid]
        if etype in ("trigger", "scan"):
            bucket["trigger_count"] += 1
        elif etype in ("injection", "inject"):
            bucket["injection_count"] += 1
        elif etype == "followed_through":
            bucket["followed_through_count"] += 1
        elif etype in ("false_positive", "fp"):
            bucket["false_positive_count"] += 1
        latency = e.get("response_latency_ms") or e.get("latency_ms")
        if isinstance(latency, (int, float)):
            bucket["latencies_ms"].append(float(latency))

    out: dict[str, dict] = {}
    for rid, bucket in agg.items():
        latencies = bucket.pop("latencies_ms")
        avg = round(sum(latencies) / len(latencies), 1) if latencies else 0.0
        efficacy = 0.0
        inj = bucket["injection_count"]
        if inj > 0:
            efficacy = round(bucket["followed_through_count"] / inj, 3)
        fp_rate = 0.0
        if inj > 0:
            fp_rate = round(bucket["false_positive_count"] / inj, 3)
        out[rid] = {
            **bucket,
            "avg_response_latency_ms": avg,
            "efficacy": efficacy,
            "false_positive_rate": fp_rate,
        }
    return out


def group_false_positives(
    events: Iterable[dict],
    min_occurrences: int = DEFAULT_FP_GROUP_MIN_OCCURRENCES,
) -> list[dict]:
    """F.5 — cluster FP events by (rule_id, trigger_context_hash).

    Returns groups that appear >= min_occurrences times, ordered by
    frequency desc. Consumers propose threshold/scope adjustments on the
    top groups.
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for e in events:
        etype = e.get("event") or e.get("type") or ""
        if etype not in ("false_positive", "fp"):
            continue
        key = (
            e.get("rule_id") or "",
            e.get("trigger_context_hash") or e.get("trigger_context") or "",
        )
        groups[key].append(e)

    out: list[dict] = []
    for (rid, ctx), items in groups.items():
        if len(items) < min_occurrences:
            continue
        out.append({
            "rule_id": rid,
            "trigger_context": ctx,
            "occurrences": len(items),
            "first_seen": min((it.get("ts") or 0) for it in items),
            "last_seen": max((it.get("ts") or 0) for it in items),
            "sample": items[:3],
        })
    out.sort(key=lambda r: r["occurrences"], reverse=True)
    return out


def collect_false_negative_candidates(
    corrections: Iterable[dict],
    injections: Iterable[dict],
    *,
    window_days: int = DEFAULT_FN_WINDOW_DAYS,
    threshold: int = DEFAULT_FN_PROMOTION_THRESHOLD,
) -> list[dict]:
    """F.6 — user corrections with no matching guardian injection are
    candidates for a new rule in shadow.

    corrections: events that represent a user correction (from
      nexo_cognitive_sentiment `is_correction=True` or `trust_event=correction`).
    injections: guardian injection events (event=injection).

    Corrections older than window_days are ignored. Returns candidates
    grouped by a fingerprint of the preceding assistant action, ordered
    by count desc, filtered by count >= threshold.
    """
    now = time.time()
    cutoff = now - (window_days * 86400)

    injection_keys = {
        (i.get("rule_id") or "", i.get("trigger_fingerprint") or "")
        for i in injections
    }

    buckets: dict[str, list[dict]] = defaultdict(list)
    for c in corrections:
        ts = c.get("ts") or c.get("at") or 0
        if ts and ts < cutoff:
            continue
        fp = c.get("fingerprint") or c.get("assistant_action_fingerprint") or ""
        if not fp:
            continue
        # Already covered by an existing guardian injection → not a
        # false-negative, it's just noise the operator could not suppress.
        if any(k[1] == fp for k in injection_keys):
            continue
        buckets[fp].append(c)

    out: list[dict] = []
    for fp, items in buckets.items():
        if len(items) < threshold:
            continue
        out.append({
            "fingerprint": fp,
            "count": len(items),
            "first_seen": min((it.get("ts") or 0) for it in items),
            "last_seen": max((it.get("ts") or 0) for it in items),
            "sample": items[:3],
        })
    out.sort(key=lambda r: r["count"], reverse=True)
    return out
