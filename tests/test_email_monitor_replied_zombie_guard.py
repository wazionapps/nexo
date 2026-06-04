"""Tests for ``_reconcile_replied_zombies`` in ``src/scripts/nexo-email-monitor.py``.

Bug 2026-05-25 (self-critiques 1111/1112): a worker NEXO session sent the reply
via ``nexo-send-reply.py`` but died (exit -9) BEFORE flipping the BD row to a
terminal status. The stuck/zombie recovery then reset the row to 'pending' and
the daemon reinjected the MID, producing a DUPLICATE reply to the operator.

``_reconcile_replied_zombies`` closes such rows as terminal ('processed') and
logs a 'resolution' marker, consulting two durable signals that survive a crash:
  1. ``email_events`` lifecycle markers ('replied'/'resolution'/'action_done').
  2. ``sent_email_events`` rows whose In-Reply-To / References point at the MID.

These tests pin that contract (followup NF-EMAIL-ZOMBIE-PROCESSING-GUARD-20260525).
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

    spec = importlib.util.spec_from_file_location("nem_replied_zombie", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        pytest.skip(f"nexo-email-monitor.py could not load with stubbed imports: {exc}")

    db_path = base_dir / "nexo-email.db"
    monkeypatch.setattr(module, "EMAIL_DB_PATH", db_path)
    monkeypatch.setattr(module, "BASE_DIR", base_dir)

    conn = sqlite3.connect(str(db_path))
    module._ensure_emails_table(conn)
    module.ensure_email_events_table(conn)
    _ensure_sent_ledger(conn)
    conn.close()

    return module


# ``sent_email_events`` lives in the same DB file (see src/email_sent_events.py).
_SENT_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS sent_email_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT,
    sender TEXT,
    to_addrs TEXT NOT NULL DEFAULT '',
    cc_addrs TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    in_reply_to TEXT NOT NULL DEFAULT '',
    references_header TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'sent',
    sent_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    body_text TEXT NOT NULL DEFAULT '',
    meta TEXT NOT NULL DEFAULT '{}'
);
"""


def _ensure_sent_ledger(conn):
    conn.executescript(_SENT_LEDGER_SQL)
    conn.commit()


def _seed_processing_email(module, *, message_id, subject="Re: prueba", status="processing"):
    conn = sqlite3.connect(str(module.EMAIL_DB_PATH))
    conn.execute(
        "INSERT OR REPLACE INTO emails (message_id, from_addr, subject, status, started_at) "
        "VALUES (?, ?, ?, ?, datetime('now','localtime','-3 hours'))",
        (message_id, "franciscocp@gmail.com", subject, status),
    )
    conn.commit()
    conn.close()


def _seed_sent_reply(module, *, in_reply_to, references_header="", sent_message_id="<reply-1@systeam.es>"):
    conn = sqlite3.connect(str(module.EMAIL_DB_PATH))
    conn.execute(
        "INSERT INTO sent_email_events (message_id, sender, to_addrs, subject, in_reply_to, references_header, sent_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime','-2 hours'))",
        (sent_message_id, "nero@systeam.es", "franciscocp@gmail.com", "Re: prueba", in_reply_to, references_header),
    )
    conn.commit()
    conn.close()


def _status_of(module, message_id):
    conn = sqlite3.connect(str(module.EMAIL_DB_PATH))
    row = conn.execute("SELECT status FROM emails WHERE message_id = ?", (message_id,)).fetchone()
    conn.close()
    return row[0] if row else None


def _resolution_events(module, message_id):
    conn = sqlite3.connect(str(module.EMAIL_DB_PATH))
    rows = conn.execute(
        "SELECT detail FROM email_events WHERE email_id = ? AND event = 'resolution'",
        (message_id,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def _run_reconcile(module):
    conn = sqlite3.connect(str(module.EMAIL_DB_PATH))
    conn.row_factory = sqlite3.Row
    sanitized = module._reconcile_replied_zombies(conn)
    conn.commit()
    conn.close()
    return sanitized


def test_processing_with_sent_ledger_reply_is_closed_not_reinjected(monitor_module):
    """Signal 2: a 'processing' row with a posterior Sent (in_reply_to == MID)
    must be closed as 'processed' (terminal -> never reinjected)."""
    mid = "<inbound-zombie@gmail.com>"
    _seed_processing_email(monitor_module, message_id=mid)
    _seed_sent_reply(monitor_module, in_reply_to=mid)

    sanitized = _run_reconcile(monitor_module)

    assert len(sanitized) == 1
    assert sanitized[0]["signal"] == "sent_email_events"
    assert _status_of(monitor_module, mid) == "processed"
    assert _resolution_events(monitor_module, mid), "saneamiento must be logged as a resolution event"


def test_sent_ledger_reply_matched_via_references_header(monitor_module):
    """The MID may appear in the References chain rather than In-Reply-To."""
    mid = "<inbound-refs@gmail.com>"
    _seed_processing_email(monitor_module, message_id=mid)
    _seed_sent_reply(
        monitor_module,
        in_reply_to="<some-other@gmail.com>",
        references_header=f"<root@gmail.com> {mid}",
    )

    sanitized = _run_reconcile(monitor_module)

    assert len(sanitized) == 1
    assert _status_of(monitor_module, mid) == "processed"


def test_processing_with_replied_event_is_closed(monitor_module):
    """Signal 1: a 'replied' lifecycle event keyed to the inbound MID also
    proves the operator was answered, even with no ledger row."""
    mid = "<inbound-event@gmail.com>"
    _seed_processing_email(monitor_module, message_id=mid)
    conn = sqlite3.connect(str(monitor_module.EMAIL_DB_PATH))
    monitor_module._insert_event(conn, mid, "replied", "Reply sent", {"k": "v"})
    conn.commit()
    conn.close()

    sanitized = _run_reconcile(monitor_module)

    assert len(sanitized) == 1
    assert sanitized[0]["signal"] == "email_event:replied"
    assert _status_of(monitor_module, mid) == "processed"


def test_processing_without_any_reply_is_left_untouched(monitor_module):
    """A genuine in-flight 'processing' row (no reply yet) must NOT be touched,
    so legitimate work still completes / can be retried."""
    mid = "<inbound-inflight@gmail.com>"
    _seed_processing_email(monitor_module, message_id=mid)

    sanitized = _run_reconcile(monitor_module)

    assert sanitized == []
    assert _status_of(monitor_module, mid) == "processing"


def test_fresh_message_in_already_answered_thread_is_not_false_positive(monitor_module):
    """A new inbound message (its own distinct MID) in a thread we previously
    answered must NOT be closed: the ledger reply points at the OLD MID only."""
    old_mid = "<thread-msg-1@gmail.com>"
    new_mid = "<thread-msg-2@gmail.com>"
    # We previously answered msg-1.
    _seed_sent_reply(monitor_module, in_reply_to=old_mid, references_header=old_mid)
    # msg-2 just arrived and is being processed for the first time.
    _seed_processing_email(monitor_module, message_id=new_mid, status="pending")

    sanitized = _run_reconcile(monitor_module)

    assert sanitized == [], "must not match a reply that targets a different MID"
    assert _status_of(monitor_module, new_mid) == "pending"
