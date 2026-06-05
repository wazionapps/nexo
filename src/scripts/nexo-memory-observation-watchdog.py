#!/usr/bin/env python3
# nexo: name=memory-observation-watchdog
# nexo: description=Keep Memory Observations v2 queue convergent without user-visible followups.
# nexo: category=memory
# nexo: runtime=python
# nexo: timeout=600
# nexo: cron_id=memory-observation-watchdog
# nexo: interval_seconds=900
# nexo: schedule_required=true
# nexo: recovery_policy=catchup
# nexo: run_on_boot=true
# nexo: run_on_wake=true
# nexo: idempotent=true
# nexo: max_catchup_age=7200
# nexo: stuck_after_seconds=600
# nexo: doctor_allow_db=true

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path


def _bootstrap_nexo_code(default_repo_src: Path) -> Path:
    nexo_home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
    raw_env = os.environ.get("NEXO_CODE", "")
    candidates: list[Path] = []
    if raw_env:
        raw = Path(raw_env).expanduser()
        candidates.extend([raw, raw / "core"])
    candidates.extend([default_repo_src, nexo_home / "core", nexo_home])
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if (candidate / "paths.py").is_file() or (candidate / "server.py").is_file():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return candidate
    fallback = candidates[0]
    if str(fallback) not in sys.path:
        sys.path.insert(0, str(fallback))
    return fallback


_SCRIPT_DIR = Path(__file__).resolve().parent
NEXO_CODE = _bootstrap_nexo_code(_SCRIPT_DIR.parent)

from paths import logs_dir, operations_dir  # noqa: E402
from memory_observation_processor import process_incremental, queue_health  # noqa: E402


LOG_FILE = logs_dir() / "memory-observation-watchdog.log"
SUMMARY_FILE = operations_dir() / "memory-observation-watchdog-latest.json"
DEFAULT_SLA_SECONDS = int(os.environ.get("NEXO_MEMORY_OBSERVATION_SLA_SECONDS", "3600") or "3600")
DEFAULT_PROCESS_LIMIT = int(os.environ.get("NEXO_MEMORY_OBSERVATION_PROCESS_LIMIT", "100") or "100")
DEFAULT_BACKFILL_LIMIT = int(os.environ.get("NEXO_MEMORY_OBSERVATION_BACKFILL_LIMIT", "100") or "100")


def log(message: str) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    line = f"[{ts}] {message}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def write_summary(payload: dict) -> None:
    SUMMARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SUMMARY_FILE.with_suffix(SUMMARY_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(SUMMARY_FILE)


def should_process(health: dict) -> bool:
    if not health.get("ok", False):
        return False
    if bool(health.get("skipped", False)):
        return False
    return bool(
        not health.get("healthy", True)
        or int(health.get("pending", 0) or 0) > 0
        or int(health.get("unqueued_events", 0) or 0) > 0
        or int(health.get("processed_missing_observations", 0) or 0) > 0
    )


def main() -> int:
    log("=== Memory Observation Watchdog starting ===")
    health = queue_health(pending_sla_seconds=DEFAULT_SLA_SECONDS)
    payload: dict = {
        "ok": bool(health.get("ok", True)),
        "processed": False,
        "health_before": health,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    if should_process(health):
        result = process_incremental(
            process_limit=DEFAULT_PROCESS_LIMIT,
            backfill_limit=DEFAULT_BACKFILL_LIMIT,
            pending_sla_seconds=DEFAULT_SLA_SECONDS,
        )
        payload.update({"processed": True, "result": result})
        log(
            "Processed queue: "
            f"backfill={result.get('backfill', {}).get('enqueued', 0)} "
            f"processed={result.get('processed', {}).get('processed', 0)} "
            f"healthy={result.get('healthy', True)}"
        )
    else:
        log("Queue healthy or unavailable; no processing needed.")

    write_summary(payload)
    return 0 if payload.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
