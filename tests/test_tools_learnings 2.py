"""Tests for learning tool behaviors."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

REPO_SRC = Path(__file__).resolve().parents[1] / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_learning_stack():
    import db._core as db_core
    import db._fts as db_fts
    import db._schema as db_schema
    import db._learnings as db_learnings
    import db
    import tools_learnings

    importlib.reload(db_core)
    importlib.reload(db_fts)
    importlib.reload(db_schema)
    importlib.reload(db_learnings)
    importlib.reload(db)
    importlib.reload(tools_learnings)
    return db, tools_learnings


@pytest.fixture
def learning_env(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    (home / "data").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    return home


def test_handle_learning_add_blocks_exact_duplicate_titles(learning_env):
    db, tools_learnings = _reload_learning_stack()
    db.init_db()

    first = tools_learnings.handle_learning_add(
        category="nexo-ops",
        title="Guard before edit",
        content="Always run the guard first.",
    )
    second = tools_learnings.handle_learning_add(
        category="NEXO-OPS",
        title="guard before edit",
        content="A duplicate should be blocked.",
    )

    conn = db.get_db()
    count = conn.execute(
        "SELECT COUNT(*) FROM learnings WHERE category = ? AND LOWER(title) = LOWER(?)",
        ("nexo-ops", "Guard before edit"),
    ).fetchone()[0]

    assert "added in nexo-ops" in first
    assert "already exists with same title" in second
    assert count == 1


def test_handle_learning_add_blocks_conflicting_file_conditioned_learning(learning_env):
    db, tools_learnings = _reload_learning_stack()
    db.init_db()

    first = tools_learnings.handle_learning_add(
        category="nexo-ops",
        title="Never edit protocol.py directly",
        content="Never edit protocol.py directly; extend wrappers around it instead.",
        applies_to="/repo/src/plugins/protocol.py",
        priority="critical",
    )
    second = tools_learnings.handle_learning_add(
        category="nexo-ops",
        title="Edit protocol.py directly for hotfixes",
        content="Edit protocol.py directly for urgent hotfixes before wrapping anything else.",
        applies_to="/repo/src/plugins/protocol.py",
        priority="critical",
    )

    conn = db.get_db()
    active = conn.execute("SELECT COUNT(*) FROM learnings WHERE status = 'active'").fetchone()[0]

    assert "added in nexo-ops" in first
    assert "Contradictory active learning" in second
    assert active == 1


def test_handle_learning_update_requires_supersede_when_conflicting_file_rule_exists(learning_env):
    db, tools_learnings = _reload_learning_stack()
    db.init_db()

    tools_learnings.handle_learning_add(
        category="nexo-ops",
        title="Always validate release before push",
        content="Always validate the release before pushing to origin.",
        applies_to="/repo/CHANGELOG.md",
        priority="critical",
    )
    tools_learnings.handle_learning_add(
        category="nexo-ops",
        title="Keep release notes compact",
        content="Keep release notes compact and evidence-backed.",
        applies_to="/repo/CHANGELOG.md",
        priority="high",
    )

    result = tools_learnings.handle_learning_update(
        2,
        title="Skip release validation on small patches",
        content="Skip release validation on small patches to move faster.",
    )

    assert "Update would conflict with active learning" in result


def test_handle_learning_add_can_supersede_existing_file_rule(learning_env):
    db, tools_learnings = _reload_learning_stack()
    db.init_db()

    tools_learnings.handle_learning_add(
        category="nexo-ops",
        title="Never edit protocol.py directly",
        content="Never edit protocol.py directly; extend wrappers instead.",
        applies_to="/repo/src/plugins/protocol.py",
        priority="critical",
    )
    result = tools_learnings.handle_learning_add(
        category="nexo-ops",
        title="Edit protocol.py only through protocol tasks",
        content="Edit protocol.py only through protocol tasks with full evidence.",
        applies_to="/repo/src/plugins/protocol.py",
        priority="critical",
        supersedes_id=1,
    )

    conn = db.get_db()
    old_row = conn.execute("SELECT status FROM learnings WHERE id = 1").fetchone()
    new_row = conn.execute("SELECT supersedes_id, status FROM learnings WHERE id = 2").fetchone()

    assert "supersedes=1" in result
    assert old_row["status"] == "superseded"
    assert new_row["supersedes_id"] == 1
    assert new_row["status"] == "active"


def test_handle_learning_add_records_repeated_error_similarity(learning_env):
    db, tools_learnings = _reload_learning_stack()
    db.init_db()

    first = tools_learnings.handle_learning_add(
        category="nexo-ops",
        title="Release validation skipped",
        content="Skipping release validation caused drift in published artifacts.",
    )
    second = tools_learnings.handle_learning_add(
        category="nexo-ops",
        title="Release validation was skipped again",
        content="Skipping release validation drifted the published artifacts again.",
    )

    conn = db.get_db()
    repetitions = conn.execute(
        "SELECT COUNT(*) FROM error_repetitions WHERE area = 'nexo-ops'"
    ).fetchone()[0]

    assert "added in nexo-ops" in first
    assert "REPETITION WARNING" in second
    assert repetitions >= 1


def test_learning_quality_scores_surface_conflict_pressure_and_richness(learning_env):
    db, tools_learnings = _reload_learning_stack()
    db.init_db()

    tools_learnings.handle_learning_add(
        category="nexo-ops",
        title="Always validate release artifacts",
        content="Validate release artifacts before publish and record the result in the change log.",
        reasoning="Recent drift came from publishing without artifact verification.",
        prevention="Run the release validator before tagging.",
        applies_to="/repo/CHANGELOG.md",
        priority="critical",
    )
    tools_learnings.handle_learning_add(
        category="nexo-ops",
        title="Skip release artifact validation",
        content="Skip release artifact validation on hotfixes.",
        applies_to="/repo/CHANGELOG.md",
        priority="critical",
        supersedes_id=1,
    )

    conn = db.get_db()
    conn.execute("UPDATE learnings SET guard_hits = 3 WHERE id = 2")
    conn.commit()
    row = conn.execute("SELECT * FROM learnings WHERE id = 2").fetchone()

    quality = tools_learnings.score_learning_quality(dict(row), conn)
    listing = tools_learnings.handle_learning_quality(id=2)

    assert quality["score"] >= 60
    assert "LEARNING QUALITY" in listing
    assert "conf=" in listing


def test_learning_list_and_search_include_quality_score(learning_env):
    db, tools_learnings = _reload_learning_stack()
    db.init_db()

    tools_learnings.handle_learning_add(
        category="nexo-ops",
        title="Guard before shared edits",
        content="Run guard before shared edits and capture evidence after the change.",
        reasoning="Shared edits without guard were repeating known mistakes.",
        prevention="Use nexo_guard_check before editing shared files.",
        priority="high",
    )

    listed = tools_learnings.handle_learning_list("nexo-ops")
    found = tools_learnings.handle_learning_search("shared edits", "nexo-ops")

    assert "q=" in listed
    assert "q=" in found
