#!/usr/bin/env python3
"""Run the curated v7.26 provider-runtime smoke matrix and persist JSON."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "release-contracts" / "smoke" / "v7.26.0.json"

SMOKE_GROUPS = [
    {
        "id": "provider_runtime_preferences",
        "description": "Provider runtime preferences map Anthropic to Claude Code and OpenAI to Codex",
        "targets": [
            "tests/test_provider_runtime.py",
            "tests/test_client_preferences.py::test_provider_runtime_maps_openai_to_codex_without_byok",
            "tests/test_client_preferences.py::test_apply_client_preferences_provider_selection_updates_backend",
            "tests/test_preferences_cli.py::test_provider_select_openai_updates_chat_and_automation_runtime",
        ],
    },
    {
        "id": "managed_codex_fail_closed",
        "description": "Desktop-managed Codex detection and install fail closed to managed runtime artifacts",
        "targets": [
            "tests/test_agent_runner.py::test_resolve_codex_cli_desktop_managed_does_not_fallback_to_global",
            "tests/test_agent_runner.py::test_resolve_codex_cli_desktop_managed_requires_vendor",
            "tests/test_client_preferences.py::test_detect_installed_clients_desktop_managed_requires_codex_vendor",
            "tests/test_client_sync.py::test_sync_codex_desktop_managed_ignores_global_codex_without_vendor",
            "tests/test_client_sync.py::test_ensure_codex_installed_desktop_managed_does_not_call_host_npm",
            "tests/test_desktop_managed_claude_deferral.py::test_desktop_managed_detection_uses_only_managed_client_binaries",
            "tests/test_desktop_managed_claude_deferral.py::test_desktop_managed_installers_never_call_host_npm_after_managed_failure",
        ],
    },
    {
        "id": "automation_session_provider_metadata",
        "description": "Codex automation and interactive sessions keep provider/client metadata",
        "targets": [
            "tests/test_agent_runner.py::test_codex_backend_records_caller_session_and_contract",
            "tests/test_agent_runner.py::test_run_automation_prompt_fails_closed_when_configured_backend_is_unavailable",
            "tests/test_session_claude_aliases.py",
        ],
    },
    {
        "id": "schema_cron_provider_metadata",
        "description": "Schema migrations and cron wrapper expose provider metadata",
        "targets": [
            "tests/test_migrations.py",
            "tests/test_cron_wrapper_contract.py::test_cron_wrapper_records_provider_runtime_metadata",
        ],
    },
    {
        "id": "packaged_codex_bundle",
        "description": "npm package stays publishable while repo/Desktop artifacts retain Codex native vendor tarballs",
        "targets": [
            "tests/test_desktop_managed_claude_deferral.py::test_npm_package_keeps_public_codex_bundle_publishable",
            "tests/test_desktop_managed_claude_deferral.py::test_repo_retains_codex_native_tarballs_for_desktop_bundle",
        ],
    },
    {
        "id": "release_surface_contract",
        "description": "Release-facing public surfaces and contract metadata stay aligned for v7.26.0",
        "args": [
            "scripts/verify_release_readiness.py",
            "--ci",
            "--contract",
            "release-contracts/v7.26.0.json",
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

    raise SystemExit(f"[v7.26-smoke] group {group.get('id')!r} has no command")


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
        "release_line": "v7.26",
        "version": _package_version(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok": all(group["ok"] for group in groups),
        "groups": groups,
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if payload["ok"]:
        print(f"[v7.26-smoke] OK -> {output_path}")
        return 0

    failed = [str(group["id"]) for group in groups if not group["ok"]]
    print(f"[v7.26-smoke] FAILED groups: {', '.join(failed)}", file=sys.stderr)
    print(f"[v7.26-smoke] Summary: {output_path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
