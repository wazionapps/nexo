from __future__ import annotations
"""Resident runtime service and MCP proxy bootstrap.

The public MCP entrypoint remains ``server.py`` for compatibility.  By
default, that entrypoint becomes a thin stdio proxy and forwards calls to a
single resident FastMCP service over loopback HTTP.  The resident process is
the only MCP process that initializes Brain, opens SQLite, and runs tool
handlers.
"""

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import paths

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17872
PORT_SCAN_LIMIT = 30
SERVICE_PATH = "/mcp"
SERVICE_ENV = "NEXO_RUNTIME_SERVICE"
DIRECT_ENV = "NEXO_MCP_DIRECT"
ADAPTER_ENV = "NEXO_MCP_RUNTIME_ADAPTER"
STATE_FILE = "runtime-service.json"
LOCK_FILE = "runtime-service.lock"
LOG_FILE = "runtime-service.log"


def env_flag(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "si"}


def service_host() -> str:
    return str(os.environ.get("NEXO_RUNTIME_HOST", DEFAULT_HOST) or DEFAULT_HOST).strip()


def service_path() -> str:
    raw = str(os.environ.get("NEXO_RUNTIME_MCP_PATH", SERVICE_PATH) or SERVICE_PATH).strip()
    return raw if raw.startswith("/") else f"/{raw}"


def service_url(host: str | None = None, port: int | None = None, path: str | None = None) -> str:
    return f"http://{host or service_host()}:{int(port or service_port())}{path or service_path()}"


def service_state_path() -> Path:
    root = paths.runtime_state_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root / STATE_FILE


def service_log_path() -> Path:
    root = paths.logs_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root / LOG_FILE


def service_lock_path() -> Path:
    root = paths.runtime_state_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root / LOCK_FILE


@contextmanager
def service_start_lock(*, timeout: float = 10.0):
    path = service_lock_path()
    handle = path.open("a+")
    deadline = time.monotonic() + max(timeout, 0.5)
    locked = False
    try:
        while not locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    if not handle.read(1):
                        handle.write("0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for NEXO runtime service lock: {path}")
                time.sleep(0.1)
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}:{time.time()}\n")
        handle.flush()
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        try:
            handle.close()
        except Exception:
            pass


def read_service_state() -> dict[str, Any]:
    try:
        path = service_state_path()
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_service_state(state: dict[str, Any]) -> None:
    path = service_state_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = dict(state)
    payload.update(current_runtime_identity())
    payload["updated_at"] = time.time()
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def is_runtime_service_process() -> bool:
    return env_flag(SERVICE_ENV)


def should_use_mcp_adapter() -> bool:
    if is_runtime_service_process():
        return False
    if env_flag(DIRECT_ENV):
        return False
    if not env_flag(ADAPTER_ENV, default=True):
        return False
    transport = str(os.environ.get("NEXO_MCP_TRANSPORT", "stdio") or "stdio").strip().lower()
    return transport == "stdio"


def service_port() -> int:
    raw = os.environ.get("NEXO_RUNTIME_PORT")
    if raw:
        try:
            return int(raw)
        except Exception:
            pass
    state = read_service_state()
    try:
        port = int(state.get("port") or 0)
        if port > 0:
            return port
    except Exception:
        pass
    return DEFAULT_PORT


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def choose_service_port(host: str | None = None) -> int:
    host = host or service_host()
    preferred = service_port()
    for offset in range(PORT_SCAN_LIMIT):
        port = preferred + offset
        if _port_is_free(host, port):
            return port
    raise RuntimeError(f"No free NEXO runtime service port in range {preferred}-{preferred + PORT_SCAN_LIMIT - 1}")


async def _probe_service_async(url: str, *, timeout: float = 1.5) -> bool:
    from fastmcp import Client

    try:
        client = Client(url, timeout=timeout, init_timeout=timeout)
        async with client:
            return bool(await client.ping())
    except Exception:
        return False


def probe_service(url: str, *, timeout: float = 1.5) -> bool:
    try:
        return bool(asyncio.run(_probe_service_async(url, timeout=timeout)))
    except RuntimeError:
        # If an event loop is already active, fall back to a tiny socket probe.
        try:
            host_port = url.split("//", 1)[1].split("/", 1)[0]
            host, port_text = host_port.rsplit(":", 1)
            with socket.create_connection((host, int(port_text)), timeout=timeout):
                return True
        except Exception:
            return False


def current_server_path() -> Path:
    return Path(__file__).resolve().with_name("server.py")


def current_runtime_identity() -> dict[str, str]:
    try:
        from runtime_versioning import compute_mcp_runtime_fingerprint, read_version_for_path

        root = current_server_path().parent
        version = read_version_for_path(root) or read_version_for_path(root.parent)
        return {
            "runtime_version": version,
            "runtime_fingerprint": compute_mcp_runtime_fingerprint(root, use_cache=True),
            "server_path": str(current_server_path()),
        }
    except Exception:
        return {"runtime_version": "", "runtime_fingerprint": "", "server_path": str(current_server_path())}


def state_matches_current_runtime(state: dict[str, Any]) -> bool:
    if not state:
        return False
    current = current_runtime_identity()
    state_server = str(state.get("server_path") or "").strip()
    if state_server and state_server != current["server_path"]:
        return False

    current_fp = str(current.get("runtime_fingerprint") or "").strip()
    state_fp = str(state.get("runtime_fingerprint") or "").strip()
    if current_fp and state_fp and current_fp != state_fp:
        return False

    current_version = str(current.get("runtime_version") or "").strip()
    state_version = str(state.get("runtime_version") or "").strip()
    if current_version and state_version and current_version != state_version:
        return False
    return True


def _terminate_pid(pid: int, *, timeout: float = 3.0) -> dict[str, Any]:
    if pid <= 0:
        return {"terminated": False, "reason": "no_pid"}
    if not pid_is_running(pid):
        return {"terminated": False, "reason": "not_running"}
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=max(timeout, 1.0),
            )
        else:
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + max(timeout, 0.2)
            while time.monotonic() < deadline:
                if not pid_is_running(pid):
                    return {"terminated": True, "pid": pid, "signal": "SIGTERM"}
                time.sleep(0.1)
            if hasattr(signal, "SIGKILL"):
                os.kill(pid, signal.SIGKILL)
        return {"terminated": True, "pid": pid}
    except Exception as exc:
        return {"terminated": False, "pid": pid, "error": str(exc)[:300]}


def stop_runtime_service(*, reason: str = "stop", timeout: float = 3.0) -> dict[str, Any]:
    state = read_service_state()
    pid = int(state.get("pid") or 0) if str(state.get("pid") or "").isdigit() else 0
    result = _terminate_pid(pid, timeout=timeout)
    result["reason"] = reason
    result["state_path"] = str(service_state_path())
    try:
        service_state_path().unlink(missing_ok=True)
        result["state_removed"] = True
    except Exception as exc:
        result["state_removed"] = False
        result["state_error"] = str(exc)[:300]
    return result


def _service_env(port: int, host: str) -> dict[str, str]:
    env = os.environ.copy()
    env[SERVICE_ENV] = "1"
    env["NEXO_MCP_TRANSPORT"] = "streamable-http"
    env["NEXO_MCP_HOST"] = host
    env["NEXO_MCP_PORT"] = str(port)
    env["NEXO_MCP_PATH"] = service_path()
    # A probe client may inherit a deliberately tiny plugin mode.  The service
    # should use the normal runtime defaults unless explicitly overridden.
    if "NEXO_RUNTIME_SERVICE_PLUGIN_MODE" in env:
        env["NEXO_MCP_PLUGIN_MODE"] = env["NEXO_RUNTIME_SERVICE_PLUGIN_MODE"]
    return env


def _spawn_service_process(port: int, host: str) -> subprocess.Popen:
    log_path = service_log_path()
    log_file = open(log_path, "ab", buffering=0)
    kwargs: dict[str, Any] = {
        "cwd": str(current_server_path().parent),
        "env": _service_env(port, host),
        "stdin": subprocess.DEVNULL,
        "stdout": log_file,
        "stderr": log_file,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen([sys.executable, str(current_server_path())], **kwargs)


def ensure_runtime_service(*, wait_seconds: float = 10.0) -> str:
    with service_start_lock(timeout=wait_seconds):
        host = service_host()
        state = read_service_state()
        state_url = str(state.get("url") or "")
        state_pid = int(state.get("pid") or 0) if str(state.get("pid") or "").isdigit() else 0
        if state_url and (state_pid <= 0 or pid_is_running(state_pid)):
            if state_matches_current_runtime(state) and probe_service(state_url):
                return state_url
            if state_pid > 0:
                stop_runtime_service(reason="stale_runtime")

        port = choose_service_port(host)
        url = service_url(host, port)
        proc = _spawn_service_process(port, host)
        write_service_state(
            {
                "pid": proc.pid,
                "port": port,
                "host": host,
                "path": service_path(),
                "url": url,
                "server_path": str(current_server_path()),
                "started_at": time.time(),
                "mode": "runtime-service",
            }
        )

        deadline = time.monotonic() + max(wait_seconds, 0.5)
        delay = 0.15
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            if probe_service(url):
                return url
            time.sleep(delay)
            delay = min(delay * 1.5, 1.0)

        code = proc.poll()
        raise RuntimeError(
            "NEXO runtime service did not become ready"
            + (f" (exit={code})" if code is not None else "")
            + f"; log={service_log_path()}"
        )


def runtime_service_status() -> dict[str, Any]:
    state = read_service_state()
    current = current_runtime_identity()
    url = str(state.get("url") or "")
    pid = int(state.get("pid") or 0) if str(state.get("pid") or "").isdigit() else 0
    alive = pid_is_running(pid)
    ready = bool(url and probe_service(url, timeout=0.8))
    return {
        "ok": ready,
        "mode": "service" if is_runtime_service_process() else "adapter",
        "pid": pid,
        "pid_alive": alive,
        "url": url,
        "stale": bool(state and not state_matches_current_runtime(state)),
        "runtime_version": current.get("runtime_version", ""),
        "runtime_fingerprint": current.get("runtime_fingerprint", ""),
        "state_runtime_version": str(state.get("runtime_version") or ""),
        "state_runtime_fingerprint": str(state.get("runtime_fingerprint") or ""),
        "state_path": str(service_state_path()),
        "log_path": str(service_log_path()),
        "server_path": str(current_server_path()),
    }


def run_mcp_proxy_adapter(*, name: str, instructions: str, run_kwargs: dict[str, Any]) -> None:
    from fastmcp.server import create_proxy

    url = ensure_runtime_service()
    proxy = create_proxy(url, name=name, instructions=instructions)
    proxy.run(**run_kwargs)
