"""Helpers for Windows/WSL runtime diagnostics."""
from __future__ import annotations

import os
import platform
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


def is_windows_mount_path(candidate: Path) -> bool:
    normalized = str(candidate or "").replace("\\", "/").lower()
    return normalized.startswith("/mnt/")


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
        "wsl_distro": str(os.environ.get("WSL_DISTRO_NAME", "")).strip(),
        "wsl_interop": bool(str(os.environ.get("WSL_INTEROP", "")).strip()),
        "nexo_home_on_windows_mount": on_windows_mount,
        "warnings": warnings,
    }
