from __future__ import annotations
"""Durable write queue for interactive MCP paths.

The queue intentionally lives outside SQLite. When SQLite is the contended
resource, "enqueue" must not require a SQLite write. Records are persisted as
atomic JSON files and a single runtime writer drains them into the Brain DB.
"""

import contextlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Iterator

import paths


QUEUE_STATES = ("queued", "processing", "committed", "retrying", "failed", "dead_letter")
PRIORITY_RANK = {"high": 0, "normal": 1, "medium": 1, "low": 2}
MAX_ATTEMPTS = 5
WORKER_INTERVAL_SECONDS = 0.5

_worker_lock = threading.Lock()
_worker_started = False
_worker_stop = threading.Event()


def _now() -> float:
    return time.time()


def _queue_root() -> Path:
    root = paths.operations_dir() / "mcp-write-queue"
    for state in QUEUE_STATES:
        (root / state).mkdir(parents=True, exist_ok=True)
    return root


def _write_id() -> str:
    return f"write_{int(_now() * 1000)}_{secrets.token_hex(4)}"


def _rank(priority: str) -> int:
    return PRIORITY_RANK.get(str(priority or "").strip().lower(), PRIORITY_RANK["normal"])


def _record_filename(record: dict[str, Any]) -> str:
    return f"{_rank(str(record.get('priority') or 'normal'))}-{float(record.get('created_at') or _now()):.6f}-{record['writeId']}.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}-{secrets.token_hex(3)}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@contextlib.contextmanager
def _writer_lock(timeout_seconds: float = 0.05) -> Iterator[bool]:
    root = _queue_root()
    lock_path = root / "writer.lock"
    handle = lock_path.open("a+")
    locked = False
    deadline = time.monotonic() + max(timeout_seconds, 0.01)
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
                    yield False
                    return
                time.sleep(0.01)
        yield True
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


def enqueue_write(
    kind: str,
    payload: dict[str, Any],
    *,
    priority: str = "normal",
    wait: bool = False,
    timeout_ms: int = 3000,
) -> dict[str, Any]:
    clean_kind = str(kind or "").strip()
    if not clean_kind:
        return {"ok": False, "accepted": False, "status": "failed", "error": "kind is required"}
    record = {
        "writeId": _write_id(),
        "kind": clean_kind,
        "payload": dict(payload or {}),
        "priority": str(priority or "normal").strip().lower() or "normal",
        "status": "queued",
        "attempts": 0,
        "created_at": _now(),
        "updated_at": _now(),
        "next_attempt_at": _now(),
        "last_error": "",
    }
    target = _queue_root() / "queued" / _record_filename(record)
    _atomic_write_json(target, record)

    if wait:
        deadline = time.monotonic() + max(timeout_ms, 100) / 1000.0
        while time.monotonic() < deadline:
            drain_write_queue(limit=10)
            status = write_status(record["writeId"])
            if status.get("status") in {"committed", "failed", "dead_letter"}:
                status["accepted"] = True
                return status
            time.sleep(0.05)
        status = write_status(record["writeId"])
        status.update({"ok": False, "accepted": True, "status": status.get("status") or "queued", "timeout": True})
        return status

    return {"ok": True, "accepted": True, "status": "queued", "writeId": record["writeId"]}


def _iter_candidate_files(limit: int) -> list[Path]:
    root = _queue_root()
    candidates = list((root / "queued").glob("*.json")) + list((root / "retrying").glob("*.json"))
    ready: list[Path] = []
    now = _now()
    for path in sorted(candidates, key=lambda item: item.name):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if float(record.get("next_attempt_at") or 0) <= now:
            ready.append(path)
        if len(ready) >= max(1, limit):
            break
    return ready


def _claim(path: Path) -> tuple[Path, dict[str, Any]] | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        record["status"] = "processing"
        record["updated_at"] = _now()
        target = _queue_root() / "processing" / path.name
        os.replace(path, target)
        _atomic_write_json(target, record)
        return target, record
    except Exception:
        return None


def _finish(path: Path, record: dict[str, Any], state: str, *, error: str = "") -> None:
    record["status"] = state
    record["updated_at"] = _now()
    if error:
        record["last_error"] = error[:1000]
    target = _queue_root() / state / path.name
    _atomic_write_json(target, record)
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def _retry(path: Path, record: dict[str, Any], error: str) -> None:
    attempts = int(record.get("attempts") or 0) + 1
    record["attempts"] = attempts
    if attempts >= MAX_ATTEMPTS:
        _finish(path, record, "dead_letter", error=error)
        return
    record["status"] = "retrying"
    record["updated_at"] = _now()
    record["next_attempt_at"] = _now() + min(30.0, 0.5 * (2 ** max(0, attempts - 1)))
    record["last_error"] = error[:1000]
    target = _queue_root() / "retrying" / path.name
    _atomic_write_json(target, record)
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def _apply_write(record: dict[str, Any]) -> None:
    kind = str(record.get("kind") or "")
    payload = dict(record.get("payload") or {})
    if kind == "heartbeat_update":
        from db import update_last_heartbeat_ts, update_session

        update_session(str(payload.get("sid") or ""), str(payload.get("task") or ""))
        update_last_heartbeat_ts(str(payload.get("sid") or ""), float(payload.get("heartbeat_ts") or _now()))
        return
    if kind == "diary_draft_upsert":
        from db import upsert_diary_draft

        upsert_diary_draft(
            sid=str(payload.get("sid") or ""),
            tasks_seen=str(payload.get("tasks_seen") or "[]"),
            change_ids=str(payload.get("change_ids") or "[]"),
            decision_ids=str(payload.get("decision_ids") or "[]"),
            last_context_hint=str(payload.get("last_context_hint") or ""),
            heartbeat_count=int(payload.get("heartbeat_count") or 0),
            summary_draft=str(payload.get("summary_draft") or ""),
        )
        return
    if kind == "session_checkpoint":
        from db import save_checkpoint

        save_checkpoint(
            sid=str(payload.get("sid") or ""),
            task=str(payload.get("task") or ""),
            task_status=str(payload.get("task_status") or "active"),
            active_files=str(payload.get("active_files") or "[]"),
            current_goal=str(payload.get("current_goal") or ""),
            decisions_summary=str(payload.get("decisions_summary") or ""),
            errors_found=str(payload.get("errors_found") or ""),
            reasoning_thread=str(payload.get("reasoning_thread") or ""),
            next_step=str(payload.get("next_step") or ""),
        )
        return
    if kind == "context_event_capture":
        from db import capture_context_event

        capture_context_event(**payload)
        return
    if kind == "followup_create":
        from tools_reminders_crud import handle_followup_create

        result = handle_followup_create(**payload)
        if str(result).startswith("ERROR:"):
            raise ValueError(result)
        return
    if kind == "change_log":
        from plugins.episodic_memory import handle_change_log

        result = handle_change_log(**payload)
        if str(result).startswith("ERROR:"):
            raise ValueError(result)
        return
    if kind == "learning_add":
        from tools_learnings import handle_learning_add

        result = handle_learning_add(**payload)
        if str(result).startswith("ERROR:"):
            raise ValueError(result)
        return
    raise ValueError(f"unsupported write kind: {kind}")


def drain_write_queue(limit: int = 50) -> dict[str, Any]:
    processed = committed = retrying = dead_letter = 0
    with _writer_lock() as locked:
        if not locked:
            return {"ok": True, "locked": False, "processed": 0, "committed": 0, "retrying": 0, "dead_letter": 0}
        for candidate in _iter_candidate_files(limit):
            claimed = _claim(candidate)
            if not claimed:
                continue
            path, record = claimed
            processed += 1
            try:
                _apply_write(record)
                _finish(path, record, "committed")
                committed += 1
            except Exception as exc:
                before = int(record.get("attempts") or 0)
                _retry(path, record, f"{type(exc).__name__}: {exc}")
                if before + 1 >= MAX_ATTEMPTS:
                    dead_letter += 1
                else:
                    retrying += 1
    return {
        "ok": True,
        "locked": True,
        "processed": processed,
        "committed": committed,
        "retrying": retrying,
        "dead_letter": dead_letter,
    }


def write_status(write_id: str) -> dict[str, Any]:
    clean = str(write_id or "").strip()
    if not clean:
        return {"ok": False, "error": "writeId is required"}
    root = _queue_root()
    for state in QUEUE_STATES:
        for path in (root / state).glob(f"*{clean}.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            record["ok"] = record.get("status") == "committed"
            return record
    return {"ok": False, "writeId": clean, "status": "unknown"}


def queue_status(limit: int = 20) -> dict[str, Any]:
    root = _queue_root()
    counts = {state: len(list((root / state).glob("*.json"))) for state in QUEUE_STATES}
    recent: list[dict[str, Any]] = []
    for state in QUEUE_STATES:
        for path in sorted((root / state).glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            recent.append(
                {
                    "writeId": record.get("writeId"),
                    "kind": record.get("kind"),
                    "priority": record.get("priority"),
                    "status": record.get("status"),
                    "attempts": record.get("attempts"),
                    "updated_at": record.get("updated_at"),
                    "last_error": record.get("last_error", "")[:300],
                }
            )
    recent.sort(key=lambda item: float(item.get("updated_at") or 0), reverse=True)
    return {"ok": True, "root": str(root), "counts": counts, "recent": recent[:limit]}


def start_write_queue_worker() -> bool:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return False
        _worker_stop.clear()
        thread = threading.Thread(target=_worker_loop, name="nexo-mcp-write-queue", daemon=True)
        thread.start()
        _worker_started = True
        return True


def stop_write_queue_worker() -> None:
    _worker_stop.set()


def _worker_loop() -> None:
    while not _worker_stop.wait(WORKER_INTERVAL_SECONDS):
        try:
            drain_write_queue(limit=50)
        except Exception:
            pass
