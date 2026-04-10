#!/usr/bin/env python3
"""Run the curated v5.0 release smoke matrix and persist a JSON summary."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "release-contracts" / "smoke" / "v5.0.0.json"

SMOKE_GROUPS = [
    {
        "id": "goal_engine",
        "description": "goal profiles exist, resolve coherently, and show up in cortex traces",
        "targets": [
            "tests/test_goal_engine.py",
        ],
    },
    {
        "id": "decision_cortex_v2",
        "description": "decision cortex ranks alternatives with outcomes, goals, and structured signals",
        "targets": [
            "tests/test_cortex_decisions.py",
            "tests/test_protocol.py",
        ],
    },
    {
        "id": "structured_learning",
        "description": "repeated outcome patterns become learnings and can influence later decisions",
        "targets": [
            "tests/test_outcomes.py",
            "tests/test_cortex_decisions.py",
        ],
    },
    {
        "id": "skill_evolution",
        "description": "skills can be seeded, promoted, ranked, or retired from outcome-backed evidence",
        "targets": [
            "tests/test_skills_v2.py",
        ],
    },
    {
        "id": "benchmark_pack",
        "description": "runtime benchmark pack and public scorecard builders stay reproducible",
        "targets": [
            "tests/test_build_runtime_benchmark_pack.py",
            "tests/test_build_public_scorecard.py",
        ],
    },
    {
        "id": "runtime_audit",
        "description": "protocol debt maintenance, doctor scoring, and update/cron integrity remain healthy",
        "targets": [
            "tests/test_protocol.py",
            "tests/test_doctor.py",
            "tests/test_cron_sync.py",
        ],
    },
]


def _package_version() -> str:
    payload = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    return str(payload["version"])


def _run_group(group: dict[str, object]) -> dict[str, object]:
    cmd = [sys.executable, "-m", "pytest", "-q", *group["targets"]]
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
        "targets": list(group["targets"]),
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
        "release_line": "v5.0",
        "version": _package_version(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok": all(group["ok"] for group in groups),
        "groups": groups,
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if payload["ok"]:
        print(f"[v5.0-smoke] OK -> {output_path}")
        return 0

    failed = [group["id"] for group in groups if not group["ok"]]
    print(f"[v5.0-smoke] FAILED groups: {', '.join(failed)}", file=sys.stderr)
    print(f"[v5.0-smoke] Summary: {output_path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
