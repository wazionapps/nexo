from __future__ import annotations

import importlib.util
import json
import os
import time
import uuid
from pathlib import Path

import db
import local_context
from local_context import api
from local_context.util import norm_path, now


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


def test_local_index_skip_lock_is_visible_in_status_log(tmp_path, monkeypatch):
    module = _load_script(tmp_path, monkeypatch)
    events = []
    monkeypatch.setattr(module, "acquire_lock", lambda: False)
    monkeypatch.setattr(module, "_log_event_best_effort", lambda *args, **kwargs: events.append((args, kwargs)))

    assert module.main() == 0

    assert "previous local-index cycle is still running" in module.LOG_FILE.read_text(encoding="utf-8")
    assert any(args[1] == "service_cycle_skipped_lock" for args, _ in events)


def test_local_index_file_log_survives_db_logging_failure(tmp_path, monkeypatch):
    module = _load_script(tmp_path, monkeypatch)
    monkeypatch.setattr(module.api, "ensure_default_roots", lambda: None)
    monkeypatch.setattr(module, "_run_index_cycle", lambda: (_ for _ in ()).throw(RuntimeError("db locked")))
    monkeypatch.setattr(module, "log_event", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("log db locked")))

    assert module.main() == 2

    text = module.LOG_FILE.read_text(encoding="utf-8")
    assert "ERROR: RuntimeError: db locked" in text
    assert "failed to record local-index event service_cycle_failed" in text


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


def test_process_jobs_retries_due_failed_jobs(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    note = root / "retry.txt"
    note.write_text("retry this document", encoding="utf-8")
    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=0)
    conn = db.get_db()
    job = conn.execute("SELECT job_id FROM local_index_jobs WHERE status='pending' LIMIT 1").fetchone()
    assert job is not None
    conn.execute(
        "UPDATE local_index_jobs SET status='failed', next_attempt_at=?, last_error_code='Transient' WHERE job_id=?",
        (now() - 1, job["job_id"]),
    )
    conn.commit()

    result = api.process_jobs(limit=5)

    assert result["recovered"]["failed"] == 1
    row = conn.execute("SELECT status FROM local_index_jobs WHERE job_id=?", (job["job_id"],)).fetchone()
    assert row["status"] == "done"


def test_process_jobs_recovers_expired_running_lease(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    note = root / "expired.txt"
    note.write_text("expired running lease", encoding="utf-8")
    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=0)
    conn = db.get_db()
    job = conn.execute("SELECT job_id FROM local_index_jobs WHERE status='pending' LIMIT 1").fetchone()
    assert job is not None
    conn.execute(
        "UPDATE local_index_jobs SET status='running', lease_expires_at=?, claimed_by='dead-worker' WHERE job_id=?",
        (now() - 1, job["job_id"]),
    )
    conn.commit()

    result = api.process_jobs(limit=5)

    assert result["recovered"]["expired"] == 1
    row = conn.execute("SELECT status FROM local_index_jobs WHERE job_id=?", (job["job_id"],)).fetchone()
    assert row["status"] == "done"


def test_status_counts_failed_and_running_jobs_as_pending_work(tmp_path, monkeypatch):
    root = tmp_path / "docs"
    root.mkdir()
    note = root / "pending.txt"
    note.write_text("pending work", encoding="utf-8")
    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=0)
    conn = db.get_db()
    jobs = conn.execute("SELECT job_id FROM local_index_jobs WHERE status='pending' LIMIT 2").fetchall()
    assert len(jobs) >= 2
    conn.execute("UPDATE local_index_jobs SET status='failed', next_attempt_at=? WHERE job_id=?", (now() + 3600, jobs[0]["job_id"]))
    conn.execute("UPDATE local_index_jobs SET status='running', lease_expires_at=? WHERE job_id=?", (now() + 300, jobs[1]["job_id"]))
    conn.commit()
    monkeypatch.setattr(api, "_local_index_service_status", lambda: {"installed": True, "running": True, "manager": "test", "platform": "test"})

    status = api.status()

    assert status["global"]["changes_pending"] >= 2
    assert status["global"]["jobs_failed"] == 1
    assert status["global"]["jobs_running"] == 1
    assert status["global"]["phase"] == "initial_indexing"


def test_status_marks_macos_loaded_service_with_failed_exit_as_problem(tmp_path, monkeypatch):
    home = tmp_path / "home"
    launch_agents = home / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    (launch_agents / "com.nexo.local-index.plist").write_text("<plist />", encoding="utf-8")

    monkeypatch.setattr(api.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(api, "system_label", lambda: "macos")
    monkeypatch.setattr(api, "_process_running", lambda pattern: False)
    monkeypatch.setattr(
        api,
        "_command_output",
        lambda args, **kwargs: (
            0,
            "-\t2\tcom.nexo.local-index\n" if args == ["launchctl", "list"] else "",
            "",
        ),
    )

    result = api.status()

    assert result["service"]["last_exit_code"] == "2"
    assert result["service"]["healthy"] is False
    assert any(problem["support_code"] == "local_index_service_last_run_failed" for problem in result["problems"])


def test_status_marks_windows_task_last_result_as_problem(monkeypatch):
    monkeypatch.setattr(api, "system_label", lambda: "windows")
    monkeypatch.setattr(api, "_command_output", lambda args, **kwargs: (0, "Ready|1|2026-05-12T19:00:00Z|\n", ""))
    monkeypatch.setattr(api, "_process_running", lambda pattern: False)

    result = api.status()

    assert result["service"]["last_task_result"] == "1"
    assert result["service"]["healthy"] is False
    assert any(problem["support_code"] == "local_index_service_last_run_failed" for problem in result["problems"])


def test_run_once_creates_default_roots_for_mcp_and_cli_paths(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("NEXO_SKIP_FS_INDEX", "0")
    monkeypatch.delenv("NEXO_LOCAL_INDEX_DEFAULT_ROOTS", raising=False)
    monkeypatch.setattr(api.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(api, "_system_volume_roots", lambda: [])
    monkeypatch.setattr(api, "_mounted_volume_roots", lambda: [])

    result = api.run_once(limit=0, process_limit=0, live_asset_limit=0, live_dir_limit=0, live_file_limit=0)

    assert result["ok"] is True
    assert [row["root_path"] for row in api.list_roots()] == [norm_path(str(home))]


def test_run_once_refreshes_new_default_roots_after_initial_setup(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    mounted = tmp_path / "external-drive"
    mounted.mkdir()
    monkeypatch.setenv("NEXO_SKIP_FS_INDEX", "0")
    monkeypatch.delenv("NEXO_LOCAL_INDEX_DEFAULT_ROOTS", raising=False)
    monkeypatch.setattr(api.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(api, "_system_volume_roots", lambda: [])
    monkeypatch.setattr(api, "_mounted_volume_roots", lambda: [str(mounted)])

    local_context.add_root(str(home), depth=api.DEFAULT_ROOT_DEPTH)
    result = api.run_once(limit=0, process_limit=0, live_asset_limit=0, live_dir_limit=0, live_file_limit=0)

    roots = {row["root_path"] for row in api.list_roots()}
    assert result["ok"] is True
    assert norm_path(str(home)) in roots
    assert norm_path(str(mounted)) in roots


def test_norm_path_preserves_windows_drive_root():
    assert norm_path("C:\\") == "C:\\"
    assert norm_path("c:/") == "C:\\"
