"""Tests for retroactive_learnings — Fase 2 item 3 of NEXO-AUDIT-2026-04-11.

Pin the matching logic, the followup surfacing, and the auto-trigger
behavior wired into handle_learning_add.
"""

from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_modules():
    """Force reimport of modules that depend on db._core after isolated_db swap."""
    import db._core as db_core
    import db._schema as db_schema
    import db
    import retroactive_learnings
    import tools_learnings

    importlib.reload(db_core)
    importlib.reload(db_schema)
    importlib.reload(db)
    importlib.reload(retroactive_learnings)
    importlib.reload(tools_learnings)
    return db, retroactive_learnings, tools_learnings


def _seed_decision(
    db,
    *,
    domain: str,
    decision: str,
    based_on: str = "",
    alternatives: str = "",
    context_ref: str = "",
    session_id: str = "test-sid",
):
    conn = db.get_db()
    conn.execute(
        "INSERT INTO decisions (session_id, domain, decision, alternatives, based_on, "
        "confidence, context_ref) VALUES (?, ?, ?, ?, ?, 'medium', ?)",
        (session_id, domain, decision, alternatives, based_on, context_ref),
    )
    try:
        conn.commit()
    except Exception:
        pass
    return conn.execute("SELECT id FROM decisions ORDER BY id DESC LIMIT 1").fetchone()[0]


def _seed_learning(
    db,
    *,
    title: str,
    content: str,
    prevention: str = "",
    applies_to: str = "",
    category: str = "nexo-core",
    priority: str = "high",
    status: str = "active",
):
    conn = db.get_db()
    now = datetime.now().timestamp()
    conn.execute(
        "INSERT INTO learnings (category, title, content, created_at, updated_at, "
        "prevention, applies_to, status, priority, weight) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.7)",
        (category, title, content, now, now, prevention, applies_to, status, priority),
    )
    try:
        conn.commit()
    except Exception:
        pass
    return conn.execute("SELECT id FROM learnings ORDER BY id DESC LIMIT 1").fetchone()[0]


# ── Pure scoring helpers ──────────────────────────────────────────────────


class TestSignificantTokens:
    def test_filters_short_tokens_and_stopwords(self):
        from retroactive_learnings import _significant_tokens
        tokens = _significant_tokens("This is a test of database connection pooling")
        # 'test', 'database', 'connection', 'pooling' qualify; 'this', 'is', 'a', 'of' filtered
        assert "database" in tokens
        assert "connection" in tokens
        assert "pooling" in tokens
        assert "this" not in tokens
        assert "test" in tokens

    def test_handles_empty_input(self):
        from retroactive_learnings import _significant_tokens
        assert _significant_tokens("") == set()
        assert _significant_tokens(None) == set()  # type: ignore[arg-type]


class TestSplitAppliesTo:
    def test_splits_comma_and_keeps_basenames(self):
        from retroactive_learnings import _split_applies_to
        result = _split_applies_to("src/db/_core.py, src/plugins/cortex.py")
        assert "src/db/_core.py" in result
        assert "_core.py" in result
        assert "src/plugins/cortex.py" in result
        assert "cortex.py" in result


# ── End-to-end retroactive scan ───────────────────────────────────────────


class TestApplyLearningRetroactively:
    def test_skips_when_learning_has_no_prevention(self, isolated_db):
        db, retroactive_learnings, _ = _reload_modules()

        learning_id = _seed_learning(
            db,
            title="No prevention here",
            content="just a narrative",
            prevention="",
        )
        _seed_decision(db, domain="db", decision="anything", based_on="anything")

        result = retroactive_learnings.apply_learning_retroactively(learning_id)
        assert result["ok"] is True
        assert result["scanned"] == 0
        assert result["matched"] == 0
        assert result["followups_created"] == 0
        assert "no prevention" in (result["skipped_reason"] or "")

    def test_skips_when_learning_inactive(self, isolated_db):
        db, retroactive_learnings, _ = _reload_modules()

        learning_id = _seed_learning(
            db,
            title="Old archived rule",
            content="superseded",
            prevention="never do x",
            status="archived",
        )
        result = retroactive_learnings.apply_learning_retroactively(learning_id)
        assert result["ok"] is True
        assert result["followups_created"] == 0
        assert "archived" in (result["skipped_reason"] or "")

    def test_finds_match_via_applies_to_overlap(self, isolated_db):
        db, retroactive_learnings, _ = _reload_modules()

        learning_id = _seed_learning(
            db,
            title="Always quote shell args in PHP scripts",
            content="Unquoted shell args lead to injection",
            prevention="Always quote shell args with double quotes",
            applies_to="canarirural,php",
        )
        decision_id = _seed_decision(
            db,
            domain="canarirural",
            decision="Use bare shell args in send_email helper because faster",
            based_on="Performance considerations",
        )
        _seed_decision(
            db,
            domain="ecommerce",
            decision="Use react useEffect for polling",
            based_on="React docs",
        )  # noise — should not match

        result = retroactive_learnings.apply_learning_retroactively(
            learning_id, lookback_days=30, max_matches=5, min_score=0.4
        )

        assert result["ok"] is True
        assert result["scanned"] == 2
        assert result["followups_created"] == 1
        assert len(result["matches"]) == 1
        match = result["matches"][0]
        assert match["decision_id"] == decision_id
        assert match["score"] >= 0.4
        assert match["followup_id"] == f"NF-RETRO-L{learning_id}-D{decision_id}"
        assert match["breakdown"]["applies_to_score"] == 1.0

    def test_finds_match_via_keyword_overlap_only(self, isolated_db):
        db, retroactive_learnings, _ = _reload_modules()

        learning_id = _seed_learning(
            db,
            title="Database migration must be idempotent",
            content="Migrations that mutate data must always be idempotent across reruns",
            prevention="Always wrap migration logic in idempotent guards",
            applies_to="",  # no applies_to — pure keyword path
        )
        decision_id = _seed_decision(
            db,
            domain="backend",
            decision="Run migration script directly without idempotent guards",
            based_on="Migration is one-time",
            alternatives="Add idempotent wrapper",
        )

        result = retroactive_learnings.apply_learning_retroactively(
            learning_id, lookback_days=30, min_score=0.3
        )

        assert result["matched"] >= 1
        assert result["followups_created"] >= 1
        match = next(m for m in result["matches"] if m["decision_id"] == decision_id)
        assert match["breakdown"]["keyword_score"] > 0.0
        assert match["breakdown"]["applies_to_score"] == 0.0

    def test_ignores_keyword_only_match_when_learning_has_applies_to_but_no_hits(self, isolated_db):
        db, retroactive_learnings, _ = _reload_modules()

        learning_id = _seed_learning(
            db,
            title="Translate technical jargon before replying",
            content="Avoid raw protocol jargon in operator-facing answers",
            prevention="Always translate protocol jargon before replying to Francisco",
            applies_to="francisco-briefing,email-digest",
        )
        _seed_decision(
            db,
            domain="operations",
            decision="Reply with direct recommendation before extra explanation",
            based_on="Operator protocol recommends short direct replies",
            alternatives="Longer technical explanation with protocol details",
        )

        result = retroactive_learnings.apply_learning_retroactively(
            learning_id, lookback_days=30, min_score=0.1
        )

        assert result["scanned"] == 1
        assert result["matched"] == 0
        assert result["followups_created"] == 0

        score, breakdown = retroactive_learnings._score_match(
            learning_keywords=retroactive_learnings._significant_tokens(
                "Translate technical jargon before replying "
                "Avoid raw protocol jargon in operator-facing answers "
                "Always translate protocol jargon before replying to Francisco"
            ),
            learning_applies_to=retroactive_learnings._split_applies_to("francisco-briefing,email-digest"),
            decision_row={
                "domain": "operations",
                "decision": "Reply with direct recommendation before extra explanation",
                "based_on": "Operator protocol recommends short direct replies",
                "alternatives": "Longer technical explanation with protocol details",
                "context_ref": "",
            },
        )
        assert score == 0.0
        assert breakdown["keyword_score"] > 0.0
        assert breakdown["applies_to_score"] == 0.0
        assert breakdown["gated_by_applies_to"] is True

    def test_caps_at_max_matches(self, isolated_db):
        db, retroactive_learnings, _ = _reload_modules()

        learning_id = _seed_learning(
            db,
            title="Always use parameterized SQL queries",
            content="Parameterized SQL queries prevent injection",
            prevention="Use parameterized queries always",
            applies_to="backend,sql",
        )
        for i in range(8):
            _seed_decision(
                db,
                domain="backend",
                decision=f"Use parameterized SQL queries inline approach #{i}",
                based_on="parameterized query approach",
            )

        result = retroactive_learnings.apply_learning_retroactively(
            learning_id, lookback_days=30, max_matches=3
        )

        assert result["scanned"] == 8
        assert result["matched"] >= 3
        assert result["followups_created"] == 3
        assert len(result["matches"]) == 3

    def test_idempotent_across_reruns(self, isolated_db):
        db, retroactive_learnings, _ = _reload_modules()

        learning_id = _seed_learning(
            db,
            title="Always quote shell args in PHP scripts",
            content="Unquoted shell args lead to injection",
            prevention="Always quote shell args",
            applies_to="canarirural,php",
        )
        _seed_decision(
            db,
            domain="canarirural",
            decision="Use bare shell args in send_email helper",
            based_on="performance shell quote args",
        )

        first = retroactive_learnings.apply_learning_retroactively(learning_id)
        second = retroactive_learnings.apply_learning_retroactively(learning_id)

        assert first["followups_created"] == 1
        assert second["followups_created"] == 1  # INSERT OR REPLACE writes the same id

        conn = db.get_db()
        rows = conn.execute(
            "SELECT id FROM followups WHERE id LIKE ?", (f"NF-RETRO-L{learning_id}-%",)
        ).fetchall()
        assert len(rows) == 1  # exactly one row, idempotent

    def test_dry_run_does_not_create_followups(self, isolated_db):
        db, retroactive_learnings, _ = _reload_modules()

        learning_id = _seed_learning(
            db,
            title="Always quote shell args",
            content="Unquoted shell args cause injection",
            prevention="Always quote shell args with double quotes",
            applies_to="canarirural,php",
        )
        _seed_decision(
            db,
            domain="canarirural",
            decision="Use bare shell args quote args injection canarirural",
            based_on="performance",
        )

        result = retroactive_learnings.apply_learning_retroactively(
            learning_id, dry_run=True
        )

        assert result["matched"] >= 1
        assert result["followups_created"] == 0
        for m in result["matches"]:
            assert m["followup_id"] is None

        conn = db.get_db()
        rows = conn.execute(
            "SELECT id FROM followups WHERE id LIKE ?", (f"NF-RETRO-L{learning_id}-%",)
        ).fetchall()
        assert rows == []

    def test_ignores_decisions_outside_lookback_window(self, isolated_db):
        db, retroactive_learnings, _ = _reload_modules()

        learning_id = _seed_learning(
            db,
            title="Always parameterize queries",
            content="Parameterized queries always prevent injection",
            prevention="Always use parameterized queries",
            applies_to="backend",
        )
        # Insert a decision with a created_at far in the past (>30 days)
        conn = db.get_db()
        conn.execute(
            "INSERT INTO decisions (session_id, created_at, domain, decision, based_on, confidence) "
            "VALUES (?, datetime('now', '-60 days'), ?, ?, ?, 'medium')",
            ("old-sid", "backend", "Use raw SQL queries instead of parameterized", "speed parameterized injection"),
        )
        try:
            conn.commit()
        except Exception:
            pass

        result = retroactive_learnings.apply_learning_retroactively(
            learning_id, lookback_days=14
        )
        assert result["scanned"] == 0  # 60d > 14d window
        assert result["followups_created"] == 0

    def test_returns_not_found_for_missing_learning(self, isolated_db):
        db, retroactive_learnings, _ = _reload_modules()
        result = retroactive_learnings.apply_learning_retroactively(99999)
        assert result["ok"] is True
        assert result["skipped_reason"] == "learning not found"


# ── Auto-trigger from handle_learning_add ─────────────────────────────────


class TestHandleLearningAddAutoTrigger:
    def test_learning_with_prevention_auto_triggers_retroactive_scan(self, isolated_db):
        db, retroactive_learnings, tools_learnings = _reload_modules()

        # Seed a candidate decision first
        _seed_decision(
            db,
            domain="canarirural",
            decision="Use bare shell args in helper for speed",
            based_on="performance shell quote args injection canarirural",
        )

        result_str = tools_learnings.handle_learning_add(
            category="canarirural",
            title="Always quote shell args in PHP scripts (test)",
            content="Unquoted shell args injection canarirural quote args",
            prevention="Always quote shell args with double quotes injection",
            applies_to="canarirural,php",
            priority="high",
        )

        assert "Learning #" in result_str
        # Either the auto-trigger fired and surfaced a followup, or the
        # message at minimum did not crash. We assert on the persistent
        # state — the followup must exist.
        conn = db.get_db()
        rows = conn.execute(
            "SELECT id FROM followups WHERE id LIKE 'NF-RETRO-L%' AND status = 'PENDING'"
        ).fetchall()
        assert len(rows) >= 1

    def test_learning_without_prevention_does_not_trigger_scan(self, isolated_db):
        db, retroactive_learnings, tools_learnings = _reload_modules()

        _seed_decision(
            db,
            domain="canarirural",
            decision="Use bare shell args in helper",
            based_on="performance",
        )

        tools_learnings.handle_learning_add(
            category="canarirural",
            title="Narrative observation about deploys",
            content="The deploy felt smooth this morning, no specific rule",
            prevention="",  # no prevention rule
            applies_to="canarirural",
            priority="low",
        )

        conn = db.get_db()
        rows = conn.execute(
            "SELECT id FROM followups WHERE id LIKE 'NF-RETRO-L%'"
        ).fetchall()
        assert rows == []
