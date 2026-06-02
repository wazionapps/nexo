from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _load_morning_agent(name: str = "nexo_morning_agent_contract_test"):
    path = SRC / "scripts" / "nexo-morning-agent.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Result:
    returncode = 0
    stderr = ""

    def __init__(self, payload):
        self.stdout = json.dumps(payload)


def test_generate_briefing_accepts_legacy_body(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo"))
    module = _load_morning_agent("nexo_morning_agent_legacy_contract_test")
    monkeypatch.setattr(module, "resolve_automation_backend", lambda: "none")
    monkeypatch.setattr(module, "run_automation_prompt", lambda *a, **k: _Result({
        "subject": "Hello",
        "body": "Plain body",
    }))

    presentation = module.generate_briefing("prompt")

    assert presentation.subject == "Hello"
    assert presentation.body_text == "Plain body"
    assert "<p>Plain body</p>" in presentation.body_html


def test_generate_briefing_sanitizes_new_body_html(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo"))
    module = _load_morning_agent("nexo_morning_agent_html_contract_test")
    monkeypatch.setattr(module, "resolve_automation_backend", lambda: "none")
    monkeypatch.setattr(module, "run_automation_prompt", lambda *a, **k: _Result({
        "subject": "Hello",
        "body_text": "Plain body",
        "body_html": "<p onclick='x()'>Plain body</p><script>bad()</script>",
    }))

    presentation = module.generate_briefing("prompt")

    assert "onclick" not in presentation.body_html
    assert "script" not in presentation.body_html.lower()
    assert "<p>Plain body</p>" in presentation.body_html


def test_send_briefing_passes_html_file_and_kind(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo"))
    module = _load_morning_agent("nexo_morning_agent_send_contract_test")
    sender = tmp_path / "nexo-send-reply.py"
    sender.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    monkeypatch.setattr(module, "get_send_reply_script_path", lambda local_script_dir=None: sender)

    calls = {}

    def fake_run(args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        class Completed:
            returncode = 0
            stdout = "OK:<id>"
            stderr = ""
        return Completed()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    output = module.send_briefing(
        recipient="user@example.com",
        subject="Subject",
        body_text="Body",
        body_html="<p>Body</p>",
    )

    assert output == "OK:<id>"
    assert "--html-file" in calls["args"]
    assert calls["args"][calls["args"].index("--message-kind") + 1] == "morning_briefing"
