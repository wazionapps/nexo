from __future__ import annotations

"""Client and automation preference helpers stored in config/schedule.json."""

import os
import shutil
import sys
import tomllib
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
AUTOMATION_TASK_PROFILE_KEYS = (
    "default",
    "fast",
    "balanced",
    "deep",
)
INSTALL_PREFERENCE_KEYS = {
    "ask",
    "auto",
    "skip",
    "manual",
}
DEFAULT_CLAUDE_CODE_MODEL = "claude-opus-4-6[1m]"
DEFAULT_CLAUDE_CODE_REASONING_EFFORT = ""
DEFAULT_CODEX_MODEL = "gpt-5.4"
DEFAULT_CODEX_REASONING_EFFORT = "xhigh"
DEFAULT_FAST_MODEL = "gpt-5.4-mini"
DEFAULT_FAST_REASONING_EFFORT = "medium"


def _user_home() -> Path:
    return Path(os.environ.get("HOME", str(Path.home()))).expanduser()


def _codex_config_path(home: Path) -> Path:
    return home / ".codex" / "config.toml"


def _codex_bootstrap_path(home: Path) -> Path:
    return home / ".codex" / "AGENTS.md"


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
        "last_terminal_client": "",
        "automation_enabled": True,
        "automation_backend": CLIENT_CLAUDE_CODE,
        "client_runtime_profiles": default_client_runtime_profiles(),
        "automation_task_profiles": default_automation_task_profiles(),
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


def _codex_artifacts_suggest_nexo_management(home: Path) -> bool:
    bootstrap_path = _codex_bootstrap_path(home)
    if bootstrap_path.is_file():
        try:
            bootstrap_text = bootstrap_path.read_text()
        except Exception:
            bootstrap_text = ""
        if (
            "nexo-codex-agents-version:" in bootstrap_text
            or "NEXO Shared Brain for Codex" in bootstrap_text
            or "<!-- nexo:core:start -->" in bootstrap_text
        ):
            return True

    config_path = _codex_config_path(home)
    if not config_path.is_file():
        return False

    try:
        payload = tomllib.loads(config_path.read_text())
    except Exception:
        try:
            raw_text = config_path.read_text()
        except Exception:
            return False
        return "[mcp_servers.nexo]" in raw_text or "[nexo.codex]" in raw_text

    if not isinstance(payload, dict):
        return False
    mcp_servers = payload.get("mcp_servers")
    if isinstance(mcp_servers, dict) and "nexo" in mcp_servers:
        return True
    nexo_table = payload.get("nexo")
    if isinstance(nexo_table, dict) and "codex" in nexo_table:
        return True
    return False


def _backfill_interactive_clients(
    interactive_clients: dict[str, bool],
    *,
    user_home: str | os.PathLike[str] | None = None,
) -> dict[str, bool]:
    normalized = dict(interactive_clients)
    home = Path(user_home).expanduser() if user_home else _user_home()
    if not normalized.get(CLIENT_CODEX, False) and _codex_artifacts_suggest_nexo_management(home):
        normalized[CLIENT_CODEX] = True
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


def normalize_last_terminal_client(value, interactive_clients: dict[str, bool] | None = None) -> str:
    interactive_clients = normalize_interactive_clients(interactive_clients or {})
    candidate = normalize_client_key(value)
    if candidate in TERMINAL_CLIENT_KEYS and interactive_clients.get(candidate, False):
        return candidate
    return ""


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
        CLIENT_CLAUDE_CODE: {
            "model": DEFAULT_CLAUDE_CODE_MODEL,
            "reasoning_effort": DEFAULT_CLAUDE_CODE_REASONING_EFFORT,
        },
        CLIENT_CODEX: {
            "model": DEFAULT_CODEX_MODEL,
            "reasoning_effort": DEFAULT_CODEX_REASONING_EFFORT,
        },
    }


def default_automation_task_profiles() -> dict[str, dict[str, str]]:
    return {
        "default": {
            "backend": "",
            "model": "",
            "reasoning_effort": "",
        },
        "fast": {
            "backend": CLIENT_CODEX,
            "model": DEFAULT_FAST_MODEL,
            "reasoning_effort": DEFAULT_FAST_REASONING_EFFORT,
        },
        "balanced": {
            "backend": "",
            "model": "",
            "reasoning_effort": "",
        },
        "deep": {
            "backend": CLIENT_CLAUDE_CODE,
            "model": DEFAULT_CLAUDE_CODE_MODEL,
            "reasoning_effort": DEFAULT_CLAUDE_CODE_REASONING_EFFORT,
        },
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


def normalize_automation_task_profiles(value) -> dict[str, dict[str, str]]:
    defaults = default_automation_task_profiles()
    normalized = {key: dict(profile) for key, profile in defaults.items()}
    if not isinstance(value, dict):
        return normalized

    for raw_profile, raw_value in value.items():
        profile_key = str(raw_profile or "").strip().lower()
        if profile_key not in AUTOMATION_TASK_PROFILE_KEYS:
            continue
        if not isinstance(raw_value, dict):
            continue
        backend = normalize_backend_key(raw_value.get("backend"))
        if backend == BACKEND_NONE:
            backend = ""
        normalized[profile_key] = {
            "backend": backend or defaults[profile_key]["backend"],
            "model": str(raw_value.get("model") or defaults[profile_key]["model"]).strip(),
            "reasoning_effort": str(
                raw_value.get("reasoning_effort") or defaults[profile_key]["reasoning_effort"]
            ).strip().lower(),
        }
    return normalized


def normalize_client_preferences(
    schedule: dict | None = None,
    *,
    user_home: str | os.PathLike[str] | None = None,
) -> dict:
    schedule = dict(schedule or {})
    interactive_clients = _backfill_interactive_clients(
        normalize_interactive_clients(schedule.get("interactive_clients")),
        user_home=user_home,
    )
    automation_enabled = normalize_automation_enabled(schedule.get("automation_enabled"))
    default_terminal_client = normalize_default_terminal_client(
        schedule.get("default_terminal_client"),
        interactive_clients=interactive_clients,
    )
    last_terminal_client = normalize_last_terminal_client(
        schedule.get("last_terminal_client"),
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
        "last_terminal_client": last_terminal_client,
        "automation_enabled": automation_enabled,
        "automation_backend": automation_backend,
        "client_runtime_profiles": runtime_profiles,
        "automation_task_profiles": normalize_automation_task_profiles(
            schedule.get("automation_task_profiles")
        ),
        "client_install_preferences": install_preferences,
    }


def apply_client_preferences(
    schedule: dict | None = None,
    *,
    interactive_clients: dict | None = None,
    default_terminal_client: str | None = None,
    last_terminal_client: str | None = None,
    automation_enabled=None,
    automation_backend: str | None = None,
    client_runtime_profiles: dict | None = None,
    automation_task_profiles: dict | None = None,
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
    merged["last_terminal_client"] = normalize_last_terminal_client(
        last_terminal_client if last_terminal_client is not None else current.get("last_terminal_client", ""),
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
    merged["automation_task_profiles"] = normalize_automation_task_profiles(
        automation_task_profiles
        if automation_task_profiles is not None
        else current["automation_task_profiles"]
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
    last_terminal_client: str | None = None,
    automation_enabled=None,
    automation_backend: str | None = None,
    client_runtime_profiles: dict | None = None,
    automation_task_profiles: dict | None = None,
    client_install_preferences: dict | None = None,
) -> Path:
    schedule = apply_client_preferences(
        load_schedule_config(),
        interactive_clients=interactive_clients,
        default_terminal_client=default_terminal_client,
        last_terminal_client=last_terminal_client,
        automation_enabled=automation_enabled,
        automation_backend=automation_backend,
        client_runtime_profiles=client_runtime_profiles,
        automation_task_profiles=automation_task_profiles,
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


def resolve_automation_task_profile(
    profile: str | None,
    *,
    preferences: dict | None = None,
) -> dict[str, str]:
    normalized = preferences or load_client_preferences()
    defaults = default_automation_task_profiles()
    profile_key = str(profile or "").strip().lower() or "default"
    if profile_key not in AUTOMATION_TASK_PROFILE_KEYS:
        profile_key = "default"
    configured = normalize_automation_task_profiles(normalized.get("automation_task_profiles"))
    selected = dict(configured.get(profile_key) or defaults[profile_key])
    backend = selected.get("backend") or resolve_automation_backend(normalized)
    runtime_profile = resolve_client_runtime_profile(backend, preferences=normalized)
    return {
        "name": profile_key,
        "backend": backend,
        "model": selected.get("model") or runtime_profile["model"],
        "reasoning_effort": selected.get("reasoning_effort") or runtime_profile["reasoning_effort"],
    }
