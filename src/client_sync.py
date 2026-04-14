from __future__ import annotations

"""Shared client sync for Claude Code, Claude Desktop, and Codex."""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

from bootstrap_docs import sync_client_bootstrap
from runtime_home import resolve_nexo_home

try:
    from client_preferences import (
        BACKEND_NONE,
        INTERACTIVE_CLIENT_KEYS,
        normalize_backend_key,
        normalize_client_key,
        normalize_client_preferences,
        resolve_client_runtime_profile,
    )
except Exception:
    BACKEND_NONE = "none"
    INTERACTIVE_CLIENT_KEYS = ("claude_code", "codex", "claude_desktop")

    def normalize_client_key(value: str | None) -> str:
        candidate = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "claude": "claude_code",
            "claude_code": "claude_code",
            "codex": "codex",
            "claude_desktop": "claude_desktop",
            "desktop": "claude_desktop",
        }
        return aliases.get(candidate, "")

    def normalize_backend_key(value: str | None) -> str:
        candidate = normalize_client_key(value)
        return candidate or (BACKEND_NONE if str(value or "").strip().lower() in {"none", "off", "disabled"} else "")

    def normalize_client_preferences(schedule: dict | None = None) -> dict:
        return {
            "interactive_clients": {
                "claude_code": True,
                "codex": False,
                "claude_desktop": False,
            },
            "default_terminal_client": "claude_code",
            "automation_enabled": True,
            "automation_backend": "claude_code",
        }

    def resolve_client_runtime_profile(client: str, preferences: dict | None = None) -> dict:
        _default_model = "claude-opus-4-6[1m]"
        defaults = {
            "claude_code": {"model": _default_model, "reasoning_effort": ""},
            "codex": {"model": _default_model, "reasoning_effort": ""},
        }
        return dict(defaults.get(client, {}))



def _user_home() -> Path:
    return Path(os.environ.get("HOME", str(Path.home()))).expanduser()


def _default_nexo_home() -> Path:
    return resolve_nexo_home(os.environ.get("NEXO_HOME", str(_user_home() / ".nexo")))


def _resolve_operator_name(nexo_home: Path, explicit: str = "") -> str:
    explicit = (explicit or "").strip()
    if explicit:
        return explicit
    env_name = os.environ.get("NEXO_NAME", "").strip()
    if env_name:
        return env_name
    version_file = nexo_home / "version.json"
    if version_file.is_file():
        try:
            candidate = str(json.loads(version_file.read_text()).get("operator_name", "")).strip()
            if candidate:
                return candidate
        except Exception:
            pass
    return "NEXO"


def _resolve_runtime_root(nexo_home: Path, runtime_root: str | os.PathLike[str] | None = None) -> Path:
    candidates: list[Path] = []
    if runtime_root:
        candidates.append(Path(runtime_root).expanduser())
    code_env = os.environ.get("NEXO_CODE", "").strip()
    if code_env:
        code_path = Path(code_env).expanduser()
        candidates.extend([code_path, code_path / "src"])
    candidates.extend([nexo_home, Path.cwd(), Path.cwd() / "src"])

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "server.py").is_file():
            return resolved
    raise FileNotFoundError(f"Could not locate runtime root with server.py (tried {len(seen)} locations)")


def _resolve_python(nexo_home: Path, explicit: str = "") -> str:
    candidates = [
        explicit,
        str(nexo_home / ".venv" / "bin" / "python3"),
        str(nexo_home / ".venv" / "bin" / "python"),
        str(nexo_home / ".venv" / "Scripts" / "python.exe"),
        shutil.which("python3") or "",
        shutil.which("python") or "",
        sys.executable,
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(Path(candidate))
    return explicit or sys.executable


def build_server_config(
    *,
    nexo_home: str | os.PathLike[str] | None = None,
    runtime_root: str | os.PathLike[str] | None = None,
    python_path: str = "",
    operator_name: str = "",
) -> dict:
    nexo_home_path = Path(nexo_home).expanduser() if nexo_home else _default_nexo_home()
    runtime_root_path = _resolve_runtime_root(nexo_home_path, runtime_root)
    config = {
        "command": _resolve_python(nexo_home_path, python_path),
        "args": [str(runtime_root_path / "server.py")],
        "env": {
            "NEXO_HOME": str(nexo_home_path),
            "NEXO_CODE": str(runtime_root_path),
        },
    }
    resolved_name = _resolve_operator_name(nexo_home_path, explicit=operator_name)
    if resolved_name:
        config["env"]["NEXO_NAME"] = resolved_name
    return config


def _claude_code_settings_path(home: Path | None = None) -> Path:
    base = home or _user_home()
    return base / ".claude" / "settings.json"


def _claude_code_mcp_path(home: Path | None = None) -> Path:
    base = home or _user_home()
    return base / ".claude.json"


def _claude_desktop_config_path(home: Path | None = None) -> Path:
    base = home or _user_home()
    if sys.platform == "darwin":
        return base / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if os.name == "nt":
        return base / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    return base / ".config" / "Claude" / "claude_desktop_config.json"


def _codex_config_path(home: Path | None = None) -> Path:
    base = home or _user_home()
    return base / ".codex" / "config.toml"


def _toml_key(key: str) -> str:
    if key.replace("_", "").replace("-", "").isalnum():
        return key
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return json.dumps(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _toml_inline_table(payload: dict) -> str:
    parts = [f"{_toml_key(str(key))} = {_toml_value(value)}" for key, value in payload.items()]
    return "{ " + ", ".join(parts) + " }"


def _toml_value(value) -> str:
    if isinstance(value, dict):
        return _toml_inline_table(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return _toml_scalar(value)


def _emit_toml_table(table: dict, prefix: tuple[str, ...] = ()) -> list[str]:
    scalar_lines: list[str] = []
    child_tables: list[tuple[str, dict]] = []
    for key, value in table.items():
        if isinstance(value, dict):
            child_tables.append((str(key), value))
        else:
            scalar_lines.append(f"{_toml_key(str(key))} = {_toml_value(value)}")

    lines: list[str] = []
    emit_header = bool(prefix and (scalar_lines or not child_tables))
    if emit_header:
        lines.append("[" + ".".join(_toml_key(part) for part in prefix) + "]")
    lines.extend(scalar_lines)

    for child_key, child_value in child_tables:
        child_lines = _emit_toml_table(child_value, prefix + (child_key,))
        if child_lines:
            if lines:
                lines.append("")
            lines.extend(child_lines)
    return lines


def _load_toml_object(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text())
    except Exception as exc:
        raise ValueError(f"Invalid TOML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected TOML table in {path}")
    return data


def _write_toml_object(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = _emit_toml_table(payload)
    path.write_text("\n".join(lines).rstrip() + "\n")


def _sync_codex_managed_config(
    path: Path,
    *,
    bootstrap_prompt: str,
    runtime_profile: dict | None,
    server_config: dict | None,
) -> dict:
    payload = _load_toml_object(path)
    action = "updated" if payload else "created"
    runtime_profile = dict(runtime_profile or {})
    server_config = dict(server_config or {})

    def _looks_like_claude_model(value: str) -> bool:
        return value.strip().lower().startswith(
            ("claude", "opus", "sonnet", "haiku")
        )

    # Heal any pre-existing Claude model written by earlier NEXO versions.
    existing_model = str(payload.get("model") or "").strip()
    if existing_model and _looks_like_claude_model(existing_model):
        payload["model"] = "gpt-5.4"

    # Only write a model from the runtime profile into Codex config if it
    # looks like a Codex/OpenAI model. Claude models are invalid for Codex
    # and cause "model not supported" errors on first run.
    profile_model = (runtime_profile.get("model") or "").strip()
    if profile_model and not _looks_like_claude_model(profile_model):
        payload["model"] = profile_model
    elif profile_model:
        # Fall back to a known-good Codex default to self-heal.
        payload["model"] = "gpt-5.4"
    if "reasoning_effort" in runtime_profile:
        payload["model_reasoning_effort"] = runtime_profile.get("reasoning_effort") or ""

    payload["initial_messages"] = [
        {
            "role": "system",
            "content": bootstrap_prompt,
        }
    ] if bootstrap_prompt else []

    nexo_table = payload.setdefault("nexo", {})
    codex_table = nexo_table.setdefault("codex", {})
    codex_table["bootstrap_managed"] = True
    codex_table["mcp_managed"] = True
    codex_table["bootstrap_bytes"] = len(bootstrap_prompt.encode("utf-8")) if bootstrap_prompt else 0
    if runtime_profile.get("model"):
        # Record the healed/actual model in use, not the raw (possibly Claude) profile.
        codex_table["managed_model"] = payload.get("model") or runtime_profile["model"]
    codex_table["managed_reasoning_effort"] = runtime_profile.get("reasoning_effort", "") or ""
    if server_config:
        mcp_servers = payload.setdefault("mcp_servers", {})
        mcp_servers["nexo"] = {
            "command": server_config.get("command", ""),
            "args": list(server_config.get("args", []) or []),
            "env": dict(server_config.get("env", {}) or {}),
        }
        codex_table["managed_server_command"] = server_config.get("command", "")

    # Ensure Codex headless crons (followup-runner, email-monitor, deep-sleep,
    # etc.) do not stall on approval prompts. Only set defaults when the user
    # hasn't configured them explicitly — never overwrite existing values.
    payload.setdefault("approval_policy", "never")
    payload.setdefault("sandbox_mode", "danger-full-access")

    _write_toml_object(path, payload)
    return {
        "ok": True,
        "action": action,
        "path": str(path),
        "bootstrap_managed": True,
        "mcp_managed": True,
        "model": runtime_profile.get("model", ""),
        "reasoning_effort": runtime_profile.get("reasoning_effort", "") or "",
    }


def _load_json_object(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _write_json_object(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


CORE_HOOK_SPECS = [
    {
        "event": "SessionStart",
        "identity": "session-start-ts",
        "timeout": 2,
        "command_template": lambda nexo_home, _runtime_root, _hooks_dir: (
            f"mkdir -p {shlex.quote(str(nexo_home / 'operations'))} && "
            f"date +%s > {shlex.quote(str(nexo_home / 'operations' / '.session-start-ts'))}"
        ),
    },
    {
        "event": "SessionStart",
        "identity": "daily-briefing-check.sh",
        "timeout": 5,
        "script": "daily-briefing-check.sh",
    },
    {
        "event": "SessionStart",
        "identity": "session-start.sh",
        "timeout": 35,
        "script": "session-start.sh",
    },
    {
        "event": "Stop",
        "identity": "session-stop.sh",
        "timeout": 10,
        "script": "session-stop.sh",
    },
    {
        "event": "PreToolUse",
        "identity": "protocol-pretool-guardrail.sh",
        "timeout": 5,
        "script": "protocol-pretool-guardrail.sh",
    },
    {
        "event": "UserPromptSubmit",
        "identity": "heartbeat-user-msg.sh",
        "timeout": 3,
        "script": "heartbeat-user-msg.sh",
    },
    {
        "event": "PostToolUse",
        "identity": "capture-tool-logs.sh",
        "timeout": 5,
        "script": "capture-tool-logs.sh",
    },
    {
        "event": "PostToolUse",
        "identity": "capture-session.sh",
        "timeout": 3,
        "script": "capture-session.sh",
    },
    {
        "event": "PostToolUse",
        "identity": "inbox-hook.sh",
        "timeout": 5,
        "script": "inbox-hook.sh",
    },
    {
        "event": "PostToolUse",
        "identity": "protocol-guardrail.sh",
        "timeout": 5,
        "script": "protocol-guardrail.sh",
    },
    {
        "event": "PostToolUse",
        "identity": "heartbeat-posttool.sh",
        "timeout": 3,
        "script": "heartbeat-posttool.sh",
    },
    {
        "event": "PreCompact",
        "identity": "pre-compact.sh",
        "timeout": 10,
        "script": "pre-compact.sh",
    },
    {
        "event": "PostCompact",
        "identity": "post-compact.sh",
        "timeout": 10,
        "script": "post-compact.sh",
    },
]

LEGACY_CORE_HOOK_IDENTITIES_BY_EVENT = {
    "PostToolUse": {
        "heartbeat-guard.sh",
    },
}


def _current_core_hook_identities_by_event() -> dict[str, set[str]]:
    identities: dict[str, set[str]] = {}
    for spec in CORE_HOOK_SPECS:
        identities.setdefault(spec["event"], set()).add(spec["identity"])
    return identities


CURRENT_CORE_HOOK_IDENTITIES_BY_EVENT = _current_core_hook_identities_by_event()


def _managed_core_hook_identities_by_event() -> dict[str, set[str]]:
    managed = {event: set(identities) for event, identities in CURRENT_CORE_HOOK_IDENTITIES_BY_EVENT.items()}
    for event, identities in LEGACY_CORE_HOOK_IDENTITIES_BY_EVENT.items():
        managed.setdefault(event, set()).update(identities)
    return managed


MANAGED_CORE_HOOK_IDENTITIES_BY_EVENT = _managed_core_hook_identities_by_event()


def _resolve_hook_source_dir(runtime_root: Path) -> Path:
    direct = runtime_root / "hooks"
    if direct.is_dir():
        return direct
    sibling = runtime_root.parent / "src" / "hooks"
    if sibling.is_dir():
        return sibling
    fallback = runtime_root.parent / "hooks"
    if fallback.is_dir():
        return fallback
    return direct


def _render_hook_command(spec: dict, *, nexo_home: Path, runtime_root: Path, hooks_dir: Path) -> str:
    command_template = spec.get("command_template")
    if callable(command_template):
        return command_template(nexo_home, runtime_root, hooks_dir)
    script_name = spec.get("script", "").strip()
    script_path = hooks_dir / script_name
    return (
        f"NEXO_HOME={shlex.quote(str(nexo_home))} "
        f"NEXO_CODE={shlex.quote(str(runtime_root))} "
        f"bash {shlex.quote(str(script_path))}"
    )


def _hook_identity(command: str) -> str:
    text = str(command or "")
    if ".session-start-ts" in text:
        return "session-start-ts"
    match = re.search(r"([A-Za-z0-9._-]+\.sh)\b", text)
    if match:
        return match.group(1)
    return text.strip()


def _normalize_hook_sections(entries) -> list[dict]:
    normalized: list[dict] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks")
        if isinstance(hooks, list):
            normalized.append(
                {
                    "matcher": entry.get("matcher", "*") or "*",
                    "hooks": [dict(hook) for hook in hooks if isinstance(hook, dict)],
                }
            )
            continue
        if entry.get("command"):
            hook = {"type": entry.get("type", "command"), "command": entry["command"]}
            if entry.get("timeout"):
                hook["timeout"] = entry["timeout"]
            normalized.append({"matcher": entry.get("matcher", "*") or "*", "hooks": [hook]})
    return normalized


def _merge_core_hooks(existing_hooks, *, runtime_root: Path, nexo_home: Path) -> tuple[dict, int]:
    hooks_payload = dict(existing_hooks) if isinstance(existing_hooks, dict) else {}
    hooks_dir = _resolve_hook_source_dir(runtime_root)
    managed_count = 0

    for event, managed_identities in MANAGED_CORE_HOOK_IDENTITIES_BY_EVENT.items():
        sections = _normalize_hook_sections(hooks_payload.get(event))
        desired_identities = CURRENT_CORE_HOOK_IDENTITIES_BY_EVENT.get(event, set())
        cleaned_sections: list[dict] = []
        for section in sections:
            cleaned_hooks = []
            for hook in section["hooks"]:
                identity = _hook_identity(hook.get("command", ""))
                if identity in managed_identities and identity not in desired_identities:
                    continue
                cleaned_hooks.append(hook)
            cleaned_sections.append(
                {
                    "matcher": section.get("matcher", "*") or "*",
                    "hooks": cleaned_hooks,
                }
            )
        hooks_payload[event] = cleaned_sections

    for spec in CORE_HOOK_SPECS:
        event = spec["event"]
        sections = _normalize_hook_sections(hooks_payload.get(event))
        hooks_payload[event] = sections
        command = _render_hook_command(spec, nexo_home=nexo_home, runtime_root=runtime_root, hooks_dir=hooks_dir)
        identity = spec["identity"]

        found = False
        for section in sections:
            for hook in section["hooks"]:
                if _hook_identity(hook.get("command", "")) != identity:
                    continue
                hook["type"] = "command"
                hook["command"] = command
                if spec.get("timeout"):
                    hook["timeout"] = spec["timeout"]
                found = True
                managed_count += 1
                break
            if found:
                break

        if found:
            continue

        target = None
        for section in sections:
            if section.get("matcher", "*") == "*":
                target = section
                break
        if target is None:
            target = {"matcher": "*", "hooks": []}
            sections.append(target)

        new_hook = {"type": "command", "command": command}
        if spec.get("timeout"):
            new_hook["timeout"] = spec["timeout"]
        target["hooks"].append(new_hook)
        managed_count += 1

    return hooks_payload, managed_count


def _sync_json_client(path: Path, server_config: dict, label: str, *, managed_metadata: dict | None = None) -> dict:
    payload = _load_json_object(path)
    mcp_servers = payload.setdefault("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}
        payload["mcpServers"] = mcp_servers
    action = "updated" if "nexo" in mcp_servers else "created"
    mcp_servers["nexo"] = server_config
    if managed_metadata is not None:
        nexo_meta = payload.setdefault("nexo", {})
        if not isinstance(nexo_meta, dict):
            nexo_meta = {}
            payload["nexo"] = nexo_meta
        nexo_meta.update(managed_metadata)
    _write_json_object(path, payload)
    return {
        "ok": True,
        "client": label,
        "action": action,
        "path": str(path),
    }


def _claude_desktop_managed_metadata(server_config: dict, *, operator_name: str) -> dict:
    return {
        "claude_desktop": {
            "shared_brain_managed": True,
            "shared_brain_mode": "mcp_only",
            "managed_operator": operator_name or server_config.get("env", {}).get("NEXO_NAME", "") or "NEXO",
            "managed_runtime_home": server_config.get("env", {}).get("NEXO_HOME", ""),
            "managed_runtime_root": server_config.get("env", {}).get("NEXO_CODE", ""),
        }
    }


# Minimum permissions allowlist required for NEXO headless automation
# (followup-runner, email-monitor, deep-sleep, etc.) to work without
# interactive approval prompts. Without this, Claude Code headless invocations
# stall waiting for MCP tool approvals.
_NEXO_HEADLESS_ALLOWLIST = (
    "Bash",
    "Read",
    "Edit",
    "Write",
    "Glob",
    "Grep",
    "Task",
    "Skill",
    "NotebookEdit",
    "WebSearch",
    "WebFetch",
    "mcp__*",
)


def _ensure_headless_permissions(payload: dict) -> None:
    """Ensure Claude Code settings.json has the minimum allowlist so headless
    NEXO crons (followup-runner, email-monitor, etc.) do not stall on MCP
    tool-approval prompts. Idempotent: only adds missing entries, never
    removes or reorders existing user customizations."""
    permissions = payload.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
        payload["permissions"] = permissions

    allow_list = permissions.get("allow")
    if not isinstance(allow_list, list):
        allow_list = []
        permissions["allow"] = allow_list

    existing = {str(item) for item in allow_list if isinstance(item, str)}
    for entry in _NEXO_HEADLESS_ALLOWLIST:
        if entry not in existing:
            allow_list.append(entry)
            existing.add(entry)

    permissions.setdefault("deny", [])
    permissions.setdefault("ask", [])
    permissions.setdefault("defaultMode", "dontAsk")


def _sync_claude_code_settings(path: Path, server_config: dict) -> dict:
    payload = _load_json_object(path)
    mcp_servers = payload.setdefault("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}
        payload["mcpServers"] = mcp_servers
    action = "updated" if "nexo" in mcp_servers else "created"
    mcp_servers["nexo"] = server_config

    runtime_root = Path(server_config.get("env", {}).get("NEXO_CODE", "")).expanduser()
    nexo_home = Path(server_config.get("env", {}).get("NEXO_HOME", "")).expanduser()
    payload["hooks"], managed_hook_count = _merge_core_hooks(
        payload.get("hooks", {}),
        runtime_root=runtime_root,
        nexo_home=nexo_home,
    )
    _ensure_headless_permissions(payload)
    _write_json_object(path, payload)
    return {
        "ok": True,
        "client": "claude_code",
        "action": action,
        "path": str(path),
        "managed_hook_count": managed_hook_count,
    }


def sync_claude_code(
    *,
    nexo_home: str | os.PathLike[str] | None = None,
    runtime_root: str | os.PathLike[str] | None = None,
    python_path: str = "",
    operator_name: str = "",
    user_home: str | os.PathLike[str] | None = None,
    preferences: dict | None = None,
) -> dict:
    server_config = build_server_config(
        nexo_home=nexo_home,
        runtime_root=runtime_root,
        python_path=python_path,
        operator_name=operator_name,
    )
    home_path = Path(user_home).expanduser() if user_home else None
    result = _sync_claude_code_settings(
        _claude_code_settings_path(home_path),
        server_config,
    )
    # Claude Code 2.1.x reads user-scoped MCP servers from ~/.claude.json.
    # Keep settings.json in sync for hooks/runtime preferences, but also write
    # the managed NEXO MCP server to the root user config so `claude mcp list`
    # and interactive sessions see the same server.
    mcp_result = _sync_json_client(
        _claude_code_mcp_path(home_path),
        server_config,
        "claude_code",
    )
    result["mcp"] = mcp_result
    result["mcp_path"] = mcp_result.get("path", "")
    bootstrap_result = sync_client_bootstrap(
        "claude_code",
        nexo_home=nexo_home,
        operator_name=operator_name,
        user_home=user_home,
    )
    result["bootstrap"] = bootstrap_result
    if not bootstrap_result.get("ok"):
        result["ok"] = False
        result["error"] = bootstrap_result.get("error", "Claude bootstrap sync failed")
    return result


def sync_claude_desktop(
    *,
    nexo_home: str | os.PathLike[str] | None = None,
    runtime_root: str | os.PathLike[str] | None = None,
    python_path: str = "",
    operator_name: str = "",
    user_home: str | os.PathLike[str] | None = None,
    preferences: dict | None = None,
) -> dict:
    server_config = build_server_config(
        nexo_home=nexo_home,
        runtime_root=runtime_root,
        python_path=python_path,
        operator_name=operator_name,
    )
    resolved_name = server_config.get("env", {}).get("NEXO_NAME", "") or _resolve_operator_name(
        Path(nexo_home).expanduser() if nexo_home else _default_nexo_home(),
        explicit=operator_name,
    )
    return _sync_json_client(
        _claude_desktop_config_path(Path(user_home).expanduser() if user_home else None),
        server_config,
        "claude_desktop",
        managed_metadata=_claude_desktop_managed_metadata(server_config, operator_name=resolved_name),
    )


def sync_codex(
    *,
    nexo_home: str | os.PathLike[str] | None = None,
    runtime_root: str | os.PathLike[str] | None = None,
    python_path: str = "",
    operator_name: str = "",
    user_home: str | os.PathLike[str] | None = None,
    preferences: dict | None = None,
) -> dict:
    nexo_home_path = Path(nexo_home).expanduser() if nexo_home else _default_nexo_home()
    home_path = Path(user_home).expanduser() if user_home else _user_home()
    active_preferences = normalize_client_preferences(preferences)
    runtime_profile = resolve_client_runtime_profile("codex", preferences=active_preferences)
    server_config = build_server_config(
        nexo_home=nexo_home_path,
        runtime_root=runtime_root,
        python_path=python_path,
        operator_name=operator_name,
    )
    codex_bin = shutil.which("codex")
    config_path = _codex_config_path(home_path)
    if not codex_bin:
        result = {
            "ok": True,
            "client": "codex",
            "skipped": True,
            "reason": "codex binary not found in PATH",
            "path": str(config_path),
        }
        bootstrap_result = sync_client_bootstrap(
            "codex",
            nexo_home=nexo_home,
            operator_name=operator_name,
            user_home=user_home,
        )
        result["bootstrap"] = bootstrap_result
        if bootstrap_result.get("ok"):
            prompt_text = bootstrap_result.get("content") or ""
            result["config"] = _sync_codex_managed_config(
                config_path,
                bootstrap_prompt=prompt_text,
                runtime_profile=runtime_profile,
                server_config=server_config,
            )
        return result

    cmd = [codex_bin, "mcp", "add", "nexo"]
    for key, value in sorted(server_config.get("env", {}).items()):
        cmd.extend(["--env", f"{key}={value}"])
    cmd.extend(["--", server_config["command"], *server_config.get("args", [])])
    env = {**os.environ, "HOME": str(home_path)}
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    sync_result = {
        "ok": True,
        "client": "codex",
        "action": "updated",
        "path": str(config_path),
        "mode": "cli" if result.returncode == 0 else "config_only",
    }
    bootstrap_result = sync_client_bootstrap(
        "codex",
        nexo_home=nexo_home_path,
        operator_name=operator_name,
        user_home=home_path,
    )
    sync_result["bootstrap"] = bootstrap_result
    if not bootstrap_result.get("ok"):
        sync_result["ok"] = False
        sync_result["error"] = bootstrap_result.get("error", "Codex bootstrap sync failed")
        return sync_result
    sync_result["config"] = _sync_codex_managed_config(
        config_path,
        bootstrap_prompt=bootstrap_result.get("content") or "",
        runtime_profile=runtime_profile,
        server_config=server_config,
    )
    if result.returncode != 0:
        sync_result["warning"] = (result.stderr or result.stdout or "codex mcp add failed").strip()
    return sync_result


def sync_all_clients(
    *,
    nexo_home: str | os.PathLike[str] | None = None,
    runtime_root: str | os.PathLike[str] | None = None,
    python_path: str = "",
    operator_name: str = "",
    user_home: str | os.PathLike[str] | None = None,
    enabled_clients: list[str] | tuple[str, ...] | set[str] | None = None,
    preferences: dict | None = None,
) -> dict:
    if enabled_clients is None:
        if preferences is None:
            enabled_set = set(INTERACTIVE_CLIENT_KEYS)
        else:
            active_preferences = normalize_client_preferences(preferences)
            enabled_set = {
                key
                for key in INTERACTIVE_CLIENT_KEYS
                if active_preferences.get("interactive_clients", {}).get(key, False)
            }
            backend_key = normalize_backend_key(active_preferences.get("automation_backend"))
            if active_preferences.get("automation_enabled", True) and backend_key and backend_key != BACKEND_NONE:
                enabled_set.add(backend_key)
            if not enabled_set:
                enabled_set.add("claude_code")
    else:
        enabled_set = {normalize_client_key(item) for item in enabled_clients if normalize_client_key(item)}
        if not enabled_set:
            enabled_set = {"claude_code"}

    def _safe(label: str, fn) -> dict:
        if label not in enabled_set:
            return {
                "ok": True,
                "client": label,
                "skipped": True,
                "reason": "disabled in client preferences",
            }
        try:
            return fn(
                nexo_home=nexo_home,
                runtime_root=runtime_root,
                python_path=python_path,
                operator_name=operator_name,
                user_home=user_home,
                preferences=preferences,
            )
        except Exception as exc:
            return {"ok": False, "client": label, "error": str(exc)}

    results = {
        "claude_code": _safe("claude_code", sync_claude_code),
        "claude_desktop": _safe("claude_desktop", sync_claude_desktop),
        "codex": _safe("codex", sync_codex),
    }
    ok = all(item.get("ok") or item.get("skipped") for item in results.values())
    return {
        "ok": ok,
        "nexo_home": str(Path(nexo_home).expanduser() if nexo_home else _default_nexo_home()),
        "runtime_root": str(_resolve_runtime_root(
            Path(nexo_home).expanduser() if nexo_home else _default_nexo_home(),
            runtime_root,
        )),
        "enabled_clients": sorted(enabled_set),
        "clients": results,
    }


def format_sync_summary(result: dict) -> str:
    labels = {
        "claude_code": "Claude Code",
        "claude_desktop": "Claude Desktop",
        "codex": "Codex",
    }
    lines = ["SHARED BRAIN SYNC"]
    for key in ["claude_code", "claude_desktop", "codex"]:
        item = result.get("clients", {}).get(key, {})
        label = labels[key]
        if item.get("skipped"):
            bootstrap = item.get("bootstrap", {})
            if bootstrap.get("ok") and not bootstrap.get("skipped"):
                lines.append(
                    f"  {label}: skipped ({item.get('reason', 'not available')}); bootstrap {bootstrap.get('action', 'synced')} -> {bootstrap.get('path', '')}"
                )
            else:
                lines.append(f"  {label}: skipped ({item.get('reason', 'not available')})")
        elif item.get("ok"):
            bootstrap = item.get("bootstrap", {})
            suffix = ""
            if bootstrap.get("ok"):
                suffix = f"; bootstrap {bootstrap.get('action', 'synced')} -> {bootstrap.get('path', '')}"
            lines.append(f"  {label}: {item.get('action', 'synced')} -> {item.get('path', '')}{suffix}")
        else:
            lines.append(f"  {label}: ERROR -> {item.get('error', 'unknown error')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync NEXO MCP config across Claude Code, Claude Desktop, and Codex.")
    parser.add_argument("--nexo-home", default=str(_default_nexo_home()))
    parser.add_argument("--runtime-root", default="")
    parser.add_argument("--python", dest="python_path", default="")
    parser.add_argument("--operator-name", default="")
    parser.add_argument(
        "--enabled-client",
        action="append",
        dest="enabled_clients",
        choices=["claude_code", "claude_desktop", "codex"],
        help="Sync only the specified client(s). Repeat for multiple values.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = sync_all_clients(
        nexo_home=args.nexo_home,
        runtime_root=args.runtime_root or None,
        python_path=args.python_path,
        operator_name=args.operator_name,
        enabled_clients=args.enabled_clients,
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(format_sync_summary(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
