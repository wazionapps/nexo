from __future__ import annotations

from pathlib import Path


def test_sent_email_events_are_queryable_for_duplicate_checks(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    monkeypatch.setenv("NEXO_HOME", str(home))

    import importlib
    import paths
    import email_sent_events

    importlib.reload(paths)
    importlib.reload(email_sent_events)

    db_path = home / "runtime" / "nexo-email" / "nexo-email.db"
    email_sent_events.record_sent_email(
        message_id="<sent-1@example.test>",
        sender="agent@example.test",
        to_addrs="Client <client@example.test>",
        subject="Release checklist",
        source="test",
        db_path=db_path,
        record_memory=False,
    )

    found = email_sent_events.find_sent_email(
        to_addr="client@example.test",
        subject="Release checklist",
        db_path=db_path,
    )
    assert found is not None
    assert found["message_id"] == "<sent-1@example.test>"

    block = email_sent_events.format_recent_sent_email_block(hours=24, limit=5)
    assert "EMAILS ENVIADOS ULTIMAS 24H POR LA OPERATIVA" in block
    assert "Release checklist" in block


def test_check_context_uses_sent_email_events_before_maildir(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    monkeypatch.setenv("NEXO_HOME", str(home))

    import importlib.util
    import sys

    src = Path(__file__).resolve().parents[1] / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    import paths
    import email_sent_events

    importlib.reload(paths)
    importlib.reload(email_sent_events)
    email_sent_events.record_sent_email(
        message_id="<sent-2@example.test>",
        sender="agent@example.test",
        to_addrs="client@example.test",
        subject="Already sent",
        record_memory=False,
    )

    script = src / "scripts" / "check-context.py"
    spec = importlib.util.spec_from_file_location("check_context_sent_email_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    checker = module.ContextChecker()
    assert checker.check_email_sent("client@example.test", "Already sent") is True
