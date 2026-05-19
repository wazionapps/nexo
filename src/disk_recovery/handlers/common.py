"""Common app restart handlers used after disk pressure is relieved."""
from __future__ import annotations

from disk_recovery.registry import RecoveryContext, RecoveryRegistry, RecoveryResult


def _has_process(ctx: RecoveryContext, *needles: str) -> bool:
    lowered = {name.lower() for name in ctx.processes}
    return any(needle.lower() in lowered for needle in needles)


def _commands_for(ctx: RecoveryContext, app: str) -> list[list[str]]:
    if ctx.platform == "darwin":
        return [["killall", app], ["open", "-a", app]]
    exe = f"{app}.exe" if not app.lower().endswith(".exe") else app
    return [
        ["taskkill", "/IM", exe, "/F"],
        ["powershell", "-NoProfile", "-Command", f"Start-Process '{app}'"],
    ]


def _restart_if_running(ctx: RecoveryContext, app: str, *process_names: str) -> RecoveryResult:
    if not _has_process(ctx, *process_names):
        return RecoveryResult(app=app, touched=False, detail="not_running")
    commands = _commands_for(ctx, app)
    errors: list[str] = []
    for command in commands:
        result = ctx.run(command)
        if not result.get("ok"):
            errors.append(str(result.get("error") or result.get("stderr") or "command_failed"))
    return RecoveryResult(app=app, touched=True, commands=commands, errors=errors)


def register_common_handlers(registry: RecoveryRegistry) -> None:
    registry.register("dropbox", {"darwin", "windows"}, lambda ctx: _restart_if_running(ctx, "Dropbox", "dropbox", "dropbox.exe"))
    registry.register("google_drive", {"darwin", "windows"}, lambda ctx: _restart_if_running(ctx, "Google Drive", "google drive", "googledrivefs", "googledrivefs.exe"))
    registry.register("slack", {"darwin", "windows"}, lambda ctx: _restart_if_running(ctx, "Slack", "slack", "slack.exe"))
