#!/usr/bin/env python3
from __future__ import annotations

"""Small CLI wrapper around the schedule-configured automation backend."""

import argparse
import os
import sys
from pathlib import Path


_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_RUNTIME_ROOT = _SCRIPT_DIR.parent
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(_DEFAULT_RUNTIME_ROOT)))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

from agent_runner import AutomationBackendUnavailableError, run_automation_prompt


def _read_text(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).expanduser().read_text()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a prompt through the configured NEXO automation backend.")
    parser.add_argument("--prompt", default="", help="Prompt text")
    parser.add_argument("--prompt-file", default="", help="Read prompt text from a file")
    parser.add_argument("--cwd", default="", help="Working directory for the backend")
    parser.add_argument("--model", default="", help="Backend model hint")
    parser.add_argument("--reasoning-effort", default="", help="Backend reasoning effort/profile")
    parser.add_argument("--timeout", type=int, default=21600, help="Timeout in seconds")
    parser.add_argument("--output-format", default="text", help="Requested output format")
    parser.add_argument("--allowed-tools", default="", help="Claude-style allowed tools contract")
    parser.add_argument("--append-system-prompt", default="", help="Extra system prompt text")
    parser.add_argument("--append-system-prompt-file", default="", help="Read extra system prompt from a file")
    args = parser.parse_args(argv)

    prompt = args.prompt or _read_text(args.prompt_file)
    if not prompt:
        prompt = sys.stdin.read()
    if not prompt.strip():
        print("No prompt provided.", file=sys.stderr)
        return 1

    append_system_prompt = args.append_system_prompt or _read_text(args.append_system_prompt_file)

    try:
        result = run_automation_prompt(
            prompt,
            cwd=args.cwd or None,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            timeout=args.timeout,
            output_format=args.output_format,
            append_system_prompt=append_system_prompt,
            allowed_tools=args.allowed_tools,
        )
    except AutomationBackendUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
