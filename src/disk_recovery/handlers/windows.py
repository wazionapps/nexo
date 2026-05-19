"""Windows sync handlers for post-disk-recovery sweep."""
from __future__ import annotations

from disk_recovery.registry import RecoveryContext, RecoveryRegistry, RecoveryResult


def _run_commands(ctx: RecoveryContext, app: str, commands: list[list[str]]) -> RecoveryResult:
    errors: list[str] = []
    for command in commands:
        result = ctx.run(command)
        if not result.get("ok"):
            errors.append(str(result.get("error") or result.get("stderr") or "command_failed"))
    return RecoveryResult(app=app, touched=True, commands=commands, errors=errors)


def onesync_handler(ctx: RecoveryContext) -> RecoveryResult:
    commands = [
        ["powershell", "-NoProfile", "-Command", "Get-Service OneSyncSvc* | Restart-Service -Force"],
    ]
    return _run_commands(ctx, "windows_onesync", commands)


def outlook_handler(ctx: RecoveryContext) -> RecoveryResult:
    commands = [[
        "powershell",
        "-NoProfile",
        "-Command",
        "$o = New-Object -ComObject Outlook.Application; $o.Session.SendAndReceive($false)",
    ]]
    return _run_commands(ctx, "outlook_send_receive", commands)


def onedrive_handler(ctx: RecoveryContext) -> RecoveryResult:
    processes = {name.lower() for name in ctx.processes}
    if "onedrive.exe" not in processes and "onedrive" not in processes:
        return RecoveryResult(app="onedrive", touched=False, detail="not_running")
    local = ctx.env.get("LOCALAPPDATA", r"%LOCALAPPDATA%")
    exe = local + r"\Microsoft\OneDrive\OneDrive.exe"
    commands = [
        [exe, "/shutdown"],
        ["powershell", "-NoProfile", "-Command", f"Start-Process '{exe}'"],
    ]
    return _run_commands(ctx, "onedrive", commands)


def register_windows_handlers(registry: RecoveryRegistry) -> None:
    registry.register("windows_onesync", {"windows"}, onesync_handler)
    registry.register("outlook_send_receive", {"windows"}, outlook_handler)
    registry.register("onedrive", {"windows"}, onedrive_handler)
