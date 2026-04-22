from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_combine_system_messages_skips_empty_items():
    from hooks.post_tool_use import _combine_system_messages

    assert _combine_system_messages("", None, "  ") is None
    assert _combine_system_messages("alpha", "", "beta") == "alpha\n\nbeta"


def test_main_emits_protocol_guardrail_output_as_system_message(monkeypatch, capsys):
    from hooks import post_tool_use

    monkeypatch.setattr(post_tool_use, "_read_stdin_json", lambda: {"session_id": "sid-1"})
    monkeypatch.setattr(post_tool_use, "_run_auto_capture", lambda payload: 0)
    monkeypatch.setattr(post_tool_use, "_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(post_tool_use, "_resolve_sid_from_payload", lambda payload: "nexo-1")
    monkeypatch.setattr(post_tool_use, "check_inbox_and_emit_reminder", lambda sid: None)

    def fake_run_step(cmd: list[str], timeout: int) -> tuple[int, str]:
        name = Path(cmd[-1]).name
        if name == "protocol-guardrail.sh":
            return 0, "NEXO DISCIPLINE:\n- PROTOCOL REMINDER: close with nexo_task_close"
        return 0, ""

    monkeypatch.setattr(post_tool_use, "_run_step", fake_run_step)

    rc = post_tool_use.main()
    out = capsys.readouterr().out.strip()

    assert rc == 0
    payload = json.loads(out)
    assert "NEXO DISCIPLINE" in payload["systemMessage"]
    assert "nexo_task_close" in payload["systemMessage"]


def test_main_merges_protocol_and_inbox_messages(monkeypatch, capsys):
    from hooks import post_tool_use

    monkeypatch.setattr(post_tool_use, "_read_stdin_json", lambda: {"session_id": "sid-2"})
    monkeypatch.setattr(post_tool_use, "_run_auto_capture", lambda payload: 0)
    monkeypatch.setattr(post_tool_use, "_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(post_tool_use, "_resolve_sid_from_payload", lambda payload: "nexo-2")
    monkeypatch.setattr(post_tool_use, "check_inbox_and_emit_reminder", lambda sid: "Inbox reminder")
    monkeypatch.setattr(
        post_tool_use,
        "_run_step",
        lambda cmd, timeout: (0, "Protocol closeout reminder" if Path(cmd[-1]).name == "protocol-guardrail.sh" else ""),
    )

    rc = post_tool_use.main()
    out = capsys.readouterr().out.strip()

    assert rc == 0
    payload = json.loads(out)
    assert payload["systemMessage"] == "Protocol closeout reminder\n\nInbox reminder"
