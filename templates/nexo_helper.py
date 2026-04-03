"""NEXO Helper — vendorable utility for personal scripts.

Provides stable access to NEXO MCP tools via the CLI.
Copy this file next to your script or keep it in NEXO_HOME/templates/.

This module does NOT import any NEXO internals (db, server, cognitive).
All communication goes through the stable `nexo scripts call` CLI.
"""
from __future__ import annotations

import json
import subprocess


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
