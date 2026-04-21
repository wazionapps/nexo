from __future__ import annotations

import importlib
import json
import os
import sys

import pytest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture(autouse=True)
def _isolated_runtime(isolated_db, tmp_path, monkeypatch):
    fake_home = tmp_path / "nexo_home"
    (fake_home / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(fake_home))
    for mod in ["enforcement_engine", "guardian_config", "guard_verbal_ack", "plugins.protocol", "db", "db._protocol"]:
        importlib.reload(importlib.import_module(mod))
    yield


def _register_session(sid: str):
    from db import register_session

    register_session(sid, "guard verbal ack test")
    return sid


def test_detect_guard_verbal_ack_uses_classifier_context():
    from guard_verbal_ack import detect_guard_verbal_ack

    seen = {}

    def classifier(**kwargs):
        seen.update(kwargs)
        return True

    assert detect_guard_verbal_ack(
        "vale",
        task_type="edit",
        goal="Delete the legacy email password file",
        file_path="/Users/test/.claude/nexo_email_pass.txt",
        guard_summary="BLOCKING RULES (#41)",
        classifier=classifier,
    ) is True
    assert "Delete the legacy email password file" in seen["context"]
    assert "/Users/test/.claude/nexo_email_pass.txt" in seen["context"]


def test_detect_guard_verbal_ack_fail_closed():
    from guard_verbal_ack import detect_guard_verbal_ack

    def boom(**_kwargs):
        raise RuntimeError("classifier down")

    assert detect_guard_verbal_ack("ok", classifier=boom) is False


def test_headless_enforcer_auto_acknowledges_single_guarded_task(monkeypatch):
    from db import get_db
    from enforcement_engine import HeadlessEnforcer
    from plugins.protocol import handle_task_open

    sid = _register_session("nexo-7101-1001")
    monkeypatch.setattr(
        "plugins.protocol.handle_guard_check",
        lambda **kwargs: "BLOCKING RULES (resolve BEFORE writing):\n  #41 [FILE RULE:/tmp/x]: Read the canonical rule first\n",
    )
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Delete the legacy password file",
            task_type="edit",
            area="nexo-ops",
            files="/tmp/x",
        )
    )
    enforcer = HeadlessEnforcer()
    enforcer.set_session_id(sid)

    enforcer._maybe_acknowledge_guard_from_user_text(
        "vale, bórralo",
        detector=lambda *_args, **_kwargs: True,
    )

    row = get_db().execute(
        "SELECT guard_acknowledged FROM protocol_tasks WHERE task_id = ?",
        (opened["task_id"],),
    ).fetchone()
    assert row["guard_acknowledged"] == 1
    debt = get_db().execute(
        "SELECT status FROM protocol_debt WHERE task_id = ? AND debt_type = 'unacknowledged_guard_blocking'",
        (opened["task_id"],),
    ).fetchone()
    assert debt is None


def test_headless_enforcer_does_not_auto_ack_multiple_guarded_tasks(monkeypatch):
    from db import get_db
    from enforcement_engine import HeadlessEnforcer
    from plugins.protocol import handle_task_open

    sid = _register_session("nexo-7101-1002")
    monkeypatch.setattr(
        "plugins.protocol.handle_guard_check",
        lambda **kwargs: "BLOCKING RULES (resolve BEFORE writing):\n  #41 [FILE RULE:/tmp/x]: Read the canonical rule first\n",
    )
    first = json.loads(
        handle_task_open(
            sid=sid,
            goal="Edit guarded file one",
            task_type="edit",
            area="nexo-ops",
            files="/tmp/x",
        )
    )
    second = json.loads(
        handle_task_open(
            sid=sid,
            goal="Edit guarded file two",
            task_type="edit",
            area="nexo-ops",
            files="/tmp/y",
        )
    )
    enforcer = HeadlessEnforcer()
    enforcer.set_session_id(sid)

    assert enforcer._maybe_acknowledge_guard_from_user_text(
        "ok",
        detector=lambda *_args, **_kwargs: True,
    ) is False

    rows = get_db().execute(
        "SELECT task_id, guard_acknowledged FROM protocol_tasks WHERE task_id IN (?, ?) ORDER BY task_id",
        (first["task_id"], second["task_id"]),
    ).fetchall()
    assert [row["guard_acknowledged"] for row in rows] == [0, 0]


def test_headless_enforcer_does_not_auto_ack_multi_file_task(monkeypatch):
    from db import get_db
    from enforcement_engine import HeadlessEnforcer
    from plugins.protocol import handle_task_open

    sid = _register_session("nexo-7101-1003")
    monkeypatch.setattr(
        "plugins.protocol.handle_guard_check",
        lambda **kwargs: (
            "BLOCKING RULES (resolve BEFORE writing):\n"
            "  #41 [FILE RULE:/tmp/x]: Read the canonical rule first\n"
            "  #42 [FILE RULE:/tmp/y]: Read the canonical rule first\n"
        ),
    )
    opened = json.loads(
        handle_task_open(
            sid=sid,
            goal="Edit guarded files",
            task_type="edit",
            area="nexo-ops",
            files='["/tmp/x", "/tmp/y"]',
        )
    )
    enforcer = HeadlessEnforcer()
    enforcer.set_session_id(sid)

    assert enforcer._maybe_acknowledge_guard_from_user_text(
        "hazlo",
        detector=lambda *_args, **_kwargs: True,
    ) is False

    row = get_db().execute(
        "SELECT guard_acknowledged FROM protocol_tasks WHERE task_id = ?",
        (opened["task_id"],),
    ).fetchone()
    assert row["guard_acknowledged"] == 0
