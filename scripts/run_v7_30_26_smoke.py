#!/usr/bin/env python3
"""Run the curated v7.30.26 release smoke matrix and persist JSON."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "release-contracts" / "smoke" / "v7.30.26.json"

SMOKE_GROUPS = [
    {
        "id": "f06_core_script_update_propagation",
        "description": "Runtime update copies packaged core scripts through the F0.6 scripts shim to the active LaunchAgent path",
        "targets": [
            "tests/test_startup_preflight.py::test_copy_runtime_from_source_updates_core_script_through_f06_shim",
            "tests/test_startup_preflight.py::test_copy_runtime_from_source_preserves_personal_script_collision",
            "tests/test_cli_scripts.py::TestRuntimeUpdate::test_update_uses_recorded_source_repo",
            "tests/test_cli_scripts.py::TestRuntimeUpdate::test_update_reports_personal_schedule_self_heal",
        ],
    },
    {
        "id": "maintenance_diagnostics_self_heal",
        "description": "Runtime maintenance diagnostics stay quiet when the installation is healthy and watchdog observes live work without warning",
        "targets": [
            "tests/test_doctor.py::TestRuntimeChecks::test_launchagent_expectations_preserve_declared_weekly_schedule",
            "tests/test_doctor.py::TestRuntimeChecks::test_launchagent_expectations_support_multiple_weekdays",
            "tests/test_doctor.py::TestRuntimeChecks::test_release_trace_hygiene_flags_stale_audit_artifacts",
            "tests/test_doctor.py::TestRuntimeChecks::test_release_trace_hygiene_is_filtered_from_installation_live",
            "tests/test_doctor.py::TestRuntimeChecks::test_automation_telemetry_tolerates_high_coverage_sparse_usage_gaps",
            "tests/test_doctor.py::TestRuntimeChecks::test_automation_telemetry_still_warns_on_material_usage_gaps",
            "tests/test_doctor.py::TestRuntimeChecks::test_local_index_hygiene_retries_transient_db_lock",
            "tests/test_doctor.py::TestRuntimeChecks::test_local_index_hygiene_quick_truncation_without_residue_is_info",
            "tests/test_shell_runtime_path_contract.py::test_watchdog_uses_runtime_paths_and_personal_config",
            "tests/test_shell_runtime_path_contract.py::test_watchdog_keeps_alive_in_flight_work_observational",
            "tests/test_watchdog_in_flight.py::test_watchdog_treats_fresh_in_flight_row_as_healthy",
            "tests/test_watchdog_in_flight.py::test_watchdog_observes_long_in_flight_with_alive_process",
        ],
    },
    {
        "id": "product_surface_alignment_contract",
        "description": "Product Knowledge catalog is aligned with managed backend route families",
        "args": ["scripts/verify_product_surface_alignment.py", "--require-backend"],
    },
    {
        "id": "product_knowledge_catalog",
        "description": "Structured Product Knowledge validates catalog, system entries, and surface alignment",
        "args": ["scripts/verify_product_kb.py"],
    },
    {
        "id": "product_knowledge_tests",
        "description": "Product Knowledge search, answers, handlers, and surface drift tests stay coherent",
        "targets": [
            "tests/test_product_knowledge.py",
            "tests/test_product_surface_alignment.py",
        ],
    },
    {
        "id": "managed_mcp_client_sync_release_gate",
        "description": "Client sync writes managed MCP defaults only after reconcile apply reports required providers healthy",
        "targets": [
            "tests/test_managed_mcp.py::test_client_sync_skips_managed_defaults_without_healthy_providers",
            "tests/test_managed_mcp.py::test_client_sync_writes_managed_defaults_after_reconcile_apply_ok",
            "tests/test_managed_mcp.py::test_client_sync_removes_nexo_owned_stale_entries_when_staging_fails",
            "tests/test_managed_mcp.py::test_codex_sync_removes_nexo_owned_toml_entries_when_staging_fails",
            "tests/test_managed_mcp.py::test_codex_sync_preserves_user_owned_same_name_when_staging_fails",
            "tests/test_managed_mcp_release_gate.py",
        ],
    },
    {
        "id": "managed_mcp_runtime_package_contract",
        "description": "Packaged runtime copies managed_mcp package and managed MCP runner",
        "targets": [
            "tests/test_managed_mcp.py::test_managed_mcp_runtime_copy_includes_package_and_runner",
        ],
    },
    {
        "id": "managed_mcp_latest_lock",
        "description": "Managed MCP provider locks are pinned to npm latest before release",
        "args": ["scripts/verify_managed_mcp_lock.py"],
    },
    {
        "id": "tool_map_contract",
        "description": "Server tool map includes managed MCP, closure-plane, product and opportunity tools",
        "args": ["scripts/verify_tool_map.py"],
    },
    {
        "id": "release_surface_contract",
        "description": "Release-facing public surfaces are synchronized for v7.30.26",
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

    raise SystemExit(f"[v7.30.26-smoke] group {group.get('id')!r} has no command")


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
        print(f"[v7.30.26-smoke] OK -> {output_path}")
        return 0

    failed = [str(group["id"]) for group in groups if not group["ok"]]
    print(f"[v7.30.26-smoke] FAILED groups: {', '.join(failed)}", file=sys.stderr)
    print(f"[v7.30.26-smoke] Summary: {output_path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
