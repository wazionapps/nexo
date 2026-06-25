"""Contract tests for the v7.3.0 PreToolUse hook entry point.

The handler lives in ``src/hooks/pre_tool_use.py``. Without it wired as a
Claude Code PreToolUse hook, the Block K Guardian gates
(G3 destructive + G3 SSH + G4 guard_check) were unreachable in production.
This file locks the entrypoint contract:

- Non-tool-monitored payload → exit 0, no JSON response on stdout.
- Hard-mode block via ``process_pre_tool_event`` → emit
  ``permissionDecision: deny`` JSON with a structured reason AND exit 2
  with the reason on stderr (7.9.34 belt-and-suspenders: terminal
  Claude Code occasionally ignored the JSON deny channel mid-loop, so
  exit 2 / stderr is the documented secondary block channel).
- Shadow-mode block (``severity != error``) → exit 0, no deny JSON
  (the debt is recorded by the gate itself; the tool proceeds).
- Internal exception path stays fail-open (the tool pipeline must never
  crash on a hook bug).
- The unified manifest registers PreToolUse so Claude Code picks it up.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = REPO_ROOT / "src" / "hooks" / "pre_tool_use.py"
MANIFEST_PATH = REPO_ROOT / "src" / "hooks" / "manifest.json"


def _invoke_hook(payload: dict, *, env_extra: dict | None = None, timeout: int = 8) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def test_manifest_registers_pre_tool_use_as_critical():
    manifest = json.loads(MANIFEST_PATH.read_text())
    matches = [h for h in manifest["hooks"] if h["event"] == "PreToolUse"]
    assert len(matches) == 1, f"manifest must register exactly one PreToolUse hook, got {matches}"
    assert matches[0]["handler"].endswith("pre_tool_use.py")
    assert matches[0]["critical"] is True


def test_read_tool_is_not_blocked_and_exits_clean():
    payload = {
        "session_id": "test-session",
        "tool_name": "Read",
        "tool_input": {"file_path": "/etc/hosts"},
    }
    proc = _invoke_hook(payload)
    assert proc.returncode == 0
    # Read does not match any write/delete gate — no deny JSON should leak.
    assert "permissionDecision" not in (proc.stdout or "")


def test_bash_rm_rf_blocked_in_hard_mode_emits_deny_json():
    payload = {
        "session_id": "test-session-hard-rmrf",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /tmp/should-be-blocked-by-g3"},
    }
    proc = _invoke_hook(payload, env_extra={"NEXO_G3_ENFORCE_DESTRUCTIVE": "hard"})
    # 7.9.34: hard block now exits 2 (documented PreToolUse blocking
    # exit) in addition to emitting deny JSON. Both channels carry the
    # same reason so terminal Claude cannot silently ignore the gate.
    assert proc.returncode == 2, proc.stderr
    response = json.loads(proc.stdout.strip())
    hso = response.get("hookSpecificOutput") or {}
    assert hso.get("hookEventName") == "PreToolUse"
    assert hso.get("permissionDecision") == "deny"
    reason = hso.get("permissionDecisionReason") or ""
    assert "rm_rf" in reason
    assert "g3_destructive_blocked" in reason
    assert "severity=error" in reason
    # Belt-and-suspenders: stderr must carry the same reason the JSON
    # carries, so the model sees it through the exit-2 channel even if
    # the JSON branch is dropped on the way to the LLM.
    assert "rm_rf" in (proc.stderr or "")
    assert "g3_destructive_blocked" in (proc.stderr or "")


def test_bash_rm_rf_in_shadow_mode_does_not_deny_tool():
    payload = {
        "session_id": "test-session-shadow-rmrf",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /tmp/should-log-shadow-debt"},
    }
    proc = _invoke_hook(payload, env_extra={"NEXO_G3_ENFORCE_DESTRUCTIVE": "shadow"})
    assert proc.returncode == 0, proc.stderr
    # Shadow records a debt row but does NOT deny the tool.
    assert "permissionDecision" not in (proc.stdout or "")


def test_bash_rm_rf_with_gate_off_does_not_touch_tool():
    payload = {
        "session_id": "test-session-off-rmrf",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /tmp/guardian-off"},
    }
    proc = _invoke_hook(payload, env_extra={"NEXO_G3_ENFORCE_DESTRUCTIVE": "off"})
    assert proc.returncode == 0, proc.stderr
    assert "permissionDecision" not in (proc.stdout or "")


def test_thinking_block_400_payload_blocks_tool_and_instructs_clear():
    payload = {
        "session_id": "test-session-thinking-400",
        "tool_name": "Read",
        "error": {
            "message": (
                "Error 400 invalid_request_error: thinking or redacted_thinking "
                "blocks cannot be modified"
            )
        },
        "tool_input": {"file_path": "/etc/hosts"},
    }
    proc = _invoke_hook(payload)
    assert proc.returncode == 2, proc.stderr
    response = json.loads(proc.stdout.strip())
    hso = response.get("hookSpecificOutput") or {}
    assert hso.get("hookEventName") == "PreToolUse"
    assert hso.get("permissionDecision") == "deny"
    reason = hso.get("permissionDecisionReason") or ""
    assert "/clear" in reason
    assert "thinking" in reason


def test_ssh_remote_write_blocked_in_hard_mode():
    payload = {
        "session_id": "test-session-hard-ssh",
        "tool_name": "Bash",
        "tool_input": {"command": 'ssh remote-host "cat > /etc/hosts"'},
    }
    proc = _invoke_hook(payload, env_extra={"NEXO_G3_SSH_ENFORCE_REMOTE_WRITE": "hard"})
    # 7.9.34 hardening applies here too — exit 2 + stderr reason.
    assert proc.returncode == 2, proc.stderr
    response = json.loads(proc.stdout.strip())
    hso = response.get("hookSpecificOutput") or {}
    assert hso.get("permissionDecision") == "deny"
    reason = hso.get("permissionDecisionReason") or ""
    assert "g3_ssh_remote_write_blocked" in reason
    assert "ssh_remote_shell_write" in reason
    assert "g3_ssh_remote_write_blocked" in (proc.stderr or "")


def test_malformed_stdin_does_not_crash_pipeline():
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input="{ not valid json",
        capture_output=True,
        text=True,
        timeout=8,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")},
    )
    # Fail-open: a malformed payload must never block an unrelated tool call.
    assert proc.returncode == 0
    assert "permissionDecision" not in (proc.stdout or "")


def test_empty_stdin_returns_clean():
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input="",
        capture_output=True,
        text=True,
        timeout=8,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")},
    )
    assert proc.returncode == 0
    assert "permissionDecision" not in (proc.stdout or "")
