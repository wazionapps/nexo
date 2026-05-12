#!/usr/bin/env python3
# nexo: name=local-index
# nexo: description=Cooperative local memory indexing cycle for Brain/Desktop.
# nexo: category=memory
# nexo: runtime=python
# nexo: timeout=900
# nexo: cron_id=local-index
# nexo: interval_seconds=60
# nexo: schedule_required=true
# nexo: recovery_policy=restart
# nexo: run_on_boot=true
# nexo: run_on_wake=true
# nexo: idempotent=true
# nexo: max_catchup_age=600
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
SCAN_LIMIT = int(os.environ.get("NEXO_LOCAL_INDEX_SCAN_LIMIT", "1000") or "1000")
PROCESS_LIMIT = int(os.environ.get("NEXO_LOCAL_INDEX_PROCESS_LIMIT", "200") or "200")


def log(message: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def acquire_lock() -> bool:
    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "created_at": time.time()}))
        return True
    except FileExistsError:
        try:
            age = time.time() - LOCK_FILE.stat().st_mtime
            if age > LOCK_STALE_SECONDS:
                LOCK_FILE.unlink(missing_ok=True)
                return acquire_lock()
        except Exception:
            pass
        return False


def release_lock() -> None:
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def main() -> int:
    if not acquire_lock():
        log("Skipped: previous local-index cycle is still running.")
        return 0
    try:
        if os.environ.get("NEXO_LOCAL_INDEX_DISABLE_DEFAULT_ROOTS", "").strip() != "1":
            api.ensure_default_roots()
        result = api.run_once(limit=SCAN_LIMIT, process_limit=PROCESS_LIMIT)
        log_event("info", "service_cycle_finished", "Local memory service cycle finished", result=result)
        log(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("ok") else 2
    except Exception as exc:
        log_event("error", "service_cycle_failed", "Local memory service cycle failed", error=type(exc).__name__)
        log(f"ERROR: {type(exc).__name__}: {exc}")
        return 2
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
