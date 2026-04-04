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
