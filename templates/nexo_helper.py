"""NEXO Helper — vendorable utility for personal scripts.

Provides stable access to NEXO MCP tools via the CLI.
Copy this file next to your script or keep it in NEXO_HOME/templates/.

This module does NOT import any NEXO internals (db, server, cognitive).
All communication goes through the stable `nexo scripts call` CLI.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
DEFAULT_ALLOWED_TOOLS = "Read,Write,Edit,Glob,Grep,Bash,mcp__nexo__*"


def _load_schedule() -> dict:
    schedule_path = NEXO_HOME / "config" / "schedule.json"
    if not schedule_path.is_file():
        return {}
    try:
        return json.loads(schedule_path.read_text())
    except Exception:
        return {}


def _resolve_automation_backend() -> str:
    data = _load_schedule()
    return str(data.get("automation_backend", "claude_code") or "claude_code")


def _load_bootstrap_prompt() -> str:
    backend = _resolve_automation_backend()
    if backend == "codex":
        path = Path.home() / ".codex" / "AGENTS.md"
    else:
        path = Path.home() / ".claude" / "CLAUDE.md"
    if not path.is_file():
        return ""
    try:
        return path.read_text()
    except Exception:
        return ""


def run_nexo(args: list[str]) -> str:
    """Run a nexo CLI command and return stdout.

    Raises RuntimeError on non-zero exit.
    """
    result = subprocess.run(
        ["nexo", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"nexo exited {result.returncode}")
    return result.stdout


def call_tool(name: str, payload: dict | None = None) -> str:
    """Call a NEXO MCP tool by name. Returns raw text output."""
    args = ["scripts", "call", name, "--input", json.dumps(payload or {})]
    return run_nexo(args)


def call_tool_text(name: str, payload: dict | None = None) -> str:
    """Call a NEXO MCP tool and return text output."""
    return call_tool(name, payload)


def call_tool_json(name: str, payload: dict | None = None) -> dict:
    """Call a NEXO MCP tool and return parsed JSON output."""
    args = ["scripts", "call", name, "--input", json.dumps(payload or {}), "--json-output"]
    out = run_nexo(args)
    return json.loads(out)


def run_automation_text(
    prompt: str,
    *,
    model: str = "",
    reasoning_effort: str = "",
    cwd: str = "",
    allowed_tools: str = DEFAULT_ALLOWED_TOOLS,
    append_system_prompt: str = "",
    include_bootstrap: bool = True,
) -> str:
    """Run the configured NEXO automation backend and return text output.

    This avoids hardcoding provider CLIs such as `claude -p` inside personal
    scripts. The runtime routes the call through the selected backend and its
    configured model profile.
    """
    runner = NEXO_HOME / "scripts" / "nexo-agent-run.py"
    if not runner.exists():
        raise RuntimeError(f"Automation runner not found: {runner}")

    cmd = [sys.executable, str(runner), "--prompt", prompt, "--output-format", "text"]
    if model:
        cmd.extend(["--model", model])
    if reasoning_effort:
        cmd.extend(["--reasoning-effort", reasoning_effort])
    if cwd:
        cmd.extend(["--cwd", cwd])
    merged_system_prompt = []
    if include_bootstrap:
        bootstrap = _load_bootstrap_prompt()
        if bootstrap:
            merged_system_prompt.append(bootstrap)
    if append_system_prompt:
        merged_system_prompt.append(append_system_prompt)
    if merged_system_prompt:
        cmd.extend(["--append-system-prompt", "\n\n".join(merged_system_prompt)])
    if allowed_tools:
        cmd.extend(["--allowed-tools", allowed_tools])

    env = os.environ.copy()
    env.setdefault("NEXO_HOME", str(NEXO_HOME))
    env.setdefault("NEXO_CODE", env.get("NEXO_CODE", str(NEXO_HOME)))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"automation backend exited {result.returncode}")
    return result.stdout
