"""Migration v50: dedupe the NEXO-product-vs-personal learning pair."""
from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _reload_stack(monkeypatch, tmp_path):
    home = tmp_path / "nexo"
    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / "brain").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    import db._core as db_core
    import db._schema as db_schema
    import db
    importlib.reload(db_core)
    importlib.reload(db_schema)
    importlib.reload(db)
    return db, home


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db, _ = _reload_stack(monkeypatch, tmp_path)
    db.init_db()
    return db


def _seed_learning(db, *, title: str, content: str, id_override: int | None = None) -> int:
    conn = db.get_db()
    if id_override is not None:
        cur = conn.execute(
            "INSERT INTO learnings (id, category, title, content, status, created_at, updated_at) "
            "VALUES (?, 'nexo-product', ?, ?, 'active', strftime('%s','now'), strftime('%s','now'))",
            (id_override, title, content),
        )
    else:
        cur = conn.execute(
            "INSERT INTO learnings (category, title, content, status, created_at, updated_at) "
            "VALUES ('nexo-product', ?, ?, 'active', strftime('%s','now'), strftime('%s','now'))",
            (title, content),
        )
    conn.commit()
    return int(cur.lastrowid or 0)


def test_m50_supersedes_older_duplicate_pointing_at_newer(tmp_path, monkeypatch):
    db, _ = _reload_stack(monkeypatch, tmp_path)
    db.init_db()  # full schema on empty DB — v50 runs but has no duplicates
    from db._core import get_db
    conn = get_db()

    older = _seed_learning(
        db,
        title="NEXO Brain producto vs instancia personal de Francisco — separar siempre",
        content="Old wording of the same rule.",
    )
    newer = _seed_learning(
        db,
        title="NEXO Brain producto publico vs instancia personal de Francisco — distinguir",
        content="Newer, more specific wording.",
    )
    assert older < newer

    # Run v50 explicitly.
    from db._schema import _m50_dedupe_nexo_product_learning_pair
    _m50_dedupe_nexo_product_learning_pair(conn)
    conn.commit()

    row_older = conn.execute(
        "SELECT status, supersedes_id FROM learnings WHERE id = ?", (older,)
    ).fetchone()
    row_newer = conn.execute(
        "SELECT status, supersedes_id FROM learnings WHERE id = ?", (newer,)
    ).fetchone()
    assert row_older["status"] == "superseded"
    assert int(row_older["supersedes_id"]) == newer
    # Survivor stays active and keeps its own supersedes_id untouched.
    assert row_newer["status"] == "active"


def test_m50_is_idempotent(tmp_path, monkeypatch):
    db, _ = _reload_stack(monkeypatch, tmp_path)
    db.init_db()  # runs all migrations including v50 on empty DB (no-op)
    from db._core import get_db
    from db._schema import _m50_dedupe_nexo_product_learning_pair
    conn = get_db()
    older = _seed_learning(
        db,
        title="NEXO Brain producto — duplicate A",
        content="A",
    )
    newer = _seed_learning(
        db,
        title="NEXO Brain producto — duplicate B",
        content="B",
    )
    _m50_dedupe_nexo_product_learning_pair(conn)
    conn.commit()
    _m50_dedupe_nexo_product_learning_pair(conn)  # second run must be a no-op
    conn.commit()
    row_older = conn.execute(
        "SELECT status, supersedes_id FROM learnings WHERE id = ?", (older,)
    ).fetchone()
    assert row_older["status"] == "superseded"
    assert int(row_older["supersedes_id"]) == newer


def test_m50_no_op_when_only_one_match(tmp_path, monkeypatch):
    db, _ = _reload_stack(monkeypatch, tmp_path)
    db.init_db()
    from db._core import get_db
    from db._schema import _m50_dedupe_nexo_product_learning_pair
    conn = get_db()
    only_one = _seed_learning(
        db,
        title="NEXO Brain producto — lone row",
        content="Only one row exists.",
    )
    _m50_dedupe_nexo_product_learning_pair(conn)
    conn.commit()
    row = conn.execute(
        "SELECT status, supersedes_id FROM learnings WHERE id = ?", (only_one,)
    ).fetchone()
    assert row["status"] == "active"
    assert row["supersedes_id"] is None


def test_m50_does_not_touch_unrelated_learnings(tmp_path, monkeypatch):
    db, _ = _reload_stack(monkeypatch, tmp_path)
    db.init_db()
    from db._core import get_db
    from db._schema import _m50_dedupe_nexo_product_learning_pair
    conn = get_db()
    unrelated_a = _seed_learning(
        db,
        title="Shopify theme sync — never push stale repo",
        content="A",
    )
    unrelated_b = _seed_learning(
        db,
        title="WAzion scaling — dark mode ProxySQL",
        content="B",
    )
    _m50_dedupe_nexo_product_learning_pair(conn)
    conn.commit()
    for rid in (unrelated_a, unrelated_b):
        row = conn.execute(
            "SELECT status, supersedes_id FROM learnings WHERE id = ?", (rid,)
        ).fetchone()
        assert row["status"] == "active"
        assert row["supersedes_id"] is None
