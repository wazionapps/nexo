from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult

import paths


CONTINUITY_API_LEVEL = 1
MCP_STATUS_SCHEMA_VERSION = 3
CLIENT_STATE_SCHEMA_VERSION = 1
PROCESS_VERSION = ""
PROCESS_FINGERPRINT = ""

# Subtrees under the runtime source root that are NOT loaded by the running
# MCP server process (subprocess scripts, test fixtures, migrations executed
# out-of-process, cron entry points spawned separately). Any change limited
# to these directories should NOT force a restart of running MCP clients.
# Anything else under the runtime root (server.py, cli.py, plugins/*, helpers)
# is included in the fingerprint by default.
_FINGERPRINT_EXCLUDE_DIRS = frozenset({
    "scripts",
    "tests",
    "migrations",
    "crons",
    "__pycache__",
    "node_modules",
    ".git",
    "versions",
})
RESTART_CLIENT_ACTIONS = {
    "claude_desktop": "restart_client_required",
    "claude_code": "restart_session_required",
    "codex": "restart_session_required",
}
RESTART_ALLOWLIST = {
    "nexo_startup",
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
    "nexo_lifecycle_write_fallback_diary",
    "nexo_continuity_snapshot_read",
    "nexo_continuity_resume_bundle",
    "nexo_continuity_audit",
    # v0.32.5 — read-only tools called by the CORE protocol immediately after
    # `nexo_startup` (memory recall, reminders, followups, context, doctor).
    # Without this allowlist they were blocked by mcp_restart_required after
    # `nexo update` while a session was active, making continuity appear lost
    # until the client was closed and reopened.
    "nexo_smart_startup",
    "nexo_session_diary_read",
    "nexo_session_diary_write",
    "nexo_session_compliance_state",
    "nexo_reminders",
    "nexo_followups",
    "nexo_recent_context",
    "nexo_doctor",
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


def _normalize_restart_client(value: str | None) -> str:
    candidate = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "claude": "claude_code",
        "claudecode": "claude_code",
        "claude_code": "claude_code",
        "claude_desktop": "claude_desktop",
        "claude_desktop_app": "claude_desktop",
        "desktop": "claude_desktop",
        "codex": "codex",
    }
    resolved = aliases.get(candidate, candidate)
    if resolved in RESTART_CLIENT_ACTIONS:
        return resolved
    return ""


def _enabled_flag(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off", "disabled", "none"}
    return bool(value)


def _restart_clients_from_preferences() -> dict[str, str]:
    try:
        from runtime_power import load_schedule_config

        prefs = load_schedule_config()
    except Exception:
        prefs = {}

    raw_clients = prefs.get("interactive_clients") if isinstance(prefs, dict) else {}
    clients: dict[str, str] = {}
    if isinstance(raw_clients, dict):
        for raw_key, raw_enabled in raw_clients.items():
            key = _normalize_restart_client(str(raw_key or ""))
            if key and _enabled_flag(raw_enabled):
                clients[key] = RESTART_CLIENT_ACTIONS[key]
    return clients


def _restart_clients_for_marker(*, client: str = "") -> dict[str, str]:
    explicit_client = _normalize_restart_client(client or os.environ.get("NEXO_MCP_CLIENT", ""))
    if explicit_client:
        return {explicit_client: RESTART_CLIENT_ACTIONS[explicit_client]}

    clients = _restart_clients_from_preferences()
    if clients:
        return clients

    # Safe default for fresh/legacy installs: Claude Code is the primary
    # terminal client, and avoiding absent clients prevents permanent markers.
    return {"claude_code": RESTART_CLIENT_ACTIONS["claude_code"]}


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


def mcp_client_state_path() -> Path:
    return paths.runtime_state_dir() / "mcp-client-state.json"


def runtime_generation(version: str = "", fingerprint: str = "", root: str = "") -> str:
    seed = "|".join(part for part in (version, fingerprint, root) if str(part or "").strip())
    if not seed:
        return "unknown"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _read_mcp_client_state_file() -> dict:
    path = mcp_client_state_path()
    if not path.is_file():
        return {
            "schema_version": CLIENT_STATE_SCHEMA_VERSION,
            "clients": {},
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "schema_version": CLIENT_STATE_SCHEMA_VERSION,
            "clients": {},
            "corrupt": True,
        }
    if not isinstance(payload, dict):
        return {
            "schema_version": CLIENT_STATE_SCHEMA_VERSION,
            "clients": {},
            "corrupt": True,
        }
    clients = payload.get("clients")
    if not isinstance(clients, dict):
        payload["clients"] = {}
    payload.setdefault("schema_version", CLIENT_STATE_SCHEMA_VERSION)
    return payload


def _write_mcp_client_state_file(payload: dict) -> None:
    payload = dict(payload)
    payload["schema_version"] = CLIENT_STATE_SCHEMA_VERSION
    payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_json_atomic(mcp_client_state_path(), payload)


def read_mcp_client_states() -> dict:
    """Return the persisted per-client MCP readiness registry."""
    payload = _read_mcp_client_state_file()
    clients = payload.get("clients")
    if not isinstance(clients, dict):
        clients = {}
    return {
        "schema_version": CLIENT_STATE_SCHEMA_VERSION,
        "path": str(mcp_client_state_path()),
        "clients": clients,
        "corrupt": bool(payload.get("corrupt")),
        "updated_at": str(payload.get("updated_at") or ""),
    }


def _probe_reason_code(probe: dict) -> str:
    if not probe.get("probe_ok", probe.get("ok", False)):
        return str(probe.get("error") or "mcp_probe_failed")
    try:
        tool_count = int(probe.get("tool_count") or 0)
    except Exception:
        tool_count = 0
    if tool_count <= 0:
        return "tools_missing"
    missing = probe.get("missing_required_tools")
    has_required_tools_contract = isinstance(
        probe.get("required_tools_present"), bool
    ) or isinstance(missing, list)
    if not has_required_tools_contract:
        return "required_tools_contract_missing"
    if probe.get("required_tools_present") is False:
        return "required_tools_missing"
    if isinstance(missing, list) and missing:
        return "required_tools_missing"
    return "ready"


def record_mcp_client_probe(*, client: str = "", probe: dict | None = None) -> dict:
    """Persist the latest probe result for one MCP client."""
    normalized = _normalize_restart_client(client or (probe or {}).get("client", ""))
    if not normalized:
        return {"ok": False, "error": "unknown_client"}
    probe = dict(probe or {})
    installed_version_value = str(probe.get("installed_version") or installed_runtime_version() or "").strip()
    installed_fp = str(probe.get("installed_fingerprint") or installed_runtime_fingerprint() or "").strip()
    root = str(active_runtime_root())
    generation = str(probe.get("runtime_generation") or runtime_generation(installed_version_value, installed_fp, root))
    reason_code = _probe_reason_code(probe)
    probe_ok = reason_code == "ready"
    try:
        tool_count = int(probe.get("tool_count") or 0)
    except Exception:
        tool_count = 0
    missing_required = probe.get("missing_required_tools")
    if not isinstance(missing_required, list):
        missing_required = []
    required_tools_present_raw = probe.get("required_tools_present")
    required_tools_present = (
        required_tools_present_raw
        if isinstance(required_tools_present_raw, bool)
        else False
    )

    payload = _read_mcp_client_state_file()
    clients = dict(payload.get("clients") or {})
    row = {
        "client": normalized,
        "last_seen_generation": generation,
        "last_tool_count": tool_count,
        "last_probe_ok": probe_ok,
        "last_fingerprint": installed_fp,
        "last_probe_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "required_tools_present": required_tools_present,
        "missing_required_tools": missing_required,
        "reason_code": reason_code,
        "client_action": "ready" if probe_ok else "reprobe",
    }
    clients[normalized] = row
    payload["clients"] = clients
    _write_mcp_client_state_file(payload)
    return {"ok": True, **row}


def fingerprint_cache_path() -> Path:
    """Where the runtime fingerprint cache lives.

    The cache lets `prime_process_fingerprint()` and `installed_runtime_fingerprint()`
    skip hashing 200+ source files on every MCP startup / tool call when the
    runtime tree on disk hasn't changed (same file count, same total size, same
    max mtime). Invalidates automatically when any source byte changes.
    """
    return paths.operations_dir() / "fingerprint-cache.json"


def _runtime_tree_signature(src_dir: Path) -> tuple[int, int, float] | None:
    """Cheap stat-only walk over the fingerprint-tracked tree.

    Returns ``(file_count, size_total, max_mtime)`` or ``None`` when the source
    tree cannot be traversed. This is the cache key — if it matches, the bytes
    haven't changed in any way the fingerprint would care about.
    """
    try:
        files = _iter_runtime_source_files(src_dir)
    except Exception:
        return None
    if not files:
        return None
    count = 0
    size_total = 0
    max_mtime = 0.0
    for path in files:
        try:
            st = path.stat()
        except Exception:
            return None
        count += 1
        size_total += int(st.st_size)
        if st.st_mtime > max_mtime:
            max_mtime = float(st.st_mtime)
    return (count, size_total, max_mtime)


def _read_fingerprint_cache(src_dir: Path) -> str:
    """Return cached fingerprint when the on-disk signature still matches.

    Empty string means cache miss (corrupt, missing, or signature drifted).
    Cache miss is always safe — caller falls through to a full hash.
    """
    cache_path = fingerprint_cache_path()
    if not cache_path.is_file():
        return ""
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    if str(payload.get("src_dir") or "") != str(src_dir):
        return ""
    sig = _runtime_tree_signature(src_dir)
    if sig is None:
        return ""
    try:
        cached_count = int(payload.get("file_count"))
        cached_size = int(payload.get("size_total"))
        cached_mtime = float(payload.get("max_mtime"))
    except (TypeError, ValueError):
        return ""
    if cached_count != sig[0] or cached_size != sig[1] or cached_mtime != sig[2]:
        return ""
    fingerprint = str(payload.get("fingerprint") or "").strip()
    return fingerprint


def _write_fingerprint_cache(src_dir: Path, fingerprint: str) -> None:
    """Persist the fingerprint+signature pair. Best-effort; failures don't propagate."""
    if not fingerprint:
        return
    sig = _runtime_tree_signature(src_dir)
    if sig is None:
        return
    payload = {
        "fingerprint": fingerprint,
        "src_dir": str(src_dir),
        "file_count": sig[0],
        "size_total": sig[1],
        "max_mtime": sig[2],
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        _write_json_atomic(fingerprint_cache_path(), payload)
    except Exception:
        pass


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


def installed_force_restart_flag() -> bool:
    """Read explicit `force_restart` opt-in from version.json/package.json.

    A release that touches behavior in subtle ways (config schema, runtime
    contract) but happens not to change any tracked MCP source byte can still
    force a restart by setting `force_restart: true` in version.json. Default
    is False — fingerprint is the source of truth.
    """
    for candidate in [active_runtime_root(), paths.home()]:
        for vfile in _candidate_version_files(candidate):
            try:
                if vfile.is_file():
                    payload = json.loads(vfile.read_text(encoding="utf-8"))
                    if isinstance(payload, dict) and bool(payload.get("force_restart")):
                        return True
            except Exception:
                continue
    return False


def _iter_runtime_source_files(src_dir: Path) -> list[Path]:
    """Return MCP-loaded `.py` files under `src_dir`, sorted by relative path."""
    out: list[Path] = []
    if not src_dir or not src_dir.is_dir():
        return out
    for path in src_dir.rglob("*.py"):
        try:
            rel = path.relative_to(src_dir)
        except ValueError:
            continue
        if any(seg in _FINGERPRINT_EXCLUDE_DIRS for seg in rel.parts):
            continue
        out.append(path)
    out.sort(key=lambda p: p.relative_to(src_dir).as_posix())
    return out


def compute_mcp_runtime_fingerprint(
    src_dir: Path | None = None, *, use_cache: bool = False
) -> str:
    """Hash of every Python source file the running MCP can import.

    Returns a sha256 hex digest, or "" when the source tree cannot be located
    or read (caller treats empty as "fingerprint unavailable" and falls back
    to the version-string mismatch check).

    Includes:
      * every `.py` under the runtime root
    Excludes:
      * subtrees in `_FINGERPRINT_EXCLUDE_DIRS` (scripts/, tests/, migrations/,
        crons/, __pycache__/, node_modules/, .git/)
      * non-`.py` assets (docs, blogs, READMEs, JSON/YAML configs, templates,
        CHANGELOG, marketing files) — these never affect what the live MCP
        process executes

    When ``use_cache=True`` (hot paths: server startup, every tool call) the
    function consults ``fingerprint-cache.json``: if the on-disk tree
    signature (file count + total size + max mtime) still matches the cached
    one, the cached digest is returned without re-reading any byte. Cache miss
    falls through to the normal full-hash path and writes a fresh entry. The
    update flow keeps ``use_cache=False`` (default) so it always sees ground
    truth around the pull/npm step.
    """
    if src_dir is None:
        candidates: list[Path] = []
        try:
            here = Path(__file__).resolve().parent
            candidates.append(here)
        except Exception:
            pass
        try:
            root = active_runtime_root()
            if root and root not in candidates:
                candidates.append(root)
        except Exception:
            pass
        try:
            home = paths.home()
            if home and home not in candidates:
                candidates.append(home)
        except Exception:
            pass
        for cand in candidates:
            if (cand / "server.py").is_file() or (cand / "cli.py").is_file():
                src_dir = cand
                break
        if src_dir is None:
            return ""

    if use_cache:
        cached = _read_fingerprint_cache(src_dir)
        if cached:
            return cached

    files = _iter_runtime_source_files(src_dir)
    if not files:
        return ""
    h = hashlib.sha256()
    for path in files:
        try:
            rel = path.relative_to(src_dir).as_posix()
        except ValueError:
            continue
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        try:
            h.update(path.read_bytes())
        except Exception:
            return ""
        h.update(b"\n")
    digest = h.hexdigest()
    if use_cache and digest:
        _write_fingerprint_cache(src_dir, digest)
    return digest


def installed_runtime_fingerprint() -> str:
    """Fingerprint of whatever runtime source tree is on disk right now.

    Hot path — runs on every MCP tool call via ``resolve_restart_required``.
    Uses the disk-signature cache so a repeated call without any source
    change is a few stat() syscalls instead of 200+ file reads.
    """
    candidates: list[Path] = []
    try:
        root = active_runtime_root()
        if root:
            candidates.append(root)
    except Exception:
        pass
    try:
        home = paths.home()
        if home and home not in candidates:
            candidates.append(home)
    except Exception:
        pass
    try:
        here = Path(__file__).resolve().parent
        if here not in candidates:
            candidates.append(here)
    except Exception:
        pass
    for cand in candidates:
        fp = compute_mcp_runtime_fingerprint(cand, use_cache=True)
        if fp:
            return fp
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
    client: str = "",
    from_fingerprint: str = "",
    to_fingerprint: str = "",
) -> dict:
    path = restart_required_marker_path()
    payload = {
        "schema_version": MCP_STATUS_SCHEMA_VERSION,
        "required": True,
        "from_version": str(from_version or "").strip(),
        "to_version": str(to_version or "").strip(),
        "from_fingerprint": str(from_fingerprint or "").strip(),
        "to_fingerprint": str(to_fingerprint or "").strip(),
        "reason": str(reason or "brain_update"),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "clients": _restart_clients_for_marker(client=client),
    }
    _write_json_atomic(path, payload)
    payload["path"] = str(path)
    return payload


def activate_versioned_runtime_snapshot(*, source_root: Path | None = None, version: str = "") -> dict:
    container = core_container_dir()
    source = Path(source_root or container)
    if source_root is None and source == container and core_current_link().exists():
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


def _runtime_version_sort_key(path: Path) -> tuple:
    parts = re.split(r"([0-9]+)", path.name)
    key = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part.lower()))
    return tuple(key)


def _active_version_name() -> str:
    current = core_current_link()
    if current.exists() or current.is_symlink():
        with contextlib.suppress(Exception):
            resolved = current.resolve(strict=False)
            if resolved.parent.name == "versions":
                return resolved.name
    return installed_runtime_version()


def prune_old_versioned_runtime_snapshots(*, keep: int = 2, active_version: str = "") -> dict:
    """Remove runtime snapshots older than the active + newest ``keep`` versions."""
    keep = max(int(keep or 0), 1)
    versions_dir = core_versions_dir()
    report = {
        "ok": True,
        "versions_dir": str(versions_dir),
        "keep": keep,
        "active_version": str(active_version or "").strip(),
        "kept": [],
        "pruned": [],
        "errors": [],
    }
    if not versions_dir.is_dir():
        return report

    snapshots = [item for item in versions_dir.iterdir() if item.is_dir() and not item.is_symlink()]
    snapshots.sort(key=_runtime_version_sort_key)
    active = str(active_version or _active_version_name() or "").strip()
    report["active_version"] = active

    keep_names = {item.name for item in snapshots[-keep:]}
    if active:
        keep_names.add(active)

    for snapshot in snapshots:
        if snapshot.name in keep_names:
            report["kept"].append(snapshot.name)
            continue
        try:
            shutil.rmtree(snapshot)
            report["pruned"].append(snapshot.name)
        except Exception as exc:
            report["ok"] = False
            report["errors"].append({"version": snapshot.name, "error": str(exc)})
    return report


def clear_restart_required_marker(
    *,
    client: str = "",
    installed_version: str = "",
    process_version: str = "",
    installed_fingerprint: str = "",
    process_fingerprint: str = "",
) -> dict:
    client = _normalize_restart_client(client)
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
    marker_to_fingerprint = str(payload.get("to_fingerprint") or "").strip()
    effective_installed_fp = str(
        installed_fingerprint or marker_to_fingerprint or ""
    ).strip()
    effective_process_fp = str(
        process_fingerprint or PROCESS_FINGERPRINT or ""
    ).strip()
    if pending_clients:
        _write_json_atomic(path, payload)
        return {"ok": True, "cleared": False, "path": str(path), "pending_clients": pending_clients}
    # Prefer fingerprint match when both sides have it. For markers that were
    # created with a target fingerprint, do not fall back to version-only
    # clearing: matching versions can still be a stale in-place source update.
    if (
        effective_installed_fp
        and effective_process_fp
        and effective_process_fp != "unknown"
    ):
        if effective_installed_fp != effective_process_fp:
            _write_json_atomic(path, payload)
            return {
                "ok": True,
                "cleared": False,
                "path": str(path),
                "pending_reason": "process_fingerprint_mismatch",
            }
    elif marker_to_fingerprint:
        _write_json_atomic(path, payload)
        return {
            "ok": True,
            "cleared": False,
            "path": str(path),
            "pending_reason": "process_fingerprint_missing",
        }
    elif effective_installed and effective_process and effective_installed != effective_process:
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


def resolve_restart_required(
    *,
    client: str = "",
    installed_version: str = "",
    process_version: str = "",
    installed_fingerprint: str = "",
    process_fingerprint: str = "",
) -> dict:
    client = _normalize_restart_client(client)
    marker = read_restart_required_marker()
    installed = str(installed_version or installed_runtime_version() or "").strip()
    process = str(process_version or PROCESS_VERSION or installed).strip()
    installed_fp = str(installed_fingerprint or installed_runtime_fingerprint() or "").strip()
    process_fp = str(process_fingerprint or PROCESS_FINGERPRINT or "").strip()
    marker_fp = str(marker.get("to_fingerprint") or "").strip()
    restart_required = False
    reason = ""
    client_action = ""
    marker_clients = dict(marker.get("clients") or {})
    fingerprint_usable = bool(installed_fp) and bool(process_fp) and process_fp != "unknown"

    if marker.get("required"):
        restart_required = True
        reason = "marker_required"
        client_action = str(marker_clients.get(client) or "")
    if marker.get("corrupt"):
        restart_required = True
        reason = "marker_corrupt"
    elif fingerprint_usable and installed_fp != process_fp:
        # Primary signal: the bytes the running process loaded differ from the
        # bytes currently on disk. Doc-only / blog-only releases produce no
        # fingerprint change and therefore never reach this branch.
        restart_required = True
        reason = reason or "fingerprint_mismatch"
    elif marker.get("required") and marker_fp and (not process_fp or process_fp == "unknown"):
        restart_required = True
        reason = reason or "process_fingerprint_missing"
    elif not fingerprint_usable and installed and process and installed != process:
        # Fallback: when fingerprint can't be computed (missing source tree,
        # unreadable files, fresh install), fall back to the legacy version
        # mismatch check so we never leave a stale process running unnoticed.
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
        "installed_fingerprint": installed_fp,
        "process_fingerprint": process_fp,
    }


def _mcp_client_readiness(
    *,
    client: str,
    state: dict,
    installed_version_value: str,
    installed_fp: str,
    service_status: dict,
) -> dict:
    generation = runtime_generation(installed_version_value, installed_fp, str(active_runtime_root()))
    process_fp = str(state.get("process_fingerprint") or "").strip()
    service_ok = bool(service_status.get("ok", True))
    fingerprint_ready = (
        bool(installed_fp)
        and bool(process_fp)
        and process_fp != "unknown"
        and installed_fp == process_fp
    )
    global_ready = (
        not bool(state.get("restart_required"))
        and fingerprint_ready
        and service_ok
    )
    if not service_ok:
        global_reason = "runtime_service_unavailable"
    elif not installed_fp:
        global_reason = "installed_fingerprint_missing"
    elif not process_fp or process_fp == "unknown":
        global_reason = "process_fingerprint_missing"
    elif installed_fp != process_fp:
        global_reason = "process_fingerprint_mismatch"
    else:
        global_reason = "ready"
    if state.get("restart_required"):
        return {
            "runtime_generation": generation,
            "global_ready": False,
            "client_ready": False,
            "reason_code": str(state.get("reason") or "restart_required"),
            "client_action": str(state.get("client_action") or "restart_client"),
            "client_state": {},
        }
    if not client:
        return {
            "runtime_generation": generation,
            "global_ready": global_ready,
            "client_ready": global_ready,
            "reason_code": "ready" if global_ready else global_reason,
            "client_action": "ready" if global_ready else "reprobe",
            "client_state": {},
        }
    if not global_ready:
        return {
            "runtime_generation": generation,
            "global_ready": False,
            "client_ready": False,
            "reason_code": global_reason,
            "client_action": "reprobe",
            "client_state": {},
        }

    registry = read_mcp_client_states()
    client_state = dict((registry.get("clients") or {}).get(client) or {})
    if not client_state:
        return {
            "runtime_generation": generation,
            "global_ready": global_ready,
            "client_ready": False,
            "reason_code": "client_probe_missing",
            "client_action": "reprobe",
            "client_state": {},
        }
    if str(client_state.get("last_seen_generation") or "") != generation:
        return {
            "runtime_generation": generation,
            "global_ready": global_ready,
            "client_ready": False,
            "reason_code": "client_generation_stale",
            "client_action": "reprobe",
            "client_state": client_state,
        }
    if not client_state.get("last_probe_ok"):
        return {
            "runtime_generation": generation,
            "global_ready": global_ready,
            "client_ready": False,
            "reason_code": str(client_state.get("reason_code") or "client_probe_failed"),
            "client_action": str(client_state.get("client_action") or "reprobe"),
            "client_state": client_state,
        }
    return {
        "runtime_generation": generation,
        "global_ready": global_ready,
        "client_ready": bool(global_ready),
        "reason_code": "ready" if global_ready else global_reason,
        "client_action": "ready" if global_ready else "reprobe",
        "client_state": client_state,
    }


def build_mcp_status(*, client: str = "") -> dict:
    client = _normalize_restart_client(client)
    state = resolve_restart_required(client=client)
    marker = state["marker"]
    installed_fp = state.get("installed_fingerprint", "")
    process_fp = state.get("process_fingerprint", "")
    try:
        from runtime_service import runtime_service_status

        service_status = runtime_service_status()
    except Exception as exc:
        service_status = {
            "ok": False,
            "error": "runtime_service_status_unavailable",
            "message": str(exc)[:300],
        }
    readiness = _mcp_client_readiness(
        client=client,
        state=state,
        installed_version_value=state["installed_version"],
        installed_fp=installed_fp,
        service_status=service_status,
    )
    client_states = read_mcp_client_states()
    return {
        "ok": True,
        "schema_version": MCP_STATUS_SCHEMA_VERSION,
        "client": str(client or "").strip(),
        "installed_version": state["installed_version"],
        "process_version": state["process_version"],
        "installed_fingerprint": installed_fp,
        "process_fingerprint": process_fp,
        "fingerprint_match": (
            bool(installed_fp)
            and bool(process_fp)
            and process_fp != "unknown"
            and installed_fp == process_fp
        ),
        "active_runtime_root": str(active_runtime_root()),
        "active_runtime_version": read_version_for_path(active_runtime_root()),
        "restart_required": bool(state["restart_required"]),
        "reason": state["reason"],
        "client_action": readiness["client_action"],
        "reason_code": readiness["reason_code"],
        "global_ready": bool(readiness["global_ready"]),
        "client_ready": bool(readiness["client_ready"]),
        "runtime_generation": readiness["runtime_generation"],
        "client_state": readiness["client_state"],
        "last_seen_generation": readiness["client_state"].get("last_seen_generation", ""),
        "last_tool_count": readiness["client_state"].get("last_tool_count", 0),
        "last_probe_ok": readiness["client_state"].get("last_probe_ok", False),
        "last_fingerprint": readiness["client_state"].get("last_fingerprint", ""),
        "client_states": client_states.get("clients", {}),
        "client_state_path": client_states.get("path", str(mcp_client_state_path())),
        "marker_path": marker.get("path", str(restart_required_marker_path())),
        "marker_exists": bool(marker.get("exists")),
        "marker_corrupt": bool(marker.get("corrupt")),
        "continuity_api_level": CONTINUITY_API_LEVEL,
        "runtime_service": service_status,
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


def prime_process_fingerprint() -> str:
    """Cache the fingerprint of the source tree this process was loaded from.

    Idempotent. Called once at MCP server startup. After that, the cached
    value reflects what the live process has actually imported, regardless of
    what is later written to disk by `nexo update`.

    Returns the cached digest (sha256 hex) or the literal string `"unknown"`
    when the source tree cannot be located/read at startup time.
    """
    global PROCESS_FINGERPRINT
    if PROCESS_FINGERPRINT:
        return PROCESS_FINGERPRINT
    candidates: list[Path] = []
    try:
        here = Path(__file__).resolve().parent
        candidates.append(here)
    except Exception:
        pass
    try:
        root = active_runtime_root()
        if root and root not in candidates:
            candidates.append(root)
    except Exception:
        pass
    try:
        home = paths.home()
        if home and home not in candidates:
            candidates.append(home)
    except Exception:
        pass
    for cand in candidates:
        fp = compute_mcp_runtime_fingerprint(cand, use_cache=True)
        if fp:
            PROCESS_FINGERPRINT = fp
            return PROCESS_FINGERPRINT
    PROCESS_FINGERPRINT = "unknown"
    return PROCESS_FINGERPRINT


_DRIFT_AUTOEXIT_SCHEDULED = False
_DRIFT_EXIT_CODE = 75
_DRIFT_EXIT_DELAY_SECONDS = 0.5


def _request_drift_exit() -> None:
    try:
        os._exit(_DRIFT_EXIT_CODE)
    except Exception:
        os._exit(1)


def _schedule_drift_autoexit() -> None:
    global _DRIFT_AUTOEXIT_SCHEDULED
    if _DRIFT_AUTOEXIT_SCHEDULED:
        return
    _DRIFT_AUTOEXIT_SCHEDULED = True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _request_drift_exit()
        return
    loop.call_later(_DRIFT_EXIT_DELAY_SECONDS, _request_drift_exit)


@dataclass
class RestartRequiredMiddleware(Middleware):
    client: str = ""

    def __post_init__(self) -> None:
        self.client = _normalize_restart_client(self.client)

    def _ack_current_client_if_restarted(self, state: dict) -> dict:
        if not self.client or not state.get("restart_required"):
            return state
        installed = str(state.get("installed_version") or "").strip()
        process = str(state.get("process_version") or "").strip()
        installed_fp = str(state.get("installed_fingerprint") or "").strip()
        process_fp = str(state.get("process_fingerprint") or "").strip()
        fingerprint_usable = (
            bool(installed_fp) and bool(process_fp) and process_fp != "unknown"
        )
        if fingerprint_usable:
            if installed_fp != process_fp:
                return state
        else:
            if not installed or not process or installed != process:
                return state

        clear_restart_required_marker(
            client=self.client,
            installed_version=installed,
            process_version=process,
            installed_fingerprint=installed_fp,
            process_fingerprint=process_fp,
        )
        return resolve_restart_required(
            client=self.client,
            installed_version=installed,
            process_version=process,
            installed_fingerprint=installed_fp,
            process_fingerprint=process_fp,
        )

    async def _tool_result_for_restart_required(self, context, payload: dict) -> ToolResult:
        payload_text = json.dumps(payload, ensure_ascii=False)
        tool = None
        try:
            fastmcp_context = getattr(context, "fastmcp_context", None)
            fastmcp_server = getattr(fastmcp_context, "fastmcp", None)
            if fastmcp_server is not None:
                tool = await fastmcp_server.get_tool(str(getattr(context.message, "name", "") or "").strip())
        except Exception:
            tool = None

        output_schema = getattr(tool, "output_schema", None)
        if isinstance(output_schema, dict) and output_schema.get("x-fastmcp-wrap-result"):
            return ToolResult(
                content=payload_text,
                structured_content={"result": payload_text},
            )
        return ToolResult(
            content=payload_text,
            structured_content=payload,
        )

    async def on_call_tool(self, context, call_next):
        tool_name = str(getattr(context.message, "name", "") or "").strip()
        state = resolve_restart_required(client=self.client)
        state = self._ack_current_client_if_restarted(state)
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
        result = await self._tool_result_for_restart_required(context, payload)
        if state.get("reason") == "fingerprint_mismatch":
            _schedule_drift_autoexit()
        return result
