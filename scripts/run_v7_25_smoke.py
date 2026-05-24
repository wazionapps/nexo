#!/usr/bin/env python3
"""Run the curated v7.25 release smoke matrix and persist a JSON summary."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "release-contracts" / "smoke" / "v7.25.6.json"

SMOKE_GROUPS = [
    {
        "id": "local_index_legacy_sidecar_migration",
        "description": "Existing Local Memory sidecar DBs repair legacy v2 columns before source-dependent indexes",
        "targets": [
            "tests/test_local_context.py::test_local_context_repairs_legacy_sidecar_v2_columns_before_source_indexes",
        ],
    },
    {
        "id": "managed_python_core_crons",
        "description": "Core cron LaunchAgents prefer the NEXO-managed Python runtime",
        "targets": [
            "tests/test_cron_sync.py::test_build_plist_uses_managed_runtime_python",
        ],
    },
    {
        "id": "local_index_roots_v2",
        "description": "Local Memory roots v2 starts conservative, preserves email/user content, and supports explicit includes",
        "targets": [
            "tests/test_local_context.py::test_file_type_rules_allow_user_include_and_exclude_overrides",
            "tests/test_local_context.py::test_roots_seed_v2_migration_removes_legacy_disk_root_but_keeps_user_content",
            "tests/test_local_context.py::test_user_include_overrides_default_skipped_tree_under_core_root",
            "tests/test_local_context.py::test_windows_drive_roots_detect_nested_paths",
        ],
    },
    {
        "id": "local_index_privacy_hygiene",
        "description": "Default root scans avoid system/noisy trees while repair paths clean removed or unsafe state",
        "targets": [
            "tests/test_local_context.py::test_default_roots_include_local_email_sources_and_extract_messages",
            "tests/test_local_context.py::test_system_volume_scan_excludes_system_but_reads_shared_app_data",
            "tests/test_local_context.py::test_system_temporary_paths_are_skipped_from_root_scan",
            "tests/test_local_context.py::test_installer_volume_is_not_a_default_mounted_root",
            "tests/test_local_context.py::test_doctor_local_index_hygiene_repairs_removed_root_residue",
        ],
    },
    {
        "id": "managed_python_contract",
        "description": "Desktop-managed Brain installs reject non-Core-pinned Python ABI versions",
        "targets": [
            "tests/test_startup_preflight.py::test_desktop_managed_venv_only_accepts_core_pinned_python",
        ],
    },
    {
        "id": "release_surface_contract",
        "description": "Release-facing public surfaces and contract metadata stay aligned for v7.25.6",
        "args": [
            "scripts/verify_release_readiness.py",
            "--ci",
            "--contract",
            "release-contracts/v7.25.6.json",
            "--require-contract-complete",
        ],
    },
]


def _package_version() -> str:
    payload = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    return str(payload["version"])


def _command_for_group(group: dict[str, object]) -> list[str]:
    targets = group.get("targets")
    if isinstance(targets, list) and targets:
        return [sys.executable, "-m", "pytest", "-q", *[str(item) for item in targets]]

    args = group.get("args")
    if isinstance(args, list) and args:
        return [sys.executable, *[str(item) for item in args]]

    raise SystemExit(f"[v7.25-smoke] group {group.get('id')!r} has no command")


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
        "release_line": "v7.25",
        "version": _package_version(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok": all(group["ok"] for group in groups),
        "groups": groups,
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if payload["ok"]:
        print(f"[v7.25-smoke] OK -> {output_path}")
        return 0

    failed = [str(group["id"]) for group in groups if not group["ok"]]
    print(f"[v7.25-smoke] FAILED groups: {', '.join(failed)}", file=sys.stderr)
    print(f"[v7.25-smoke] Summary: {output_path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
