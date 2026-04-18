"""Tests for Fase B R04 (retroactive complete suggestion) + R12 (cognitive write dedup)."""
from __future__ import annotations

import importlib
import json
import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")),
)


@pytest.fixture(autouse=True)
def r04_r12_runtime(isolated_db):
    import db._core as db_core
    import db._reminders as db_reminders
    import db
    import tools_reminders_crud
    import plugins.simple_api as simple_api

    importlib.reload(db_core)
    importlib.reload(db_reminders)
    importlib.reload(db)
    importlib.reload(tools_reminders_crud)
    importlib.reload(simple_api)
    yield


# ──────────────────────────────────────────────────────────────────────
# R04 — find_completable_followups helper
# ──────────────────────────────────────────────────────────────────────


def test_r04_suggests_matching_active_followup():
    """Tight Jaccard: near-identical phrasing clearly crosses the 0.70 gate."""
    from tools_reminders_crud import handle_followup_create, find_completable_followups
    handle_followup_create(
        id="NF-R04-A",
        description="Review deep sleep nightly cron finish timing",
    )
    suggestions = find_completable_followups(
        "Review deep sleep nightly cron finish timing report",
    )
    assert len(suggestions) == 1
    assert suggestions[0]["id"] == "NF-R04-A"
    assert suggestions[0]["similarity"] >= 0.70


def test_r04_semantic_mismatch_not_suggested():
    """Default 0.70 threshold should NOT fire on loosely related content."""
    from tools_reminders_crud import handle_followup_create, find_completable_followups
    handle_followup_create(
        id="NF-R04-SEMANTIC",
        description="Verify PR 214 is merged in wazionapps/nexo",
    )
    # This action description shares only a few tokens; Jaccard ~0.55
    # which is below default threshold — correct conservative behaviour.
    suggestions = find_completable_followups(
        "Merged PR 214 in wazionapps/nexo after review with changes",
    )
    assert suggestions == []


def test_r04_no_match_low_similarity():
    from tools_reminders_crud import handle_followup_create, find_completable_followups
    handle_followup_create(
        id="NF-R04-B",
        description="Review nightly deep sleep cron finish",
    )
    suggestions = find_completable_followups(
        "Implement shopify product recommendations banner",
    )
    assert suggestions == []


def test_r04_empty_context_returns_empty():
    from tools_reminders_crud import find_completable_followups
    assert find_completable_followups("") == []
    assert find_completable_followups("   ") == []


def test_r04_custom_threshold_relaxes_matching():
    from tools_reminders_crud import handle_followup_create, find_completable_followups
    handle_followup_create(
        id="NF-R04-THRESH",
        description="Review morning briefing outcome for WAzion digest",
    )
    # At default 0.70 this loose context may not match.
    loose = find_completable_followups("morning briefing review", threshold=0.30)
    assert len(loose) >= 1
    tight = find_completable_followups("morning briefing review", threshold=0.95)
    assert tight == []


def test_r04_caps_at_five():
    from tools_reminders_crud import handle_followup_create, find_completable_followups
    for i in range(7):
        handle_followup_create(
            id=f"NF-R04-CAP-{i:02d}",
            description=f"Verify recovery optimizer status item number {i}",
            force="true",  # R01 would otherwise reject near-duplicates
        )
    suggestions = find_completable_followups("recovery optimizer status")
    assert len(suggestions) <= 5


# ──────────────────────────────────────────────────────────────────────
# R12 — handle_remember cognitive write dedup
# ──────────────────────────────────────────────────────────────────────


def test_r12_accepts_first_memory():
    from plugins.simple_api import handle_remember
    out = handle_remember(
        content="NEXO Desktop requires approval modal feedback for tool calls",
        title="Desktop approval modal",
        domain="nexo-desktop",
    )
    payload = json.loads(out)
    assert payload["ok"] is True
    assert "merged_into" not in payload


def test_r12_merges_near_duplicate():
    from plugins.simple_api import handle_remember
    first = handle_remember(
        content="The deep sleep cron must finish before morning briefing otherwise data is stale",
        title="Deep sleep cron timing",
        domain="nexo-ops",
    )
    assert "merged_into" not in json.loads(first)
    second = handle_remember(
        content="Deep sleep cron must finish before the morning briefing or the data becomes stale",
        title="Deep sleep cron briefing timing",
        domain="nexo-ops",
    )
    payload = json.loads(second)
    assert payload["ok"] is True
    assert "merged_into" in payload
    assert payload["similarity"] >= 0.80
    assert "R12" in payload["note"]


def test_r12_different_domain_allows_similar_content():
    """Same phrasing in different domain should NOT be auto-merged."""
    from plugins.simple_api import handle_remember
    first = handle_remember(
        content="Watch cron timing carefully around briefing windows",
        domain="nexo-ops",
    )
    second = handle_remember(
        content="Watch cron timing carefully around briefing windows",
        domain="shopify",
    )
    assert "merged_into" not in json.loads(first)
    assert "merged_into" not in json.loads(second)


def test_r12_force_bypasses_dedup():
    from plugins.simple_api import handle_remember
    handle_remember(
        content="The deep sleep cron must finish before morning briefing",
        title="Deep sleep cron",
        domain="nexo-ops",
    )
    out = handle_remember(
        content="The deep sleep cron must finish before morning briefing",
        title="Deep sleep cron",
        domain="nexo-ops",
        force=True,
    )
    payload = json.loads(out)
    assert "merged_into" not in payload


def test_r12_empty_content_error():
    from plugins.simple_api import handle_remember
    out = handle_remember(content="")
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "required" in payload.get("error", "")
