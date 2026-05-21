#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from client_sync import _claude_code_cortex_path, _claude_code_mcp_path, _claude_code_settings_path  # noqa: E402
from runtime_home import legacy_nexo_home, managed_nexo_home, resolve_nexo_home, user_home  # noqa: E402


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize(val) for key, val in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, str) and value.startswith("~"):
        return str(Path(value).expanduser())
    return value


def _server_command(config: dict[str, Any] | None) -> str:
    if not isinstance(config, dict):
        return ""
    parts = [str(config.get("command", "")).strip()]
    parts.extend(str(arg).strip() for arg in (config.get("args") or []))
    return " ".join(part for part in parts if part)


def _parse_claude_mcp_list(output: str) -> dict[str, dict[str, str]]:
    servers: dict[str, dict[str, str]] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("Checking MCP server health"):
            continue
        if ": " not in line or " - " not in line:
            continue
        name, rest = line.split(": ", 1)
        command, status = rest.rsplit(" - ", 1)
        servers[name.strip()] = {
            "command": command.strip(),
            "status": status.strip(),
            "raw": line,
        }
    return servers


def _walk_strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        strings: list[str] = []
        for item in value.values():
            strings.extend(_walk_strings(item))
        return strings
    if isinstance(value, list):
        strings: list[str] = []
        for item in value:
            strings.extend(_walk_strings(item))
        return strings
    if isinstance(value, str):
        return [value]
    return []


def _is_same_file(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    return _normalize(left or {}) == _normalize(right or {})


def _find_workspace_file(start: Path, relative_path: str) -> Path | None:
    start = start.resolve()
    for current in [start, *start.parents]:
        candidate = current / relative_path
        if candidate.is_file():
            return candidate
    return None


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except Exception:
        return False


def _run_claude_mcp_list() -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["claude", "mcp", "list"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "claude command not found"
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", "claude mcp list timed out"


def inspect_claude_code_mcp(
    *,
    server_name: str = "nexo",
    home: Path | None = None,
    workspace: Path | None = None,
    cli_output: str | None = None,
    cli_returncode: int = 0,
    cli_stderr: str = "",
) -> dict[str, Any]:
    home = (home or user_home()).expanduser()
    workspace = (workspace or Path.cwd()).expanduser()
    managed_home = managed_nexo_home(home=home)
    legacy_home = legacy_nexo_home(home=home)
    root_path = _claude_code_mcp_path(home)
    settings_path = _claude_code_settings_path(home)
    cortex_path = _claude_code_cortex_path(home)

    root_payload = _load_json(root_path)
    settings_payload = _load_json(settings_path)
    cortex_payload = _load_json(cortex_path)
    root_server = (root_payload.get("mcpServers") or {}).get(server_name)
    settings_server = (settings_payload.get("mcpServers") or {}).get(server_name)
    cortex_server = (cortex_payload.get("mcpServers") or {}).get(server_name)

    workspace_mcp_path = _find_workspace_file(workspace, ".mcp.json")
    workspace_server = None
    if workspace_mcp_path:
        workspace_payload = _load_json(workspace_mcp_path)
        workspace_server = (workspace_payload.get("mcpServers") or {}).get(server_name)

    if cli_output is None:
        cli_returncode, cli_output, cli_stderr = _run_claude_mcp_list()

    cli_servers = _parse_claude_mcp_list(cli_output)
    cli_server = cli_servers.get(server_name)

    global_config = root_server or settings_server or {}
    active_source_label = "root"
    active_source_path = str(root_path)
    active_source_config = root_server
    candidate_sources = []
    if workspace_server:
        candidate_sources.append(("workspace", str(workspace_mcp_path), workspace_server))
    if root_server:
        candidate_sources.append(("root", str(root_path), root_server))
    if cortex_server:
        candidate_sources.append(("cortex", str(cortex_path), cortex_server))
    if settings_server:
        candidate_sources.append(("settings", str(settings_path), settings_server))
    if cli_server:
        for label, path, config in candidate_sources:
            if _server_command(config) == cli_server["command"]:
                active_source_label = label
                active_source_path = path
                active_source_config = config
                break

    global_home_raw = str((global_config.get("env") or {}).get("NEXO_HOME", managed_home))
    global_code_raw = str((global_config.get("env") or {}).get("NEXO_CODE", ""))
    resolved_home = resolve_nexo_home(global_home_raw)
    active_db = resolved_home / "data" / "nexo.db"
    legacy_db_candidates = [
        legacy_home / "data" / "nexo.db",
        legacy_home / "brain" / "nexo.db",
        resolved_home / "brain" / "nexo.db",
    ]
    server_path = None
    if isinstance(global_config, dict):
        args = global_config.get("args") or []
        if args:
            server_path = Path(str(args[0])).expanduser()

    issues: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    if not root_server and settings_server:
        issues.append(
            f"{root_path} does not define mcpServers.{server_name}, but {settings_path} does. "
            "Claude Code 2.1.x reads the root config for user-scoped MCP."
        )
    elif not root_server:
        issues.append(f"{root_path} does not define mcpServers.{server_name}.")

    if root_server and settings_server and not _is_same_file(root_server, settings_server):
        issues.append(
            f"{root_path} and {settings_path} define different {server_name} entries. "
            f"The root config is the source of truth and settings.json is out of sync."
        )
    if root_server and cortex_server and not _is_same_file(root_server, cortex_server):
        issues.append(
            f"{root_path} and {cortex_path} define different {server_name} entries. "
            "A third MCP surface is out of sync."
        )

    if workspace_server and root_server and not _is_same_file(workspace_server, root_server):
        if active_source_label == "workspace":
            issues.append(
                f"The CLI is loading {server_name} from {workspace_mcp_path}, not from {root_path}. "
                "Before blaming the global server, inspect the workspace-local attach."
            )
        else:
            issues.append(
                f"{workspace_mcp_path} defines a different {server_name} than the global root. "
                "Before blaming the server, confirm whether the workspace is attaching another MCP."
            )
    elif workspace_server:
        notes.append(f"Local workspace config detected: {workspace_mcp_path}")

    if cli_returncode != 0:
        issues.append(
            f"`claude mcp list` failed with rc={cli_returncode}: {(cli_stderr or cli_output).strip() or 'no detail'}"
        )
    elif not cli_server:
        issues.append(
            f"`claude mcp list` does not report {server_name}; the active CLI is not loading that server."
        )
    else:
        expected = _server_command(active_source_config or global_config)
        if expected and cli_server["command"] != expected:
            issues.append(
                f"`claude mcp list` carga `{cli_server['command']}`, pero {active_source_path} apunta a `{expected}`."
            )
        if "Connected" not in cli_server["status"]:
            issues.append(
                f"`claude mcp list` sees {server_name} but it is not connected: {cli_server['status']}."
            )

    legacy_tokens = [str(legacy_home), "~/claude", f"{home}/claude"]
    seen_strings = _walk_strings(root_server) + _walk_strings(settings_server) + _walk_strings(workspace_server)
    seen_strings.extend(_walk_strings(cortex_server))
    if cli_server:
        seen_strings.append(cli_server["command"])
    legacy_hits = sorted({value for value in seen_strings for token in legacy_tokens if token and token in value})
    if legacy_hits:
        issues.append("Legacy paths detected in the active configuration: " + "; ".join(legacy_hits))

    if global_home_raw:
        expanded_home = Path(global_home_raw).expanduser()
        if expanded_home == legacy_home or resolved_home == legacy_home:
            issues.append(
                f"NEXO_HOME points to legacy `{legacy_home}`. The current managed home is `{managed_home}`."
            )

    if server_path and not server_path.exists():
        issues.append(f"server.py does not exist at the configured path: {server_path}")

    if isinstance(global_config, dict):
        command = str(global_config.get("command", "")).strip()
        if command.startswith("/") and not Path(command).exists():
            issues.append(f"The configured binary does not exist: {command}")

    if global_code_raw:
        expanded_code = Path(global_code_raw).expanduser()
        if expanded_code == legacy_home:
            issues.append(
                f"NEXO_CODE still points to legacy `{legacy_home}` instead of the real runtime or repo."
            )
        managed_core = managed_home / "core"
        if _is_relative_to(expanded_code, managed_core / "versions"):
            issues.append(
                f"NEXO_CODE points to a versioned snapshot `{expanded_code}`. "
                f"It must point to the stable managed runtime `{managed_core}`."
            )

    if server_path and _is_relative_to(server_path, managed_home / "core" / "versions"):
        issues.append(
            f"server.py points to a versioned snapshot `{server_path}` instead of the stable managed runtime "
            f"`{managed_home / 'core' / 'server.py'}`."
        )

    if not active_db.exists():
        issues.append(f"The expected active DB does not exist: {active_db}")

    stale_legacy_dbs = [path for path in legacy_db_candidates if path.exists() and path != active_db]
    for path in stale_legacy_dbs:
        warnings.append(f"Legacy DB is present but not active: {path}")

    if resolved_home == managed_home and active_db.exists():
        notes.append(f"NEXO_HOME resolves correctly to {resolved_home}")
    if cli_server:
        notes.append(f"`claude mcp list` reports: {cli_server['raw']}")

    return {
        "ok": not issues,
        "server_name": server_name,
        "root_path": str(root_path),
        "settings_path": str(settings_path),
        "cortex_path": str(cortex_path),
        "workspace_mcp_path": str(workspace_mcp_path) if workspace_mcp_path else "",
        "active_source_label": active_source_label,
        "active_source_path": active_source_path,
        "managed_home": str(managed_home),
        "legacy_home": str(legacy_home),
        "resolved_home": str(resolved_home),
        "active_db": str(active_db),
        "root_server": root_server,
        "settings_server": settings_server,
        "cortex_server": cortex_server,
        "workspace_server": workspace_server,
        "cli_server": cli_server,
        "issues": issues,
        "warnings": warnings,
        "notes": notes,
    }


def _print_human(report: dict[str, Any]) -> None:
    status = "OK" if report["ok"] else "FAIL"
    print(f"[claude-code-mcp] {status}")
    print(f"source_of_truth: {report['root_path']}")
    print(f"settings_mirror: {report['settings_path']}")
    print(f"cortex_mirror: {report['cortex_path']}")
    if report["workspace_mcp_path"]:
        print(f"workspace_mcp: {report['workspace_mcp_path']}")
    print(f"active_source: {report['active_source_label']} -> {report['active_source_path']}")
    print(f"resolved_nexo_home: {report['resolved_home']}")
    print(f"active_db: {report['active_db']}")
    if report["issues"]:
        print("issues:")
        for item in report["issues"]:
            print(f"- {item}")
    if report["warnings"]:
        print("warnings:")
        for item in report["warnings"]:
            print(f"- {item}")
    if report["notes"]:
        print("notes:")
        for item in report["notes"]:
            print(f"- {item}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose which MCP Claude Code actually loads so the effective configuration is checked before blaming the server."
    )
    parser.add_argument("--server", default="nexo", help="MCP server name to check (default: nexo)")
    parser.add_argument("--workspace", default="", help="Workspace to inspect for a local .mcp.json (default: cwd)")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON")
    args = parser.parse_args(argv)

    report = inspect_claude_code_mcp(
        server_name=args.server,
        workspace=Path(args.workspace).expanduser() if args.workspace else Path.cwd(),
    )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_human(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
