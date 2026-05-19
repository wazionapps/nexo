from __future__ import annotations

from continuity_sources import build_continuity_bundle, source_plan_for_intent


def test_prior_work_uses_operational_sources_before_transcripts():
    plan = source_plan_for_intent("prior_work")

    assert plan[:4] == ["recent", "tasks", "workflows", "change_log"]
    assert plan.index("transcripts") > plan.index("diary")


def test_schedule_commitment_prioritizes_followups_and_reminders():
    plan = source_plan_for_intent("schedule_commitment")

    assert plan[:2] == ["followups", "reminders"]
    assert "email" in plan


def test_build_continuity_bundle_ranks_fallback_after_operational_sources():
    providers = {
        "recent": lambda _query, _limit: [{"title": "recent hit", "score": 0.2, "timestamp": 1}],
        "tasks": lambda _query, _limit: [],
        "workflows": lambda _query, _limit: [],
        "change_log": lambda _query, _limit: [{"title": "change hit", "score": 0.5, "timestamp": 1}],
        "diary": lambda _query, _limit: [],
        "memory": lambda _query, _limit: [],
        "transcripts": lambda _query, _limit: [{"title": "transcript hit", "score": 0.9, "timestamp": 999}],
    }

    bundle = build_continuity_bundle(intent="prior_work", query="ya hiciste esto", providers=providers)

    assert bundle["consulted"] == ["recent", "tasks", "workflows", "change_log", "diary", "memory", "transcripts"]
    assert [row["title"] for row in bundle["records"]] == ["change hit", "recent hit", "transcript hit"]


def test_build_continuity_bundle_reports_skipped_sources():
    bundle = build_continuity_bundle(
        intent="identity_authorship",
        query="lo hice yo?",
        providers={"recent": lambda _query, _limit: []},
    )

    assert bundle["consulted"] == ["recent"]
    assert "change_log" in bundle["skipped"]
    assert "transcripts" in bundle["skipped"]
