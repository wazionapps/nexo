"""Tests for Deep Sleep transcript collection across Claude Code and Codex."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
COLLECT_PATH = REPO_ROOT / "src" / "scripts" / "deep-sleep" / "collect.py"


def _load_collect_module(monkeypatch, home: Path):
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NEXO_HOME", str(home / "nexo-home"))
    monkeypatch.setenv("NEXO_CODE", str(REPO_ROOT / "src"))
    sys.modules.pop("deep_sleep_collect_test", None)
    spec = importlib.util.spec_from_file_location("deep_sleep_collect_test", COLLECT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_collect_transcripts_since_reads_claude_and_codex(monkeypatch, tmp_path):
    claude_file = tmp_path / ".claude" / "projects" / "demo" / "session-1.jsonl"
    claude_file.parent.mkdir(parents=True)
    claude_file.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "message": {"content": "Need help with deploy"}}),
                json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Sure."}]}}),
                json.dumps({"type": "user", "message": {"content": "The backend path changed"}}),
                json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Check nginx."}]}}),
                json.dumps({"type": "user", "message": {"content": "Now it works"}}),
                json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Great."}]}}),
            ]
        )
        + "\n"
    )

    codex_file = tmp_path / ".codex" / "sessions" / "2026" / "04" / "05" / "rollout-demo.jsonl"
    codex_file.parent.mkdir(parents=True)
    codex_file.write_text(
        "\n".join(
            [
                json.dumps({
                    "timestamp": "2026-04-05T01:00:00Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "codex-demo",
                        "cwd": "/repo",
                        "originator": "codex_cli_rs",
                        "source": "cli",
                    },
                }),
                json.dumps({"timestamp": "2026-04-05T01:00:01Z", "type": "event_msg", "payload": {"type": "user_message", "message": "Need to debug watcher"}}),
                json.dumps({"timestamp": "2026-04-05T01:00:02Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Checking logs."}]}}),
                json.dumps({"timestamp": "2026-04-05T01:00:03Z", "type": "event_msg", "payload": {"type": "user_message", "message": "The cron did not run"}}),
                json.dumps({"timestamp": "2026-04-05T01:00:04Z", "type": "response_item", "payload": {"type": "function_call", "name": "mcp__nexo__nexo_heartbeat", "arguments": "{\"sid\":\"x\"}"}}),
                json.dumps({"timestamp": "2026-04-05T01:00:05Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "I found the issue."}]}}),
                json.dumps({"timestamp": "2026-04-05T01:00:06Z", "type": "event_msg", "payload": {"type": "user_message", "message": "Fix it please"}}),
                json.dumps({"timestamp": "2026-04-05T01:00:07Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Patched."}]}}),
            ]
        )
        + "\n"
    )

    collect = _load_collect_module(monkeypatch, tmp_path)
    sessions = collect.collect_transcripts_since("2000-01-01T00:00:00")

    assert len(sessions) == 2
    by_client = {session["client"]: session for session in sessions}
    assert by_client["claude_code"]["session_file"] == "claude_code:session-1.jsonl"
    assert by_client["codex"]["session_file"] == "codex:rollout-demo.jsonl"
    assert by_client["codex"]["tool_use_count"] == 1
    assert by_client["codex"]["originator"] == "codex_cli_rs"


def test_extract_codex_session_ignores_environment_context(monkeypatch, tmp_path):
    codex_file = tmp_path / ".codex" / "sessions" / "2026" / "04" / "05" / "rollout-env.jsonl"
    codex_file.parent.mkdir(parents=True)
    codex_file.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": "codex-env", "source": "cli"}}),
                json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "<environment_context>\n<cw>/tmp</cw>\n</environment_context>"}}),
                json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "one"}}),
                json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "two"}}),
                json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "three"}}),
                json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]}}),
            ]
        )
        + "\n"
    )

    collect = _load_collect_module(monkeypatch, tmp_path)
    session = collect.extract_codex_session(codex_file)

    assert session is not None
    assert session["user_message_count"] == 3
    assert all("<environment_context>" not in msg["text"] for msg in session["messages"])
