from __future__ import annotations

import json
from types import SimpleNamespace


def test_sdk_remember_calls_nexo_cli(monkeypatch):
    from nexo_sdk import NEXOClient

    calls = []

    def fake_run(cmd, capture_output, text, check):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "memory_id": 9}), stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    client = NEXOClient(nexo_bin="nexo-test")

    result = client.remember("Remember this", title="Note")

    assert result["memory_id"] == 9
    assert calls[0][:4] == ["nexo-test", "call", "nexo_remember", "--input"]
    assert calls[0][-1] == "--json-output"


def test_sdk_run_workflow_serializes_steps(monkeypatch):
    from nexo_sdk import NEXOClient

    captured = {}

    def fake_run(cmd, capture_output, text, check):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "run_id": "WF-9"}), stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    client = NEXOClient()

    result = client.run_workflow(
        "SID-1",
        "Ship release",
        steps=[{"step_key": "plan", "title": "Plan"}],
        shared_state={"phase": "plan"},
    )

    assert result["run_id"] == "WF-9"
    payload = json.loads(captured["cmd"][4])
    assert payload["sid"] == "SID-1"
    assert json.loads(payload["steps"])[0]["step_key"] == "plan"
    assert json.loads(payload["shared_state"])["phase"] == "plan"


def test_sdk_raises_on_cli_error(monkeypatch):
    from nexo_sdk import NEXOClient

    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )

    client = NEXOClient()
    try:
        client.consolidate()
    except RuntimeError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError")
