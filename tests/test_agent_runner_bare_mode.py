"""Tests for run_automation_prompt(bare_mode=...) and API key resolution.

Context: Claude Code 2.1.x auto-loads ``~/.claude/CLAUDE.md`` and runs a
handful of background processes (hook sync, plugin refresh, keychain
probe) on every invocation. On the reference install that adds ~7 seconds
of latency to each call — which is why Session 1 of deep-sleep took 57
minutes before v5.9.2. The ``--bare`` flag skips all of that, at the cost
of requiring an explicit ANTHROPIC_API_KEY in the env.

What this suite locks down:

- ``_resolve_anthropic_api_key`` looks at ``ANTHROPIC_API_KEY`` env, then
  ``~/.claude/anthropic-api-key.txt``, then ``~/.nexo/config``.
- ``BARE_MODE_SAFE_CALLERS`` contains only callers that use no MCP tools.
- When ``bare_mode=True`` and a key is available, the command includes
  ``--bare`` and the env carries ``ANTHROPIC_API_KEY``.
- When ``bare_mode=None`` and the caller is in the safe set, bare is
  auto-enabled.
- When bare is requested but no key is available, the command falls back
  to the normal path silently (not a raise).
- ``--bare`` replaces ``--dangerously-skip-permissions`` in the command
  line (claude 2.1.x rejects them together because bare implies skip).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_runner


def _fake_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["/fake/claude"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_bare_mode_safe_callers_contains_deep_sleep():
    assert "deep-sleep/extract" in agent_runner.BARE_MODE_SAFE_CALLERS
    assert "deep-sleep/synthesize" in agent_runner.BARE_MODE_SAFE_CALLERS


def test_bare_mode_safe_callers_excludes_mcp_using_callers():
    # Callers that allow mcp__nexo__* cannot run under --bare because
    # --bare disables MCP bootstrap. Keep them out of the safe set.
    for unsafe in ("catchup/morning", "evolution/run", "daily_self_audit"):
        assert unsafe not in agent_runner.BARE_MODE_SAFE_CALLERS, (
            f"{unsafe} uses mcp__nexo__* tools and must not be bare-safe"
        )


def test_resolve_api_key_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-from-env")
    monkeypatch.setattr(
        agent_runner,
        "_ANTHROPIC_API_KEY_SEARCH_PATHS",
        (tmp_path / "never.txt",),
    )
    assert agent_runner._resolve_anthropic_api_key() == "sk-test-from-env"


def test_resolve_api_key_reads_from_claude_home(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    key_file = tmp_path / "key.txt"
    key_file.write_text("  sk-from-file\n")
    monkeypatch.setattr(
        agent_runner, "_ANTHROPIC_API_KEY_SEARCH_PATHS", (key_file,)
    )
    assert agent_runner._resolve_anthropic_api_key() == "sk-from-file"


def test_resolve_api_key_returns_empty_when_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        agent_runner,
        "_ANTHROPIC_API_KEY_SEARCH_PATHS",
        (tmp_path / "nope.txt",),
    )
    assert agent_runner._resolve_anthropic_api_key() == ""


def test_bare_mode_explicit_true_activates_bare_and_injects_key(monkeypatch):
    """bare_mode=True + an available API key must add --bare to the cmd
    and drop --dangerously-skip-permissions (they are mutually exclusive
    in claude 2.1.x)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-bare-test")

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env", {})
        return _fake_completed(stdout='{"type":"result","result":"ok"}')

    # Avoid the enforcement_engine path (unrelated to bare mode).
    monkeypatch.setattr(
        agent_runner, "_resolve_claude_cli", lambda: "/fake/claude"
    )
    monkeypatch.setattr(subprocess, "run", fake_run)
    # Force the enforcement_engine import to fail so fake_run is used.
    monkeypatch.setitem(sys.modules, "enforcement_engine", None)

    result = agent_runner.run_automation_prompt(
        "hello",
        caller="deep-sleep/extract",
        backend="claude_code",
        bare_mode=True,
    )

    assert result.returncode == 0
    assert "--bare" in captured["cmd"]
    assert "--dangerously-skip-permissions" not in captured["cmd"]
    assert captured["env"].get("ANTHROPIC_API_KEY") == "sk-bare-test"


def test_bare_mode_auto_enabled_for_safe_caller(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-auto")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed(stdout='{"type":"result","result":"ok"}')

    monkeypatch.setattr(agent_runner, "_resolve_claude_cli", lambda: "/fake/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setitem(sys.modules, "enforcement_engine", None)

    # bare_mode not passed → defaults to None → auto-enables for safe caller
    agent_runner.run_automation_prompt(
        "hello",
        caller="deep-sleep/synthesize",
        backend="claude_code",
    )
    assert "--bare" in captured["cmd"]


def test_bare_mode_not_auto_enabled_for_unsafe_caller(monkeypatch):
    """catchup/morning uses mcp__nexo__* — auto-detection must NOT enable
    bare for it even when an API key is available."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-use")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed(stdout='{"type":"result","result":"ok"}')

    monkeypatch.setattr(agent_runner, "_resolve_claude_cli", lambda: "/fake/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setitem(sys.modules, "enforcement_engine", None)

    agent_runner.run_automation_prompt(
        "hello",
        caller="catchup/morning",
        backend="claude_code",
    )
    assert "--bare" not in captured["cmd"]
    assert "--dangerously-skip-permissions" in captured["cmd"]


def test_bare_mode_falls_back_silently_without_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        agent_runner,
        "_ANTHROPIC_API_KEY_SEARCH_PATHS",
        (tmp_path / "missing.txt",),
    )
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed(stdout='{"type":"result","result":"ok"}')

    monkeypatch.setattr(agent_runner, "_resolve_claude_cli", lambda: "/fake/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setitem(sys.modules, "enforcement_engine", None)

    agent_runner.run_automation_prompt(
        "hello",
        caller="deep-sleep/extract",
        backend="claude_code",
        bare_mode=True,
    )

    # No key → fall back to non-bare path. No exception raised.
    assert "--bare" not in captured["cmd"]
    assert "--dangerously-skip-permissions" in captured["cmd"]


def test_bare_mode_false_overrides_safe_caller(monkeypatch):
    """Explicit bare_mode=False must disable bare even for a safe caller.
    Useful when the caller needs to run in a constrained env where API
    keys should not be materialised into env vars."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-available")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed(stdout='{"type":"result","result":"ok"}')

    monkeypatch.setattr(agent_runner, "_resolve_claude_cli", lambda: "/fake/claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setitem(sys.modules, "enforcement_engine", None)

    agent_runner.run_automation_prompt(
        "hello",
        caller="deep-sleep/extract",
        backend="claude_code",
        bare_mode=False,
    )
    assert "--bare" not in captured["cmd"]
    assert "--dangerously-skip-permissions" in captured["cmd"]
