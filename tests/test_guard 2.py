"""Tests for guard conditioned file learnings."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

REPO_SRC = Path(__file__).resolve().parents[1] / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_guard_stack():
    import db._core as db_core
    import db._fts as db_fts
    import db._schema as db_schema
    import db._learnings as db_learnings
    import db
    import plugins.guard as guard

    importlib.reload(db_core)
    importlib.reload(db_fts)
    importlib.reload(db_schema)
    importlib.reload(db_learnings)
    importlib.reload(db)
    importlib.reload(guard)
    return db, guard


@pytest.fixture
def guard_env(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    (home / "data").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(REPO_SRC))
    return home


def test_handle_guard_file_check_surfaces_conditioned_learning(guard_env):
    db, guard = _reload_guard_stack()
    db.init_db()

    conn = db.get_db()
    created = db.create_learning(
        "nexo-ops",
        "Read protocol rules before editing",
        "Protocol changes require reading the active rule first.",
        prevention="Read the conditioned learning before touching the file.",
        applies_to="/repo/src/plugins/protocol.py",
        status="active",
    )
    conn.execute(
        "UPDATE learnings SET priority = 'critical', weight = 0.9 WHERE id = ?",
        (created["id"],),
    )
    conn.commit()

    output = guard.handle_guard_file_check(["/repo/src/plugins/protocol.py"])

    assert "WARNINGS — resolve before editing:" in output
    assert "conditioned learning" in output
    assert "Read protocol rules before editing" in output


def test_handle_guard_check_promotes_conditioned_learning_to_blocking_rule(guard_env):
    db, guard = _reload_guard_stack()
    db.init_db()

    conn = db.get_db()
    created = db.create_learning(
        "nexo-ops",
        "Never edit migration history blindly",
        "Never edit migration history blindly; read the conditioned rule first.",
        prevention="Review the learning before editing schema files.",
        applies_to="/repo/src/db/_schema.py",
        status="active",
    )
    conn.execute(
        "UPDATE learnings SET priority = 'critical', weight = 1.0 WHERE id = ?",
        (created["id"],),
    )
    conn.commit()

    output = guard.handle_guard_check(files="/repo/src/db/_schema.py", area="nexo")

    assert "BLOCKING RULES" in output
    assert "FILE RULE:/repo/src/db/_schema.py" in output
    assert "Never edit migration history blindly" in output


def test_handle_guard_check_does_not_promote_file_scoped_rules_to_universal_rules(guard_env):
    db, guard = _reload_guard_stack()
    db.init_db()

    conn = db.get_db()
    db.create_learning(
        "nexo-ops",
        "Never edit guard.py directly",
        "Never edit guard.py directly; route all fixes through wrapper helpers instead.",
        prevention="Use the conditioned hotfix path instead.",
        applies_to="/repo/src/plugins/guard.py",
        status="active",
    )
    conn.commit()

    output = guard.handle_guard_check(files="/repo/src/doctor/providers/runtime.py", area="nexo")

    assert "UNIVERSAL RULES" not in output
    assert "Never edit guard.py directly" not in output


def test_handle_guard_file_check_skips_file_scoped_rules_for_other_files(guard_env):
    db, guard = _reload_guard_stack()
    db.init_db()

    db.create_learning(
        "nexo-ops",
        "Never edit guard.py directly",
        "Never edit guard.py directly; route all fixes through wrapper helpers instead.",
        applies_to="/repo/src/plugins/guard.py",
        status="active",
    )

    output = guard.handle_guard_file_check(["/repo/src/doctor/providers/runtime.py"])

    assert "Never edit guard.py directly" not in output
