"""Tests for the Voyager-style skill co-activation detector — Fase 5 item 5."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


_skills_seeded: set[str] = set()


def _ensure_skill_exists(skill_id: str):
    """Insert a stub skill row so the FK on skill_usage holds."""
    if skill_id in _skills_seeded:
        return
    from db import get_db
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO skills (id, name, description, level, content) "
        "VALUES (?, ?, '', 'draft', '')",
        (skill_id, f"Test skill {skill_id}"),
    )
    try:
        conn.commit()
    except Exception:
        pass
    _skills_seeded.add(skill_id)


def _seed_skill_usage(skill_id: str, session_id: str, success: bool = True):
    _ensure_skill_exists(skill_id)
    from db import get_db
    conn = get_db()
    conn.execute(
        "INSERT INTO skill_usage (skill_id, session_id, success, context, created_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        (skill_id, session_id, 1 if success else 0, "test seed"),
    )
    try:
        conn.commit()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_seeded_skills():
    """Reset the cache between tests so each isolated_db starts clean."""
    _skills_seeded.clear()
    yield
    _skills_seeded.clear()


# ── Empty / sparse cases ─────────────────────────────────────────────────


class TestEmptyCases:
    def test_empty_skill_usage_returns_empty_list(self, isolated_db):
        from skills_runtime import detect_skill_coactivation_candidates
        assert detect_skill_coactivation_candidates() == []

    def test_single_skill_per_session_no_pairs(self, isolated_db):
        _seed_skill_usage("SK-A", "sid-1")
        _seed_skill_usage("SK-A", "sid-2")
        from skills_runtime import detect_skill_coactivation_candidates
        result = detect_skill_coactivation_candidates(min_co_occurrence=1)
        assert result == []

    def test_pair_below_min_co_occurrence_excluded(self, isolated_db):
        _seed_skill_usage("SK-A", "sid-1")
        _seed_skill_usage("SK-B", "sid-1")
        _seed_skill_usage("SK-A", "sid-2")
        _seed_skill_usage("SK-B", "sid-2")
        from skills_runtime import detect_skill_coactivation_candidates
        result = detect_skill_coactivation_candidates(min_co_occurrence=3)
        assert result == []


# ── Co-occurrence detection ──────────────────────────────────────────────


class TestCoOccurrenceDetection:
    def test_strong_pair_returns_one_candidate(self, isolated_db):
        for sid in ("sid-1", "sid-2", "sid-3"):
            _seed_skill_usage("SK-A", sid, success=True)
            _seed_skill_usage("SK-B", sid, success=True)
        from skills_runtime import detect_skill_coactivation_candidates
        result = detect_skill_coactivation_candidates(min_co_occurrence=3)
        assert len(result) == 1
        candidate = result[0]
        assert {candidate["skill_a"], candidate["skill_b"]} == {"SK-A", "SK-B"}
        assert candidate["co_occurrence"] == 3
        assert candidate["joint_success_rate"] == 1.0
        assert candidate["suggested_skill_id"] == "SK-COMPOSE-SK-A+SK-B"
        assert "sid-1" in candidate["sessions"]

    def test_pair_below_success_rate_excluded(self, isolated_db):
        _seed_skill_usage("SK-A", "sid-1", success=True)
        _seed_skill_usage("SK-B", "sid-1", success=True)
        for sid in ("sid-2", "sid-3", "sid-4"):
            _seed_skill_usage("SK-A", sid, success=True)
            _seed_skill_usage("SK-B", sid, success=False)
        from skills_runtime import detect_skill_coactivation_candidates
        result = detect_skill_coactivation_candidates(
            min_co_occurrence=3, min_success_rate=0.6
        )
        assert result == []

    def test_multiple_pairs_sorted_by_co_occurrence_desc(self, isolated_db):
        for i in range(5):
            sid = f"ab-{i}"
            _seed_skill_usage("SK-A", sid)
            _seed_skill_usage("SK-B", sid)
        for i in range(3):
            sid = f"cd-{i}"
            _seed_skill_usage("SK-C", sid)
            _seed_skill_usage("SK-D", sid)

        from skills_runtime import detect_skill_coactivation_candidates
        result = detect_skill_coactivation_candidates(min_co_occurrence=3)
        assert len(result) == 2
        assert result[0]["co_occurrence"] == 5
        assert result[1]["co_occurrence"] == 3

    def test_three_skills_in_one_session_yields_three_pairs(self, isolated_db):
        for i in range(3):
            sid = f"abc-{i}"
            _seed_skill_usage("SK-A", sid)
            _seed_skill_usage("SK-B", sid)
            _seed_skill_usage("SK-C", sid)

        from skills_runtime import detect_skill_coactivation_candidates
        result = detect_skill_coactivation_candidates(min_co_occurrence=3)
        assert len(result) == 3

    def test_suggested_skill_id_is_deterministic_and_sorted(self, isolated_db):
        for i in range(3):
            sid = f"order-{i}"
            _seed_skill_usage("SK-Z", sid)
            _seed_skill_usage("SK-A", sid)

        from skills_runtime import detect_skill_coactivation_candidates
        result = detect_skill_coactivation_candidates(min_co_occurrence=3)
        assert len(result) == 1
        assert result[0]["suggested_skill_id"] == "SK-COMPOSE-SK-A+SK-Z"


# ── MCP tool handler shape ───────────────────────────────────────────────


class TestSkillComposeCandidatesTool:
    def test_handler_returns_json_array(self, isolated_db):
        for i in range(3):
            sid = f"tool-{i}"
            _seed_skill_usage("SK-X", sid)
            _seed_skill_usage("SK-Y", sid)

        from plugins.skills import handle_skill_compose_candidates
        import json
        out = handle_skill_compose_candidates(min_co_occurrence=3)
        payload = json.loads(out)
        assert isinstance(payload, list)
        assert len(payload) == 1
        assert payload[0]["skill_a"] in {"SK-X", "SK-Y"}

    def test_handler_with_no_data_returns_empty_array(self, isolated_db):
        from plugins.skills import handle_skill_compose_candidates
        import json
        out = handle_skill_compose_candidates(min_co_occurrence=3)
        assert json.loads(out) == []
