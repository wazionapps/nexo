"""NEXO Helper — vendorable utility for personal scripts.

Provides stable access to NEXO MCP tools via the CLI.
Copy this file next to your script or keep it in NEXO_HOME/templates/.

This module does NOT import any NEXO internals (db, server, cognitive).
All communication goes through the stable `nexo scripts call` CLI.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _detect_nexo_home() -> Path:
    env_home = os.environ.get("NEXO_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser()

    helper_path = Path(__file__).resolve()
    inferred_home = helper_path.parent.parent
    if (
        helper_path.parent.name == "templates"
        and (inferred_home / "scripts").is_dir()
        and (inferred_home / "config").is_dir()
    ):
        return inferred_home

    claude_home = Path.home() / "claude"
    if claude_home.is_dir():
        return claude_home

    return Path.home() / ".nexo"


NEXO_HOME = _detect_nexo_home()
DEFAULT_ALLOWED_TOOLS = "Read,Write,Edit,Glob,Grep,Bash,mcp__nexo__*"
DEFAULT_NEXO_TIMEOUT_SECONDS = max(15, int(os.environ.get("NEXO_HELPER_TIMEOUT", "90")))


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


def _resolve_nexo_cli() -> str:
    candidates = []

    env_cli = os.environ.get("NEXO_BIN", "").strip()
    if env_cli:
        candidates.append(Path(env_cli).expanduser())

    candidates.extend(
        [
            NEXO_HOME / "bin" / "nexo",
            Path.home() / ".local" / "bin" / "nexo",
            Path.home() / "bin" / "nexo",
        ]
    )

    for candidate in candidates:
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue

    return shutil.which("nexo") or "nexo"


def run_nexo(args: list[str]) -> str:
    """Run a nexo CLI command and return stdout.

    Raises RuntimeError on non-zero exit.
    """
    env = os.environ.copy()
    env.setdefault("NEXO_HOME", str(NEXO_HOME))
    result = subprocess.run(
        [_resolve_nexo_cli(), *args],
        capture_output=True,
        text=True,
        timeout=DEFAULT_NEXO_TIMEOUT_SECONDS,
        env=env,
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


def _extract_json_object(text: str) -> dict:
    raw = str(text or "").strip()
    if not raw:
        raise RuntimeError("Automation backend returned empty output.")

    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 2:
            end = len(lines)
            for i in range(len(lines) - 1, 0, -1):
                if lines[i].strip() == "```":
                    end = i
                    break
            raw = "\n".join(lines[1:end]).strip()

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = raw.find("{")
    if start < 0:
        raise RuntimeError("Automation backend did not return a JSON object.")

    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = raw[start : idx + 1]
                try:
                    parsed = json.loads(candidate)
                except Exception as exc:
                    raise RuntimeError(f"Automation backend returned invalid JSON object: {exc}") from exc
                if isinstance(parsed, dict):
                    return parsed
                break

    raise RuntimeError("Automation backend did not return a parseable JSON object.")


def run_automation_text(
    prompt: str,
    *,
    model: str = "",
    reasoning_effort: str = "",
    cwd: str = "",
    timeout: int | None = None,
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
        timeout=(int(timeout) if timeout else None),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"automation backend exited {result.returncode}")
    return result.stdout


def run_automation_json(
    prompt: str,
    *,
    model: str = "",
    reasoning_effort: str = "",
    cwd: str = "",
    timeout: int | None = None,
    allowed_tools: str = DEFAULT_ALLOWED_TOOLS,
    append_system_prompt: str = "",
    include_bootstrap: bool = True,
) -> dict:
    """Run the configured backend and return a parsed JSON object."""
    runner = NEXO_HOME / "scripts" / "nexo-agent-run.py"
    if not runner.exists():
        raise RuntimeError(f"Automation runner not found: {runner}")

    cmd = [sys.executable, str(runner), "--prompt", prompt, "--output-format", "json"]
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
        timeout=(int(timeout) if timeout else None),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"automation backend exited {result.returncode}")
    return _extract_json_object(result.stdout)
