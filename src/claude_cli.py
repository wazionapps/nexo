from __future__ import annotations

"""Shared Claude CLI resolution helpers.

Desktop-managed installs must never silently escape to a global `claude`
binary from PATH. This helper centralises that contract so automation
surfaces can share the same resolution policy.
"""

import json
import os
import shutil
from pathlib import Path


def _user_home(user_home: Path | None = None) -> Path:
    if user_home is not None:
        return Path(user_home).expanduser()
    return Path(os.environ.get("HOME", str(Path.home()))).expanduser()


def _desktop_install_markers(home: Path, *, include_global_markers: bool) -> list[Path]:
    markers: list[Path] = [
        home / "Applications" / "NEXO Desktop.app",
        home / "Library" / "Application Support" / "NEXO Desktop",
        home / ".local" / "share" / "NEXO Desktop",
        home / ".config" / "NEXO Desktop",
    ]
    # Treat the global app bundle path as a stable install marker even when
    # tests run on non-macOS CI hosts. Explicit homes still suppress it.
    if include_global_markers:
        markers.insert(0, Path("/Applications/NEXO Desktop.app"))
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
        roaming = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
        markers.extend(
            [
                local / "Programs" / "NEXO Desktop",
                roaming / "NEXO Desktop",
            ]
        )
    return markers


def desktop_product_requested(user_home: Path | None = None) -> bool:
    if str(os.environ.get("NEXO_DESKTOP_MANAGED", "")).strip() == "1":
        return True
    explicit_home = user_home is not None
    home = _user_home(user_home)
    mode_paths = (
        home / ".nexo" / "personal" / "config" / "product-mode.json",
        home / ".nexo" / "config" / "product-mode.json",
    )
    for mode_path in mode_paths:
        try:
            payload = json.loads(mode_path.read_text())
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("desktop_managed") is True:
            return True
        if str(payload.get("product_mode") or "").strip().lower() == "desktop_closed_product":
            return True
    for marker in _desktop_install_markers(home, include_global_markers=not explicit_home):
        try:
            if marker.exists():
                return True
        except Exception:
            continue
    return False


def managed_claude_prefix(user_home: Path | None = None) -> Path:
    explicit = str(os.environ.get("NEXO_CLAUDE_PREFIX", "")).strip()
    if explicit:
        return Path(explicit).expanduser()
    return _user_home(user_home) / ".nexo" / "runtime" / "bootstrap" / "npm-global"


def _path_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.expanduser().resolve(strict=False).relative_to(parent.expanduser().resolve(strict=False))
        return True
    except Exception:
        return False


def managed_claude_binary(user_home: Path | None = None) -> str:
    home = _user_home(user_home)
    managed_prefix = managed_claude_prefix(home)
    persisted_paths = (
        home / ".nexo" / "config" / "claude-cli-path",
        home / ".nexo" / "personal" / "config" / "claude-cli-path",
    )
    candidates: list[Path] = []
    for persisted in persisted_paths:
        try:
            raw = persisted.read_text(encoding="utf-8").strip()
        except Exception:
            raw = ""
        if raw:
            candidates.append(Path(raw))
    env_path = str(os.environ.get("CLAUDE_BIN", "")).strip()
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(managed_prefix / "bin" / "claude")
    for candidate in candidates:
        try:
            if not candidate.exists():
                continue
        except Exception:
            continue
        if _path_within(candidate, managed_prefix):
            return str(candidate)
    return ""


def resolve_claude_cli(user_home: Path | None = None) -> str:
    home = _user_home(user_home)
    if desktop_product_requested(home):
        return managed_claude_binary(home)

    persisted_paths = (
        home / ".nexo" / "config" / "claude-cli-path",
        home / ".nexo" / "personal" / "config" / "claude-cli-path",
    )
    for persisted in persisted_paths:
        try:
            candidate = persisted.read_text(encoding="utf-8").strip()
        except Exception:
            candidate = ""
        if candidate and Path(candidate).exists():
            return candidate

    env_path = str(os.environ.get("CLAUDE_BIN", "")).strip()
    if env_path and Path(env_path).exists():
        return env_path

    discovered = shutil.which("claude")
    if discovered:
        return discovered

    for candidate in (
        home / ".local" / "bin" / "claude",
        home / ".npm-global" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
    ):
        if candidate.exists():
            return str(candidate)
    return ""
