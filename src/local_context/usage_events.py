from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

import paths

USAGE_DB_NAME = "local-context-usage.db"
USAGE_TABLE = "local_context_usage_events"
DEFAULT_USAGE_WINDOW_SECONDS = 24 * 60 * 60

_SENSITIVE_KEY_PARTS = (
    "auth",
    "cookie",
    "credential",
    "key",
    "password",
    "secret",
    "token",
)
_SECRET_VALUE_PATTERNS = (
    (r"\b(?:(?:sk|pk|rk)(?:[-_](?:live|test|proj))?[-_][A-Za-z0-9_=-]{10,}|(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|(?:xoxb|xoxp)-[A-Za-z0-9_=-]{10,})\b", "[REDACTED_SECRET]"),
    (r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b", "Bearer [REDACTED_SECRET]"),
    (r"\b(api[_-]?key|token|secret|password|passwd|pwd|authorization)\s*[:=]\s*['\"]?[^'\"\s,;]+", r"\1=[REDACTED_SECRET]"),
)


def usage_db_path() -> Path:
    override = os.environ.get("NEXO_LOCAL_CONTEXT_USAGE_DB", "").strip()
    if override:
        return Path(override).expanduser()
    test_db = os.environ.get("NEXO_TEST_DB", "").strip()
    if test_db:
        return Path(test_db).expanduser().with_name("test_local_context_usage.db")
    return paths.memory_dir() / USAGE_DB_NAME


def hash_query(query: str) -> str:
    clean = str(query or "").strip()
    if not clean:
        return ""
    return hashlib.sha256(clean.encode("utf-8", errors="ignore")).hexdigest()


def _now() -> float:
    return time.time()


def _connect_usage_db(*, create: bool, db_path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    path = Path(db_path).expanduser() if db_path else usage_db_path()
    if not create and not path.exists():
        raise FileNotFoundError(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=0.2, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=200")
    if create:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _ensure_schema(conn)
    else:
        conn.execute("PRAGMA query_only=ON")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {USAGE_TABLE} (
            event_id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            client TEXT NOT NULL DEFAULT '',
            tool TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'local_context',
            route_stage TEXT NOT NULL DEFAULT '',
            intent TEXT NOT NULL DEFAULT '',
            query_hash TEXT NOT NULL DEFAULT '',
            elapsed_ms INTEGER NOT NULL DEFAULT 0,
            deadline_ms INTEGER NOT NULL DEFAULT 0,
            timed_out INTEGER NOT NULL DEFAULT 0,
            result_count INTEGER NOT NULL DEFAULT 0,
            should_inject INTEGER NOT NULL DEFAULT 0,
            injected_chars INTEGER NOT NULL DEFAULT 0,
            evidence_refs_count INTEGER NOT NULL DEFAULT 0,
            aborted_reason TEXT NOT NULL DEFAULT '',
            used_before_response INTEGER NOT NULL DEFAULT 0,
            index_count INTEGER NOT NULL DEFAULT 0,
            index_phase TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{{}}'
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{USAGE_TABLE}_created_at ON {USAGE_TABLE}(created_at)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{USAGE_TABLE}_intent ON {USAGE_TABLE}(intent)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{USAGE_TABLE}_pre_answer ON {USAGE_TABLE}(used_before_response, created_at)"
    )
    conn.commit()


def _clean_text(value: Any, *, max_chars: int = 240) -> str:
    text = str(value or "")
    for pattern, replacement in _SECRET_VALUE_PATTERNS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    if len(text) > max_chars:
        return text[: max(0, max_chars - 3)].rstrip() + "..."
    return text


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return default


def _safe_metadata(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _clean_text(value, max_chars=400)
    if isinstance(value, (list, tuple)):
        return [_safe_metadata(item, depth=depth + 1) for item in list(value)[:20]]
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:50]:
            key = _clean_text(raw_key, max_chars=80)
            if any(part in key.lower() for part in _SENSITIVE_KEY_PARTS):
                safe[key] = "[redacted]"
            else:
                safe[key] = _safe_metadata(raw_value, depth=depth + 1)
        return safe
    return _clean_text(value)


def _metadata_json(metadata: dict[str, Any] | None) -> str:
    safe = _safe_metadata(metadata or {})
    return json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def record_usage_event(
    *,
    query: str = "",
    query_hash: str | None = None,
    client: str = "",
    tool: str = "",
    source: str = "local_context",
    route_stage: str = "",
    intent: str = "answer",
    elapsed_ms: int | float | None = None,
    deadline_ms: int | float | None = None,
    timed_out: bool = False,
    result_count: int = 0,
    should_inject: bool = False,
    injected_chars: int = 0,
    evidence_refs_count: int = 0,
    aborted_reason: str = "",
    used_before_response: bool = False,
    index_count: int = 0,
    index_phase: str = "",
    error: str = "",
    metadata: dict[str, Any] | None = None,
    created_at: float | None = None,
    db_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    event = {
        "event_id": f"lcu_{uuid.uuid4().hex}",
        "created_at": float(created_at if created_at is not None else _now()),
        "client": _clean_text(client, max_chars=80),
        "tool": _clean_text(tool, max_chars=120),
        "source": _clean_text(source or "local_context", max_chars=80),
        "route_stage": _clean_text(route_stage, max_chars=80),
        "intent": _clean_text(intent or "answer", max_chars=80),
        "query_hash": query_hash if query_hash is not None else hash_query(query),
        "elapsed_ms": _safe_int(elapsed_ms),
        "deadline_ms": _safe_int(deadline_ms),
        "timed_out": bool(timed_out),
        "result_count": _safe_int(result_count),
        "should_inject": bool(should_inject),
        "injected_chars": _safe_int(injected_chars),
        "evidence_refs_count": _safe_int(evidence_refs_count),
        "aborted_reason": _clean_text(aborted_reason, max_chars=160),
        "used_before_response": bool(used_before_response),
        "index_count": _safe_int(index_count),
        "index_phase": _clean_text(index_phase, max_chars=80),
        "error": _clean_text(error, max_chars=240),
        "metadata_json": _metadata_json(metadata),
    }
    try:
        conn = _connect_usage_db(create=True, db_path=db_path)
    except sqlite3.OperationalError as exc:
        return {"ok": False, "error": "usage_store_busy", "detail": str(exc), "event": event}
    try:
        conn.execute(
            f"""
            INSERT INTO {USAGE_TABLE} (
                event_id, created_at, client, tool, source, route_stage, intent, query_hash,
                elapsed_ms, deadline_ms, timed_out, result_count, should_inject,
                injected_chars, evidence_refs_count, aborted_reason, used_before_response,
                index_count, index_phase, error, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                event["created_at"],
                event["client"],
                event["tool"],
                event["source"],
                event["route_stage"],
                event["intent"],
                event["query_hash"],
                event["elapsed_ms"],
                event["deadline_ms"],
                int(event["timed_out"]),
                event["result_count"],
                int(event["should_inject"]),
                event["injected_chars"],
                event["evidence_refs_count"],
                event["aborted_reason"],
                int(event["used_before_response"]),
                event["index_count"],
                event["index_phase"],
                event["error"],
                event["metadata_json"],
            ),
        )
        conn.commit()
    except sqlite3.OperationalError as exc:
        return {"ok": False, "error": "usage_store_busy", "detail": str(exc), "event": event}
    finally:
        conn.close()
    public_event = dict(event)
    public_event.pop("metadata_json", None)
    return {"ok": True, "event": public_event, "store_path": str(Path(db_path).expanduser() if db_path else usage_db_path())}


def record_router_usage(
    query: str,
    router_payload: dict[str, Any],
    *,
    client: str = "",
    tool: str = "",
    route_stage: str = "pre_answer",
    intent: str = "answer",
    started_at: float | None = None,
    elapsed_ms: int | None = None,
    deadline_ms: int | None = None,
    used_before_response: bool = True,
    db_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    evidence_refs = router_payload.get("evidence_refs") or []
    rendered = str(router_payload.get("rendered") or "")
    measured_elapsed_ms = elapsed_ms
    if measured_elapsed_ms is None and started_at is not None:
        measured_elapsed_ms = int(max(0.0, _now() - float(started_at)) * 1000)
    aborted_reason = str(router_payload.get("aborted_reason") or "")
    timed_out = bool(
        router_payload.get("timed_out")
        or aborted_reason in {"timeout", "source_timeout", "deadline_exhausted"}
    )
    return record_usage_event(
        query=query,
        client=client,
        tool=tool,
        route_stage=route_stage,
        intent=str(router_payload.get("intent") or intent or "answer"),
        elapsed_ms=measured_elapsed_ms or 0,
        deadline_ms=deadline_ms or router_payload.get("deadline_ms") or 0,
        timed_out=timed_out,
        result_count=len(evidence_refs),
        should_inject=bool(router_payload.get("should_inject")),
        injected_chars=len(rendered),
        evidence_refs_count=len(evidence_refs),
        aborted_reason=aborted_reason,
        used_before_response=used_before_response,
        metadata={
            "truncated": bool(router_payload.get("truncated")),
            "usage_hint_present": bool(router_payload.get("usage_hint")),
        },
        db_path=db_path,
    )


def _empty_summary(*, since: float, window_seconds: int, store_path: Path) -> dict[str, Any]:
    return {
        "ok": True,
        "store_path": str(store_path),
        "window_seconds": int(window_seconds),
        "since": float(since),
        "total_events": 0,
        "used_before_response_events": 0,
        "injected_events": 0,
        "result_events": 0,
        "timeout_events": 0,
        "latest_event_at": 0.0,
        "latest_used_before_response_at": 0.0,
        "by_intent": {},
        "by_source": {},
        "by_route_stage": {},
    }


def summarize_usage(
    *,
    window_seconds: int = DEFAULT_USAGE_WINDOW_SECONDS,
    db_path: str | os.PathLike[str] | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    path = Path(db_path).expanduser() if db_path else usage_db_path()
    window = max(0, int(window_seconds))
    current = float(now_ts if now_ts is not None else _now())
    since = 0.0 if window == 0 else current - window
    if not path.exists():
        return _empty_summary(since=since, window_seconds=window, store_path=path)
    try:
        conn = _connect_usage_db(create=False, db_path=path)
    except sqlite3.OperationalError as exc:
        return {
            "ok": False,
            "error": "usage_store_busy",
            "detail": str(exc),
            "store_path": str(path),
            "window_seconds": window,
            "since": since,
        }
    except sqlite3.DatabaseError as exc:
        return {
            "ok": False,
            "error": "usage_store_unreadable",
            "detail": str(exc),
            "store_path": str(path),
            "window_seconds": window,
            "since": since,
        }
    try:
        totals = conn.execute(
            f"""
            SELECT
              COUNT(*) AS total_events,
              SUM(CASE WHEN used_before_response=1 THEN 1 ELSE 0 END) AS used_before_response_events,
              SUM(CASE WHEN should_inject=1 THEN 1 ELSE 0 END) AS injected_events,
              SUM(CASE WHEN result_count > 0 THEN 1 ELSE 0 END) AS result_events,
              SUM(CASE WHEN timed_out=1 THEN 1 ELSE 0 END) AS timeout_events,
              MAX(created_at) AS latest_event_at,
              MAX(CASE WHEN used_before_response=1 THEN created_at ELSE 0 END) AS latest_used_before_response_at
            FROM {USAGE_TABLE}
            WHERE created_at >= ?
            """,
            (since,),
        ).fetchone()
        intent_rows = conn.execute(
            f"""
            SELECT intent, COUNT(*) AS total
            FROM {USAGE_TABLE}
            WHERE created_at >= ?
            GROUP BY intent
            ORDER BY total DESC, intent ASC
            """,
            (since,),
        ).fetchall()
        source_rows = conn.execute(
            f"""
            SELECT source, COUNT(*) AS total
            FROM {USAGE_TABLE}
            WHERE created_at >= ?
            GROUP BY source
            ORDER BY total DESC, source ASC
            """,
            (since,),
        ).fetchall()
        stage_rows = conn.execute(
            f"""
            SELECT route_stage, COUNT(*) AS total
            FROM {USAGE_TABLE}
            WHERE created_at >= ?
            GROUP BY route_stage
            ORDER BY total DESC, route_stage ASC
            """,
            (since,),
        ).fetchall()
    finally:
        conn.close()
    return {
        "ok": True,
        "store_path": str(path),
        "window_seconds": window,
        "since": since,
        "total_events": int(totals["total_events"] or 0),
        "used_before_response_events": int(totals["used_before_response_events"] or 0),
        "injected_events": int(totals["injected_events"] or 0),
        "result_events": int(totals["result_events"] or 0),
        "timeout_events": int(totals["timeout_events"] or 0),
        "latest_event_at": float(totals["latest_event_at"] or 0.0),
        "latest_used_before_response_at": float(totals["latest_used_before_response_at"] or 0.0),
        "by_intent": {str(row["intent"]): int(row["total"] or 0) for row in intent_rows},
        "by_source": {str(row["source"]): int(row["total"] or 0) for row in source_rows},
        "by_route_stage": {str(row["route_stage"]): int(row["total"] or 0) for row in stage_rows},
    }


def usage_snapshot(
    *,
    indexed_files: int | None = None,
    index_phase: str = "",
    window_seconds: int = DEFAULT_USAGE_WINDOW_SECONDS,
    db_path: str | os.PathLike[str] | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    summary = summarize_usage(window_seconds=window_seconds, db_path=db_path, now_ts=now_ts)
    indexed_count = 0 if indexed_files is None else max(0, int(indexed_files))
    used_count = int(summary.get("used_before_response_events") or 0) if summary.get("ok") else 0
    if indexed_files is None:
        status = "usage_unknown"
    elif indexed_count <= 0:
        status = "not_indexed"
    elif used_count > 0:
        status = "indexed_and_used"
    else:
        status = "indexed_not_used"
    return {
        "ok": bool(summary.get("ok")),
        "status": status,
        "indexed": {
            "files_found": indexed_count,
            "phase": _clean_text(index_phase, max_chars=80),
        },
        "used_before_response": {
            "events": used_count,
            "latest_at": float(summary.get("latest_used_before_response_at") or 0.0),
            "window_seconds": int(window_seconds),
        },
        "usage": summary,
    }


def list_recent_events(
    *,
    limit: int = 50,
    db_path: str | os.PathLike[str] | None = None,
) -> list[dict[str, Any]]:
    path = Path(db_path).expanduser() if db_path else usage_db_path()
    if not path.exists():
        return []
    conn = _connect_usage_db(create=False, db_path=path)
    try:
        rows = conn.execute(
            f"""
            SELECT *
            FROM {USAGE_TABLE}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(int(limit or 50), 500)),),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]
