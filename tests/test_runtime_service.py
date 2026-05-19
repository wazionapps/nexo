from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_runtime_service(monkeypatch, home: Path):
    monkeypatch.setenv("NEXO_HOME", str(home))
    sys.modules.pop("paths", None)
    sys.modules.pop("runtime_service", None)
    import runtime_service

    return importlib.reload(runtime_service)


def test_mcp_adapter_is_default_only_for_stdio(monkeypatch, tmp_path):
    runtime_service = _reload_runtime_service(monkeypatch, tmp_path)

    monkeypatch.delenv("NEXO_RUNTIME_SERVICE", raising=False)
    monkeypatch.delenv("NEXO_MCP_DIRECT", raising=False)
    monkeypatch.delenv("NEXO_MCP_RUNTIME_ADAPTER", raising=False)
    monkeypatch.delenv("NEXO_MCP_TRANSPORT", raising=False)
    assert runtime_service.should_use_mcp_adapter() is True

    monkeypatch.setenv("NEXO_MCP_TRANSPORT", "streamable-http")
    assert runtime_service.should_use_mcp_adapter() is False

    monkeypatch.setenv("NEXO_MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("NEXO_MCP_DIRECT", "1")
    assert runtime_service.should_use_mcp_adapter() is False

    monkeypatch.delenv("NEXO_MCP_DIRECT", raising=False)
    monkeypatch.setenv("NEXO_RUNTIME_SERVICE", "1")
    assert runtime_service.should_use_mcp_adapter() is False


def test_service_state_roundtrip_is_under_runtime_state(monkeypatch, tmp_path):
    runtime_service = _reload_runtime_service(monkeypatch, tmp_path)
    expected_version = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))["version"]

    runtime_service.write_service_state({"pid": 123, "port": 17872, "url": "http://127.0.0.1:17872/mcp"})
    path = runtime_service.service_state_path()

    assert path == tmp_path / "runtime" / "state" / "runtime-service.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["pid"] == 123
    assert data["port"] == 17872
    assert data["url"] == "http://127.0.0.1:17872/mcp"
    assert data["server_path"].endswith("server.py")
    assert data["runtime_version"] == expected_version
    assert "runtime_fingerprint" in data
    assert data["runtime_generation"]
    assert data["runtime_instance_id"].startswith("rt-")
    assert runtime_service.read_service_state()["pid"] == 123


def test_service_start_lock_is_under_runtime_state(monkeypatch, tmp_path):
    runtime_service = _reload_runtime_service(monkeypatch, tmp_path)

    with runtime_service.service_start_lock(timeout=1):
        path = runtime_service.service_lock_path()
        assert path == tmp_path / "runtime" / "state" / "runtime-service.lock"
        assert path.exists()


def test_state_matching_rejects_stale_runtime_fingerprint(monkeypatch, tmp_path):
    runtime_service = _reload_runtime_service(monkeypatch, tmp_path)
    monkeypatch.setattr(
        runtime_service,
        "current_runtime_identity",
        lambda: {
            "runtime_version": "1.0.1",
            "runtime_fingerprint": "new",
            "server_path": "/runtime/server.py",
        },
    )

    assert runtime_service.state_matches_current_runtime(
        {
            "runtime_version": "1.0.1",
            "runtime_fingerprint": "old",
            "server_path": "/runtime/server.py",
        }
    ) is False
    assert runtime_service.state_matches_current_runtime(
        {
            "runtime_version": "1.0.1",
            "runtime_fingerprint": "new",
            "server_path": "/runtime/server.py",
        }
    ) is True


def test_ensure_runtime_service_reuses_ready_state(monkeypatch, tmp_path):
    runtime_service = _reload_runtime_service(monkeypatch, tmp_path)
    runtime_service.write_service_state({"pid": os.getpid(), "port": 17872, "url": "http://127.0.0.1:17872/mcp"})
    monkeypatch.setattr(runtime_service, "probe_service", lambda url, **kwargs: url.endswith("/mcp"))

    assert runtime_service.ensure_runtime_service() == "http://127.0.0.1:17872/mcp"


def test_ensure_runtime_service_stops_stale_state_before_spawn(monkeypatch, tmp_path):
    runtime_service = _reload_runtime_service(monkeypatch, tmp_path)
    runtime_service.write_service_state({"pid": os.getpid(), "port": 17872, "url": "http://127.0.0.1:17872/mcp"})
    calls = {"stopped": 0, "spawned": 0}

    monkeypatch.setattr(runtime_service, "state_matches_current_runtime", lambda state: False)
    monkeypatch.setattr(runtime_service, "probe_service", lambda url, **kwargs: calls.setdefault("probed", url) and True)
    monkeypatch.setattr(runtime_service, "choose_service_port", lambda host=None: 18002)

    def fake_stop(**kwargs):
        calls["stopped"] += 1
        return {"terminated": True}

    class FakeProc:
        pid = 789

        def poll(self):
            return None

    def fake_spawn(port, host):
        calls["spawned"] += 1
        return FakeProc()

    monkeypatch.setattr(runtime_service, "stop_runtime_service", fake_stop)
    monkeypatch.setattr(runtime_service, "_spawn_service_process", fake_spawn)

    assert runtime_service.ensure_runtime_service() == "http://127.0.0.1:18002/mcp"
    assert calls["stopped"] == 1
    assert calls["spawned"] == 1


def test_ensure_runtime_service_spawns_with_service_env(monkeypatch, tmp_path):
    runtime_service = _reload_runtime_service(monkeypatch, tmp_path)
    captured = {}

    monkeypatch.setattr(runtime_service, "probe_service", lambda url, **kwargs: captured.setdefault("probed", url) and True)
    monkeypatch.setattr(runtime_service, "choose_service_port", lambda host=None: 18001)

    class FakeProc:
        pid = 456

        def poll(self):
            return None

    def fake_spawn(port, host):
        env = runtime_service._service_env(port, host)
        captured["env"] = env
        captured["port"] = port
        captured["host"] = host
        return FakeProc()

    monkeypatch.setattr(runtime_service, "_spawn_service_process", fake_spawn)

    url = runtime_service.ensure_runtime_service()

    assert url == "http://127.0.0.1:18001/mcp"
    assert captured["env"]["NEXO_RUNTIME_SERVICE"] == "1"
    assert captured["env"]["NEXO_MCP_TRANSPORT"] == "streamable-http"
    assert captured["env"]["NEXO_MCP_PORT"] == "18001"


def test_runtime_service_status_reports_probe_result(monkeypatch, tmp_path):
    runtime_service = _reload_runtime_service(monkeypatch, tmp_path)
    runtime_service.write_service_state({"pid": os.getpid(), "port": 17872, "url": "http://127.0.0.1:17872/mcp"})
    monkeypatch.setattr(runtime_service, "probe_service", lambda url, **kwargs: True)

    status = runtime_service.runtime_service_status()

    assert status["ok"] is True
    assert status["pid_alive"] is True
    assert status["url"] == "http://127.0.0.1:17872/mcp"
    assert status["runtime_generation"]
    assert status["runtime_instance_id"].startswith("rt-")
    assert status["state_runtime_generation"]
