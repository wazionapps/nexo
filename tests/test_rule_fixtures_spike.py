"""Plan Consolidado 0.8 + 0.14 — R13 spike over labelled fixtures.

Gates:
  * FP rate < 5% (no R13 injection expected → no injection returned)
  * P95 latency < 3 seconds on the decision function

The fixture file lives at tests/fixtures_rules_validation.json.
"""

import json
import time
from pathlib import Path

import pytest


FIXTURES_PATH = Path(__file__).parent / "fixtures_rules_validation.json"


def _build_record(call, base_ts):
    from r13_pre_edit_guard import ToolCallRecord

    ts_offset = call.get("ts_offset", 0)
    return ToolCallRecord(
        tool=call["name"],
        ts=base_ts + ts_offset,
        files=tuple(call.get("files", [])),
        meta=call.get("input") or {},
    )


def _load_cases():
    data = json.loads(FIXTURES_PATH.read_text())
    return data["cases"]


def _cases_for_r13():
    for case in _load_cases():
        if case.get("rule") != "R13":
            continue
        if "seq" not in case and "seq_generator" not in case:
            continue
        yield case


def _build_seq(case, base_ts):
    if "seq" in case:
        *prior, final = case["seq"]
        return [dict(c) for c in prior], dict(final)

    gen = case["seq_generator"]
    prior = []
    for i in range(gen["prepend_noop_calls"]):
        prior.append({
            "op": "tool",
            "name": "Read",
            "files": [f"/tmp/noop-{i}.txt"],
            "ts_offset": -1 * (gen["prepend_noop_calls"] - i),
        })
    prior.append({
        "op": "tool",
        "name": "nexo_guard_check",
        "files": ["/repo/x.py"],
        "ts_offset": gen["guard_ts_offset"],
    })
    return prior, dict(gen["final"])


def test_r13_spike_gates():
    from r13_pre_edit_guard import should_inject_r13, ToolCallRecord

    cases = list(_cases_for_r13())
    assert len(cases) >= 10, (
        f"expected >=10 R13 fixtures, got {len(cases)}"
    )

    latencies: list[float] = []
    failures: list[str] = []
    total = 0
    fp = 0

    now = time.time()

    for case in cases:
        prior_calls, final_call = _build_seq(case, base_ts=now)
        recent = [_build_record(c, now) for c in prior_calls]

        t0 = time.time()
        injection = should_inject_r13(
            current_tool=final_call["name"],
            current_files=final_call.get("files", []),
            recent_calls=recent,
            current_ts=now,
        )
        latencies.append(time.time() - t0)
        total += 1

        expected = set(case.get("expected_rules") or [])
        should_fire = "R13_pre_edit_guard" in expected

        if should_fire and injection is None:
            failures.append(f"{case['id']}: expected R13, got None")
        elif not should_fire and injection is not None:
            failures.append(f"{case['id']}: expected None, got {injection}")
            fp += 1

    assert not failures, "\n".join(failures)

    # FP gate: <5%
    fp_rate = fp / max(total, 1)
    assert fp_rate < 0.05, f"FP rate {fp_rate:.2%} >= 5%"

    # P95 gate: <3s
    if latencies:
        latencies.sort()
        p95_idx = min(len(latencies) - 1, int(0.95 * len(latencies)))
        p95 = latencies[p95_idx]
        assert p95 < 3.0, f"P95 latency {p95:.3f}s >= 3s"
