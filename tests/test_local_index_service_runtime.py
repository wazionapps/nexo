from __future__ import annotations

import importlib.util
import json
import os
import time
import uuid
from pathlib import Path

import db
from local_context import api
from local_context.util import now


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "scripts" / "nexo-local-index.py"


def _load_script(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo-home"))
    spec = importlib.util.spec_from_file_location(f"nexo_local_index_{uuid.uuid4().hex}", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_local_index_lock_recovers_dead_pid(tmp_path, monkeypatch):
    module = _load_script(tmp_path, monkeypatch)
    module.LOCK_FILE.write_text(json.dumps({"pid": 99999999, "created_at": time.time()}), encoding="utf-8")
    monkeypatch.setattr(module, "_pid_running", lambda pid: False)

    assert module.acquire_lock() is True

    lock = json.loads(module.LOCK_FILE.read_text(encoding="utf-8"))
    assert lock["pid"] == os.getpid()
    assert "dead pid 99999999" in module.LOG_FILE.read_text(encoding="utf-8")
    module.release_lock()


def test_local_index_run_cycle_falls_back_for_mixed_runtime_versions(tmp_path, monkeypatch):
    module = _load_script(tmp_path, monkeypatch)
    calls = []
    events = []

    def fake_run_once(**kwargs):
        calls.append(kwargs)
        if "live_asset_limit" in kwargs:
            raise TypeError("run_once() got an unexpected keyword argument 'live_asset_limit'")
        return {"ok": True, "scan": {"seen": 1}, "jobs": {"processed": 1}}

    monkeypatch.setattr(module.api, "run_once", fake_run_once)
    monkeypatch.setattr(module, "log_event", lambda *args, **kwargs: events.append((args, kwargs)))

    result = module._run_index_cycle()

    assert result["ok"] is True
    assert "live_asset_limit" in calls[0]
    assert calls[1] == {"limit": module.SCAN_LIMIT, "process_limit": module.PROCESS_LIMIT}
    assert any(args[1] == "service_cycle_compat_fallback" for args, _ in events)


def test_status_surfaces_unrecovered_service_cycle_failure():
    conn = db.get_db()
    conn.execute(
        """
        INSERT INTO local_index_logs(created_at, level, event, message, metadata_json)
        VALUES (?, 'error', 'service_cycle_failed', 'Local memory service cycle failed', ?)
        """,
        (now(), json.dumps({"error": "TypeError"})),
    )
    conn.commit()

    problems = api.status()["problems"]

    assert any(problem["support_code"] == "service_cycle_failed" for problem in problems)


def test_status_hides_service_cycle_failure_after_success():
    conn = db.get_db()
    failure_at = now()
    conn.execute(
        """
        INSERT INTO local_index_logs(created_at, level, event, message, metadata_json)
        VALUES (?, 'error', 'service_cycle_failed', 'Local memory service cycle failed', ?)
        """,
        (failure_at, json.dumps({"error": "TypeError"})),
    )
    conn.execute(
        """
        INSERT INTO local_index_logs(created_at, level, event, message, metadata_json)
        VALUES (?, 'info', 'service_cycle_finished', 'Local memory service cycle finished', '{}')
        """,
        (failure_at + 1,),
    )
    conn.commit()

    problems = api.status()["problems"]

    assert not any(problem["support_code"] == "service_cycle_failed" for problem in problems)
