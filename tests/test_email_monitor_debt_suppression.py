from __future__ import annotations

import importlib.util
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest


SCRIPT = Path(__file__).resolve().parent.parent / "src" / "scripts" / "nexo-email-monitor.py"


@pytest.fixture
def monitor_module(monkeypatch, tmp_path):
    nexo_home = tmp_path / "nexo"
    base_dir = nexo_home / "nexo-email"
    base_dir.mkdir(parents=True, exist_ok=True)
    brain_dir = nexo_home / "brain"
    brain_dir.mkdir(parents=True, exist_ok=True)
    nexo_db = nexo_home / "runtime" / "data" / "nexo.db"
    nexo_db.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    monkeypatch.setenv("NEXO_DB", str(nexo_db))

    fake_modules = {
        "automation_controls": MagicMock(),
        "paths": MagicMock(brain_dir=lambda: brain_dir, nexo_email_dir=lambda: base_dir),
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

    spec = importlib.util.spec_from_file_location("nem_debt_suppression", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        pytest.skip(f"nexo-email-monitor.py could not load with stubbed imports: {exc}")

    monkeypatch.setattr(module, "EMAIL_DB_PATH", base_dir / "nexo-email.db")
    monkeypatch.setattr(module, "BASE_DIR", base_dir)
    return module


def _seed_email_with_commitment(module, message_id: str):
    conn = sqlite3.connect(str(module.EMAIL_DB_PATH))
    conn.row_factory = sqlite3.Row
    module._ensure_emails_table(conn)
    module.ensure_email_events_table(conn)
    conn.execute(
        """
        INSERT INTO emails (message_id, from_addr, subject, received_at, status)
        VALUES (?, 'nora@canarirural.com', 'Runtime Maria memory', '2000-01-01 00:00:00', 'processed')
        """,
        (message_id,),
    )
    conn.execute(
        """
        INSERT INTO email_events (email_id, event, timestamp, detail)
        VALUES (?, 'commitment', '2000-01-01 00:00:00', 'commitment since old')
        """,
        (message_id,),
    )
    conn.commit()
    conn.close()


def _create_hot_context_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE hot_context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            context_key TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            summary TEXT DEFAULT '',
            context_type TEXT DEFAULT 'topic',
            state TEXT DEFAULT 'active',
            owner TEXT DEFAULT '',
            source_type TEXT DEFAULT '',
            source_id TEXT DEFAULT '',
            session_id TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}',
            first_seen_at REAL NOT NULL,
            last_event_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def test_scan_debt_skips_commitment_with_recent_waiting_resolution(monitor_module):
    mid = "<178012257164.85002.7549934263756911856@canarirural.com>"
    _seed_email_with_commitment(monitor_module, mid)
    conn = sqlite3.connect(str(monitor_module.EMAIL_DB_PATH))
    conn.execute(
        """
        INSERT INTO email_events (email_id, event, timestamp, detail)
        VALUES (?, 'resolution', datetime('now','localtime'), ?)
        """,
        (mid, "expected_wait_third_party — wakeup queue stale, waiting_third_party activo"),
    )
    conn.commit()
    conn.close()

    assert monitor_module.scan_debt(db_path=monitor_module.EMAIL_DB_PATH, max_items=5) == ""


def test_scan_debt_skips_commitment_with_waiting_hot_context(monitor_module, monkeypatch):
    mid = "<178012257164.85002.7549934263756911856@canarirural.com>"
    _seed_email_with_commitment(monitor_module, mid)
    nexo_db = Path(sys.modules["runtime_home"].export_resolved_nexo_home()) / "runtime" / "data" / "nexo.db"
    _create_hot_context_db(nexo_db)
    now = time.time()
    conn = sqlite3.connect(str(nexo_db))
    conn.execute(
        """
        INSERT INTO hot_context (
            context_key, title, summary, context_type, state, owner, source_type, source_id,
            first_seen_at, last_event_at, expires_at, created_at, updated_at
        )
        VALUES (?, 'Runtime Maria memory', 'Esperando tercero', 'email', 'waiting_third_party',
            'nora', 'email', ?, ?, ?, ?, ?, ?)
        """,
        (
            f"email:{mid.strip('<>')}",
            mid,
            now,
            now,
            now + 48 * 3600,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("NEXO_DB", str(nexo_db))

    assert monitor_module.scan_debt(db_path=monitor_module.EMAIL_DB_PATH, max_items=5) == ""


def test_scan_debt_still_flags_unresolved_commitment(monitor_module):
    mid = "<unresolved@example.com>"
    _seed_email_with_commitment(monitor_module, mid)

    debt = monitor_module.scan_debt(db_path=monitor_module.EMAIL_DB_PATH, max_items=5)

    assert "COMMITMENT without closure" in debt
    assert mid in debt
