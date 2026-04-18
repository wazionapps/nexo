"""Plan Consolidado F0.0.4 — hook_guardrails must respect NEXO_MIGRATING=1."""

from __future__ import annotations


def test_pretool_skips_blocking_when_migrating(monkeypatch):
    from hook_guardrails import process_pre_tool_event

    monkeypatch.setenv("NEXO_MIGRATING", "1")
    payload = {
        "tool_name": "Edit",
        "session_id": "does-not-matter",
        "tool_input": {
            "file_path": "/tmp/migration-target.py",
            "new_string": "# migration in progress\n",
        },
    }
    result = process_pre_tool_event(payload)
    assert result["ok"] is True
    assert result["skipped"] is True
    assert "NEXO_MIGRATING" in result["reason"]


def test_pretool_still_skips_non_write_ops_when_not_migrating(monkeypatch):
    from hook_guardrails import process_pre_tool_event

    monkeypatch.delenv("NEXO_MIGRATING", raising=False)
    payload = {
        "tool_name": "Read",
        "session_id": "",
        "tool_input": {"file_path": "/tmp/x"},
    }
    result = process_pre_tool_event(payload)
    assert result["ok"] is True
    assert result["skipped"] is True
    assert "NEXO_MIGRATING" not in (result.get("reason") or "")
