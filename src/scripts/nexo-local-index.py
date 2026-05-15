#!/usr/bin/env python3
# nexo: name=local-index
# nexo: description=Cooperative local memory indexing cycle for Brain/Desktop.
# nexo: category=memory
# nexo: runtime=python
# nexo: timeout=21600
# nexo: cron_id=local-index
# nexo: interval_seconds=60
# nexo: schedule_required=true
# nexo: recovery_policy=restart
# nexo: run_on_boot=true
# nexo: run_on_wake=true
# nexo: idempotent=true
# nexo: max_catchup_age=600
# nexo: stuck_after_seconds=21600
# nexo: doctor_allow_db=true

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent
if str(_repo_src) not in sys.path:
    sys.path.insert(0, str(_repo_src))

from paths import logs_dir
from local_context import api
from local_context.logging import log_event

LOG_DIR = logs_dir()
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "local-index.log"
LOCK_FILE = LOG_DIR / "local-index.lock"
LOCK_STALE_SECONDS = int(os.environ.get("NEXO_LOCAL_INDEX_LOCK_STALE_SECONDS", "1800") or "1800")


def _optional_env_int(name: str) -> int | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        log(f"Ignoring invalid integer env {name}={value!r}.")
        return None
    return parsed if parsed > 0 else None


def log(message: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _log_event_best_effort(level: str, event: str, message: str, **metadata) -> None:
    try:
        log_event(level, event, message, **metadata)
    except Exception as exc:
        log(f"ERROR: failed to record local-index event {event}: {type(exc).__name__}: {exc}")


def _read_lock() -> dict:
    try:
        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def acquire_lock() -> bool:
    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "created_at": time.time()}))
        return True
    except FileExistsError:
        try:
            lock = _read_lock()
            pid = int(lock.get("pid") or 0)
            age = time.time() - float(lock.get("created_at") or LOCK_FILE.stat().st_mtime)
            if pid and not _pid_running(pid):
                LOCK_FILE.unlink(missing_ok=True)
                log(f"Removed stale local-index lock for dead pid {pid}.")
                return acquire_lock()
            if age > LOCK_STALE_SECONDS:
                LOCK_FILE.unlink(missing_ok=True)
                log(f"Removed stale local-index lock older than {int(age)} seconds.")
                return acquire_lock()
        except Exception:
            pass
        return False


def release_lock() -> None:
    try:
        lock = _read_lock()
        pid = int(lock.get("pid") or 0)
        if pid and pid != os.getpid():
            return
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _run_single_index_cycle(config: dict) -> dict:
    scan_limit = _optional_env_int("NEXO_LOCAL_INDEX_SCAN_LIMIT") or int(config["scan_limit"])
    process_limit = _optional_env_int("NEXO_LOCAL_INDEX_PROCESS_LIMIT") or int(config["process_limit"])
    live_asset_limit = _optional_env_int("NEXO_LOCAL_INDEX_LIVE_ASSET_LIMIT") or int(config["live_asset_limit"])
    live_dir_limit = _optional_env_int("NEXO_LOCAL_INDEX_LIVE_DIR_LIMIT") or int(config["live_dir_limit"])
    live_file_limit = _optional_env_int("NEXO_LOCAL_INDEX_LIVE_FILE_LIMIT") or int(config["live_file_limit"])
    try:
        return api.run_once(
            limit=scan_limit,
            process_limit=process_limit,
            live_asset_limit=live_asset_limit,
            live_dir_limit=live_dir_limit,
            live_file_limit=live_file_limit,
        )
    except TypeError as exc:
        message = str(exc)
        live_kwargs = ("live_asset_limit", "live_dir_limit", "live_file_limit")
        if not any(name in message for name in live_kwargs):
            raise
        _log_event_best_effort(
            "warn",
            "service_cycle_compat_fallback",
            "Local memory service used compatibility fallback",
            error=message,
        )
        log(f"Compatibility fallback: api.run_once does not accept live reconcile limits ({message}).")
        return api.run_once(limit=scan_limit, process_limit=process_limit)


def _run_index_cycle() -> dict:
    config = api.performance_config()
    cycles = _optional_env_int("NEXO_LOCAL_INDEX_CYCLES_PER_RUN") or int(config.get("cycles_per_run") or 1)
    results = []
    ok = True
    for _index in range(max(1, cycles)):
        result = _run_single_index_cycle(config)
        results.append(result)
        ok = ok and bool(result.get("ok"))
        paused = bool(result.get("paused") or result.get("scan", {}).get("paused") or result.get("jobs", {}).get("paused"))
        if paused or not result.get("ok"):
            break
    return {
        "ok": ok,
        "profile": config["profile"],
        "cycles": len(results),
        "result": results[-1] if results else {},
        "results": results,
    }


def main() -> int:
    if not acquire_lock():
        log("Skipped: previous local-index cycle is still running.")
        _log_event_best_effort("warn", "service_cycle_skipped_lock", "Local memory service skipped because a previous cycle is still running")
        return 0
    try:
        if os.environ.get("NEXO_LOCAL_INDEX_DISABLE_DEFAULT_ROOTS", "").strip() != "1":
            api.ensure_default_roots()
        result = _run_index_cycle()
        _log_event_best_effort("info", "service_cycle_finished", "Local memory service cycle finished", result=result)
        log(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("ok") else 2
    except Exception as exc:
        log(f"ERROR: {type(exc).__name__}: {exc}")
        _log_event_best_effort("error", "service_cycle_failed", "Local memory service cycle failed", error=type(exc).__name__)
        return 2
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
