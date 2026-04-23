from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult

import paths


CONTINUITY_API_LEVEL = 1
MCP_STATUS_SCHEMA_VERSION = 1
PROCESS_VERSION = ""
RESTART_ALLOWLIST = {
    "nexo_status",
    "nexo_system_catalog",
    "nexo_tool_explain",
    "nexo_heartbeat",
    "nexo_stop",
    "nexo_session_portable_context",
    "nexo_session_export_bundle",
    "nexo_lifecycle_event",
    "nexo_lifecycle_status",
    "nexo_lifecycle_complete_canonical",
    "nexo_lifecycle_wait_for_diary",
    "nexo_continuity_snapshot_read",
    "nexo_continuity_resume_bundle",
    "nexo_continuity_audit",
}


def _read_json_file(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def core_container_dir() -> Path:
    return paths.home() / "core"


def core_versions_dir() -> Path:
    return core_container_dir() / "versions"


def core_current_link() -> Path:
    return core_container_dir() / "current"


def active_runtime_root() -> Path:
    current = core_current_link()
    if current.exists():
        try:
            resolved = current.resolve(strict=False)
            if resolved.exists():
                return resolved
        except Exception:
            pass
        return current
    core_dir = core_container_dir()
    if (core_dir / "cli.py").is_file() or (core_dir / "server.py").is_file():
        return core_dir
    return paths.home()


def restart_required_marker_path() -> Path:
    return paths.operations_dir() / "mcp-restart-required.json"


def _candidate_version_files(base: Path) -> list[Path]:
    return [
        base / "version.json",
        base / "package.json",
    ]


def read_version_for_path(base: Path) -> str:
    for candidate in _candidate_version_files(base):
        try:
            if candidate.is_file():
                payload = json.loads(candidate.read_text(encoding="utf-8"))
                version = str(payload.get("version", "")).strip()
                if version:
                    return version
        except Exception:
            continue
    return ""


def installed_runtime_version() -> str:
    for candidate in [active_runtime_root(), paths.home()]:
        version = read_version_for_path(candidate)
        if version:
            return version
    return ""


def read_restart_required_marker() -> dict:
    path = restart_required_marker_path()
    if not path.exists():
        return {"required": False, "path": str(path), "exists": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("marker is not an object")
        payload.setdefault("required", True)
        payload["path"] = str(path)
        payload["exists"] = True
        return payload
    except Exception as exc:
        return {
            "required": True,
            "exists": True,
            "path": str(path),
            "corrupt": True,
            "error": str(exc),
        }


def write_restart_required_marker(
    *,
    from_version: str,
    to_version: str,
    reason: str = "brain_update",
) -> dict:
    path = restart_required_marker_path()
    payload = {
        "schema_version": MCP_STATUS_SCHEMA_VERSION,
        "required": True,
        "from_version": str(from_version or "").strip(),
        "to_version": str(to_version or "").strip(),
        "reason": str(reason or "brain_update"),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "clients": {
            "claude_desktop": "restart_client_required",
            "claude_code": "restart_session_required",
            "codex": "restart_session_required",
        },
    }
    _write_json_atomic(path, payload)
    payload["path"] = str(path)
    return payload


def activate_versioned_runtime_snapshot(*, source_root: Path | None = None, version: str = "") -> dict:
    container = core_container_dir()
    source = Path(source_root or container)
    if source == container and core_current_link().exists():
        try:
            source = core_current_link().resolve(strict=False)
        except Exception:
            pass
    resolved_version = str(version or read_version_for_path(source) or installed_runtime_version()).strip()
    if not resolved_version:
        return {"ok": False, "error": "missing_version", "source_root": str(source)}

    versions_dir = core_versions_dir()
    target = versions_dir / resolved_version
    versions_dir.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for item in source.iterdir():
        if item.name in {"versions", "current", "__pycache__"}:
            continue
        dest = target / item.name
        if dest.exists() or dest.is_symlink():
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        if item.is_dir():
            shutil.copytree(item, dest, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        else:
            shutil.copy2(item, dest)
        copied.append(item.name)

    current = core_current_link()
    tmp_link = current.with_name(f".current.{os.getpid()}.tmp")
    if tmp_link.exists() or tmp_link.is_symlink():
        tmp_link.unlink()
    target_rel = Path("versions") / resolved_version
    os.symlink(str(target_rel), str(tmp_link))
    os.replace(str(tmp_link), str(current))
    return {
        "ok": True,
        "version": resolved_version,
        "source_root": str(source),
        "target_root": str(target),
        "current_link": str(current),
        "copied": copied,
    }


def clear_restart_required_marker(*, client: str = "", installed_version: str = "", process_version: str = "") -> dict:
    path = restart_required_marker_path()
    marker = read_restart_required_marker()
    if not marker.get("required"):
        return {"ok": True, "cleared": False, "path": str(path)}
    if marker.get("corrupt"):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return {"ok": True, "cleared": True, "path": str(path), "corrupt": True}

    payload = dict(marker)
    clients = dict(payload.get("clients") or {})
    if client:
        clients[client] = "ok"
        payload["clients"] = clients
    pending_clients = {k: v for k, v in clients.items() if v != "ok"}
    effective_installed = str(installed_version or payload.get("to_version") or "").strip()
    effective_process = str(process_version or "").strip()
    if pending_clients:
        _write_json_atomic(path, payload)
        return {"ok": True, "cleared": False, "path": str(path), "pending_clients": pending_clients}
    if effective_installed and effective_process and effective_installed != effective_process:
        _write_json_atomic(path, payload)
        return {
            "ok": True,
            "cleared": False,
            "path": str(path),
            "pending_reason": "process_version_mismatch",
        }
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
    return {"ok": True, "cleared": True, "path": str(path)}


def resolve_restart_required(*, client: str = "", installed_version: str = "", process_version: str = "") -> dict:
    marker = read_restart_required_marker()
    installed = str(installed_version or installed_runtime_version() or "").strip()
    process = str(process_version or PROCESS_VERSION or installed).strip()
    restart_required = False
    reason = ""
    client_action = ""
    marker_clients = dict(marker.get("clients") or {})

    if marker.get("required"):
        restart_required = True
        reason = "marker_required"
        client_action = str(marker_clients.get(client) or "")
    if marker.get("corrupt"):
        restart_required = True
        reason = "marker_corrupt"
    elif installed and process and installed != process:
        restart_required = True
        reason = reason or "version_mismatch"
    elif client and client_action == "ok":
        restart_required = False
        reason = ""

    return {
        "restart_required": restart_required,
        "reason": reason,
        "client_action": client_action,
        "marker": marker,
        "installed_version": installed,
        "process_version": process,
    }


def build_mcp_status(*, client: str = "") -> dict:
    state = resolve_restart_required(client=client)
    marker = state["marker"]
    return {
        "ok": True,
        "schema_version": MCP_STATUS_SCHEMA_VERSION,
        "client": str(client or "").strip(),
        "installed_version": state["installed_version"],
        "process_version": state["process_version"],
        "active_runtime_root": str(active_runtime_root()),
        "active_runtime_version": read_version_for_path(active_runtime_root()),
        "restart_required": bool(state["restart_required"]),
        "reason": state["reason"],
        "client_action": state["client_action"],
        "marker_path": marker.get("path", str(restart_required_marker_path())),
        "marker_exists": bool(marker.get("exists")),
        "marker_corrupt": bool(marker.get("corrupt")),
        "continuity_api_level": CONTINUITY_API_LEVEL,
        "version_match": (
            bool(state["installed_version"])
            and bool(state["process_version"])
            and state["installed_version"] == state["process_version"]
        ),
    }


def prime_process_version() -> str:
    global PROCESS_VERSION
    if PROCESS_VERSION:
        return PROCESS_VERSION
    for candidate in [Path(__file__).resolve().parent, active_runtime_root(), paths.home()]:
        version = read_version_for_path(candidate)
        if version:
            PROCESS_VERSION = version
            return version
    PROCESS_VERSION = "unknown"
    return PROCESS_VERSION


@dataclass
class RestartRequiredMiddleware(Middleware):
    client: str = ""

    async def on_call_tool(self, context, call_next):
        tool_name = str(getattr(context.message, "name", "") or "").strip()
        state = resolve_restart_required(client=self.client)
        if not state["restart_required"] or tool_name in RESTART_ALLOWLIST:
            return await call_next(context)

        payload = {
            "ok": False,
            "error": "mcp_restart_required",
            "message": "NEXO Brain was updated. Restart this MCP client/session.",
            "restart_required": True,
            "tool": tool_name,
            "installed_version": state["installed_version"],
            "process_version": state["process_version"],
            "reason": state["reason"],
            "client_action": state["client_action"],
        }
        return ToolResult(
            content=json.dumps(payload, ensure_ascii=False),
            structured_content=payload,
        )
