"""Tests for Fase B R01 (followup dedup) + R05 (learning auto-merge)."""
from __future__ import annotations

import importlib
import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")),
)


@pytest.fixture(autouse=True)
def r01_r05_runtime(isolated_db):
    import db._core as db_core
    import db._reminders as db_reminders
    import db._learnings as db_learnings
    import db
    import tools_reminders_crud
    import tools_learnings

    importlib.reload(db_core)
    importlib.reload(db_reminders)
    importlib.reload(db_learnings)
    importlib.reload(db)
    importlib.reload(tools_reminders_crud)
    importlib.reload(tools_learnings)
    yield


# ──────────────────────────────────────────────────────────────────────
# R01 — followup_create dedup
# ──────────────────────────────────────────────────────────────────────


def test_r01_accepts_first_followup():
    from tools_reminders_crud import handle_followup_create
    out = handle_followup_create(
        id="NF-R01-001",
        description="Verify nightly deep-sleep cron finishes before 05:00",
    )
    assert "Followup created" in out or "Followup updated" in out
    assert "ERROR" not in out


def test_r01_rejects_near_duplicate():
    """Second followup with overlapping keywords is rejected as near-duplicate."""
    from tools_reminders_crud import handle_followup_create
    first = handle_followup_create(
        id="NF-R01-DUP-A",
        description="Verify nightly deep-sleep cron finishes before 05:00",
    )
    assert "ERROR" not in first
    second = handle_followup_create(
        id="NF-R01-DUP-B",
        description="Verify nightly deep-sleep cron finishes before 05:00",
    )
    assert "ERROR" in second
    assert "R01" in second or "Near-duplicate" in second
    assert "force='true'" in second


def test_r01_force_override_allows_duplicate():
    from tools_reminders_crud import handle_followup_create
    first = handle_followup_create(
        id="NF-R01-FORCE-A",
        description="Investigate WhatsApp Meta token expiration path",
    )
    assert "ERROR" not in first
    second = handle_followup_create(
        id="NF-R01-FORCE-B",
        description="Investigate WhatsApp Meta token expiration path",
        force="true",
    )
    assert "ERROR" not in second


def test_r01_different_topic_not_flagged():
    from tools_reminders_crud import handle_followup_create
    first = handle_followup_create(
        id="NF-R01-DIFF-A",
        description="Verify nightly deep-sleep cron finishes before 05:00",
    )
    assert "ERROR" not in first
    second = handle_followup_create(
        id="NF-R01-DIFF-B",
        description="Review shopify theme banner CSS on homepage",
    )
    assert "ERROR" not in second


def test_r01_empty_description_passes():
    """Degenerate case: empty description has no keywords so no dedup fires."""
    from tools_reminders_crud import handle_followup_create
    out = handle_followup_create(id="NF-R01-EMPTY", description="")
    # Might succeed or fail on other validation, but R01 must not block it
    assert "R01" not in out


# ──────────────────────────────────────────────────────────────────────
# R05 — learning_add auto-merge
# ──────────────────────────────────────────────────────────────────────


def test_r05_auto_merges_high_similarity():
    from tools_learnings import handle_learning_add
    first = handle_learning_add(
        category="nexo-ops",
        title="Deep sleep cron must finish before morning briefing",
        content="The deep-sleep process has to complete all stages before 05:30 "
        "otherwise the 06:00 briefing has stale data",
    )
    assert "ERROR" not in first
    second = handle_learning_add(
        category="nexo-ops",
        title="Deep sleep cron must finish before morning briefing (dup)",
        content="The deep-sleep process has to complete all stages before 05:30 "
        "otherwise the 06:00 briefing has stale data",
    )
    # Should be auto-merged, not a fresh ID.
    assert "R05" in second or "merge" in second.lower() or "matched" in second.lower()
    assert "No duplicate created" in second


def test_r05_different_category_not_merged():
    from tools_learnings import handle_learning_add
    first = handle_learning_add(
        category="nexo-ops",
        title="Watch cron timing around briefing",
        content="Cron xyz window matters for briefing alignment",
    )
    assert "ERROR" not in first
    second = handle_learning_add(
        category="shopify",
        title="Watch cron timing around briefing",
        content="Cron xyz window matters for briefing alignment",
    )
    # Different category → not a merge target, create new.
    assert "merge" not in second.lower() and "matched" not in second.lower()


def test_r05_low_similarity_creates_new():
    from tools_learnings import handle_learning_add
    first = handle_learning_add(
        category="frontend",
        title="Validate UI in browser before declaring release",
        content="Run npm start and verify the fix visually before saying done",
    )
    assert "ERROR" not in first
    second = handle_learning_add(
        category="frontend",
        title="Tailwind v4 uses CSS custom properties",
        content="Use @theme and @custom-variant for dark mode",
    )
    # Different topic, low overlap → new row created.
    assert "merge" not in second.lower() and "matched" not in second.lower()
