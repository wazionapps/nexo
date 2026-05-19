"""Extensible registry for post-disk-recovery app sync nudges."""
from __future__ import annotations

import os
import platform as platform_module
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Callable


Runner = Callable[[list[str]], dict]
Handler = Callable[["RecoveryContext"], "RecoveryResult"]


@dataclass
class RecoveryContext:
    platform: str
    processes: set[str] = field(default_factory=set)
    dry_run: bool = False
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    runner: Runner | None = None

    def run(self, command: list[str]) -> dict:
        if self.dry_run:
            return {"ok": True, "dry_run": True, "command": command}
        runner = self.runner or default_runner
        return runner(command)


@dataclass
class RecoveryResult:
    app: str
    touched: bool
    commands: list[list[str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    detail: str = ""


def default_runner(command: list[str]) -> dict:
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=20)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-1000:],
            "stderr": proc.stderr[-1000:],
            "command": command,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "command": command}


def default_processes(system: str | None = None) -> set[str]:
    system = (system or platform_module.system()).lower()
    command = ["tasklist"] if system == "windows" else ["ps", "-axo", "comm="]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=5)
    except Exception:
        return set()
    names: set[str] = set()
    for raw in proc.stdout.splitlines():
        name = _process_name_from_line(raw, windows=system == "windows")
        if not name:
            continue
        names.add(name)
    return names


def _process_name_from_line(raw: str, *, windows: bool) -> str:
    line = str(raw or "").strip()
    if not line:
        return ""
    if not windows:
        return line.strip('"').rsplit("/", 1)[-1].lower()
    lower = line.lower()
    if lower.startswith("image name") or lower.startswith("="):
        return ""
    if line.startswith('"') and "," in line:
        return line.split(",", 1)[0].strip().strip('"').lower()
    return line.split(None, 1)[0].strip().strip('"').lower()


class RecoveryRegistry:
    def __init__(self) -> None:
        self._handlers: list[tuple[str, set[str], Handler]] = []

    def register(self, name: str, platforms: set[str], handler: Handler) -> None:
        self._handlers.append((name, {p.lower() for p in platforms}, handler))

    def run(
        self,
        *,
        platform: str | None = None,
        processes: set[str] | None = None,
        dry_run: bool = False,
        runner: Runner | None = None,
        env: dict[str, str] | None = None,
    ) -> dict:
        system = (platform or platform_module.system()).lower()
        ctx = RecoveryContext(
            platform=system,
            processes=processes if processes is not None else default_processes(system),
            dry_run=dry_run,
            env=env or dict(os.environ),
            runner=runner,
        )
        results: list[RecoveryResult] = []
        for _name, platforms, handler in self._handlers:
            if "all" not in platforms and system not in platforms:
                continue
            results.append(handler(ctx))
        touched = [result.app for result in results if result.touched]
        return {
            "ok": not any(result.errors for result in results),
            "platform": system,
            "dry_run": dry_run,
            "touched_apps": touched,
            "results": [asdict(result) for result in results],
        }


def build_default_registry() -> RecoveryRegistry:
    from .handlers.common import register_common_handlers
    from .handlers.macos import register_macos_handlers
    from .handlers.windows import register_windows_handlers

    registry = RecoveryRegistry()
    register_common_handlers(registry)
    register_macos_handlers(registry)
    register_windows_handlers(registry)
    return registry


def run_sweep(**kwargs) -> dict:
    return build_default_registry().run(**kwargs)
