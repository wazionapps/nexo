from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


REPO_SRC = Path(__file__).resolve().parents[1] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _fresh_db(monkeypatch, tmp_path):
    home = tmp_path / "nexo-home"
    db_path = home / "data" / "nexo.db"
    (home / "data").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_TEST_DB", str(db_path))
    # Drop only the dependent client modules so they rebind to the reloaded db.
    # Reload ``db`` IN PLACE (never pop the ``db`` package object): popping ``db``
    # and re-importing makes a NEW module object that orphans the ``import db``
    # global other already-collected test modules (e.g. test_resolution_cache)
    # bound at collection time, contaminating the resolution_cache isolation
    # tests with a stale connection. ``db/__init__`` reloads its submodules in
    # place, so reloading the package object re-points the stack coherently.
    for name in ("tools_learnings", "tools_sessions", "plugins.protocol"):
        sys.modules.pop(name, None)
    import db

    importlib.reload(db)
    db.init_db()
    return db


def test_detected_correction_opens_debt_but_does_not_block_task_close(monkeypatch, tmp_path):
    """Ola 1: a detected correction without a learning no longer BLOCKS the close
    (hard block was friction). It closes the task AND opens a non-blocking debt;
    persisting the learning resolves the requirement."""
    db = _fresh_db(monkeypatch, tmp_path)
    from plugins.protocol import handle_task_close
    from tools_learnings import handle_learning_add

    sid = "nexo-5100-6100"
    task = db.create_protocol_task(
        sid,
        "Fix D.5 regression",
        task_type="edit",
        files=["/repo/src/plugins/protocol.py"],
    )
    db.record_session_correction_requirement(
        sid,
        "Te has equivocado; eso no debe volver a pasar.",
        source="heartbeat",
    )

    # SOFT enforcement: the close SUCCEEDS but opens an error-severity debt.
    closed = json.loads(handle_task_close(sid, task["task_id"], outcome="done", evidence="Verified with targeted regression test output."))
    assert closed["ok"] is True
    assert closed.get("blocked_by") != "d5_correction_learning_required"
    debts = db.list_protocol_debts(session_id=sid, status="open", debt_type="missing_learning_after_correction")
    assert len(debts) >= 1
    assert db.list_session_correction_requirements(session_id=sid, status="open")

    # Closing again must NOT stack a second debt (idempotent _ensure_open_debt).
    json.loads(handle_task_close(sid, task["task_id"], outcome="done", evidence="Verified again."))
    debts_again = db.list_protocol_debts(session_id=sid, status="open", debt_type="missing_learning_after_correction")
    assert len(debts_again) == len(debts)

    # Persisting the learning resolves the open correction requirement.
    added = handle_learning_add(
        "nexo-ops",
        "Persist learning after user correction before closure",
        "When a user correction is detected, persist the reusable rule with nexo_learning_add.",
        reasoning="Regression test for D.5 soft correction compliance.",
        prevention="Check session_correction_requirements before task_close.",
        applies_to="/repo/src/plugins/protocol.py",
        priority="high",
    )
    assert "D.5: resolved 1 pending correction" in added
    assert not db.list_session_correction_requirements(session_id=sid, status="open")


def test_detected_correction_blocks_session_stop_until_learning_add(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    from tools_learnings import handle_learning_add
    from tools_sessions import handle_stop

    sid = "nexo-5200-6200"
    db.register_session(sid, "release work")
    db.record_session_correction_requirement(
        sid,
        "No cierres sin capturar este aprendizaje.",
        source="heartbeat",
    )

    assert handle_stop(sid).startswith("ERROR: session has user correction")

    handle_learning_add(
        "nexo-ops",
        "Stop waits for correction learning",
        "If a correction is detected, nexo_stop must wait until nexo_learning_add records the reusable rule.",
        reasoning="Regression test for D.5 session-stop blocking.",
        prevention="Resolve session_correction_requirements from learning_add.",
        applies_to="src/tools_sessions.py",
        priority="high",
    )

    assert handle_stop(sid) == f"Session {sid} closed."
