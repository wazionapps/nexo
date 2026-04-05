#!/usr/bin/env python3
"""Verify shared-brain client parity guardrails."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PARITY_TESTS = [
    "tests/test_client_sync.py",
    "tests/test_doctor.py",
    "tests/test_client_parity_audit.py",
    "tests/test_deep_sleep_collect.py",
    "tests/test_deep_sleep_apply.py",
    "tests/test_cognitive.py",
]

REQUIRED_DOC_SNIPPETS = {
    "README.md": [
        "docs/client-parity-checklist.md",
        "Managed via bootstrap + Codex config `initial_messages` + `mcp_servers.nexo`",
        "Managed MCP-only shared-brain metadata",
        "Runtime doctor parity audit",
    ],
    "CONTRIBUTING.md": [
        "docs/client-parity-checklist.md",
    ],
    "docs/client-parity-checklist.md": [
        "Codex managed config still persists `mcp_servers.nexo`",
        "Runtime doctor still audits recent Codex sessions for startup discipline",
        "Deep Sleep still reads both Claude Code and Codex transcript sources",
    ],
}


def _fail(message: str) -> None:
    print(f"[client-parity] FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def _check_docs() -> None:
    for relpath, snippets in REQUIRED_DOC_SNIPPETS.items():
        text = (ROOT / relpath).read_text()
        for snippet in snippets:
            if snippet not in text:
                _fail(f"missing required parity snippet in {relpath}: {snippet}")
    print("[client-parity] docs OK")


def _run_tests() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    cmd = [sys.executable, "-m", "pytest", "-q", *PARITY_TESTS]
    result = subprocess.run(cmd, cwd=ROOT, env=env)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    print("[client-parity] parity tests OK")


def main() -> int:
    _check_docs()
    _run_tests()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
