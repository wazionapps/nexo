from __future__ import annotations

"""Client and automation preference helpers stored in config/schedule.json."""

import os
import shutil
import sys
from pathlib import Path

from runtime_power import load_schedule_config, save_schedule_config


CLIENT_CLAUDE_CODE = "claude_code"
CLIENT_CODEX = "codex"
CLIENT_CLAUDE_DESKTOP = "claude_desktop"
BACKEND_NONE = "none"

INTERACTIVE_CLIENT_KEYS = (
    CLIENT_CLAUDE_CODE,
    CLIENT_CODEX,
    CLIENT_CLAUDE_DESKTOP,
)
TERMINAL_CLIENT_KEYS = (
    CLIENT_CLAUDE_CODE,
    CLIENT_CODEX,
)
AUTOMATION_BACKEND_KEYS = (
    BACKEND_NONE,
    CLIENT_CLAUDE_CODE,
    CLIENT_CODEX,
)
INSTALL_PREFERENCE_KEYS = {
    "ask",
    "auto",
    "skip",
    "manual",
}
DEFAULT_CLIENT_RUNTIME_PROFILES = {
    CLIENT_CLAUDE_CODE: {
        "model": "opus",
        "reasoning_effort": "",
    },
    CLIENT_CODEX: {
        "model": "gpt-5.4",
        "reasoning_effort": "xhigh",
    },
}


def _user_home() -> Path:
    return Path(os.environ.get("HOME", str(Path.home()))).expanduser()


def _coerce_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    candidate = str(value).strip().lower()
    if candidate in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if candidate in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    return default


def default_client_preferences() -> dict:
    return {
        "interactive_clients": {
            CLIENT_CLAUDE_CODE: True,
            CLIENT_CODEX: False,
            CLIENT_CLAUDE_DESKTOP: False,
        },
        "default_terminal_client": CLIENT_CLAUDE_CODE,
        "automation_enabled": True,
        "automation_backend": CLIENT_CLAUDE_CODE,
        "client_runtime_profiles": default_client_runtime_profiles(),
        "client_install_preferences": {
            CLIENT_CLAUDE_CODE: "ask",
            CLIENT_CODEX: "ask",
            CLIENT_CLAUDE_DESKTOP: "manual",
        },
    }


def normalize_client_key(value: str | None) -> str:
    candidate = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "claude": CLIENT_CLAUDE_CODE,
        "claude_code": CLIENT_CLAUDE_CODE,
        "claudecode": CLIENT_CLAUDE_CODE,
        "claudecli": CLIENT_CLAUDE_CODE,
        "codex": CLIENT_CODEX,
        "openai_codex": CLIENT_CODEX,
        "claude_desktop": CLIENT_CLAUDE_DESKTOP,
        "claudedesktop": CLIENT_CLAUDE_DESKTOP,
        "desktop": CLIENT_CLAUDE_DESKTOP,
        "claude_app": CLIENT_CLAUDE_DESKTOP,
    }
    return aliases.get(candidate, "")


def normalize_backend_key(value: str | None) -> str:
    candidate = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if candidate in {"", "none", "off", "disabled", "false", "0"}:
        return BACKEND_NONE
    client_key = normalize_client_key(candidate)
    if client_key in TERMINAL_CLIENT_KEYS:
        return client_key
    return ""


def normalize_interactive_clients(value) -> dict[str, bool]:
    if not isinstance(value, dict):
        return dict(default_client_preferences()["interactive_clients"])

    normalized = {
        CLIENT_CLAUDE_CODE: False,
        CLIENT_CODEX: False,
        CLIENT_CLAUDE_DESKTOP: False,
    }

    for raw_key, raw_value in value.items():
        key = normalize_client_key(raw_key)
        if key:
            normalized[key] = _coerce_bool(raw_value, False)
    return normalized


def normalize_default_terminal_client(value, interactive_clients: dict[str, bool] | None = None) -> str:
    interactive_clients = normalize_interactive_clients(interactive_clients or {})
    candidate = normalize_client_key(value)
    if candidate in TERMINAL_CLIENT_KEYS and interactive_clients.get(candidate, False):
        return candidate
    for terminal_client in TERMINAL_CLIENT_KEYS:
        if interactive_clients.get(terminal_client, False):
            return terminal_client
    return CLIENT_CLAUDE_CODE


def normalize_automation_enabled(value) -> bool:
    return _coerce_bool(value, True)


def normalize_automation_backend(value, *, automation_enabled: bool = True) -> str:
    if not automation_enabled:
        return BACKEND_NONE
    candidate = normalize_backend_key(value)
    if candidate in TERMINAL_CLIENT_KEYS:
        return candidate
    return CLIENT_CLAUDE_CODE


def normalize_client_install_preferences(value) -> dict[str, str]:
    defaults = default_client_preferences()["client_install_preferences"]
    normalized = dict(defaults)
    if not isinstance(value, dict):
        return normalized
    for raw_key, raw_value in value.items():
        key = normalize_client_key(raw_key)
        pref = str(raw_value or "").strip().lower()
        if key and pref in INSTALL_PREFERENCE_KEYS:
            normalized[key] = pref
    return normalized


def default_client_runtime_profiles() -> dict[str, dict[str, str]]:
    return {
        client_key: dict(profile)
        for client_key, profile in DEFAULT_CLIENT_RUNTIME_PROFILES.items()
    }


def _normalize_runtime_model(value, *, default: str) -> str:
    candidate = str(value or "").strip()
    return candidate or default


def _normalize_runtime_reasoning_effort(value, *, default: str) -> str:
    candidate = str(value or "").strip().lower()
    return candidate or default


def normalize_client_runtime_profiles(value) -> dict[str, dict[str, str]]:
    defaults = default_client_runtime_profiles()
    normalized = default_client_runtime_profiles()
    if not isinstance(value, dict):
        return normalized

    for raw_client, raw_profile in value.items():
        client_key = normalize_client_key(raw_client)
        if client_key not in TERMINAL_CLIENT_KEYS:
            continue
        if isinstance(raw_profile, dict):
            normalized[client_key] = {
                "model": _normalize_runtime_model(
                    raw_profile.get("model"),
                    default=defaults[client_key]["model"],
                ),
                "reasoning_effort": _normalize_runtime_reasoning_effort(
                    raw_profile.get("reasoning_effort"),
                    default=defaults[client_key]["reasoning_effort"],
                ),
            }
            continue
        normalized[client_key] = {
            "model": _normalize_runtime_model(raw_profile, default=defaults[client_key]["model"]),
            "reasoning_effort": defaults[client_key]["reasoning_effort"],
        }
    return normalized


def normalize_client_preferences(schedule: dict | None = None) -> dict:
    schedule = dict(schedule or {})
    interactive_clients = normalize_interactive_clients(schedule.get("interactive_clients"))
    automation_enabled = normalize_automation_enabled(schedule.get("automation_enabled"))
    default_terminal_client = normalize_default_terminal_client(
        schedule.get("default_terminal_client"),
        interactive_clients=interactive_clients,
    )
    automation_backend = normalize_automation_backend(
        schedule.get("automation_backend"),
        automation_enabled=automation_enabled,
    )
    install_preferences = normalize_client_install_preferences(
        schedule.get("client_install_preferences")
    )
    runtime_profiles = normalize_client_runtime_profiles(
        schedule.get("client_runtime_profiles")
    )
    return {
        "interactive_clients": interactive_clients,
        "default_terminal_client": default_terminal_client,
        "automation_enabled": automation_enabled,
        "automation_backend": automation_backend,
        "client_runtime_profiles": runtime_profiles,
        "client_install_preferences": install_preferences,
    }


def apply_client_preferences(
    schedule: dict | None = None,
    *,
    interactive_clients: dict | None = None,
    default_terminal_client: str | None = None,
    automation_enabled=None,
    automation_backend: str | None = None,
    client_runtime_profiles: dict | None = None,
    client_install_preferences: dict | None = None,
) -> dict:
    merged = dict(schedule or {})
    current = normalize_client_preferences(schedule)
    merged["interactive_clients"] = normalize_interactive_clients(
        interactive_clients if interactive_clients is not None else current["interactive_clients"]
    )
    merged["automation_enabled"] = normalize_automation_enabled(
        automation_enabled if automation_enabled is not None else current["automation_enabled"]
    )
    merged["default_terminal_client"] = normalize_default_terminal_client(
        default_terminal_client if default_terminal_client is not None else current["default_terminal_client"],
        interactive_clients=merged["interactive_clients"],
    )
    merged["automation_backend"] = normalize_automation_backend(
        automation_backend if automation_backend is not None else current["automation_backend"],
        automation_enabled=merged["automation_enabled"],
    )
    merged["client_runtime_profiles"] = normalize_client_runtime_profiles(
        client_runtime_profiles
        if client_runtime_profiles is not None
        else current["client_runtime_profiles"]
    )
    merged["client_install_preferences"] = normalize_client_install_preferences(
        client_install_preferences
        if client_install_preferences is not None
        else current["client_install_preferences"]
    )
    return merged


def load_client_preferences() -> dict:
    return normalize_client_preferences(load_schedule_config())


def save_client_preferences(
    *,
    interactive_clients: dict | None = None,
    default_terminal_client: str | None = None,
    automation_enabled=None,
    automation_backend: str | None = None,
    client_runtime_profiles: dict | None = None,
    client_install_preferences: dict | None = None,
) -> Path:
    schedule = apply_client_preferences(
        load_schedule_config(),
        interactive_clients=interactive_clients,
        default_terminal_client=default_terminal_client,
        automation_enabled=automation_enabled,
        automation_backend=automation_backend,
        client_runtime_profiles=client_runtime_profiles,
        client_install_preferences=client_install_preferences,
    )
    return save_schedule_config(schedule)


def _claude_desktop_config_path(home: Path) -> Path:
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if os.name == "nt":
        return home / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def detect_installed_clients(user_home: str | os.PathLike[str] | None = None) -> dict[str, dict]:
    home = Path(user_home).expanduser() if user_home else _user_home()

    claude_bin = os.environ.get("CLAUDE_BIN", "").strip() or shutil.which("claude") or ""
    codex_bin = os.environ.get("CODEX_BIN", "").strip() or shutil.which("codex") or ""

    if sys.platform == "darwin":
        desktop_app = next(
            (
                str(candidate)
                for candidate in (
                    home / "Applications" / "Claude.app",
                    Path("/Applications/Claude.app"),
                )
                if candidate.exists()
            ),
            "",
        )
    else:
        desktop_app = ""
    desktop_config = _claude_desktop_config_path(home)

    return {
        CLIENT_CLAUDE_CODE: {
            "installed": bool(claude_bin),
            "path": claude_bin,
            "detected_by": "binary" if claude_bin else "missing",
        },
        CLIENT_CODEX: {
            "installed": bool(codex_bin),
            "path": codex_bin,
            "detected_by": "binary" if codex_bin else "missing",
        },
        CLIENT_CLAUDE_DESKTOP: {
            "installed": bool(desktop_app or desktop_config.exists()),
            "path": desktop_app or str(desktop_config),
            "detected_by": "app" if desktop_app else ("config" if desktop_config.exists() else "missing"),
        },
    }


def resolve_terminal_client(requested: str | None = None, *, preferences: dict | None = None) -> str:
    normalized = preferences or load_client_preferences()
    if requested is not None:
        interactive_clients = normalized["interactive_clients"]
        return normalize_default_terminal_client(requested, interactive_clients=interactive_clients)
    return normalize_default_terminal_client(
        normalized["default_terminal_client"],
        interactive_clients=normalized["interactive_clients"],
    )


def resolve_automation_backend(preferences: dict | None = None) -> str:
    normalized = preferences or load_client_preferences()
    return normalize_automation_backend(
        normalized["automation_backend"],
        automation_enabled=normalized["automation_enabled"],
    )


def resolve_client_runtime_profile(
    client: str | None,
    *,
    preferences: dict | None = None,
) -> dict[str, str]:
    normalized = preferences or load_client_preferences()
    client_key = normalize_client_key(client)
    defaults = default_client_runtime_profiles()
    if client_key not in TERMINAL_CLIENT_KEYS:
        client_key = CLIENT_CLAUDE_CODE
    profiles = normalize_client_runtime_profiles(normalized.get("client_runtime_profiles"))
    profile = profiles.get(client_key) or defaults[client_key]
    return {
        "model": _normalize_runtime_model(
            profile.get("model"),
            default=defaults[client_key]["model"],
        ),
        "reasoning_effort": _normalize_runtime_reasoning_effort(
            profile.get("reasoning_effort"),
            default=defaults[client_key]["reasoning_effort"],
        ),
    }
