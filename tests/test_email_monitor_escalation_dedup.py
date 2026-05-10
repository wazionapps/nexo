"""Tests for the dedup of operator-facing escalation emails sent by
``_escalate_exhausted_emails`` in ``src/scripts/nexo-email-monitor.py``.

Bug 2026-05-10: Francisco received the same "Los siguientes emails ya se han
intentado 3 veces" notification multiple times for the same Baileys Issue
#2454 email. Root cause: there was no persistent flag to mark which emails
the operator had already been notified about, so any later code path that
re-entered the function with the same exhausted email would re-send the
escalation. The fix introduces ``emails.escalation_notified_at`` and a
filter step before the operator email is built.

These tests pin that contract so the regression cannot come back.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


SCRIPT = Path(__file__).resolve().parent.parent / "src" / "scripts" / "nexo-email-monitor.py"


@pytest.fixture
def monitor_module(monkeypatch, tmp_path):
    nexo_home = tmp_path / "nexo"
    base_dir = nexo_home / "nexo-email"
    base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))

    fake_modules = {
        "automation_controls": MagicMock(),
        "paths": MagicMock(brain_dir=lambda: nexo_home / "brain", nexo_email_dir=lambda: base_dir),
        "runtime_home": MagicMock(export_resolved_nexo_home=lambda *a, **kw: nexo_home),
        "agent_runner": MagicMock(
            AutomationBackendUnavailableError=Exception,
            run_automation_prompt=MagicMock(),
        ),
        "client_preferences": MagicMock(),
        "core_prompts": MagicMock(render_core_prompt=lambda *a, **kw: ""),
        "calibration_runtime": MagicMock(get_operator_profile=lambda: {}),
        "operator_extra_instructions": MagicMock(format_operator_extra_instructions_block=lambda *a, **kw: ""),
        "send_reply_locator": MagicMock(get_send_reply_script_path=lambda *a, **kw: "/tmp/fake-send.py"),
        "automation_session_locks": MagicMock(),
        "email_config": MagicMock(),
        "hot_context_recall": MagicMock(read_recent_hot_context=lambda *a, **kw: ""),
        "nexo_helper": MagicMock(),
        "script_runtime": MagicMock(get_script_runtime_contract=lambda name: {"available": True}),
    }
    for name, mock in fake_modules.items():
        monkeypatch.setitem(sys.modules, name, mock)

    spec = importlib.util.spec_from_file_location("nem_dedup", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        pytest.skip(f"nexo-email-monitor.py could not load with stubbed imports: {exc}")

    db_path = base_dir / "nexo-email.db"
    monkeypatch.setattr(module, "EMAIL_DB_PATH", db_path)
    monkeypatch.setattr(module, "BASE_DIR", base_dir)
    monkeypatch.setattr(module, "MAX_EMAIL_ATTEMPTS", 3)
    monkeypatch.setattr(module, "_get_operator_info", lambda: ("Francisco", "Nero", "es"))
    monkeypatch.setattr(module, "get_send_reply_script_path", lambda *a, **kw: "/tmp/fake-send.py")

    conn = sqlite3.connect(str(db_path))
    module._ensure_emails_table(conn)
    conn.close()

    return module


def _seed_email(module, *, message_id, attempts):
    conn = sqlite3.connect(str(module.EMAIL_DB_PATH))
    module._ensure_emails_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO emails (message_id, from_addr, subject, attempts, status) "
        "VALUES (?, ?, ?, ?, 'pending')",
        (message_id, "info@wazion.com", "Fwd: Baileys bug", attempts),
    )
    conn.commit()
    conn.close()


def _read_notified_at(module, message_id):
    conn = sqlite3.connect(str(module.EMAIL_DB_PATH))
    row = conn.execute(
        "SELECT escalation_notified_at FROM emails WHERE message_id = ?",
        (message_id,),
    ).fetchone()
    conn.close()
    return row[0] if row else None


class _SubprocResult:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stdout = b""
        self.stderr = b""


def test_first_escalation_notifies_and_stamps(monitor_module, monkeypatch):
    mid = "<first@example.com>"
    _seed_email(monitor_module, message_id=mid, attempts=2)
    batch = [{"message_id": mid, "attempts": 2, "subject": "x", "from_addr": "info@wazion.com"}]

    calls = []
    monkeypatch.setattr(monitor_module.subprocess, "run", lambda *a, **kw: (calls.append((a, kw)) or _SubprocResult(0)))

    monitor_module._escalate_exhausted_emails({"operator_email": "francisco@example.com"}, batch)

    assert len(calls) == 1, "first escalation must send exactly one operator email"
    assert _read_notified_at(monitor_module, mid) is not None


def test_second_escalation_for_same_email_is_skipped(monitor_module, monkeypatch):
    mid = "<dup@example.com>"
    _seed_email(monitor_module, message_id=mid, attempts=2)
    batch = [{"message_id": mid, "attempts": 2, "subject": "x", "from_addr": "info@wazion.com"}]

    calls = []
    monkeypatch.setattr(monitor_module.subprocess, "run", lambda *a, **kw: (calls.append((a, kw)) or _SubprocResult(0)))

    monitor_module._escalate_exhausted_emails({"operator_email": "francisco@example.com"}, batch)
    monitor_module._escalate_exhausted_emails({"operator_email": "francisco@example.com"}, batch)

    assert len(calls) == 1, "second escalation for the same exhausted email must NOT re-notify"


def test_mixed_batch_only_notifies_new_emails(monitor_module, monkeypatch):
    already = "<already@example.com>"
    fresh = "<fresh@example.com>"
    _seed_email(monitor_module, message_id=already, attempts=2)
    _seed_email(monitor_module, message_id=fresh, attempts=2)

    calls = []
    monkeypatch.setattr(monitor_module.subprocess, "run", lambda *a, **kw: (calls.append((a, kw)) or _SubprocResult(0)))

    monitor_module._escalate_exhausted_emails(
        {"operator_email": "francisco@example.com"},
        [{"message_id": already, "attempts": 2, "subject": "a", "from_addr": "info@wazion.com"}],
    )
    monitor_module._escalate_exhausted_emails(
        {"operator_email": "francisco@example.com"},
        [
            {"message_id": already, "attempts": 2, "subject": "a", "from_addr": "info@wazion.com"},
            {"message_id": fresh, "attempts": 2, "subject": "b", "from_addr": "info@wazion.com"},
        ],
    )

    assert len(calls) == 2, "second invocation must escalate only the fresh email, not re-notify the already-notified one"
    body_path = monitor_module.BASE_DIR / ".escalation-body.txt"
    assert not body_path.exists(), "body file must be cleaned up after send"
    assert _read_notified_at(monitor_module, fresh) is not None


def test_failed_send_does_not_stamp_notified_at(monitor_module, monkeypatch):
    mid = "<fail@example.com>"
    _seed_email(monitor_module, message_id=mid, attempts=2)
    batch = [{"message_id": mid, "attempts": 2, "subject": "x", "from_addr": "info@wazion.com"}]

    monkeypatch.setattr(monitor_module.subprocess, "run", lambda *a, **kw: _SubprocResult(returncode=1))

    monitor_module._escalate_exhausted_emails({"operator_email": "francisco@example.com"}, batch)

    assert _read_notified_at(monitor_module, mid) is None, (
        "if the send fails, escalation_notified_at must remain NULL so a future "
        "successful run can still notify the operator"
    )
