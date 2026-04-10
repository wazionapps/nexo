#!/usr/bin/env python3
"""Run the curated v4.5 release smoke matrix and persist a JSON summary."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "release-contracts" / "smoke" / "v4.5.0.json"

SMOKE_GROUPS = [
    {
        "id": "protocol_flow",
        "description": "startup, protocol contract, and high-stakes close path stay coherent",
        "targets": [
            "tests/test_startup_preflight.py",
            "tests/test_protocol.py",
        ],
    },
    {
        "id": "outcome_loop",
        "description": "decision/followup/protocol outcomes close with met or missed evidence",
        "targets": [
            "tests/test_outcomes.py",
        ],
    },
    {
        "id": "priority_loop",
        "description": "impact scoring persists and reorders real followup queues",
        "targets": [
            "tests/test_impact_scoring.py",
        ],
    },
    {
        "id": "decision_loop",
        "description": "cortex evaluates, persists, and allows override on high-stakes actions",
        "targets": [
            "tests/test_cortex_decisions.py",
        ],
    },
    {
        "id": "drive_semantics",
        "description": "drive detects anomaly/pattern/gap/opportunity semantically across phrasing variants",
        "targets": [
            "tests/test_drive.py",
        ],
    },
    {
        "id": "public_proof",
        "description": "scorecard/compare generation remains readable and inspectable",
        "targets": [
            "tests/test_build_public_scorecard.py",
        ],
    },
    {
        "id": "dashboard_state",
        "description": "dashboard exposes sane protocol and operations state",
        "targets": [
            "tests/test_dashboard_app.py",
        ],
    },
    {
        "id": "followup_history",
        "description": "followups and reminders keep history-aware create/read/delete/restore flows coherent",
        "targets": [
            "tests/test_reminders_history.py",
            "tests/test_followup_hygiene.py",
        ],
    },
    {
        "id": "deep_sleep",
        "description": "deep sleep apply still produces the expected summaries and rollups",
        "targets": [
            "tests/test_deep_sleep_apply.py",
        ],
    },
    {
        "id": "cron_integrity",
        "description": "cron sync and recovery keep managed schedules healthy without drift",
        "targets": [
            "tests/test_cron_sync.py",
            "tests/test_cron_recovery.py",
        ],
    },
    {
        "id": "runtime_cli",
        "description": "runtime CLI paths and scripts call flow still behave under isolated environments",
        "targets": [
            "tests/test_cli_scripts.py",
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
        "release_line": "v4.5",
        "version": _package_version(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok": all(group["ok"] for group in groups),
        "groups": groups,
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if payload["ok"]:
        print(f"[v4.5-smoke] OK -> {output_path}")
        return 0

    failed = [group["id"] for group in groups if not group["ok"]]
    print(f"[v4.5-smoke] FAILED groups: {', '.join(failed)}", file=sys.stderr)
    print(f"[v4.5-smoke] Summary: {output_path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
