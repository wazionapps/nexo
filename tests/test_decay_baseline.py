"""Coverage baseline for cognitive/_decay.py — Fase 4 item 2.

Pre-Fase 4 the module had 7% coverage (168 statements, 156 missing).
This file pins direct exercises of the decay/promote/gc helpers so a
future regression cannot silently break STM lifecycle without surfacing
in CI.
"""

from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _seed_stm(content: str, *, source_type: str = "session", access_count: int = 0,
              strength: float = 1.0, age_hours: float = 1.0,
              stability: float = 1.0, difficulty: float = 0.5) -> int:
    """Insert a single STM row through the cognitive backend and return its id."""
    import cognitive
    db = cognitive._get_db()
    last = (datetime.utcnow() - timedelta(hours=age_hours)).isoformat()
    created = (datetime.utcnow() - timedelta(hours=age_hours)).isoformat()
    cur = db.execute(
        "INSERT INTO stm_memories (content, embedding, source_type, source_id, "
        "source_title, domain, last_accessed, access_count, strength, stability, "
        "difficulty, created_at, promoted_to_ltm) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (
            content,
            cognitive._array_to_blob(cognitive.embed(content)),
            source_type,
            "test:1",
            "Test",
            "test",
            last,
            access_count,
            strength,
            stability,
            difficulty,
            created,
        ),
    )
    db.commit()
    return cur.lastrowid


def _seed_ltm(content: str, *, source_type: str = "learning",
              strength: float = 1.0, age_hours: float = 1.0,
              stability: float = 1.0, difficulty: float = 0.5) -> int:
    import cognitive
    db = cognitive._get_db()
    last = (datetime.utcnow() - timedelta(hours=age_hours)).isoformat()
    cur = db.execute(
        "INSERT INTO ltm_memories (content, embedding, source_type, source_id, "
        "source_title, domain, last_accessed, strength, stability, difficulty, "
        "is_dormant) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (
            content,
            cognitive._array_to_blob(cognitive.embed(content)),
            source_type,
            "test:1",
            "Test",
            "test",
            last,
            strength,
            stability,
            difficulty,
        ),
    )
    db.commit()
    return cur.lastrowid


# ── apply_decay ───────────────────────────────────────────────────────────


class TestApplyDecay:
    def test_apply_decay_reduces_stm_strength_over_time(self, isolated_db):
        from cognitive._decay import apply_decay
        import cognitive
        sid = _seed_stm("decaying memory", strength=1.0, age_hours=48)

        apply_decay(adaptive=False)

        db = cognitive._get_db()
        row = db.execute("SELECT strength FROM stm_memories WHERE id = ?", (sid,)).fetchone()
        assert row["strength"] < 1.0

    def test_apply_decay_marks_weak_ltm_as_dormant(self, isolated_db):
        from cognitive._decay import apply_decay
        import cognitive
        # Strength already very low — after a long age window decay pushes it under 0.1
        lid = _seed_ltm("weak ltm", strength=0.15, age_hours=24 * 365, stability=0.5, difficulty=0.9)

        apply_decay(adaptive=False)

        db = cognitive._get_db()
        row = db.execute(
            "SELECT strength, is_dormant FROM ltm_memories WHERE id = ?", (lid,)
        ).fetchone()
        assert row["is_dormant"] == 1
        assert row["strength"] < 0.1

    def test_apply_decay_skips_pinned_memories(self, isolated_db):
        from cognitive._decay import apply_decay
        import cognitive
        sid = _seed_stm("pinned memory", strength=1.0, age_hours=48)
        cognitive._get_db().execute(
            "UPDATE stm_memories SET lifecycle_state = 'pinned' WHERE id = ?", (sid,)
        )
        cognitive._get_db().commit()

        apply_decay(adaptive=False)

        db = cognitive._get_db()
        row = db.execute("SELECT strength FROM stm_memories WHERE id = ?", (sid,)).fetchone()
        assert row["strength"] == 1.0  # untouched


# ── promote_stm_to_ltm ────────────────────────────────────────────────────


class TestPromoteStmToLtm:
    def test_promotes_stm_with_high_access_count(self, isolated_db):
        from cognitive._decay import promote_stm_to_ltm
        import cognitive
        sid = _seed_stm("frequently accessed", access_count=5, source_type="session")

        promoted = promote_stm_to_ltm()
        assert promoted >= 1

        db = cognitive._get_db()
        row = db.execute("SELECT promoted_to_ltm FROM stm_memories WHERE id = ?", (sid,)).fetchone()
        assert row["promoted_to_ltm"] == 1
        ltm = db.execute(
            "SELECT id FROM ltm_memories WHERE original_stm_id = ?", (sid,)
        ).fetchone()
        assert ltm is not None

    def test_promotes_high_value_source_types(self, isolated_db):
        from cognitive._decay import promote_stm_to_ltm
        sid = _seed_stm("a learning", source_type="learning", access_count=0)
        sid2 = _seed_stm("a decision", source_type="decision", access_count=0)
        sid3 = _seed_stm("noise", source_type="random_noise", access_count=0)

        promote_stm_to_ltm()

        import cognitive
        db = cognitive._get_db()
        learning_row = db.execute(
            "SELECT promoted_to_ltm FROM stm_memories WHERE id = ?", (sid,)
        ).fetchone()
        decision_row = db.execute(
            "SELECT promoted_to_ltm FROM stm_memories WHERE id = ?", (sid2,)
        ).fetchone()
        noise_row = db.execute(
            "SELECT promoted_to_ltm FROM stm_memories WHERE id = ?", (sid3,)
        ).fetchone()
        assert learning_row["promoted_to_ltm"] == 1
        assert decision_row["promoted_to_ltm"] == 1
        assert noise_row["promoted_to_ltm"] == 0  # not promoted


# ── gc_stm + gc_test_memories ─────────────────────────────────────────────


class TestGarbageCollection:
    def test_gc_stm_deletes_weak_old_memories(self, isolated_db):
        from cognitive._decay import gc_stm
        import cognitive
        # Manually backdate the row so the WHERE clause hits it
        sid = _seed_stm("weak old", strength=0.1, age_hours=24 * 30)  # 30 days, weak
        cognitive._get_db().execute(
            "UPDATE stm_memories SET created_at = datetime('now', '-30 days') WHERE id = ?",
            (sid,),
        )
        cognitive._get_db().commit()

        deleted = gc_stm()
        assert deleted >= 1
        row = cognitive._get_db().execute(
            "SELECT id FROM stm_memories WHERE id = ?", (sid,)
        ).fetchone()
        assert row is None

    def test_gc_stm_keeps_strong_recent_memories(self, isolated_db):
        from cognitive._decay import gc_stm
        import cognitive
        sid = _seed_stm("strong recent", strength=0.9, age_hours=1)

        gc_stm()
        row = cognitive._get_db().execute(
            "SELECT id FROM stm_memories WHERE id = ?", (sid,)
        ).fetchone()
        assert row is not None

    def test_gc_test_memories_returns_count(self, isolated_db):
        from cognitive._decay import gc_test_memories
        # Function exists and returns a non-negative int even on empty DB.
        result = gc_test_memories()
        assert isinstance(result, int)
        assert result >= 0
