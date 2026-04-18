"""Plan F.2 + F.5 + F.6 — telemetry loops produce stable aggregates."""

from __future__ import annotations

import time


def test_aggregate_per_rule_computes_efficacy_and_fp_rate():
    from fase_f_loops import aggregate_per_rule

    now = time.time()
    events = [
        {"rule_id": "R13", "event": "injection", "ts": now - 10, "response_latency_ms": 120},
        {"rule_id": "R13", "event": "followed_through", "ts": now - 9},
        {"rule_id": "R13", "event": "injection", "ts": now - 8, "response_latency_ms": 200},
        {"rule_id": "R13", "event": "false_positive", "ts": now - 7},
        {"rule_id": "R14", "event": "injection", "ts": now - 6},
        {"rule_id": "R14", "event": "injection", "ts": now - 5},
        {"rule_id": "R14", "event": "followed_through", "ts": now - 4},
        {"rule_id": "R14", "event": "followed_through", "ts": now - 3},
    ]
    agg = aggregate_per_rule(events)
    assert agg["R13"]["injection_count"] == 2
    assert agg["R13"]["followed_through_count"] == 1
    assert agg["R13"]["false_positive_count"] == 1
    assert agg["R13"]["efficacy"] == 0.5
    assert agg["R13"]["false_positive_rate"] == 0.5
    assert agg["R13"]["avg_response_latency_ms"] == 160.0

    assert agg["R14"]["injection_count"] == 2
    assert agg["R14"]["followed_through_count"] == 2
    assert agg["R14"]["efficacy"] == 1.0


def test_group_false_positives_sorted_and_thresholded():
    from fase_f_loops import group_false_positives

    events = [
        {"rule_id": "R13", "event": "fp", "trigger_context_hash": "abc"},
        {"rule_id": "R13", "event": "fp", "trigger_context_hash": "abc"},
        {"rule_id": "R13", "event": "fp", "trigger_context_hash": "abc"},
        {"rule_id": "R13", "event": "fp", "trigger_context_hash": "xyz"},
        {"rule_id": "R14", "event": "fp", "trigger_context_hash": "same"},
        {"rule_id": "R14", "event": "fp", "trigger_context_hash": "same"},
        {"rule_id": "R14", "event": "fp", "trigger_context_hash": "same"},
    ]
    groups = group_false_positives(events, min_occurrences=3)
    assert len(groups) == 2
    assert groups[0]["occurrences"] >= groups[1]["occurrences"]


def test_collect_fn_candidates_respects_threshold_and_window():
    from fase_f_loops import collect_false_negative_candidates

    now = time.time()
    corrections = [
        {"fingerprint": "pattern_a", "ts": now - 1},
        {"fingerprint": "pattern_a", "ts": now - 2},
        {"fingerprint": "pattern_a", "ts": now - 3},
        {"fingerprint": "pattern_b", "ts": now - 1},  # only 1 occurrence
        {"fingerprint": "pattern_c", "ts": now - (30 * 86400)},  # outside window
    ]
    injections = [
        {"rule_id": "R99", "trigger_fingerprint": "pattern_d"},  # unrelated
    ]
    candidates = collect_false_negative_candidates(
        corrections, injections, window_days=14, threshold=3
    )
    names = [c["fingerprint"] for c in candidates]
    assert "pattern_a" in names
    assert "pattern_b" not in names
    assert "pattern_c" not in names


def test_collect_fn_candidates_filters_already_covered_rules():
    from fase_f_loops import collect_false_negative_candidates

    now = time.time()
    corrections = [
        {"fingerprint": "already_covered", "ts": now - 1},
        {"fingerprint": "already_covered", "ts": now - 2},
        {"fingerprint": "already_covered", "ts": now - 3},
    ]
    injections = [
        {"rule_id": "R13", "trigger_fingerprint": "already_covered"},
    ]
    candidates = collect_false_negative_candidates(
        corrections, injections, window_days=14, threshold=3
    )
    assert not candidates, "covered patterns must NOT be flagged as FN candidates"
