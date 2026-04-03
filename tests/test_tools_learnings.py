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
