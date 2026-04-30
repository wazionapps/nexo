"""Helpers for Windows/WSL runtime diagnostics."""
from __future__ import annotations

import csv
import io
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any


def running_inside_wsl(*, system: str | None = None, release: str | None = None) -> bool:
    resolved_system = str(system or platform.system() or "").strip()
    resolved_release = str(release or platform.release() or "").strip().lower()
    if resolved_system != "Linux":
        return False
    if "microsoft" in resolved_release:
        return True
    if str(os.environ.get("WSL_DISTRO_NAME", "")).strip():
        return True
    if str(os.environ.get("WSL_INTEROP", "")).strip():
        return True
    return False


def running_from_windows_host() -> bool:
    value = str(os.environ.get("NEXO_WINDOWS_HOST", "")).strip().lower()
    return value not in ("", "0", "false", "no", "off")


def bridge_mode() -> str:
    value = str(os.environ.get("NEXO_WINDOWS_BRIDGE", "")).strip()
    return "wsl-exec" if value else ""


def is_windows_mount_path(candidate: Path) -> bool:
    normalized = str(candidate or "").replace("\\", "/").lower()
    return normalized.startswith("/mnt/")


def _default_windows_runner(args: list[str], *, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def resolve_windows_host_binary(
    command: str,
    *,
    which_func=shutil.which,
) -> str:
    direct = str(which_func(command) or "").strip()
    if direct:
        return direct
    fallbacks = {
        "powershell.exe": [
            "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            "/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        ],
        "schtasks.exe": [
            "/mnt/c/Windows/System32/schtasks.exe",
            "/c/Windows/System32/schtasks.exe",
        ],
    }
    for candidate in fallbacks.get(command.lower(), ()):
        if Path(candidate).exists():
            return candidate
    return ""


def windows_host_interop_available(*, which_func=shutil.which) -> bool:
    return bool(
        resolve_windows_host_binary("powershell.exe", which_func=which_func)
        or resolve_windows_host_binary("schtasks.exe", which_func=which_func)
    )


def query_windows_host_special_folders(
    *,
    runner=_default_windows_runner,
    which_func=shutil.which,
) -> dict[str, Any]:
    powershell = resolve_windows_host_binary("powershell.exe", which_func=which_func)
    if not powershell:
        return {
            "available": False,
            "error": "powershell_missing",
            "folders": {},
        }

    script = (
        "$obj = [ordered]@{"
        "LocalApplicationData=[Environment]::GetFolderPath('LocalApplicationData');"
        "ApplicationData=[Environment]::GetFolderPath('ApplicationData');"
        "Programs=[Environment]::GetFolderPath('Programs')"
        "};"
        "$obj | ConvertTo-Json -Compress"
    )
    try:
        result = runner([powershell, "-NoProfile", "-Command", script], timeout=20)
    except Exception as exc:
        return {
            "available": False,
            "error": str(exc),
            "folders": {},
        }
    raw = str(result.stdout or "").strip()
    if result.returncode != 0 or not raw:
        return {
            "available": False,
            "error": str(result.stderr or result.stdout or "windows_special_folders_failed").strip(),
            "folders": {},
        }
    try:
        payload = json.loads(raw)
    except Exception as exc:
        return {
            "available": False,
            "error": f"invalid_json: {exc}",
            "folders": {},
        }
    if not isinstance(payload, dict):
        return {
            "available": False,
            "error": "invalid_payload",
            "folders": {},
        }
    folders = {str(key): str(value or "").strip() for key, value in payload.items()}
    return {
        "available": True,
        "folders": folders,
    }


def query_windows_host_tasks(
    *,
    runner=_default_windows_runner,
    which_func=shutil.which,
) -> dict[str, Any]:
    schtasks = resolve_windows_host_binary("schtasks.exe", which_func=which_func)
    if not schtasks:
        return {
            "available": False,
            "error": "schtasks_missing",
            "tasks": [],
        }

    try:
        result = runner([schtasks, "/Query", "/FO", "CSV", "/NH"], timeout=30)
    except Exception as exc:
        return {
            "available": False,
            "error": str(exc),
            "tasks": [],
        }

    if result.returncode != 0:
        return {
            "available": False,
            "error": str(result.stderr or result.stdout or "schtasks_query_failed").strip(),
            "tasks": [],
        }

    tasks: list[str] = []
    try:
        reader = csv.reader(io.StringIO(str(result.stdout or "")))
        for row in reader:
            if not row:
                continue
            name = str(row[0] or "").strip()
            if name and "nexo" in name.lower():
                tasks.append(name)
    except Exception as exc:
        return {
            "available": False,
            "error": f"invalid_csv: {exc}",
            "tasks": [],
        }

    return {
        "available": True,
        "tasks": sorted(set(tasks)),
    }


def build_windows_host_cleanup_plan(
    *,
    delete_data: bool = False,
    runner=_default_windows_runner,
    which_func=shutil.which,
) -> dict[str, Any]:
    folders_payload = query_windows_host_special_folders(runner=runner, which_func=which_func)
    tasks_payload = query_windows_host_tasks(runner=runner, which_func=which_func)
    folders = folders_payload.get("folders", {}) if folders_payload.get("available") else {}
    local = str(folders.get("LocalApplicationData", "")).strip()
    roaming = str(folders.get("ApplicationData", "")).strip()
    programs = str(folders.get("Programs", "")).strip()

    runtime_paths = [
        path for path in (
            f"{local}\\Programs\\NEXO Desktop" if local else "",
            f"{local}\\Programs\\NEXO Desktop Support" if local else "",
        ) if path
    ]
    data_paths = [
        path for path in (
            f"{roaming}\\NEXO Desktop" if roaming else "",
            f"{roaming}\\NEXO Desktop Support" if roaming else "",
            f"{local}\\NEXO Desktop" if local else "",
            f"{local}\\NEXO Desktop Support" if local else "",
        ) if path
    ]
    shortcut_paths = [
        path for path in (
            f"{programs}\\NEXO Desktop.lnk" if programs else "",
            f"{programs}\\NEXO Desktop Support.lnk" if programs else "",
        ) if path
    ]
    return {
        "available": bool(folders_payload.get("available") or tasks_payload.get("available")),
        "folders": folders,
        "runtime_paths": runtime_paths,
        "data_paths": data_paths if delete_data else [],
        "shortcut_paths": shortcut_paths,
        "tasks": list(tasks_payload.get("tasks", [])),
        "errors": [
            value for value in (
                folders_payload.get("error"),
                tasks_payload.get("error"),
            ) if value
        ],
    }


def cleanup_windows_host_artifacts(
    *,
    delete_data: bool = False,
    dry_run: bool = False,
    runner=_default_windows_runner,
    which_func=shutil.which,
) -> dict[str, Any]:
    powershell = resolve_windows_host_binary("powershell.exe", which_func=which_func)
    schtasks = resolve_windows_host_binary("schtasks.exe", which_func=which_func)
    plan = build_windows_host_cleanup_plan(
        delete_data=delete_data,
        runner=runner,
        which_func=which_func,
    )
    actions: list[dict[str, str]] = []
    errors = list(plan.get("errors", []))

    for path in plan["runtime_paths"]:
        actions.append({"category": "remove-win-host-app", "detail": Path(path).name, "path": path})
    for path in plan["shortcut_paths"]:
        actions.append({"category": "remove-win-host-shortcut", "detail": Path(path).name, "path": path})
    for path in plan["data_paths"]:
        actions.append({"category": "remove-win-host-data", "detail": Path(path).name, "path": path})
    for task_name in plan["tasks"]:
        actions.append({"category": "remove-win-host-task", "detail": task_name, "path": task_name})

    if dry_run or not plan["available"]:
        return {"available": plan["available"], "actions": actions, "errors": errors}

    if powershell:
        remove_targets = plan["runtime_paths"] + plan["shortcut_paths"] + plan["data_paths"]
        if remove_targets:
            quoted = ", ".join(json.dumps(target) for target in remove_targets)
            script = (
                f"$targets = @({quoted}); "
                "foreach ($target in $targets) { "
                "if (Test-Path -LiteralPath $target) { "
                "Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue "
                "} }"
            )
            try:
                result = runner([powershell, "-NoProfile", "-Command", script], timeout=60)
                if result.returncode != 0:
                    errors.append(str(result.stderr or result.stdout or "windows_remove_failed").strip())
            except Exception as exc:
                errors.append(str(exc))

    if schtasks:
        for task_name in plan["tasks"]:
            try:
                result = runner([schtasks, "/Delete", "/TN", task_name, "/F"], timeout=20)
                if result.returncode != 0:
                    errors.append(str(result.stderr or result.stdout or f"task_delete_failed:{task_name}").strip())
            except Exception as exc:
                errors.append(f"{task_name}: {exc}")

    return {"available": plan["available"], "actions": actions, "errors": errors}


def windows_runtime_status(nexo_home: Path, *, system: str | None = None, release: str | None = None) -> dict[str, Any]:
    resolved_system = str(system or platform.system() or "").strip()
    resolved_release = str(release or platform.release() or "").strip()
    inside_wsl = running_inside_wsl(system=resolved_system, release=resolved_release)
    on_windows_mount = is_windows_mount_path(nexo_home)
    warnings: list[dict[str, str]] = []

    if resolved_system == "Windows":
        warnings.append(
            {
                "code": "brain_requires_wsl",
                "message": "NEXO Brain on Windows is supported via WSL, not native win32 mode.",
            }
        )
    if on_windows_mount:
        warnings.append(
            {
                "code": "nexo_home_on_windows_mount",
                "message": "NEXO_HOME is inside /mnt/*; keep the canonical Brain runtime inside the WSL filesystem.",
            }
        )

    return {
        "supported_brain_mode": "wsl",
        "inside_wsl": inside_wsl,
        "windows_host_bridge": running_from_windows_host(),
        "bridge_mode": bridge_mode(),
        "windows_host_interop": windows_host_interop_available(),
        "wsl_distro": str(os.environ.get("WSL_DISTRO_NAME", "")).strip(),
        "wsl_interop": bool(str(os.environ.get("WSL_INTEROP", "")).strip()),
        "nexo_home_on_windows_mount": on_windows_mount,
        "warnings": warnings,
    }
