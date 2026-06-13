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
_BLOCKED_METADATA_KEYS = {
    "query",
    "query_preview",
    "raw_query",
    "payload",
    "rendered",
    "text",
    "content",
    "prompt",
    "messages",
    "current_context",
}
_BUDGET_COLUMNS = {
    "budget_tier": "TEXT NOT NULL DEFAULT ''",
    "budget_decision_uid": "TEXT NOT NULL DEFAULT ''",
    "policy_version": "TEXT NOT NULL DEFAULT ''",
    "surface": "TEXT NOT NULL DEFAULT ''",
    "risk_level": "TEXT NOT NULL DEFAULT ''",
    "first_response_deadline_ms": "INTEGER NOT NULL DEFAULT 0",
    "required_sources_count": "INTEGER NOT NULL DEFAULT 0",
    "missing_required_sources_count": "INTEGER NOT NULL DEFAULT 0",
    "optional_sources_skipped_count": "INTEGER NOT NULL DEFAULT 0",
    "gap_disclosed": "INTEGER NOT NULL DEFAULT 0",
    "privacy_level": "TEXT NOT NULL DEFAULT 'normal'",
}
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
            budget_tier TEXT NOT NULL DEFAULT '',
            budget_decision_uid TEXT NOT NULL DEFAULT '',
            policy_version TEXT NOT NULL DEFAULT '',
            surface TEXT NOT NULL DEFAULT '',
            risk_level TEXT NOT NULL DEFAULT '',
            first_response_deadline_ms INTEGER NOT NULL DEFAULT 0,
            required_sources_count INTEGER NOT NULL DEFAULT 0,
            missing_required_sources_count INTEGER NOT NULL DEFAULT 0,
            optional_sources_skipped_count INTEGER NOT NULL DEFAULT 0,
            gap_disclosed INTEGER NOT NULL DEFAULT 0,
            privacy_level TEXT NOT NULL DEFAULT 'normal',
            metadata_json TEXT NOT NULL DEFAULT '{{}}'
        )
        """
    )
    _ensure_budget_columns(conn)
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{USAGE_TABLE}_created_at ON {USAGE_TABLE}(created_at)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{USAGE_TABLE}_intent ON {USAGE_TABLE}(intent)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{USAGE_TABLE}_pre_answer ON {USAGE_TABLE}(used_before_response, created_at)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{USAGE_TABLE}_budget_tier ON {USAGE_TABLE}(budget_tier, created_at)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{USAGE_TABLE}_source_budget ON {USAGE_TABLE}(source, budget_tier, created_at)"
    )
    conn.commit()


def _ensure_budget_columns(conn: sqlite3.Connection) -> None:
    existing = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({USAGE_TABLE})").fetchall()
    }
    for column, ddl in _BUDGET_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE {USAGE_TABLE} ADD COLUMN {column} {ddl}")


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
            key_lower = key.lower()
            if key_lower in _BLOCKED_METADATA_KEYS or any(part in key_lower for part in _SENSITIVE_KEY_PARTS):
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
    budget_tier: str = "",
    budget_decision_uid: str = "",
    policy_version: str = "",
    surface: str = "",
    risk_level: str = "",
    first_response_deadline_ms: int | float | None = None,
    required_sources_count: int = 0,
    missing_required_sources_count: int = 0,
    optional_sources_skipped_count: int = 0,
    gap_disclosed: bool = False,
    privacy_level: str = "normal",
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
        "budget_tier": _clean_text(budget_tier, max_chars=40),
        "budget_decision_uid": _clean_text(budget_decision_uid, max_chars=128),
        "policy_version": _clean_text(policy_version, max_chars=80),
        "surface": _clean_text(surface, max_chars=80),
        "risk_level": _clean_text(risk_level, max_chars=40),
        "first_response_deadline_ms": _safe_int(first_response_deadline_ms),
        "required_sources_count": _safe_int(required_sources_count),
        "missing_required_sources_count": _safe_int(missing_required_sources_count),
        "optional_sources_skipped_count": _safe_int(optional_sources_skipped_count),
        "gap_disclosed": bool(gap_disclosed),
        "privacy_level": _clean_text(privacy_level or "normal", max_chars=40),
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
                index_count, index_phase, error, budget_tier, budget_decision_uid,
                policy_version, surface, risk_level, first_response_deadline_ms,
                required_sources_count, missing_required_sources_count,
                optional_sources_skipped_count, gap_disclosed, privacy_level,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                event["budget_tier"],
                event["budget_decision_uid"],
                event["policy_version"],
                event["surface"],
                event["risk_level"],
                event["first_response_deadline_ms"],
                event["required_sources_count"],
                event["missing_required_sources_count"],
                event["optional_sources_skipped_count"],
                int(event["gap_disclosed"]),
                event["privacy_level"],
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
        or aborted_reason in {"timeout", "source_timeout", "deadline_exhausted", "required_source_timeout"}
    )
    budget_policy = router_payload.get("runtime_budget_policy") or router_payload.get("budget_policy") or {}
    if not isinstance(budget_policy, dict):
        budget_policy = {}
    metadata = {
        "truncated": bool(router_payload.get("truncated")),
        "usage_hint_present": bool(router_payload.get("usage_hint")),
        "reason_codes": budget_policy.get("reason_codes") or [],
        "escalated_from": router_payload.get("escalated_from") or budget_policy.get("escalated_from") or "",
        "escalated_to": router_payload.get("escalated_to") or budget_policy.get("escalated_to") or "",
        "route_cache_key": budget_policy.get("route_cache_key") or "",
        "max_sources": budget_policy.get("max_sources") or 0,
        "max_source_timeout_ms": budget_policy.get("max_source_timeout_ms") or 0,
        "allowed_sources": budget_policy.get("allowed_sources") or [],
        "forbidden_sources": budget_policy.get("forbidden_sources") or [],
        "required_sources": budget_policy.get("required_sources") or [],
        "fallback_policy": budget_policy.get("fallback_policy") or "",
        "escalation_policy": budget_policy.get("escalation_policy") or "",
    }
    query_hash = hash_query(query)
    source_stats = router_payload.get("source_stats") or router_payload.get("sources") or []
    if isinstance(source_stats, list):
        for item in source_stats[:20]:
            if not isinstance(item, dict):
                continue
            source_name = str(item.get("source") or "").strip()
            if not source_name:
                continue
            source_aborted = str(item.get("aborted_reason") or "")
            source_timeout = source_aborted in {"timeout", "source_timeout", "deadline_exhausted"}
            source_refs = item.get("evidence_refs") or []
            if isinstance(source_refs, str):
                source_refs = [source_refs]
            record_usage_event(
                query_hash=query_hash,
                client=client,
                tool=tool,
                source=source_name,
                route_stage=f"{route_stage}:source",
                intent=str(router_payload.get("intent") or intent or "answer"),
                elapsed_ms=_safe_int(item.get("elapsed_ms")),
                deadline_ms=deadline_ms or router_payload.get("deadline_ms") or 0,
                timed_out=source_timeout,
                result_count=_safe_int(item.get("result_count")),
                should_inject=False,
                injected_chars=0,
                evidence_refs_count=_safe_int(item.get("evidence_refs_count"), len(source_refs) if isinstance(source_refs, list) else 0),
                aborted_reason=source_aborted,
                used_before_response=used_before_response,
                budget_tier=str(router_payload.get("budget_tier") or budget_policy.get("budget_tier") or ""),
                budget_decision_uid=str(
                    router_payload.get("budget_decision_uid") or budget_policy.get("budget_decision_uid") or ""
                ),
                policy_version=str(router_payload.get("policy_version") or budget_policy.get("policy_version") or ""),
                surface=str(budget_policy.get("surface") or route_stage),
                risk_level=str(budget_policy.get("risk_level") or ""),
                first_response_deadline_ms=_safe_int(
                    router_payload.get("first_response_deadline_ms")
                    or budget_policy.get("first_response_deadline_ms")
                ),
                required_sources_count=_safe_int(router_payload.get("required_sources_count")),
                missing_required_sources_count=_safe_int(router_payload.get("missing_required_sources_count")),
                optional_sources_skipped_count=_safe_int(router_payload.get("optional_sources_skipped_count")),
                gap_disclosed=bool(router_payload.get("gap_disclosed")),
                privacy_level=str(budget_policy.get("privacy_level") or "normal"),
                metadata={"source_phase": item.get("phase") or "", "source_ok": bool(item.get("ok", True))},
                db_path=db_path,
            )

    return record_usage_event(
        query=query,
        query_hash=query_hash,
        client=client,
        tool=tool,
        source=str(router_payload.get("source") or ("pre_answer_router" if source_stats else "local_context")),
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
        budget_tier=str(router_payload.get("budget_tier") or budget_policy.get("budget_tier") or ""),
        budget_decision_uid=str(router_payload.get("budget_decision_uid") or budget_policy.get("budget_decision_uid") or ""),
        policy_version=str(router_payload.get("policy_version") or budget_policy.get("policy_version") or ""),
        surface=str(budget_policy.get("surface") or route_stage),
        risk_level=str(budget_policy.get("risk_level") or ""),
        first_response_deadline_ms=_safe_int(
            router_payload.get("first_response_deadline_ms") or budget_policy.get("first_response_deadline_ms")
        ),
        required_sources_count=_safe_int(router_payload.get("required_sources_count")),
        missing_required_sources_count=_safe_int(router_payload.get("missing_required_sources_count")),
        optional_sources_skipped_count=_safe_int(router_payload.get("optional_sources_skipped_count")),
        gap_disclosed=bool(router_payload.get("gap_disclosed")),
        privacy_level=str(budget_policy.get("privacy_level") or "normal"),
        metadata=metadata,
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
        "by_budget_tier": {},
        "runtime_budget_metrics": {
            "by_tier": {},
            "by_tier_source": {},
            "critical_missing_required_count": 0,
            "simple_heavy_source_violations": 0,
            "escalation_events": 0,
        },
    }


def _nearest_rank(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(int(value) for value in values)
    rank = max(1, int((len(ordered) * percentile) + 0.999999))
    return ordered[min(rank - 1, len(ordered) - 1)]


def _metric_payload(rows: list[sqlite3.Row]) -> dict[str, Any]:
    elapsed = [int(row["elapsed_ms"] or 0) for row in rows]
    timeout_events = sum(1 for row in rows if int(row["timed_out"] or 0))
    injected = [int(row["injected_chars"] or 0) for row in rows]
    sample_count = len(rows)
    return {
        "sample_count": sample_count,
        "timeout_events": timeout_events,
        "timeout_rate": (timeout_events / sample_count) if sample_count else None,
        "p50_elapsed_ms": _nearest_rank(elapsed, 0.50),
        "p95_elapsed_ms": _nearest_rank(elapsed, 0.95),
        "average_injected_chars": (sum(injected) / sample_count) if sample_count else None,
    }


def _budget_metrics(rows: list[sqlite3.Row]) -> dict[str, Any]:
    by_tier_rows: dict[str, list[sqlite3.Row]] = {}
    by_tier_source_rows: dict[str, dict[str, list[sqlite3.Row]]] = {}
    critical_missing_required_count = 0
    simple_heavy_source_violations = 0
    escalation_events = 0
    heavy_sources = {"memory", "cognitive", "local_context", "transcripts"}
    for row in rows:
        tier = str(row["budget_tier"] or "unclassified")
        source = str(row["source"] or "")
        by_tier_rows.setdefault(tier, []).append(row)
        by_tier_source_rows.setdefault(tier, {}).setdefault(source, []).append(row)
        if tier == "critical" and int(row["missing_required_sources_count"] or 0) > 0:
            critical_missing_required_count += 1
        if tier in {"instant", "quick"} and source in heavy_sources:
            simple_heavy_source_violations += 1
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except Exception:
            metadata = {}
        if metadata.get("escalated_from") and metadata.get("escalated_to"):
            escalation_events += 1
    return {
        "by_tier": {tier: _metric_payload(items) for tier, items in by_tier_rows.items()},
        "by_tier_source": {
            tier: {source: _metric_payload(items) for source, items in sources.items()}
            for tier, sources in by_tier_source_rows.items()
        },
        "critical_missing_required_count": critical_missing_required_count,
        "simple_heavy_source_violations": simple_heavy_source_violations,
        "escalation_events": escalation_events,
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
        columns = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({USAGE_TABLE})").fetchall()
        }
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
        if "budget_tier" in columns:
            budget_rows = conn.execute(
                f"""
                SELECT budget_tier, COUNT(*) AS total
                FROM {USAGE_TABLE}
                WHERE created_at >= ?
                GROUP BY budget_tier
                ORDER BY total DESC, budget_tier ASC
                """,
                (since,),
            ).fetchall()
            metric_rows = conn.execute(
                f"""
                SELECT source, budget_tier, elapsed_ms, timed_out, injected_chars,
                       missing_required_sources_count, metadata_json
                FROM {USAGE_TABLE}
                WHERE created_at >= ?
                """,
                (since,),
            ).fetchall()
        else:
            budget_rows = []
            metric_rows = []
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
        "by_budget_tier": {str(row["budget_tier"] or ""): int(row["total"] or 0) for row in budget_rows},
        "runtime_budget_metrics": _budget_metrics(metric_rows),
    }


def summarize_query_events(
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
        return {
            "ok": True,
            "store_path": str(path),
            "window_seconds": window,
            "since": since,
            "total": 0,
            "latest_at": 0.0,
            "by_intent": {},
        }
    try:
        conn = _connect_usage_db(create=False, db_path=path)
    except sqlite3.OperationalError as exc:
        return {"ok": False, "error": "usage_store_busy", "detail": str(exc), "store_path": str(path)}
    except sqlite3.DatabaseError as exc:
        return {"ok": False, "error": "usage_store_unreadable", "detail": str(exc), "store_path": str(path)}
    try:
        totals = conn.execute(
            f"""
            SELECT COUNT(*) AS total, MAX(created_at) AS latest_at
            FROM {USAGE_TABLE}
            WHERE created_at >= ?
              AND (source = 'local_context_query' OR route_stage = 'context_query')
            """,
            (since,),
        ).fetchone()
        intent_rows = conn.execute(
            f"""
            SELECT intent, COUNT(*) AS total
            FROM {USAGE_TABLE}
            WHERE created_at >= ?
              AND (source = 'local_context_query' OR route_stage = 'context_query')
            GROUP BY intent
            ORDER BY total DESC, intent ASC
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
        "total": int(totals["total"] or 0),
        "latest_at": float(totals["latest_at"] or 0.0),
        "by_intent": {str(row["intent"]): int(row["total"] or 0) for row in intent_rows},
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


def list_recent_query_events(
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
            WHERE source = 'local_context_query' OR route_stage = 'context_query'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(int(limit or 50), 500)),),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]
