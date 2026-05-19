"""macOS sync handlers for post-disk-recovery sweep."""
from __future__ import annotations

from disk_recovery.registry import RecoveryContext, RecoveryRegistry, RecoveryResult


def _run_commands(ctx: RecoveryContext, app: str, commands: list[list[str]]) -> RecoveryResult:
    errors: list[str] = []
    for command in commands:
        result = ctx.run(command)
        if not result.get("ok"):
            errors.append(str(result.get("error") or result.get("stderr") or "command_failed"))
    return RecoveryResult(app=app, touched=True, commands=commands, errors=errors)


def calendar_mail_handler(ctx: RecoveryContext) -> RecoveryResult:
    commands = [
        ["killall", "CalendarAgent"],
        ["killall", "Calendar"],
        ["osascript", "-e", 'tell application "Mail" to check for new mail'],
    ]
    return _run_commands(ctx, "macos_calendar_mail", commands)


def icloud_handler(ctx: RecoveryContext) -> RecoveryResult:
    commands: list[list[str]] = []
    processes = {name.lower() for name in ctx.processes}
    if "cloudd" in processes:
        commands.append(["killall", "cloudd"])
    if "bird" in processes:
        commands.append(["killall", "bird"])
    if not commands:
        return RecoveryResult(app="icloud_drive", touched=False, detail="not_running")
    return _run_commands(ctx, "icloud_drive", commands)


def register_macos_handlers(registry: RecoveryRegistry) -> None:
    registry.register("macos_calendar_mail", {"darwin"}, calendar_mail_handler)
    registry.register("icloud_drive", {"darwin"}, icloud_handler)
