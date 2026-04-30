"""Generic runtime snapshot for support and diagnostics.

Open-source boundary:
- no billing
- no customer workflow
- no managed support policy
- only local runtime/install state
"""
from __future__ import annotations

import json
import os
import platform
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import paths
from doctor.formatters import format_report
from doctor.orchestrator import run_doctor
from health_check import collect as collect_health
from windows_runtime import query_windows_host_tasks, running_inside_wsl, windows_runtime_status


def _nexo_home() -> Path:
    return Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))


def _read_version() -> str:
    candidates = [
        _nexo_home() / "version.json",
        paths.core_dir() / "package.json",
        Path(__file__).resolve().parents[1] / "package.json",
    ]
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
            payload = json.loads(candidate.read_text())
            version = str(payload.get("version") or "").strip()
            if version:
                return version
        except Exception:
            continue
    return "unknown"


def _path_status() -> dict[str, dict[str, Any]]:
    mapping = {
        "home": _nexo_home(),
        "runtime": paths.runtime_dir(),
        "core": paths.core_dir(),
        "data": paths.data_dir(),
        "logs": paths.logs_dir(),
        "operations": paths.operations_dir(),
        "config": paths.config_dir(),
    }
    result: dict[str, dict[str, Any]] = {}
    for key, path in mapping.items():
        try:
            exists = path.exists()
        except Exception:
            exists = False
        result[key] = {
            "path": str(path),
            "exists": exists,
            "is_dir": path.is_dir() if exists else False,
        }
    return result


def _recent_logs(lines: int = 80) -> dict[str, Any]:
    lines = max(1, int(lines))
    home = _nexo_home()
    events_file = home / "runtime" / "events.ndjson"
    ops_dir = paths.operations_dir()
    event_tail: list[dict[str, Any]] = []
    operation_tail: list[dict[str, Any]] = []

    if events_file.is_file():
        try:
            raw = events_file.read_text(errors="ignore").splitlines()[-lines:]
            for line in raw:
                try:
                    event_tail.append(json.loads(line))
                except Exception:
                    continue
        except Exception as exc:
            event_tail.append({"error": str(exc)})

    if ops_dir.is_dir():
        try:
            files = sorted(
                ops_dir.glob("*.log"),
                key=lambda item: item.stat().st_mtime if item.exists() else 0,
                reverse=True,
            )[:5]
            for log_file in files:
                try:
                    for line in log_file.read_text(errors="ignore").splitlines()[-lines:]:
                        operation_tail.append({"file": log_file.name, "line": line})
                except Exception as exc:
                    operation_tail.append({"file": log_file.name, "error": str(exc)})
        except Exception as exc:
            operation_tail.append({"error": str(exc)})

    return {
        "events": event_tail[-lines:],
        "operations": operation_tail[-lines:],
    }


def collect_snapshot(*, log_lines: int = 80, include_doctor: bool = False) -> dict[str, Any]:
    system = platform.system()
    release = platform.release()
    payload: dict[str, Any] = {
        "generated_at": time.time(),
        "version": _read_version(),
        "platform": {
            "system": system,
            "release": release,
            "machine": platform.machine(),
            "python": platform.python_version(),
            "is_wsl": running_inside_wsl(system=system, release=release),
        },
        "windows_runtime": windows_runtime_status(_nexo_home(), system=system, release=release),
        "windows_host": {
            "tasks": query_windows_host_tasks(),
        },
        "paths": _path_status(),
        "health": collect_health(),
        "logs": _recent_logs(log_lines),
    }

    if include_doctor:
        report = run_doctor(tier="runtime", fix=False, plane="runtime_personal")
        payload["doctor"] = asdict(report)
        payload["doctor_text"] = format_report(report, fmt="text")

    return payload


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="NEXO generic support snapshot")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--include-doctor", action="store_true", help="Include runtime doctor report")
    parser.add_argument("--log-lines", type=int, default=80, help="How many recent log lines to include")
    args = parser.parse_args(argv)

    payload = collect_snapshot(log_lines=args.log_lines, include_doctor=args.include_doctor)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
