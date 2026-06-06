#!/usr/bin/env python3
"""Run the curated v7.30.21 release smoke matrix and persist JSON."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "release-contracts" / "smoke" / "v7.30.21.json"

SMOKE_GROUPS = [
    {
        "id": "managed_mcp_latest_lock",
        "description": "Managed MCP provider locks are pinned to npm latest before release",
        "args": ["scripts/verify_managed_mcp_lock.py"],
    },
    {
        "id": "managed_mcp_reconcile_contract",
        "description": "Managed MCP catalog, lock, runner metadata, and client merge ownership stay coherent",
        "targets": [
            "tests/test_managed_mcp.py",
            "tests/test_managed_mcp_release_gate.py",
        ],
    },
    {
        "id": "closure_plane_contract",
        "description": "Operational Closure Plane migration, backfill, evidence, and close behavior stay coherent",
        "targets": [
            "tests/test_closure_plane.py",
            "tests/test_migrations.py::test_init_db_creates_core_tables",
            "tests/test_migrations.py::test_migrations_idempotent",
        ],
    },
    {
        "id": "tool_map_contract",
        "description": "Server tool map includes managed MCP and closure-plane tools",
        "args": ["scripts/verify_tool_map.py"],
    },
    {
        "id": "release_surface_contract",
        "description": "Release-facing public surfaces are synchronized for v7.30.21",
        "args": [
            "scripts/verify_release_readiness.py",
            "--ci",
        ],
    },
]


def _package_version() -> str:
    payload = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    return str(payload["version"])


def _command_for_group(group: dict[str, object]) -> list[str]:
    targets = group.get("targets")
    if isinstance(targets, list) and targets:
        return [
            "env",
            "NEXO_NO_MODEL_DOWNLOAD=1",
            "NEXO_LOCAL_MODELS_NO_DOWNLOAD=1",
            sys.executable,
            "-m",
            "pytest",
            "-q",
            *[str(item) for item in targets],
        ]

    args = group.get("args")
    if isinstance(args, list) and args:
        return [sys.executable, *[str(item) for item in args]]

    raise SystemExit(f"[v7.30.21-smoke] group {group.get('id')!r} has no command")


def _run_group(group: dict[str, object]) -> dict[str, object]:
    cmd = _command_for_group(group)
    started = time.time()
    result = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    duration = round(time.time() - started, 2)
    return {
        "id": group["id"],
        "description": group["description"],
        "command": cmd,
        "targets": list(group.get("targets") or group.get("args") or []),
        "returncode": result.returncode,
        "ok": result.returncode == 0,
        "duration_seconds": duration,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Path to the JSON summary to write.",
    )
    args = parser.parse_args()

    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    groups = [_run_group(group) for group in SMOKE_GROUPS]
    payload = {
        "release_line": "v7.30",
        "version": _package_version(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok": all(group["ok"] for group in groups),
        "groups": groups,
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if payload["ok"]:
        print(f"[v7.30.21-smoke] OK -> {output_path}")
        return 0

    failed = [str(group["id"]) for group in groups if not group["ok"]]
    print(f"[v7.30.21-smoke] FAILED groups: {', '.join(failed)}", file=sys.stderr)
    print(f"[v7.30.21-smoke] Summary: {output_path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
