"""Tests for Fase B R07 (memory age_days annotation) + R08 (recurrence conflict)."""
from __future__ import annotations

import importlib
import os
import sys
import time

import pytest

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")),
)


@pytest.fixture(autouse=True)
def r07_r08_runtime(isolated_db):
    import db._core as db_core
    import db._reminders as db_reminders
    import db
    import tools_reminders_crud
    import cognitive._memory as cog_memory

    importlib.reload(db_core)
    importlib.reload(db_reminders)
    importlib.reload(db)
    importlib.reload(tools_reminders_crud)
    importlib.reload(cog_memory)
    yield


# ──────────────────────────────────────────────────────────────────────
# R07 — age_days annotation
# ──────────────────────────────────────────────────────────────────────


def test_r07_age_annotation_recent():
    from cognitive._memory import format_results, _compute_age_days
    from datetime import datetime, timezone
    now_str = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    age = _compute_age_days(now_str)
    assert age == 0
    results = [{
        "score": 0.9, "source_type": "learning", "domain": "nexo",
        "source_title": "test", "content": "recent memory",
        "store": "ltm", "created_at": now_str,
    }]
    out = format_results(results)
    assert "[0d]" in out
    assert "stale" not in out.lower()
    # Annotation persists on the dict so callers can gate behaviour.
    assert results[0]["age_days"] == 0


def test_r07_age_annotation_stale():
    from cognitive._memory import format_results
    from datetime import datetime, timedelta, timezone
    ten_days_ago = (
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)
    ).strftime("%Y-%m-%d %H:%M:%S")
    results = [{
        "score": 0.9, "source_type": "learning", "domain": "",
        "source_title": "", "content": "stale memory",
        "store": "ltm", "created_at": ten_days_ago,
    }]
    out = format_results(results)
    assert "stale:10d" in out
    assert results[0]["age_days"] == 10


def test_r07_age_missing_timestamp_no_crash():
    from cognitive._memory import format_results
    results = [{
        "score": 0.5, "source_type": "change", "domain": "",
        "source_title": "", "content": "no timestamp",
        "store": "stm",
    }]
    out = format_results(results)
    # No crash, no age tag.
    assert "stale" not in out.lower()
    assert "[0d]" not in out


def test_r07_age_computation_bad_input_returns_none():
    from cognitive._memory import _compute_age_days
    assert _compute_age_days(None) is None
    assert _compute_age_days("") is None
    assert _compute_age_days("not-a-date") is None


def test_r07_age_computation_epoch_float():
    from cognitive._memory import _compute_age_days
    two_days_ago = time.time() - 2 * 86400
    age = _compute_age_days(two_days_ago)
    assert age in {1, 2}  # boundary tolerance


# ──────────────────────────────────────────────────────────────────────
# R08 — recurrence conflict
# ──────────────────────────────────────────────────────────────────────


def test_r08_no_recurrence_no_check():
    from tools_reminders_crud import handle_followup_create
    out1 = handle_followup_create(
        id="NF-R08-001",
        description="Review nightly deep sleep finish times",
    )
    out2 = handle_followup_create(
        id="NF-R08-002",
        description="Another distinct topic to avoid R01",
    )
    assert "ERROR" not in out1
    assert "ERROR" not in out2


def test_r08_recurrence_conflict_rejected():
    from tools_reminders_crud import handle_followup_create
    out1 = handle_followup_create(
        id="NF-R08-REC-A",
        description="Weekly Monday check for topic A",
        recurrence="weekly:monday",
    )
    assert "ERROR" not in out1
    out2 = handle_followup_create(
        id="NF-R08-REC-B",
        description="Totally different unrelated task bear elephant",
        recurrence="weekly:monday",
    )
    assert "ERROR" in out2
    assert "R08" in out2
    assert "weekly:monday" in out2
    assert "force='true'" in out2


def test_r08_different_recurrence_allowed():
    from tools_reminders_crud import handle_followup_create
    out1 = handle_followup_create(
        id="NF-R08-DIFF-A",
        description="Weekly Monday topic",
        recurrence="weekly:monday",
    )
    out2 = handle_followup_create(
        id="NF-R08-DIFF-B",
        description="Totally different unrelated task bear elephant",
        recurrence="weekly:tuesday",
    )
    assert "ERROR" not in out1
    assert "ERROR" not in out2


def test_r08_force_override():
    from tools_reminders_crud import handle_followup_create
    out1 = handle_followup_create(
        id="NF-R08-FORCE-A",
        description="Weekly Monday topic A",
        recurrence="weekly:monday",
    )
    out2 = handle_followup_create(
        id="NF-R08-FORCE-B",
        description="Totally different unrelated task bear elephant",
        recurrence="weekly:monday",
        force="true",
    )
    assert "ERROR" not in out1
    assert "ERROR" not in out2
