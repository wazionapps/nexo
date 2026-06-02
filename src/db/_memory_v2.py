from __future__ import annotations
"""NEXO DB - Memory Observations v2 primitives.

Phase 1 only owns the append-only ``memory_events`` log. Later phases can
derive observations, indexes, viewer rows, and promotion records from this
stable substrate without changing hook behaviour again.
"""

import hashlib
import importlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from typing import Any


def _core():
    module = sys.modules.get("db._core")
    if module is None:
        module = importlib.import_module("db._core")
    return module


_REDACT_PATTERNS = (
    re.compile(r"sk-[a-zA-Z0-9_\-]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}"),
    re.compile(r"shpat_[a-f0-9]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"xox[bp]-[a-zA-Z0-9\-]{20,}"),
    re.compile(r"Bearer\s+[a-zA-Z0-9_\-.=+/]{20,}", re.IGNORECASE),
    re.compile(r"(token\s*[=:]\s*['\"]?)[a-zA-Z0-9_\-]{20,}", re.IGNORECASE),
    re.compile(r"(password\s*[=:]\s*['\"]?)[^\s'\"]{8,}", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[=:]\s*['\"]?)[a-zA-Z0-9_\-]{16,}", re.IGNORECASE),
)


def _table_exists(conn, table_name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
            (table_name,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def _is_virtual_fts_table(conn, table_name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = ? LIMIT 1",
            (table_name,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    sql = str(row["sql"] if row else "").upper()
    return "VIRTUAL TABLE" in sql and "FTS5" in sql


def _truncate(text: str | None, limit: int) -> str:
    clean = str(text or "").strip()
    return clean if len(clean) <= limit else clean[: limit - 3] + "..."


def _json(value: Any, default: Any) -> str:
    if value in (None, ""):
        value = default
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    except Exception:
        return json.dumps(default, ensure_ascii=True, sort_keys=True)


def _parse_json(value: str, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else default
    except Exception:
        return default


def _normalize_paths(paths: Any) -> list[str]:
    if isinstance(paths, str):
        items = [item.strip() for item in paths.split(",")]
    elif isinstance(paths, (list, tuple, set)):
        items = [str(item).strip() for item in paths]
    else:
        items = []
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result[:50]


def _redact_text(value: str) -> tuple[str, bool]:
    text = str(value or "")
    redacted = text
    for pattern in _REDACT_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted, redacted != text


def _redact_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        changed = False
        clean: dict[str, Any] = {}
        for key, item in value.items():
            clean_item, item_changed = _redact_value(item)
            clean[str(key)] = clean_item
            changed = changed or item_changed
        return clean, changed
    if isinstance(value, (list, tuple, set)):
        changed = False
        clean_items = []
        for item in value:
            clean_item, item_changed = _redact_value(item)
            clean_items.append(clean_item)
            changed = changed or item_changed
        return clean_items, changed
    return value, False


def _memory_executive_shadow_decision(
    *,
    event_uid: str,
    source_type: str,
    source_id: str,
    event_type: str,
    actor: str,
    session_id: str,
    project_key: str,
    text: str,
    metadata: dict[str, Any],
    privacy_level: str,
    idempotency_key: str,
    created_at: float,
) -> dict[str, Any]:
    try:
        from memory_executive import audit_record, decide
    except Exception as exc:
        return {"error": f"memory_executive_unavailable:{_truncate(str(exc), 160)}"}

    raw_refs = metadata.get("evidence_refs") or metadata.get("evidence_ref") or []
    if isinstance(raw_refs, str):
        evidence_refs = [raw_refs]
    elif isinstance(raw_refs, (list, tuple, set)):
        evidence_refs = [str(item) for item in raw_refs if str(item).strip()]
    else:
        evidence_refs = []
    event = {
        "event_uid": event_uid,
        "source_type": source_type,
        "source_id": source_id,
        "event_type": event_type,
        "actor": actor,
        "session_id": session_id,
        "project_key": project_key,
        "text": _truncate(text, 1200),
        "metadata": metadata,
        "evidence_refs": evidence_refs,
        "privacy_level": privacy_level,
        "idempotency_key": idempotency_key,
        "created_at": str(created_at),
    }
    try:
        decision = decide(event, shadow_mode=True)
        return audit_record(event, decision)
    except Exception as exc:
        return {"error": f"memory_executive_error:{_truncate(str(exc), 160)}"}


def _stable_hash(value: Any) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        value = _json(value, {})
    redacted, _ = _redact_text(value)
    return hashlib.sha1(redacted.encode("utf-8", "replace"), usedforsecurity=False).hexdigest()[:24]


def _parse_created_at(value: Any) -> float:
    if value in (None, ""):
        return _core().now_epoch()
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text.replace("Z", "").split("+")[0], fmt).timestamp()
        except Exception:
            continue
    return _core().now_epoch()


def build_memory_event_uid(
    *,
    source_type: str,
    source_id: str = "",
    event_type: str,
    session_id: str = "",
    tool_name: str = "",
    idempotency_key: str = "",
    created_at: float | None = None,
) -> str:
    clean_key = (idempotency_key or "").strip()
    if clean_key:
        base = clean_key
    else:
        stable_source = (source_id or "").strip()
        if stable_source:
            base = "|".join([
                (source_type or "").strip(),
                stable_source,
                (event_type or "").strip(),
                (session_id or "").strip(),
                (tool_name or "").strip(),
            ])
        else:
            base = "|".join([
                (source_type or "").strip(),
                (event_type or "").strip(),
                (session_id or "").strip(),
                (tool_name or "").strip(),
                str(created_at if created_at is not None else _core().now_epoch()),
            ])
    digest = hashlib.sha1(base.encode("utf-8", "replace"), usedforsecurity=False).hexdigest()[:32]
    return f"ME-{digest}"


def _row_to_event(row) -> dict:
    item = dict(row)
    item["file_paths"] = _parse_json(item.pop("file_paths_json", "[]"), [])
    item["metadata"] = _parse_json(item.pop("metadata_json", "{}"), {})
    item["redaction_applied"] = bool(item.get("redaction_applied"))
    return item


def _row_to_observation(row) -> dict:
    item = dict(row)
    item["facts"] = _parse_json(item.pop("facts_json", "{}"), {})
    item["evidence_refs"] = _parse_json(item.pop("evidence_refs_json", "[]"), [])
    item["entities"] = _parse_json(item.pop("entities_json", "[]"), [])
    item["metadata"] = _parse_json(item.pop("metadata_json", "{}"), {})
    return item


def _enqueue_memory_event(conn, event_uid: str, created_at: float) -> None:
    if not _table_exists(conn, "memory_observation_queue"):
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO memory_observation_queue (event_uid, status, created_at, updated_at)
        VALUES (?, 'pending', ?, ?)
        """,
        (event_uid, created_at, created_at),
    )


def record_memory_event(
    *,
    event_type: str,
    source_type: str,
    source_id: str = "",
    session_id: str = "",
    external_session_id: str = "",
    client: str = "",
    conversation_id: str = "",
    project_key: str = "",
    actor: str = "",
    tool_name: str = "",
    file_paths: Any = None,
    command_digest: str = "",
    tool_input: Any = None,
    tool_output: Any = None,
    raw_ref: str = "",
    privacy_level: str = "normal",
    confidence: float = 1.0,
    metadata: dict[str, Any] | None = None,
    event_uid: str = "",
    idempotency_key: str = "",
    created_at: float | None = None,
    enqueue_observation: bool = True,
) -> dict:
    clean_event_type = (event_type or "").strip().lower()
    clean_source_type = (source_type or "").strip().lower()
    if not clean_event_type:
        return {"ok": False, "error": "event_type is required"}
    if not clean_source_type:
        return {"ok": False, "error": "source_type is required"}

    conn = _core().get_db()
    if not _table_exists(conn, "memory_events"):
        return {"ok": True, "skipped": True, "reason": "memory_events table unavailable"}

    now = float(created_at if created_at is not None else _core().now_epoch())
    paths = _normalize_paths(file_paths)
    meta = dict(metadata or {})
    redaction_applied = False

    clean_command, command_redacted = _redact_text(command_digest)
    redaction_applied = redaction_applied or command_redacted
    clean_raw_ref, raw_ref_redacted = _redact_text(raw_ref)
    redaction_applied = redaction_applied or raw_ref_redacted
    clean_meta, meta_redacted = _redact_value(meta)
    redaction_applied = redaction_applied or meta_redacted

    _, input_redacted = _redact_value(tool_input)
    _, output_redacted = _redact_value(tool_output)
    redaction_applied = redaction_applied or input_redacted or output_redacted
    input_hash = _stable_hash(tool_input)
    output_hash = _stable_hash(tool_output)
    uid = (event_uid or "").strip() or build_memory_event_uid(
        source_type=clean_source_type,
        source_id=source_id,
        event_type=clean_event_type,
        session_id=session_id,
        tool_name=tool_name,
        idempotency_key=idempotency_key,
        created_at=now,
    )
    executive_text = " ".join(
        str(part or "").strip()
        for part in (
            clean_meta.get("summary") if isinstance(clean_meta, dict) else "",
            clean_meta.get("statement") if isinstance(clean_meta, dict) else "",
            clean_meta.get("goal") if isinstance(clean_meta, dict) else "",
            clean_meta.get("outcome") if isinstance(clean_meta, dict) else "",
            clean_command,
            clean_raw_ref,
        )
        if str(part or "").strip()
    )
    if isinstance(clean_meta, dict) and "memory_executive" not in clean_meta:
        clean_meta["memory_executive"] = _memory_executive_shadow_decision(
            event_uid=uid,
            source_type=clean_source_type,
            source_id=_truncate(source_id, 200),
            event_type=clean_event_type,
            actor=_truncate(actor, 120),
            session_id=_truncate(session_id, 160),
            project_key=_truncate(project_key, 120),
            text=executive_text,
            metadata=clean_meta,
            privacy_level="secret" if redaction_applied else (privacy_level or "normal"),
            idempotency_key=idempotency_key or uid,
            created_at=now,
        )
        if clean_meta["memory_executive"].get("decision_kind") == "quarantine":
            enqueue_observation = False

    try:
        cursor = conn.execute(
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
                _truncate(project_key, 120),
                clean_source_type,
                _truncate(source_id, 200),
                clean_event_type,
                _truncate(actor, 120),
                _truncate(tool_name, 120),
                _json(paths, []),
                _truncate(clean_command, 240),
                input_hash,
                output_hash,
                _truncate(clean_raw_ref, 500),
                _truncate(privacy_level or "normal", 40),
                1 if redaction_applied else 0,
                max(0.0, min(1.0, float(confidence or 0.0))),
                _json(clean_meta, {}),
            ),
        )
        if enqueue_observation:
            _enqueue_memory_event(conn, uid, now)
        conn.commit()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "event_uid": uid}

    row = conn.execute("SELECT * FROM memory_events WHERE event_uid = ?", (uid,)).fetchone()
    event = _row_to_event(row) if row else {"event_uid": uid}
    event["ok"] = True
    event["inserted"] = bool(cursor.rowcount)
    return event


def _derive_observation(event: dict) -> dict:
    metadata = event.get("metadata") or {}
    paths = event.get("file_paths") or []
    event_type = event.get("event_type") or ""
    source_type = event.get("source_type") or ""
    source_id = event.get("source_id") or ""
    tool_name = event.get("tool_name") or ""

    observation_type = "event"
    subject = source_id or event.get("event_uid") or ""
    salience = 0.45
    summary = metadata.get("summary") or ""
    entities: list[str] = []

    if event_type == "tool_write":
        observation_type = "code_change"
        subject = paths[0] if paths else tool_name or source_id
        count = len(paths)
        file_note = ", ".join(paths[:4]) if paths else "unknown files"
        summary = summary or f"{tool_name or 'Tool'} wrote {count} file(s): {file_note}."
        entities.extend(paths[:8])
        salience = 0.62
    elif event_type.startswith("protocol_task_"):
        observation_type = "task_result"
        outcome = event_type.removeprefix("protocol_task_") or "closed"
        subject = metadata.get("goal") or source_id
        goal = metadata.get("goal") or source_id or "protocol task"
        summary = summary or f"Protocol task {outcome}: {goal}"
        if metadata.get("outcome"):
            entities.append(str(metadata["outcome"]))
        salience = 0.72 if outcome == "done" else 0.58
    elif "correction" in event_type:
        observation_type = "correction"
        subject = metadata.get("subject") or source_id
        summary = summary or f"Correction captured from {source_type}:{source_id}"
        salience = 0.9
    elif "decision" in event_type:
        observation_type = "decision"
        subject = metadata.get("subject") or source_id
        summary = summary or f"Decision captured from {source_type}:{source_id}"
        salience = 0.82

    raw_ref = event.get("raw_ref") or ""
    evidence_refs = [f"memory_event:{event.get('event_uid')}"]
    if raw_ref:
        evidence_refs.append(raw_ref)
    if source_type and source_id:
        evidence_refs.append(f"{source_type}:{source_id}")

    facts = {
        "event_uid": event.get("event_uid"),
        "event_type": event_type,
        "source_type": source_type,
        "source_id": source_id,
        "tool_name": tool_name,
        "file_paths": paths,
        "created_at": event.get("created_at"),
    }
    if metadata:
        facts["metadata"] = metadata

    source_hash = hashlib.sha1(
        _json(
            {
                "event_uid": event.get("event_uid"),
                "summary": summary,
                "facts": facts,
            },
            {},
        ).encode("utf-8", "replace"),
        usedforsecurity=False,
    ).hexdigest()[:24]
    uid = f"MO-{hashlib.sha1(str(event.get('event_uid')).encode('utf-8'), usedforsecurity=False).hexdigest()[:32]}"

    return {
        "observation_uid": uid,
        "created_at": float(event.get("created_at") or _core().now_epoch()),
        "updated_at": _core().now_epoch(),
        "project_key": event.get("project_key") or "",
        "session_id": event.get("session_id") or "",
        "observation_type": observation_type,
        "subject": _truncate(subject, 240),
        "summary": _truncate(summary, 1000),
        "facts": facts,
        "evidence_refs": evidence_refs,
        "entities": sorted({str(item) for item in entities if str(item).strip()}),
        "salience": salience,
        "confidence": float(event.get("confidence") or 0.5),
        "stability": 1.0,
        "status": "active",
        "promotion_state": "observation",
        "decay_policy": "normal",
        "source_hash": source_hash,
        "metadata": {
            "source_event_id": event.get("id"),
            "phase": "passive_observation",
        },
    }


def _intraday_facts_enabled() -> bool:
    value = os.environ.get("NEXO_INTRADAY_FACTS_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off", "disabled"}


def _intraday_fact_candidate(observation: dict) -> bool:
    if str(observation.get("status") or "active").lower() != "active":
        return False
    if float(observation.get("salience") or 0.0) < 0.62:
        return False
    if str(observation.get("observation_type") or "") not in {
        "code_change",
        "correction",
        "decision",
        "task_result",
    }:
        return False
    if not str(observation.get("summary") or "").strip():
        return False

    facts = observation.get("facts") if isinstance(observation.get("facts"), dict) else {}
    metadata = facts.get("metadata") if isinstance(facts.get("metadata"), dict) else {}
    event_type = str(facts.get("event_type") or "")
    source_type = str(facts.get("source_type") or "")
    refs = [str(ref) for ref in observation.get("evidence_refs") or []]
    observation_type = str(observation.get("observation_type") or "")

    if observation_type == "task_result":
        outcome = str(metadata.get("outcome") or event_type.removeprefix("protocol_task_") or "").lower()
        return outcome in {"done", "closed", "completed", "success", "partial"}
    if observation_type == "code_change":
        verification_keys = {
            "verified",
            "verification",
            "change_verify",
            "test_output",
            "tests_passed",
            "evidence",
        }
        if source_type in {"change_log", "evidence_ledger", "protocol_task"}:
            return True
        if any(key in metadata and str(metadata.get(key) or "").strip() for key in verification_keys):
            return True
        return any(ref.startswith(("change_log:", "evidence:", "protocol_task:")) for ref in refs)
    return observation_type in {"correction", "decision"}


def publish_intraday_fact(observation: dict, *, ttl_hours: int = 36) -> dict:
    """Expose high-salience observations as temporary hot context.

    This is deliberately not long-term promotion. Deep Sleep can later promote,
    merge, or discard the observation; the intraday fact only keeps today's
    important work visible while the operator keeps working.
    """
    if not _intraday_facts_enabled():
        return {"ok": True, "skipped": True, "reason": "intraday facts disabled"}
    if not _intraday_fact_candidate(observation):
        return {"ok": True, "skipped": True, "reason": "not an intraday fact candidate"}

    uid = str(observation.get("observation_uid") or "").strip()
    if not uid:
        return {"ok": False, "error": "observation_uid is required"}

    try:
        from db._hot_context import capture_context_event

        result = capture_context_event(
            event_type="intraday_fact",
            title=_truncate(observation.get("subject") or uid, 160),
            summary=_truncate(observation.get("summary") or "", 600),
            body=_truncate(observation.get("summary") or "", 1600),
            context_key=f"intraday_fact:{uid}",
            context_title=_truncate(observation.get("subject") or uid, 160),
            context_summary=_truncate(observation.get("summary") or "", 600),
            context_type="intraday_fact",
            state="active",
            owner="nexo",
            actor="memory-observation-processor",
            source_type="memory_observation",
            source_id=uid,
            session_id=str(observation.get("session_id") or ""),
            metadata={
                "observation_type": observation.get("observation_type") or "",
                "project_key": observation.get("project_key") or "",
                "promotion_state": observation.get("promotion_state") or "observation",
                "evidence_refs": observation.get("evidence_refs") or [],
            },
            ttl_hours=ttl_hours,
            created_at=float(observation.get("updated_at") or _core().now_epoch()),
        )
        return {"ok": True, "context_key": result.get("context_key"), "result": result}
    except Exception as exc:
        return {"ok": False, "error": _truncate(str(exc), 500)}


def upsert_memory_observation(observation: dict) -> dict:
    conn = _core().get_db()
    if not _table_exists(conn, "memory_observations"):
        return {"ok": True, "skipped": True, "reason": "memory_observations table unavailable"}
    uid = (observation.get("observation_uid") or "").strip()
    if not uid:
        return {"ok": False, "error": "observation_uid is required"}
    now = float(observation.get("updated_at") or _core().now_epoch())
    clean_subject, subject_redacted = _redact_text(observation.get("subject"))
    clean_summary, summary_redacted = _redact_text(observation.get("summary"))
    clean_facts, facts_redacted = _redact_value(observation.get("facts"))
    clean_refs, refs_redacted = _redact_value(observation.get("evidence_refs"))
    clean_entities, entities_redacted = _redact_value(observation.get("entities"))
    clean_metadata, metadata_redacted = _redact_value(observation.get("metadata"))
    if any((subject_redacted, summary_redacted, facts_redacted, refs_redacted, entities_redacted, metadata_redacted)):
        if not isinstance(clean_metadata, dict):
            clean_metadata = {}
        clean_metadata["redaction_applied"] = True
    conn.execute(
        """
        INSERT INTO memory_observations (
            observation_uid, created_at, updated_at, project_key, session_id,
            observation_type, subject, summary, facts_json, evidence_refs_json,
            entities_json, salience, confidence, stability, status, promotion_state,
            decay_policy, source_hash, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(observation_uid) DO UPDATE SET
            updated_at = excluded.updated_at,
            project_key = excluded.project_key,
            session_id = excluded.session_id,
            observation_type = excluded.observation_type,
            subject = excluded.subject,
            summary = excluded.summary,
            facts_json = excluded.facts_json,
            evidence_refs_json = excluded.evidence_refs_json,
            entities_json = excluded.entities_json,
            salience = excluded.salience,
            confidence = excluded.confidence,
            stability = excluded.stability,
            status = excluded.status,
            promotion_state = excluded.promotion_state,
            decay_policy = excluded.decay_policy,
            source_hash = excluded.source_hash,
            metadata_json = excluded.metadata_json
        """,
        (
            uid,
            float(observation.get("created_at") or now),
            now,
            _truncate(observation.get("project_key"), 120),
            _truncate(observation.get("session_id"), 160),
            _truncate(observation.get("observation_type"), 80),
            _truncate(clean_subject, 240),
            _truncate(clean_summary, 1000),
            _json(clean_facts, {}),
            _json(clean_refs, []),
            _json(clean_entities, []),
            max(0.0, min(1.0, float(observation.get("salience") or 0.5))),
            max(0.0, min(1.0, float(observation.get("confidence") or 0.5))),
            max(0.1, min(3.0, float(observation.get("stability") or 1.0))),
            _truncate(observation.get("status") or "active", 40),
            _truncate(observation.get("promotion_state") or "observation", 60),
            _truncate(observation.get("decay_policy") or "normal", 60),
            _truncate(observation.get("source_hash"), 80),
            _json(clean_metadata, {}),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM memory_observations WHERE observation_uid = ?", (uid,)).fetchone()
    result = _row_to_observation(row) if row else {"observation_uid": uid}
    result["ok"] = True
    return result


def process_memory_observation_queue(limit: int = 25) -> dict:
    conn = _core().get_db()
    if not _table_exists(conn, "memory_observation_queue") or not _table_exists(conn, "memory_observations"):
        return {"ok": True, "processed": 0, "failed": 0, "skipped": True, "reason": "observation tables unavailable"}
    rows = conn.execute(
        """
        SELECT q.id AS queue_id, q.event_uid, e.*
          FROM memory_observation_queue q
          JOIN memory_events e ON e.event_uid = q.event_uid
         WHERE q.status IN ('pending', 'failed')
         ORDER BY q.created_at ASC
         LIMIT ?
        """,
        (max(1, min(int(limit or 25), 200)),),
    ).fetchall()
    processed = 0
    failed = 0
    intraday_facts = 0
    now = _core().now_epoch()
    for row in rows:
        event = _row_to_event(row)
        queue_id = row["queue_id"]
        try:
            observation = _derive_observation(event)
            upsert_memory_observation(observation)
            intraday_result = publish_intraday_fact(observation)
            if intraday_result.get("ok") and not intraday_result.get("skipped"):
                intraday_facts += 1
            conn.execute(
                """
                UPDATE memory_observation_queue
                   SET status = 'processed',
                       attempts = attempts + 1,
                       last_error = '',
                       updated_at = ?,
                       processed_at = ?
                 WHERE id = ?
                """,
                (now, now, queue_id),
            )
            processed += 1
        except Exception as exc:
            conn.execute(
                """
                UPDATE memory_observation_queue
                   SET status = 'failed',
                       attempts = attempts + 1,
                       last_error = ?,
                       updated_at = ?
                 WHERE id = ?
                """,
                (_truncate(str(exc), 500), now, queue_id),
            )
            failed += 1
    conn.commit()
    return {
        "ok": failed == 0,
        "processed": processed,
        "failed": failed,
        "intraday_facts": intraday_facts,
        "total_seen": len(rows),
    }


def list_memory_events(
    *,
    query: str = "",
    event_type: str = "",
    source_type: str = "",
    source_id: str = "",
    session_id: str = "",
    project_key: str = "",
    limit: int = 20,
) -> list[dict]:
    conn = _core().get_db()
    if not _table_exists(conn, "memory_events"):
        return []
    clauses = ["1=1"]
    params: list[Any] = []
    if event_type.strip():
        clauses.append("event_type = ?")
        params.append(event_type.strip().lower())
    if source_type.strip():
        clauses.append("source_type = ?")
        params.append(source_type.strip().lower())
    if source_id.strip():
        clauses.append("source_id = ?")
        params.append(source_id.strip())
    if session_id.strip():
        clauses.append("session_id = ?")
        params.append(session_id.strip())
    if project_key.strip():
        clauses.append("project_key = ?")
        params.append(project_key.strip())
    if query.strip():
        like = f"%{query.strip()}%"
        clauses.append(
            "(event_uid LIKE ? OR source_id LIKE ? OR event_type LIKE ? OR tool_name LIKE ? OR file_paths_json LIKE ? OR metadata_json LIKE ?)"
        )
        params.extend([like, like, like, like, like, like])

    rows = conn.execute(
        f"""
        SELECT * FROM memory_events
         WHERE {' AND '.join(clauses)}
         ORDER BY created_at DESC, id DESC
         LIMIT ?
        """,
        params + [max(1, min(int(limit or 20), 200))],
    ).fetchall()
    return [_row_to_event(row) for row in rows]


def list_memory_observations(
    *,
    query: str = "",
    observation_type: str = "",
    session_id: str = "",
    project_key: str = "",
    status: str = "",
    limit: int = 20,
) -> list[dict]:
    conn = _core().get_db()
    if not _table_exists(conn, "memory_observations"):
        return []
    clauses = ["1=1"]
    params: list[Any] = []
    if observation_type.strip():
        clauses.append("observation_type = ?")
        params.append(observation_type.strip().lower())
    if session_id.strip():
        clauses.append("session_id = ?")
        params.append(session_id.strip())
    if project_key.strip():
        clauses.append("project_key = ?")
        params.append(project_key.strip())
    if status.strip():
        clauses.append("status = ?")
        params.append(status.strip().lower())
    if query.strip():
        like = f"%{query.strip()}%"
        clauses.append(
            "(observation_uid LIKE ? OR observation_type LIKE ? OR subject LIKE ? OR summary LIKE ? OR facts_json LIKE ? OR entities_json LIKE ?)"
        )
        params.extend([like, like, like, like, like, like])

    rows = conn.execute(
        f"""
        SELECT * FROM memory_observations
         WHERE {' AND '.join(clauses)}
         ORDER BY salience DESC, created_at DESC, id DESC
         LIMIT ?
        """,
        params + [max(1, min(int(limit or 20), 200))],
    ).fetchall()
    return [_row_to_observation(row) for row in rows]


def _fts_query(query: str) -> str:
    words = [word for word in re.findall(r"[A-Za-z0-9_./:-]{2,}", query or "") if len(word) >= 2]
    return " OR ".join(f'"{word}"' for word in words[:12])


def search_memory_observations_fts(
    query: str,
    *,
    project_key: str = "",
    limit: int = 20,
) -> list[dict]:
    conn = _core().get_db()
    if not _table_exists(conn, "memory_observations_fts"):
        return []
    fts = _fts_query(query)
    if not fts:
        return []
    sql = """
        SELECT o.*
          FROM memory_observations_fts f
          JOIN memory_observations o ON o.id = f.rowid
         WHERE memory_observations_fts MATCH ?
    """
    params: list[Any] = [fts]
    if project_key.strip():
        sql += " AND o.project_key = ?"
        params.append(project_key.strip())
    sql += " ORDER BY rank LIMIT ?"
    params.append(max(1, min(int(limit or 20), 200)))
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        return []
    return [_row_to_observation(row) for row in rows]


def memory_observation_stats(days: int = 7) -> dict:
    conn = _core().get_db()
    if not _table_exists(conn, "memory_observations"):
        return {"total": 0, "by_observation_type": {}, "queue": {}, "window_days": days}
    window_days = max(1, int(days or 7))
    cutoff = _core().now_epoch() - (window_days * 86400)
    total = int(
        conn.execute(
            "SELECT COUNT(*) FROM memory_observations WHERE created_at >= ?",
            (cutoff,),
        ).fetchone()[0]
    )
    type_rows = conn.execute(
        """
        SELECT observation_type, COUNT(*) AS cnt
          FROM memory_observations
         WHERE created_at >= ?
         GROUP BY observation_type
         ORDER BY cnt DESC, observation_type ASC
        """,
        (cutoff,),
    ).fetchall()
    queue = {}
    if _table_exists(conn, "memory_observation_queue"):
        queue_rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM memory_observation_queue GROUP BY status"
        ).fetchall()
        queue = {row["status"]: int(row["cnt"]) for row in queue_rows}
    return {
        "window_days": window_days,
        "total": total,
        "by_observation_type": {row["observation_type"]: int(row["cnt"]) for row in type_rows},
        "queue": queue,
    }


def _backfill_uid(source_type: str, source_id: str) -> str:
    digest = hashlib.sha1(f"{source_type}:{source_id}".encode("utf-8"), usedforsecurity=False).hexdigest()[:32]
    return f"MB-{digest}"


def _register_backfill_sql_functions(conn) -> None:
    raw = getattr(conn, "_conn", conn)
    try:
        raw.create_function("memory_backfill_uid", 2, lambda source_type, source_id: _backfill_uid(str(source_type or ""), str(source_id or "")))
    except Exception:
        pass


def _backfill_limit(value: int) -> int:
    return max(1, min(int(value or 100), 1000))


def _backfill_observation(
    *,
    source_type: str,
    source_id: str,
    created_at: Any,
    session_id: str = "",
    project_key: str = "",
    observation_type: str,
    subject: str,
    summary: str,
    facts: dict[str, Any] | None = None,
    salience: float = 0.45,
) -> dict:
    clean_summary = _truncate(summary, 1000)
    if not clean_summary:
        return {"ok": False, "skipped": True, "reason": "empty_summary"}
    refs = [f"{source_type}:{source_id}"]
    return upsert_memory_observation(
        {
            "observation_uid": _backfill_uid(source_type, source_id),
            "created_at": _parse_created_at(created_at),
            "updated_at": _core().now_epoch(),
            "project_key": project_key,
            "session_id": session_id,
            "observation_type": observation_type,
            "subject": subject,
            "summary": clean_summary,
            "facts": {"source_type": source_type, "source_id": source_id, **(facts or {})},
            "evidence_refs": refs,
            "entities": [subject] if subject else [],
            "salience": salience,
            "confidence": 0.72,
            "stability": 1.0,
            "status": "active",
            "promotion_state": "backfilled",
            "decay_policy": "normal",
            "source_hash": _stable_hash({"source_type": source_type, "source_id": source_id, "summary": clean_summary}),
            "metadata": {"phase": "controlled_backfill"},
        }
    )


def backfill_memory_observations(
    *,
    sources: list[str] | None = None,
    limit: int = 100,
) -> dict:
    conn = _core().get_db()
    if not _table_exists(conn, "memory_observations"):
        return {"ok": True, "created": 0, "skipped": True, "reason": "memory_observations table unavailable"}
    requested = {item.strip() for item in (sources or []) if item.strip()}
    if not requested:
        requested = {"protocol_tasks", "change_log", "session_diary", "recent_events"}
    _register_backfill_sql_functions(conn)
    max_rows = _backfill_limit(limit)
    created = 0
    seen = 0

    if "protocol_tasks" in requested and _table_exists(conn, "protocol_tasks"):
        rows = conn.execute(
            """
            SELECT * FROM protocol_tasks
             WHERE status != 'open'
               AND NOT EXISTS (
                   SELECT 1 FROM memory_observations
                    WHERE observation_uid = memory_backfill_uid('protocol_task', protocol_tasks.task_id)
               )
             ORDER BY COALESCE(closed_at, opened_at) DESC
             LIMIT ?
            """,
            (max_rows,),
        ).fetchall()
        for row in rows:
            item = dict(row)
            seen += 1
            result = _backfill_observation(
                source_type="protocol_task",
                source_id=item.get("task_id") or "",
                created_at=item.get("closed_at") or item.get("opened_at"),
                session_id=item.get("session_id") or "",
                project_key=item.get("project_hint") or item.get("area") or "",
                observation_type="task_result",
                subject=item.get("goal") or item.get("task_id") or "",
                summary=f"Protocol task {item.get('status')}: {item.get('goal') or ''}".strip(),
                facts={"status": item.get("status"), "files_changed": item.get("files_changed")},
                salience=0.68,
            )
            created += 1 if result.get("ok") else 0

    if "change_log" in requested and _table_exists(conn, "change_log"):
        rows = conn.execute(
            """
            SELECT * FROM change_log
             WHERE NOT EXISTS (
                   SELECT 1 FROM memory_observations
                    WHERE observation_uid = memory_backfill_uid('change_log', change_log.id)
               )
             ORDER BY created_at DESC
             LIMIT ?
            """,
            (max_rows,),
        ).fetchall()
        for row in rows:
            item = dict(row)
            seen += 1
            result = _backfill_observation(
                source_type="change_log",
                source_id=str(item.get("id") or ""),
                created_at=item.get("created_at"),
                session_id=item.get("session_id") or "",
                project_key=item.get("affects") or "",
                observation_type="code_change",
                subject=item.get("files") or "",
                summary=item.get("what_changed") or "",
                facts={"why": item.get("why"), "verify": item.get("verify"), "files": item.get("files")},
                salience=0.62,
            )
            created += 1 if result.get("ok") else 0

    if "session_diary" in requested and _table_exists(conn, "session_diary"):
        rows = conn.execute(
            """
            SELECT * FROM session_diary
             WHERE NOT EXISTS (
                   SELECT 1 FROM memory_observations
                    WHERE observation_uid = memory_backfill_uid('session_diary', session_diary.id)
               )
             ORDER BY created_at DESC
             LIMIT ?
            """,
            (max_rows,),
        ).fetchall()
        for row in rows:
            item = dict(row)
            seen += 1
            result = _backfill_observation(
                source_type="session_diary",
                source_id=str(item.get("id") or ""),
                created_at=item.get("created_at"),
                session_id=item.get("session_id") or "",
                project_key=item.get("domain") or "",
                observation_type="conversation_summary",
                subject=item.get("domain") or item.get("session_id") or "",
                summary=item.get("summary") or "",
                facts={"decisions": item.get("decisions"), "pending": item.get("pending")},
                salience=0.52,
            )
            created += 1 if result.get("ok") else 0

    if "recent_events" in requested and _table_exists(conn, "recent_events"):
        rows = conn.execute(
            """
            SELECT * FROM recent_events
             WHERE NOT EXISTS (
                   SELECT 1 FROM memory_observations
                    WHERE observation_uid = memory_backfill_uid('recent_event', recent_events.id)
               )
             ORDER BY created_at DESC
             LIMIT ?
            """,
            (max_rows,),
        ).fetchall()
        for row in rows:
            item = dict(row)
            seen += 1
            result = _backfill_observation(
                source_type="recent_event",
                source_id=str(item.get("id") or ""),
                created_at=item.get("created_at"),
                session_id=item.get("session_id") or "",
                project_key=item.get("context_key") or "",
                observation_type="recent_context",
                subject=item.get("title") or item.get("context_key") or "",
                summary=item.get("summary") or item.get("body") or item.get("title") or "",
                facts={"event_type": item.get("event_type"), "context_key": item.get("context_key")},
                salience=0.48,
            )
            created += 1 if result.get("ok") else 0

    return {"ok": True, "sources": sorted(requested), "seen": seen, "created_or_updated": created}


def memory_observation_health(*, pending_sla_seconds: int = 3600, now: float | None = None) -> dict:
    conn = _core().get_db()
    tables = {
        "memory_events": _table_exists(conn, "memory_events"),
        "memory_observations": _table_exists(conn, "memory_observations"),
        "memory_observation_queue": _table_exists(conn, "memory_observation_queue"),
        "memory_observations_fts": _table_exists(conn, "memory_observations_fts"),
    }
    counts = {"events": 0, "observations": 0, "queue": {}}
    latest = {"event_created_at": None, "observation_created_at": None}
    if tables["memory_events"]:
        counts["events"] = int(conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0])
        latest["event_created_at"] = conn.execute("SELECT MAX(created_at) FROM memory_events").fetchone()[0]
    if tables["memory_observations"]:
        counts["observations"] = int(conn.execute("SELECT COUNT(*) FROM memory_observations").fetchone()[0])
        latest["observation_created_at"] = conn.execute("SELECT MAX(created_at) FROM memory_observations").fetchone()[0]
    pending_sla = max(1, int(pending_sla_seconds or 3600))
    pending_older_than_sla = 0
    oldest_pending = None
    max_pending_age_seconds = 0.0
    if tables["memory_observation_queue"]:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM memory_observation_queue GROUP BY status"
        ).fetchall()
        counts["queue"] = {row["status"]: int(row["cnt"]) for row in rows}
        stamp = float(now if now is not None else _core().now_epoch())
        stale_cutoff = stamp - pending_sla
        pending_older_than_sla = int(
            conn.execute(
                """
                SELECT COUNT(*)
                  FROM memory_observation_queue
                 WHERE status IN ('pending', 'failed')
                   AND created_at <= ?
                """,
                (stale_cutoff,),
            ).fetchone()[0]
        )
        oldest = conn.execute(
            """
            SELECT event_uid, status, created_at, updated_at, last_error
              FROM memory_observation_queue
             WHERE status IN ('pending', 'failed')
             ORDER BY created_at ASC, id ASC
             LIMIT 1
            """
        ).fetchone()
        if oldest:
            oldest_pending = dict(oldest)
            max_pending_age_seconds = max(0.0, stamp - float(oldest["created_at"] or stamp))

    fts_enabled = _is_virtual_fts_table(conn, "memory_observations_fts")
    fts_queryable = False
    if tables["memory_observations_fts"]:
        try:
            conn.execute("SELECT rowid FROM memory_observations_fts LIMIT 1").fetchone()
            fts_queryable = True
        except Exception:
            fts_queryable = False

    missing_required = [name for name in ("memory_events", "memory_observations", "memory_observation_queue") if not tables[name]]
    failed_queue = int(counts["queue"].get("failed", 0))
    warnings = []
    if pending_older_than_sla:
        warnings.append(
            {
                "code": "pending_sla_breached",
                "pending_older_than_sla": pending_older_than_sla,
                "pending_sla_seconds": pending_sla,
                "max_pending_age_seconds": max_pending_age_seconds,
                "oldest_pending": oldest_pending,
            }
        )
    if failed_queue:
        warnings.append({"code": "queue_failed", "failed": failed_queue})
    return {
        "ok": not missing_required and failed_queue == 0 and pending_older_than_sla == 0,
        "tables": tables,
        "missing_required": missing_required,
        "counts": counts,
        "latest": latest,
        "queue_sla": {
            "pending_sla_seconds": pending_sla,
            "pending_sla_ok": pending_older_than_sla == 0,
            "pending_older_than_sla": pending_older_than_sla,
            "oldest_pending": oldest_pending,
            "max_pending_age_seconds": max_pending_age_seconds,
        },
        "warnings": warnings,
        "fts_enabled": fts_enabled,
        "fts_degraded": tables["memory_observations_fts"] and not fts_enabled,
        "fts_queryable": fts_queryable,
    }


def maintain_memory_observations(
    *,
    process_limit: int = 100,
    retry_failed: bool = True,
    backfill_sources: list[str] | None = None,
    backfill_limit: int = 0,
) -> dict:
    conn = _core().get_db()
    reset_failed = 0
    if retry_failed and _table_exists(conn, "memory_observation_queue"):
        cursor = conn.execute(
            """
            UPDATE memory_observation_queue
               SET status = 'pending',
                   updated_at = ?
             WHERE status = 'failed'
               AND attempts < 5
            """,
            (_core().now_epoch(),),
        )
        reset_failed = int(cursor.rowcount or 0)
        conn.commit()

    processed = process_memory_observation_queue(limit=process_limit)
    backfill = {"ok": True, "skipped": True}
    if int(backfill_limit or 0) > 0:
        backfill = backfill_memory_observations(sources=backfill_sources, limit=backfill_limit)
    health = memory_observation_health()
    return {
        "ok": bool(processed.get("ok")) and bool(backfill.get("ok")) and bool(health.get("ok")),
        "reset_failed": reset_failed,
        "processed": processed,
        "backfill": backfill,
        "health": health,
    }


def memory_event_stats(days: int = 7) -> dict:
    conn = _core().get_db()
    if not _table_exists(conn, "memory_events"):
        return {"total": 0, "by_event_type": {}, "by_source_type": {}, "window_days": days}
    window_days = max(1, int(days or 7))
    cutoff = _core().now_epoch() - (window_days * 86400)
    total = int(
        conn.execute(
            "SELECT COUNT(*) FROM memory_events WHERE created_at >= ?",
            (cutoff,),
        ).fetchone()[0]
    )
    event_rows = conn.execute(
        """
        SELECT event_type, COUNT(*) AS cnt
          FROM memory_events
         WHERE created_at >= ?
         GROUP BY event_type
         ORDER BY cnt DESC, event_type ASC
        """,
        (cutoff,),
    ).fetchall()
    source_rows = conn.execute(
        """
        SELECT source_type, COUNT(*) AS cnt
          FROM memory_events
         WHERE created_at >= ?
         GROUP BY source_type
         ORDER BY cnt DESC, source_type ASC
        """,
        (cutoff,),
    ).fetchall()
    return {
        "window_days": window_days,
        "total": total,
        "by_event_type": {row["event_type"]: int(row["cnt"]) for row in event_rows},
        "by_source_type": {row["source_type"]: int(row["cnt"]) for row in source_rows},
    }
