from __future__ import annotations

"""Internal evidence ledger over existing continuity stores.

The ledger is intentionally virtual: it normalises rows from existing tables
and records new evidence in ``memory_events`` so G15 can wire public entry
points later without a schema change in this group.
"""

import hashlib
import importlib
import json
import re
import sqlite3
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable


LEDGER_SCHEMA_VERSION = 1
DEFAULT_LIMIT = 20
MAX_LIMIT = 200

_SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]+"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"shp(?:at|ss)_[A-Fa-f0-9]+"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"AIza[A-Za-z0-9_-]{30,}"),
    re.compile(r"ya29\.[A-Za-z0-9_-]+"),
    re.compile(r"xox[bpsa]-[A-Za-z0-9-]+"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-.=+/]{20,}", re.IGNORECASE),
    re.compile(r"(password\s*[=:]\s*['\"]?)[^\s'\"]{6,}", re.IGNORECASE),
    re.compile(r"(secret\s*[=:]\s*['\"]?)[^\s'\"]{6,}", re.IGNORECASE),
    re.compile(r"(token\s*[=:]\s*['\"]?)[A-Za-z0-9_.:/+=-]{8,}", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[=:]\s*['\"]?)[A-Za-z0-9_.:/+=-]{8,}", re.IGNORECASE),
)

_SOURCE_ALIASES = {
    "tasks": "task",
    "protocol_task": "task",
    "protocol_tasks": "task",
    "workflows": "workflow",
    "workflow_run": "workflow",
    "workflow_runs": "workflow",
    "workflow_checkpoint": "workflow_checkpoint",
    "workflow_checkpoints": "workflow_checkpoint",
    "changes": "change_log",
    "change": "change_log",
    "diaries": "diary",
    "session_diary": "diary",
    "lifecycle_events": "lifecycle",
    "continuity_snapshots": "continuity",
    "local-context": "local_context",
    "local_context_query": "local_context",
    "transcripts": "transcript",
    "evidence": "evidence_record",
    "evidence_ledger": "evidence_record",
}


@dataclass(frozen=True)
class EvidenceEntry:
    evidence_id: str
    source_type: str
    source_id: str
    created_at: str
    actor: str = ""
    session_id: str = ""
    client: str = ""
    conversation_id: str = ""
    object_type: str = ""
    object_ref: str = ""
    action: str = ""
    summary: str = ""
    refs: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 1.0
    privacy_level: str = "normal"
    consumer_last_seen_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def record_evidence(
    *,
    source_type: str = "evidence_ledger",
    source_id: str = "",
    session_id: str = "",
    external_session_id: str = "",
    client: str = "",
    conversation_id: str = "",
    actor: str = "",
    object_type: str = "artifact",
    object_ref: str = "",
    action: str = "recorded",
    summary: str,
    refs: Iterable[Any] | None = None,
    file_paths: Iterable[str] | str | None = None,
    task_id: str = "",
    workflow_id: str = "",
    output: Any = None,
    error: Any = None,
    verification: str = "",
    confidence: float = 1.0,
    privacy_level: str = "normal",
    idempotency_key: str = "",
    conn: sqlite3.Connection | None = None,
) -> EvidenceEntry:
    """Record a compact evidence pointer without storing raw command output.

    The raw ``output`` and ``error`` values are reduced to stable hashes. A
    redacted error snippet is kept because failures are often the evidence.
    """

    if not str(summary or "").strip():
        raise ValueError("summary is required")

    db = conn or _get_db()
    if not _table_exists(db, "memory_events"):
        raise RuntimeError("memory_events table is unavailable")

    now = time.time()
    clean_refs = _sanitize_refs(refs or [])
    clean_paths = _normalize_paths(file_paths)
    output_hash = _stable_hash(output)
    error_hash = _stable_hash(error)
    error_snippet = _truncate(_redact_text(error), 500) if error not in (None, "") else ""
    clean_summary = _truncate(_redact_text(summary), 1200)
    clean_verification = _truncate(_redact_text(verification), 800)
    clean_object_ref = _truncate(_redact_text(object_ref), 500)
    clean_source_type = _canonical_source(source_type) or "evidence_record"
    clean_source_id = _truncate(_redact_text(source_id), 200)
    event_type = _event_type_for_action(action)
    uid = _memory_event_uid(
        idempotency_key=idempotency_key,
        source_type=clean_source_type,
        source_id=clean_source_id or clean_object_ref,
        event_type=event_type,
        session_id=session_id,
        created_at=now,
    )
    metadata = _sanitize_obj(
        {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "object_type": object_type,
            "object_ref": clean_object_ref,
            "task_id": task_id,
            "workflow_id": workflow_id,
            "conversation_id": conversation_id,
            "action": action,
            "summary": clean_summary,
            "refs": clean_refs,
            "verification": clean_verification,
            "error": error_snippet,
            "output_hash": output_hash,
            "error_hash": error_hash,
        }
    )

    db.execute(
        """
        INSERT OR IGNORE INTO memory_events (
            event_uid, created_at, session_id, external_session_id, client, conversation_id,
            project_key, source_type, source_id, event_type, actor, tool_name,
            file_paths_json, command_digest, input_hash, output_digest, raw_ref,
            privacy_level, redaction_applied, confidence, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uid,
            now,
            _truncate(session_id, 160),
            _truncate(external_session_id, 160),
            _truncate(client, 80),
            _truncate(conversation_id, 160),
            "",
            "evidence_ledger",
            clean_source_id,
            event_type,
            _truncate(actor, 120),
            "",
            _json(clean_paths, []),
            "",
            "",
            output_hash,
            clean_object_ref,
            _truncate(privacy_level or "normal", 40),
            1,
            _clamp_confidence(confidence),
            _json(metadata, {}),
        ),
    )
    if _table_exists(db, "memory_observation_queue"):
        db.execute(
            """
            INSERT OR IGNORE INTO memory_observation_queue (event_uid, status, created_at, updated_at)
            VALUES (?, 'pending', ?, ?)
            """,
            (uid, now, now),
        )
    db.commit()
    row = db.execute("SELECT * FROM memory_events WHERE event_uid = ?", (uid,)).fetchone()
    if row is None:
        raise RuntimeError("evidence record was not persisted")
    return _entry_from_memory_event(_row_dict(row))


def register_evidence(**kwargs: Any) -> EvidenceEntry:
    """Alias kept for call sites that read more naturally as register_*."""

    return record_evidence(**kwargs)


def search_evidence(
    query: str = "",
    *,
    artifact: str = "",
    task_id: str = "",
    workflow_id: str = "",
    conversation_id: str = "",
    file_path: str = "",
    source_types: Iterable[str] | None = None,
    include_transcripts: bool = False,
    transcript_hours: int = 24,
    limit: int = DEFAULT_LIMIT,
    conn: sqlite3.Connection | None = None,
) -> list[EvidenceEntry]:
    """Search evidence across tasks, workflows, change log, diaries and fallbacks."""

    max_rows = _clamp_limit(limit)
    allowed = _canonical_source_set(source_types)
    candidates: list[EvidenceEntry] = []
    for db in _candidate_dbs(conn):
        candidates.extend(_collect_recorded_evidence(db, max_rows, allowed))
        candidates.extend(_collect_tasks(db, max_rows, allowed))
        candidates.extend(_collect_workflows(db, max_rows, allowed))
        candidates.extend(_collect_change_log(db, max_rows, allowed))
        candidates.extend(_collect_diary(db, max_rows, allowed))
        candidates.extend(_collect_lifecycle(db, max_rows, allowed))
        candidates.extend(_collect_continuity(db, max_rows, allowed))
        candidates.extend(_collect_local_context(db, max_rows, allowed))
    if include_transcripts and _source_allowed("transcript", allowed):
        candidates.extend(_collect_transcripts(query, transcript_hours, max_rows))
    candidates = _dedupe_entries(candidates)

    scored: list[tuple[float, EvidenceEntry]] = []
    for entry in candidates:
        score = _match_score(
            entry,
            query=query,
            artifact=artifact,
            task_id=task_id,
            workflow_id=workflow_id,
            conversation_id=conversation_id,
            file_path=file_path,
        )
        if score is None:
            continue
        scored.append((score, entry))
    scored.sort(key=lambda item: (item[0], _sort_timestamp(item[1].created_at), item[1].evidence_id), reverse=True)
    return [entry for _, entry in scored[:max_rows]]


def evidence_to_dicts(entries: Iterable[EvidenceEntry]) -> list[dict[str, Any]]:
    return [entry.to_dict() for entry in entries]


def _get_db():
    from db._core import get_db

    return get_db()


def _candidate_dbs(conn: sqlite3.Connection | None = None) -> list[sqlite3.Connection]:
    if conn is not None:
        return [conn]

    candidates: list[sqlite3.Connection] = []
    seen: set[int] = set()

    def add(factory: Any) -> None:
        if not callable(factory):
            return
        try:
            candidate = factory()
            candidate.execute("SELECT 1")
        except Exception:
            return
        marker = id(candidate)
        if marker in seen:
            return
        seen.add(marker)
        candidates.append(candidate)

    add(_get_db)
    try:
        import db as db_package

        add(getattr(db_package, "get_db", None))
    except Exception:
        pass

    # Some reload-heavy tests and long-lived clients can leave concrete DB
    # modules with stale get_db aliases. Search must still unify evidence that
    # was written through those source-specific modules.
    for module_name in ("db._protocol", "db._workflow", "db._episodic", "db._continuity", "db._sessions", "db._core"):
        try:
            module = sys.modules.get(module_name) or importlib.import_module(module_name)
        except Exception:
            continue
        add(getattr(module, "get_db", None))
    return candidates


def _dedupe_entries(entries: Iterable[EvidenceEntry]) -> list[EvidenceEntry]:
    deduped: list[EvidenceEntry] = []
    seen: set[str] = set()
    for entry in entries:
        key = "\0".join((entry.evidence_id, entry.source_type, entry.source_id, entry.summary))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ? LIMIT 1",
            (table,),
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _json(value: Any, default: Any) -> str:
    try:
        return json.dumps(value if value not in (None, "") else default, ensure_ascii=False, sort_keys=True)
    except Exception:
        return json.dumps(default, ensure_ascii=False, sort_keys=True)


def _parse_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
        return parsed if parsed is not None else default
    except Exception:
        return default


def _redact_text(value: Any) -> str:
    text = str(value or "")
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(_redact_match, redacted)
    return redacted


def _redact_match(match: re.Match[str]) -> str:
    if match.lastindex:
        return f"{match.group(1)}[REDACTED]"
    return "[REDACTED]"


def _sanitize_obj(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {str(key): _sanitize_obj(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_obj(item) for item in value]
    return value


def _sanitize_refs(refs: Iterable[Any]) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    for item in refs:
        if item in (None, ""):
            continue
        if isinstance(item, dict):
            clean.append(_sanitize_obj(dict(item)))
        else:
            clean.append({"kind": "ref", "value": _redact_text(item)})
    return clean[:50]


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _stable_hash(value: Any) -> str:
    if value in (None, ""):
        return ""
    clean = _redact_text(value if isinstance(value, str) else _json(value, {}))
    return hashlib.sha1(clean.encode("utf-8", "replace"), usedforsecurity=False).hexdigest()[:24]


def _memory_event_uid(
    *,
    idempotency_key: str,
    source_type: str,
    source_id: str,
    event_type: str,
    session_id: str,
    created_at: float,
) -> str:
    base = idempotency_key.strip() or "|".join(
        [
            source_type.strip(),
            source_id.strip(),
            event_type.strip(),
            session_id.strip(),
            str(created_at),
        ]
    )
    digest = hashlib.sha1(base.encode("utf-8", "replace"), usedforsecurity=False).hexdigest()[:32]
    return f"EV-{digest}"


def _event_type_for_action(action: str) -> str:
    clean = re.sub(r"[^a-z0-9_]+", "_", str(action or "recorded").strip().lower()).strip("_")
    if not clean:
        clean = "recorded"
    return f"evidence_{clean}" if not clean.startswith("evidence_") else clean


def _canonical_source(value: str) -> str:
    clean = str(value or "").strip().lower().replace(" ", "_")
    return _SOURCE_ALIASES.get(clean, clean)


def _canonical_source_set(source_types: Iterable[str] | None) -> set[str] | None:
    if not source_types:
        return None
    result = {_canonical_source(item) for item in source_types if str(item or "").strip()}
    return result or None


def _source_allowed(source_type: str, allowed: set[str] | None) -> bool:
    if allowed is None:
        return True
    source = _canonical_source(source_type)
    if source in allowed:
        return True
    if source == "workflow_checkpoint" and "workflow" in allowed:
        return True
    return source == "evidence_record" and "evidence_ledger" in allowed


def _clamp_limit(limit: int) -> int:
    try:
        value = int(limit)
    except Exception:
        value = DEFAULT_LIMIT
    return max(1, min(value, MAX_LIMIT))


def _clamp_confidence(value: Any) -> float:
    try:
        raw = float(value)
    except Exception:
        raw = 0.5
    return max(0.0, min(1.0, raw))


def _normalize_paths(paths: Iterable[str] | str | None) -> list[str]:
    if isinstance(paths, str):
        items = [item.strip() for item in paths.replace("\n", ",").split(",")]
    elif paths is None:
        items = []
    else:
        items = [str(item or "").strip() for item in paths]
    result: list[str] = []
    seen = set()
    for item in items:
        clean = _redact_text(item)
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result[:50]


def _format_created_at(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = str(value).strip()
    try:
        return datetime.fromtimestamp(float(text), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return text


def _sort_timestamp(value: str) -> float:
    if not value:
        return 0.0
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        pass
    try:
        return datetime.strptime(str(value).split(".")[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return 0.0


def _ref(kind: str, value: Any) -> dict[str, Any] | None:
    clean = _redact_text(value).strip()
    if not clean:
        return None
    return {"kind": kind, "value": clean}


def _refs_from_values(*items: tuple[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for kind, value in items:
        if isinstance(value, str) and kind == "file_path":
            for path in _normalize_paths(value):
                refs.append({"kind": kind, "value": path})
            continue
        ref = _ref(kind, value)
        if ref:
            refs.append(ref)
    return refs


def _entry(
    *,
    evidence_id: str,
    source_type: str,
    source_id: Any,
    created_at: Any,
    summary: Any,
    actor: Any = "",
    session_id: Any = "",
    client: Any = "",
    conversation_id: Any = "",
    object_type: Any = "",
    object_ref: Any = "",
    action: Any = "",
    refs: Iterable[Any] | None = None,
    confidence: Any = 1.0,
    privacy_level: Any = "normal",
    consumer_last_seen_at: Any = "",
    metadata: dict[str, Any] | None = None,
) -> EvidenceEntry:
    return EvidenceEntry(
        evidence_id=_truncate(_redact_text(evidence_id), 240),
        source_type=_canonical_source(source_type),
        source_id=_truncate(_redact_text(source_id), 240),
        created_at=_format_created_at(created_at),
        actor=_truncate(_redact_text(actor), 120),
        session_id=_truncate(_redact_text(session_id), 160),
        client=_truncate(_redact_text(client), 80),
        conversation_id=_truncate(_redact_text(conversation_id), 160),
        object_type=_truncate(_redact_text(object_type), 80),
        object_ref=_truncate(_redact_text(object_ref), 500),
        action=_truncate(_redact_text(action), 120),
        summary=_truncate(_redact_text(summary), 1200),
        refs=_sanitize_refs(refs or []),
        confidence=_clamp_confidence(confidence),
        privacy_level=_truncate(_redact_text(privacy_level or "normal"), 40),
        consumer_last_seen_at=_format_created_at(consumer_last_seen_at),
        metadata=_sanitize_obj(metadata or {}),
    )


def _select_rows(conn: sqlite3.Connection, table: str, order_by: str, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, table):
        return []
    try:
        rows = conn.execute(
            f"SELECT * FROM {table} ORDER BY {order_by} DESC LIMIT ?",
            (max(limit * 4, 50),),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [_row_dict(row) for row in rows]


def _collect_recorded_evidence(conn: sqlite3.Connection, limit: int, allowed: set[str] | None) -> list[EvidenceEntry]:
    if not _source_allowed("evidence_record", allowed):
        return []
    if not _table_exists(conn, "memory_events"):
        return []
    try:
        rows = conn.execute(
            """
            SELECT * FROM memory_events
            WHERE source_type = 'evidence_ledger' OR event_type LIKE 'evidence_%'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(limit * 4, 50),),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [_entry_from_memory_event(_row_dict(row)) for row in rows]


def _entry_from_memory_event(row: dict[str, Any]) -> EvidenceEntry:
    metadata = _parse_json(row.get("metadata_json"), {})
    refs = metadata.get("refs") or []
    file_paths = _parse_json(row.get("file_paths_json"), [])
    for path in file_paths:
        refs.append({"kind": "file_path", "value": path})
    if metadata.get("verification"):
        refs.append({"kind": "verification", "value": metadata.get("verification")})
    if metadata.get("output_hash"):
        refs.append({"kind": "output_hash", "value": metadata.get("output_hash")})
    if metadata.get("error_hash"):
        refs.append({"kind": "error_hash", "value": metadata.get("error_hash")})
    summary = metadata.get("summary") or row.get("raw_ref") or row.get("source_id") or row.get("event_type")
    if metadata.get("error"):
        summary = f"{summary} Error: {metadata.get('error')}"
    return _entry(
        evidence_id=f"evidence_record:{row.get('event_uid', '')}",
        source_type="evidence_record",
        source_id=row.get("event_uid") or row.get("source_id") or "",
        created_at=row.get("created_at"),
        actor=row.get("actor") or "",
        session_id=row.get("session_id") or "",
        client=row.get("client") or "",
        conversation_id=metadata.get("conversation_id") or row.get("conversation_id") or "",
        object_type=metadata.get("object_type") or "evidence",
        object_ref=metadata.get("object_ref") or row.get("raw_ref") or row.get("source_id") or "",
        action=metadata.get("action") or row.get("event_type") or "",
        summary=summary,
        refs=refs,
        confidence=row.get("confidence") or 1.0,
        privacy_level=row.get("privacy_level") or "normal",
        metadata={
            "task_id": metadata.get("task_id") or "",
            "workflow_id": metadata.get("workflow_id") or "",
            "output_hash": metadata.get("output_hash") or "",
            "error_hash": metadata.get("error_hash") or "",
            "schema_version": metadata.get("schema_version") or LEDGER_SCHEMA_VERSION,
        },
    )


def _collect_tasks(conn: sqlite3.Connection, limit: int, allowed: set[str] | None) -> list[EvidenceEntry]:
    if not _source_allowed("task", allowed):
        return []
    entries = []
    for row in _select_rows(conn, "protocol_tasks", "opened_at", limit):
        files = _parse_json(row.get("files"), [])
        files_changed = _parse_json(row.get("files_changed"), [])
        refs = _sanitize_refs(_parse_json(row.get("evidence_refs"), []))
        refs.extend(_refs_from_values(
            ("file_path", ",".join(files + files_changed)),
            ("change_log_id", row.get("change_log_id")),
            ("followup_id", row.get("followup_id")),
            ("verification_step", row.get("verification_step")),
        ))
        summary = " ".join(
            part for part in [
                row.get("goal"),
                row.get("close_evidence"),
                row.get("outcome_notes"),
            ] if part
        )
        entries.append(
            _entry(
                evidence_id=f"task:{row.get('task_id')}",
                source_type="task",
                source_id=row.get("task_id"),
                created_at=row.get("closed_at") or row.get("opened_at"),
                actor=row.get("task_type"),
                session_id=row.get("session_id"),
                object_type="task",
                object_ref=row.get("task_id"),
                action=row.get("status") or row.get("task_type"),
                summary=summary or row.get("context_hint") or row.get("task_id"),
                refs=refs,
                confidence=0.95 if row.get("status") in {"done", "partial"} else 0.65,
                metadata={"area": row.get("area") or "", "project_hint": row.get("project_hint") or ""},
            )
        )
    return entries


def _collect_workflows(conn: sqlite3.Connection, limit: int, allowed: set[str] | None) -> list[EvidenceEntry]:
    entries = []
    if _source_allowed("workflow", allowed):
        for row in _select_rows(conn, "workflow_runs", "updated_at", limit):
            entries.append(
                _entry(
                    evidence_id=f"workflow:{row.get('run_id')}",
                    source_type="workflow",
                    source_id=row.get("run_id"),
                    created_at=row.get("updated_at") or row.get("opened_at"),
                    actor=row.get("owner"),
                    session_id=row.get("session_id"),
                    object_type="workflow",
                    object_ref=row.get("run_id"),
                    action=row.get("status"),
                    summary=" ".join(part for part in [row.get("goal"), row.get("next_action"), row.get("last_checkpoint_label")] if part),
                    refs=_refs_from_values(
                        ("goal_id", row.get("goal_id")),
                        ("protocol_task_id", row.get("protocol_task_id")),
                        ("current_step_key", row.get("current_step_key")),
                    ),
                    confidence=0.9 if row.get("status") in {"completed", "failed", "cancelled"} else 0.7,
                    metadata={"workflow_kind": row.get("workflow_kind") or "", "priority": row.get("priority") or ""},
                )
            )
    if _source_allowed("workflow_checkpoint", allowed):
        for row in _select_rows(conn, "workflow_checkpoints", "created_at", limit):
            entries.append(
                _entry(
                    evidence_id=f"workflow_checkpoint:{row.get('id')}",
                    source_type="workflow_checkpoint",
                    source_id=str(row.get("id") or ""),
                    created_at=row.get("created_at"),
                    actor=row.get("actor"),
                    object_type="workflow",
                    object_ref=row.get("run_id"),
                    action=row.get("checkpoint_label") or row.get("step_status") or row.get("run_status"),
                    summary=" ".join(part for part in [row.get("summary"), row.get("evidence"), row.get("next_action")] if part),
                    refs=_refs_from_values(
                        ("workflow_id", row.get("run_id")),
                        ("step_key", row.get("step_key")),
                        ("run_status", row.get("run_status")),
                    ),
                    confidence=0.85,
                )
            )
    return entries


def _collect_change_log(conn: sqlite3.Connection, limit: int, allowed: set[str] | None) -> list[EvidenceEntry]:
    if not _source_allowed("change_log", allowed):
        return []
    entries = []
    for row in _select_rows(conn, "change_log", "created_at", limit):
        entries.append(
            _entry(
                evidence_id=f"change_log:{row.get('id')}",
                source_type="change_log",
                source_id=str(row.get("id") or ""),
                created_at=row.get("created_at"),
                session_id=row.get("session_id"),
                object_type="file_path",
                object_ref=row.get("files"),
                action="changed",
                summary=" ".join(part for part in [row.get("what_changed"), row.get("why"), row.get("verify")] if part),
                refs=_refs_from_values(
                    ("file_path", row.get("files")),
                    ("commit_ref", row.get("commit_ref")),
                    ("triggered_by", row.get("triggered_by")),
                    ("risks", row.get("risks")),
                ),
                confidence=0.9,
                metadata={"affects": row.get("affects") or ""},
            )
        )
    return entries


def _collect_diary(conn: sqlite3.Connection, limit: int, allowed: set[str] | None) -> list[EvidenceEntry]:
    if not _source_allowed("diary", allowed):
        return []
    entries = []
    for row in _select_rows(conn, "session_diary", "created_at", limit):
        entries.append(
            _entry(
                evidence_id=f"diary:{row.get('id')}",
                source_type="diary",
                source_id=str(row.get("id") or ""),
                created_at=row.get("created_at"),
                actor=row.get("source"),
                session_id=row.get("session_id"),
                object_type="session",
                object_ref=row.get("session_id"),
                action="diary_write",
                summary=" ".join(part for part in [row.get("summary"), row.get("decisions"), row.get("pending"), row.get("context_next")] if part),
                refs=_refs_from_values(("domain", row.get("domain")), ("user_signals", row.get("user_signals"))),
                confidence=0.8,
                metadata={"mental_state": row.get("mental_state") or ""},
            )
        )
    return entries


def _collect_lifecycle(conn: sqlite3.Connection, limit: int, allowed: set[str] | None) -> list[EvidenceEntry]:
    if not _source_allowed("lifecycle", allowed):
        return []
    entries = []
    for row in _select_rows(conn, "lifecycle_events", "created_at", limit):
        payload = _parse_json(row.get("payload_snapshot"), {})
        entries.append(
            _entry(
                evidence_id=f"lifecycle:{row.get('event_id')}",
                source_type="lifecycle",
                source_id=row.get("event_id"),
                created_at=row.get("processed_at") or row.get("created_at"),
                actor=row.get("source"),
                session_id=row.get("session_id") or "",
                client=row.get("source") or "",
                conversation_id=row.get("conversation_id") or "",
                object_type="conversation",
                object_ref=row.get("conversation_id") or row.get("event_id"),
                action=row.get("action"),
                summary=" ".join(part for part in [row.get("reason"), row.get("delivery_status"), row.get("last_error")] if part),
                refs=_refs_from_values(
                    ("canonical_plan_id", row.get("canonical_plan_id")),
                    ("status", row.get("delivery_status")),
                    ("payload_summary", payload.get("summary") if isinstance(payload, dict) else ""),
                ),
                confidence=0.85 if row.get("delivery_status") in {"processed", "canonical_done", "already_processed"} else 0.65,
                metadata={"retry_count": row.get("retry_count") or 0},
            )
        )
    return entries


def _collect_continuity(conn: sqlite3.Connection, limit: int, allowed: set[str] | None) -> list[EvidenceEntry]:
    if not _source_allowed("continuity", allowed):
        return []
    entries = []
    for row in _select_rows(conn, "continuity_snapshots", "updated_at", limit):
        payload = _parse_json(row.get("payload_json"), {})
        summary = ""
        if isinstance(payload, dict):
            summary = payload.get("summary") or payload.get("message") or payload.get("context") or ""
        entries.append(
            _entry(
                evidence_id=f"continuity:{row.get('id')}",
                source_type="continuity",
                source_id=str(row.get("id") or ""),
                created_at=row.get("updated_at") or row.get("created_at"),
                actor=row.get("client"),
                session_id=row.get("session_id"),
                client=row.get("client"),
                conversation_id=row.get("conversation_id"),
                object_type="conversation",
                object_ref=row.get("conversation_id"),
                action=row.get("event_type"),
                summary=summary or row.get("trace_id") or row.get("event_type"),
                refs=_refs_from_values(
                    ("external_session_id", row.get("external_session_id")),
                    ("trace_id", row.get("trace_id")),
                    ("idempotency_key", row.get("idempotency_key")),
                ),
                confidence=0.75,
            )
        )
    return entries


def _collect_local_context(conn: sqlite3.Connection, limit: int, allowed: set[str] | None) -> list[EvidenceEntry]:
    if not _source_allowed("local_context", allowed):
        return []
    entries = []
    for row in _select_rows(conn, "local_context_queries", "created_at", limit):
        warnings = _parse_json(row.get("warnings_json"), [])
        intent = str(row.get("intent") or "answer")
        intent_label = intent.replace("_", " ").replace("-", " ")
        entries.append(
            _entry(
                evidence_id=f"local_context:{row.get('id')}",
                source_type="local_context",
                source_id=str(row.get("id") or ""),
                created_at=row.get("created_at"),
                object_type="local_context_query",
                object_ref=row.get("query_hash"),
                action=intent,
                summary=(
                    f"local-context query intent={intent_label} "
                    f"result_count={row.get('result_count') or 0}"
                ),
                refs=_sanitize_refs(warnings),
                confidence=row.get("confidence") or 0.0,
                privacy_level="metadata",
            )
        )
    return entries


def _collect_transcripts(query: str, hours: int, limit: int) -> list[EvidenceEntry]:
    if not str(query or "").strip():
        return []
    try:
        from transcript_utils import search_transcripts

        rows = search_transcripts(query, hours=hours, limit=min(limit, 20))
    except Exception:
        return []
    entries = []
    for row in rows:
        snippets = row.get("matched_messages") or []
        snippet_text = " ".join(str(item.get("snippet") or "") for item in snippets if isinstance(item, dict))
        entries.append(
            _entry(
                evidence_id=f"transcript:{row.get('session_file') or row.get('display_name')}",
                source_type="transcript",
                source_id=row.get("session_file") or row.get("display_name") or "",
                created_at=row.get("modified") or "",
                client=row.get("client") or "",
                object_type="transcript",
                object_ref=row.get("session_path") or row.get("display_name") or "",
                action="matched",
                summary=snippet_text or row.get("display_name") or "",
                refs=_refs_from_values(
                    ("session_path", row.get("session_path")),
                    ("message_count", row.get("message_count")),
                    ("score", row.get("_score")),
                ),
                confidence=min(1.0, float(row.get("_score") or 0.5)),
                privacy_level="redacted",
            )
        )
    return entries


def _normalize_text(text: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    return normalized.encode("ascii", "ignore").decode("ascii").lower()


def _tokens(text: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9._:/-]{1,}", _normalize_text(text))
        if len(token) >= 3
    }


def _entry_haystack(entry: EvidenceEntry) -> str:
    return " ".join(
        [
            entry.evidence_id,
            entry.source_type,
            entry.source_id,
            entry.session_id,
            entry.client,
            entry.conversation_id,
            entry.object_type,
            entry.object_ref,
            entry.action,
            entry.summary,
            json.dumps(entry.refs, ensure_ascii=False),
            json.dumps(entry.metadata, ensure_ascii=False),
        ]
    )


def _contains_filter(entry: EvidenceEntry, value: str) -> bool:
    if not str(value or "").strip():
        return True
    needle = _normalize_text(value)
    return needle in _normalize_text(_entry_haystack(entry))


def _task_matches(entry: EvidenceEntry, task_id: str) -> bool:
    if not task_id:
        return True
    return _contains_filter(entry, task_id)


def _workflow_matches(entry: EvidenceEntry, workflow_id: str) -> bool:
    if not workflow_id:
        return True
    return _contains_filter(entry, workflow_id)


def _conversation_matches(entry: EvidenceEntry, conversation_id: str) -> bool:
    if not conversation_id:
        return True
    needle = _normalize_text(conversation_id)
    return needle in _normalize_text(entry.conversation_id) or needle in _normalize_text(entry.object_ref)


def _file_matches(entry: EvidenceEntry, file_path: str) -> bool:
    if not file_path:
        return True
    needle = _normalize_text(file_path).replace("\\", "/")
    haystack = _normalize_text(_entry_haystack(entry)).replace("\\", "/")
    basename = needle.rsplit("/", 1)[-1]
    return needle in haystack or (len(basename) >= 3 and basename in haystack)


def _match_score(
    entry: EvidenceEntry,
    *,
    query: str,
    artifact: str,
    task_id: str,
    workflow_id: str,
    conversation_id: str,
    file_path: str,
) -> float | None:
    if not _contains_filter(entry, artifact):
        return None
    if not _task_matches(entry, task_id):
        return None
    if not _workflow_matches(entry, workflow_id):
        return None
    if not _conversation_matches(entry, conversation_id):
        return None
    if not _file_matches(entry, file_path):
        return None

    score = 0.0
    haystack = _entry_haystack(entry)
    query_tokens = _tokens(query)
    if query_tokens:
        haystack_tokens = _tokens(haystack)
        overlap = query_tokens & haystack_tokens
        if not overlap:
            return None
        score += len(overlap) / max(1, len(query_tokens))
    if artifact:
        score += 0.3
    if task_id:
        score += 0.6
    if workflow_id:
        score += 0.6
    if conversation_id:
        score += 0.4
    if file_path:
        score += 0.5
    score += entry.confidence * 0.2
    return score
