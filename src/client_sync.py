from __future__ import annotations

"""Shared client sync for Claude Code, Claude Desktop, and Codex."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from client_preferences import (
        BACKEND_NONE,
        INTERACTIVE_CLIENT_KEYS,
        normalize_backend_key,
        normalize_client_key,
        normalize_client_preferences,
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



def _user_home() -> Path:
    return Path(os.environ.get("HOME", str(Path.home()))).expanduser()


def _default_nexo_home() -> Path:
    return Path(os.environ.get("NEXO_HOME", str(_user_home() / ".nexo"))).expanduser()


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
            return str(json.loads(version_file.read_text()).get("operator_name", "")).strip()
        except Exception:
            pass
    return ""


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


def _sync_json_client(path: Path, server_config: dict, label: str) -> dict:
    payload = _load_json_object(path)
    mcp_servers = payload.setdefault("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}
        payload["mcpServers"] = mcp_servers
    action = "updated" if "nexo" in mcp_servers else "created"
    mcp_servers["nexo"] = server_config
    _write_json_object(path, payload)
    return {
        "ok": True,
        "client": label,
        "action": action,
        "path": str(path),
    }


def sync_claude_code(
    *,
    nexo_home: str | os.PathLike[str] | None = None,
    runtime_root: str | os.PathLike[str] | None = None,
    python_path: str = "",
    operator_name: str = "",
    user_home: str | os.PathLike[str] | None = None,
) -> dict:
    server_config = build_server_config(
        nexo_home=nexo_home,
        runtime_root=runtime_root,
        python_path=python_path,
        operator_name=operator_name,
    )
    return _sync_json_client(
        _claude_code_settings_path(Path(user_home).expanduser() if user_home else None),
        server_config,
        "claude_code",
    )


def sync_claude_desktop(
    *,
    nexo_home: str | os.PathLike[str] | None = None,
    runtime_root: str | os.PathLike[str] | None = None,
    python_path: str = "",
    operator_name: str = "",
    user_home: str | os.PathLike[str] | None = None,
) -> dict:
    server_config = build_server_config(
        nexo_home=nexo_home,
        runtime_root=runtime_root,
        python_path=python_path,
        operator_name=operator_name,
    )
    return _sync_json_client(
        _claude_desktop_config_path(Path(user_home).expanduser() if user_home else None),
        server_config,
        "claude_desktop",
    )


def sync_codex(
    *,
    nexo_home: str | os.PathLike[str] | None = None,
    runtime_root: str | os.PathLike[str] | None = None,
    python_path: str = "",
    operator_name: str = "",
    user_home: str | os.PathLike[str] | None = None,
) -> dict:
    nexo_home_path = Path(nexo_home).expanduser() if nexo_home else _default_nexo_home()
    home_path = Path(user_home).expanduser() if user_home else _user_home()
    server_config = build_server_config(
        nexo_home=nexo_home_path,
        runtime_root=runtime_root,
        python_path=python_path,
        operator_name=operator_name,
    )
    codex_bin = shutil.which("codex")
    config_path = _codex_config_path(home_path)
    if not codex_bin:
        return {
            "ok": True,
            "client": "codex",
            "skipped": True,
            "reason": "codex binary not found in PATH",
            "path": str(config_path),
        }

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
    if result.returncode != 0:
        return {
            "ok": False,
            "client": "codex",
            "path": str(config_path),
            "error": (result.stderr or result.stdout or "codex mcp add failed").strip(),
        }
    return {
        "ok": True,
        "client": "codex",
        "action": "updated",
        "path": str(config_path),
        "mode": "cli",
    }


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
            lines.append(f"  {label}: skipped ({item.get('reason', 'not available')})")
        elif item.get("ok"):
            lines.append(f"  {label}: {item.get('action', 'synced')} -> {item.get('path', '')}")
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
