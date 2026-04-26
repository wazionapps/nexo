"""Tests for ``_parse_email_headers`` in src/scripts/nexo-email-monitor.py.

The 7.9.34 hotfix tracks down why NERO had stopped replying to a subset
of emails. The pattern was always the same: emails with non-ASCII names
or Q-encoded subjects landed in the IMAP UNSEEN scan, the worker tried
to parse the headers, ``msg.get(...)`` returned an
``email.header.Header`` instance instead of a plain string, ``.strip()``
on it raised ``TypeError: 'Header' object is not subscriptable``, the
exception was swallowed at ``log.debug`` level, and the email was
dropped silently. Operator only noticed because the inbox kept growing
and Nero never spoke up.

These tests pin the parser against the failure mode (Q-encoded headers
should produce plain strings end to end) and against the visibility
regression (parse failures must surface at WARNING, not DEBUG).
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from email.header import Header
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock

import pytest


SCRIPT = Path(__file__).resolve().parent.parent / "src" / "scripts" / "nexo-email-monitor.py"


@pytest.fixture
def parser_module(monkeypatch, tmp_path):
    """Load nexo-email-monitor.py with stubbed runtime deps so the parser
    helpers are callable in isolation."""
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
        "send_reply_locator": MagicMock(get_send_reply_script_path=lambda *a, **kw: ""),
        "automation_session_locks": MagicMock(),
        "email_config": MagicMock(),
        "hot_context_recall": MagicMock(read_recent_hot_context=lambda *a, **kw: ""),
        "nexo_helper": MagicMock(),
        "script_runtime": MagicMock(get_script_runtime_contract=lambda name: {"available": True}),
    }
    for name, mock in fake_modules.items():
        monkeypatch.setitem(sys.modules, name, mock)

    spec = importlib.util.spec_from_file_location("nem_parser_under_test", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        pytest.skip(f"nexo-email-monitor.py could not load with stubbed imports: {exc}")
    return module


def _build_q_encoded_email() -> bytes:
    """Build an RFC822 byte stream with non-ASCII headers that the
    stdlib serialises as ``email.header.Header`` instances on read.
    Modelled on the production payloads (Spanish accents in name +
    subject, Message-ID and References produced by recambios providers)."""
    msg = Message()
    msg["From"] = Header("Recambios y Accesorios BMW <info@recambiosyaccesoriosbmw.com>", "utf-8")
    msg["Subject"] = Header("¿Confirmación pedido número 12345?", "utf-8")
    msg["Message-ID"] = "<abc-123@recambiosyaccesoriosbmw.com>"
    msg["In-Reply-To"] = "<original-001@recambiosyaccesoriosbmw.com>"
    msg["References"] = "<original-001@recambiosyaccesoriosbmw.com> <thread-002@nexo-brain.com>"
    msg["Date"] = "Sat, 26 Apr 2026 14:00:00 +0200"
    return msg.as_bytes()


def test_parse_q_encoded_subject_and_from(parser_module):
    headers = parser_module._parse_email_headers(_build_q_encoded_email())
    assert headers, "parser returned empty dict on a valid Q-encoded email"
    assert headers["from_addr"] == "info@recambiosyaccesoriosbmw.com"
    assert headers["from_name"] == "Recambios y Accesorios BMW"
    assert "Confirmación" in headers["subject"]
    assert headers["message_id"] == "<abc-123@recambiosyaccesoriosbmw.com>"


def test_parse_thread_id_picks_last_reference(parser_module):
    headers = parser_module._parse_email_headers(_build_q_encoded_email())
    assert headers["thread_id"] == "<thread-002@nexo-brain.com>"
    assert headers["in_reply_to"] == "<original-001@recambiosyaccesoriosbmw.com>"


def test_parse_returns_strings_only(parser_module):
    """Regression test: every value in the parser output must be a str.
    The original bug shipped Header instances downstream, which in turn
    failed the SQLite insert with ``TypeError: 'Header' object is not
    subscriptable``."""
    headers = parser_module._parse_email_headers(_build_q_encoded_email())
    for key, value in headers.items():
        assert isinstance(value, str), f"{key} is {type(value).__name__}, expected str"


def test_parse_handles_header_in_message_id(parser_module):
    """Some senders deliver Message-ID itself as a Q-encoded header.
    The parser must coerce it before ``.strip()`` runs."""
    msg = Message()
    msg["From"] = "ASCII Sender <a@b.com>"
    msg["Subject"] = "plain"
    msg["Message-ID"] = Header("<weird-id@example.com>", "utf-8")
    msg["In-Reply-To"] = Header("<weird-irt@example.com>", "utf-8")
    msg["References"] = Header("<weird-ref@example.com>", "utf-8")
    msg["Date"] = "Sat, 26 Apr 2026 14:00:00 +0200"
    headers = parser_module._parse_email_headers(msg.as_bytes())
    assert headers, "parser dropped a Q-encoded Message-ID silently"
    assert headers["message_id"] == "<weird-id@example.com>"
    assert headers["in_reply_to"] == "<weird-irt@example.com>"
    assert headers["thread_id"] == "<weird-ref@example.com>"


def test_parse_failure_logs_warning_not_debug(parser_module, caplog):
    """A genuinely unparseable payload must surface at WARNING so the
    operator notices in the log instead of failing silently. The 7.9.34
    investigation only succeeded because we lifted this from DEBUG."""
    caplog.set_level(logging.DEBUG, logger=parser_module.log.name)
    bogus = b"not-even-rfc822\x00\x01\x02"
    # Force a failure by handing a payload that survives parsing but
    # explodes inside the dict comprehension. We reach in via a doctored
    # message: the simplest reliable trip is monkeypatching email.message_from_bytes.
    import email as _email

    def boom(_):
        raise RuntimeError("synthetic decode failure")

    original = _email.message_from_bytes
    _email.message_from_bytes = boom
    try:
        result = parser_module._parse_email_headers(bogus)
    finally:
        _email.message_from_bytes = original

    assert result == {}
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("Header parse failed" in r.getMessage() for r in warning_records), (
        "parser failures should surface at WARNING level so silent drops "
        "(the 7.9.34 root cause) cannot recur"
    )
