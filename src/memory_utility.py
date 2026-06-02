"""Memory usefulness ledger and conservative delta application.

This module records how a concrete memory was used. It does not rank search
results, run decay, or write from pre-answer hot paths.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from typing import Any


POLICY_VERSION = "memory_utility_v1"
USE_STAGES = {"retrieved", "injected", "cited", "acted_on", "validated", "not_used"}
OUTCOMES = {"unknown", "helpful", "neutral", "noise", "harmful"}
PRIVACY_LEVELS = {"public", "normal", "private", "sensitive", "secret"}
PRIVACY_ALIASES = {"internal": "normal", "confidential": "sensitive"}

MEMORY_KINDS = {
    "cognitive_stm",
    "cognitive_ltm",
    "memory_observation",
    "memory_event",
    "local_context",
    "commitment",
    "causal_edge",
    "change_log",
    "protocol_task",
    "workflow_run",
    "workflow_checkpoint",
    "session_diary",
    "learning",
}

SECRET_PATTERNS = (
    re.compile(
        r"\b(?:(?:sk|pk|rk)(?:[-_](?:live|test|proj))?[-_][A-Za-z0-9_=-]{10,}|"
        r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
        r"(?:xoxb|xoxp)-[A-Za-z0-9_=-]{10,})\b"
    ),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE),
    re.compile(
        r"\b(api[_-]?key|token|secret|password|passwd|pwd|authorization)\s*[:=]\s*['\"]?[^'\"\s,;]+",
        re.IGNORECASE,
    ),
)
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PRIVATE_PATH_PATTERN = re.compile(r"(?<!\w)/(?:Users|home)/[^/\s]+/[^\s,;:]*")


def _db():
    import db

    return db.get_db()


def _cognitive_db():
    import cognitive

    return cognitive._get_db()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _parse_json(value: str | None, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else default
    except Exception:
        return default


def _hash_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _clean_ref(value: Any) -> str:
    return str(value or "").strip()


def _privacy(value: str | None) -> str:
    clean = _normalize(value)
    clean = PRIVACY_ALIASES.get(clean, clean)
    return clean if clean in PRIVACY_LEVELS else "normal"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _table_exists(conn, table_name: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone() is not None
    except Exception:
        return False


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    try:
        return any(row["name"] == column_name for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall())
    except Exception:
        return False


def _query_exists(conn, table: str, column: str, value: str) -> bool:
    if not _table_exists(conn, table):
        return False
    return conn.execute(f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1", (value,)).fetchone() is not None


def _bool_int(value: Any) -> int:
    return 1 if bool(value) else 0


def query_hash_for(query_text: str | None) -> str:
    text = str(query_text or "")
    if not text:
        return ""
    return _hash_text(" ".join(text.split()))


def redact_preview(query_text: str | None, *, privacy_level: str = "normal", max_chars: int = 160) -> tuple[str, bool]:
    privacy = _privacy(privacy_level)
    text = str(query_text or "").strip()
    if privacy == "secret":
        return "", bool(text)
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    redacted = EMAIL_PATTERN.sub("[REDACTED_EMAIL]", redacted)
    redacted = PRIVATE_PATH_PATTERN.sub("[REDACTED_PATH]", redacted)
    redacted = " ".join(redacted.split())
    if len(redacted) > max_chars:
        redacted = redacted[: max(0, max_chars - 3)].rstrip() + "..."
    return redacted, redacted != text


def memory_kind_for_ref(memory_ref: str) -> str:
    prefix, sep, _rest = _clean_ref(memory_ref).partition(":")
    if not sep:
        return ""
    return prefix if prefix in MEMORY_KINDS else ""


def _ref_value(memory_ref: str) -> str:
    _prefix, _sep, rest = _clean_ref(memory_ref).partition(":")
    return rest.strip()


def _validate_cognitive_ref(kind: str, value: str) -> tuple[bool, str]:
    if not value.isdigit():
        return False, "missing_ref"
    table = "stm_memories" if kind == "cognitive_stm" else "ltm_memories"
    return (_query_exists(_cognitive_db(), table, "id", value), "missing_ref")


def _validate_causal_edge(value: str) -> tuple[bool, str]:
    if not value:
        return False, "missing_ref"
    try:
        import causal_graph

        if causal_graph._active_edge_by_uid(value):
            return True, ""
    except Exception:
        pass
    return False, "missing_ref"


def validate_memory_ref(memory_ref: str) -> dict[str, Any]:
    kind = memory_kind_for_ref(memory_ref)
    value = _ref_value(memory_ref)
    if not kind or not value:
        return {"ok": False, "memory_kind": kind or "unknown", "reason": "unsupported_memory_ref"}
    if kind in {"cognitive_stm", "cognitive_ltm"}:
        ok, reason = _validate_cognitive_ref(kind, value)
        return {"ok": ok, "memory_kind": kind, "reason": "" if ok else reason}
    if kind == "memory_observation":
        ok = _query_exists(_db(), "memory_observations", "observation_uid", value)
        return {"ok": ok, "memory_kind": kind, "reason": "" if ok else "missing_ref"}
    if kind == "memory_event":
        ok = _query_exists(_db(), "memory_events", "event_uid", value)
        return {"ok": ok, "memory_kind": kind, "reason": "" if ok else "missing_ref"}
    if kind == "local_context":
        return {"ok": True, "memory_kind": kind, "reason": ""}
    if kind == "commitment":
        ok = _query_exists(_db(), "commitments", "id", value)
        return {"ok": ok, "memory_kind": kind, "reason": "" if ok else "missing_ref"}
    if kind == "causal_edge":
        ok, reason = _validate_causal_edge(value)
        return {"ok": ok, "memory_kind": kind, "reason": "" if ok else reason}
    if kind == "change_log":
        ok = _query_exists(_db(), "change_log", "id", value)
        return {"ok": ok, "memory_kind": kind, "reason": "" if ok else "missing_ref"}
    if kind == "protocol_task":
        ok = _query_exists(_db(), "protocol_tasks", "task_id", value)
        return {"ok": ok, "memory_kind": kind, "reason": "" if ok else "missing_ref"}
    if kind == "workflow_run":
        ok = _query_exists(_db(), "workflow_runs", "run_id", value)
        return {"ok": ok, "memory_kind": kind, "reason": "" if ok else "missing_ref"}
    if kind == "workflow_checkpoint":
        ok = _query_exists(_db(), "workflow_checkpoints", "id", value)
        return {"ok": ok, "memory_kind": kind, "reason": "" if ok else "missing_ref"}
    if kind == "session_diary":
        ok = _query_exists(_db(), "session_diary", "id", value)
        return {"ok": ok, "memory_kind": kind, "reason": "" if ok else "missing_ref"}
    if kind == "learning":
        ok = _query_exists(_db(), "learnings", "id", value)
        return {"ok": ok, "memory_kind": kind, "reason": "" if ok else "missing_ref"}
    return {"ok": False, "memory_kind": kind, "reason": "unsupported_memory_ref"}


def use_event_uid_for(
    *,
    retrieval_trace_id: str = "",
    route_event_id: str = "",
    memory_ref: str,
    consumer_ref: str = "",
    use_stage: str = "retrieved",
    outcome: str = "unknown",
    validated_by_ref: str = "",
    evidence_refs: list[str] | tuple[str, ...] | None = None,
    policy_version: str = POLICY_VERSION,
) -> str:
    refs = sorted(_clean_ref(ref) for ref in (evidence_refs or []) if _clean_ref(ref))
    seed = "|".join(
        [
            _clean_ref(retrieval_trace_id),
            _clean_ref(route_event_id),
            _clean_ref(memory_ref),
            _clean_ref(consumer_ref),
            _clean_ref(use_stage),
            _clean_ref(outcome),
            _clean_ref(validated_by_ref),
            ",".join(refs),
            _clean_ref(policy_version) or POLICY_VERSION,
        ]
    )
    return _hash_text(seed)


def application_uid_for(
    *,
    memory_ref: str,
    target_field: str,
    policy_version: str,
    reason_code: str,
    event_uids_hash: str,
) -> str:
    seed = "|".join(
        [
            _clean_ref(memory_ref),
            _clean_ref(target_field),
            _clean_ref(policy_version) or POLICY_VERSION,
            _clean_ref(reason_code),
            _clean_ref(event_uids_hash),
        ]
    )
    return _hash_text(seed)


def _row_to_use_event(row: sqlite3.Row | None) -> dict[str, Any]:
    if not row:
        return {}
    item = dict(row)
    item["evidence_refs"] = _parse_json(item.pop("evidence_refs_json", "[]"), [])
    item["delta"] = _parse_json(item.pop("delta_json", "{}"), {})
    item["metadata"] = _parse_json(item.pop("metadata_json", "{}"), {})
    for key in ("used_in_answer", "cited_in_answer", "acted_on", "redaction_applied"):
        item[key] = bool(item.get(key))
    return item


def _has_feedback_evidence(
    *,
    outcome: str,
    use_stage: str,
    cited_in_answer: bool,
    acted_on: bool,
    validated_by_ref: str,
    evidence_refs: list[str],
) -> bool:
    if outcome not in {"helpful", "noise", "harmful"}:
        return True
    if use_stage not in {"cited", "acted_on", "validated"} and not (cited_in_answer or acted_on):
        return False
    if validated_by_ref or evidence_refs:
        return True
    return False


def record_use_event(
    *,
    memory_ref: str,
    retrieval_trace_id: str = "",
    route_event_id: str = "",
    session_id: str = "",
    conversation_id: str = "",
    project_key: str = "",
    client: str = "",
    consumer_ref: str = "",
    source_ref: str = "",
    query_text: str = "",
    query_hash: str = "",
    context_kind: str = "",
    use_stage: str = "retrieved",
    outcome: str = "unknown",
    used_in_answer: bool = False,
    cited_in_answer: bool = False,
    acted_on: bool = False,
    validated_by_ref: str = "",
    evidence_refs: list[str] | tuple[str, ...] | None = None,
    reason_code: str = "",
    delta: dict[str, Any] | None = None,
    policy_version: str = POLICY_VERSION,
    confidence: float = 0.5,
    privacy_level: str = "normal",
    metadata: dict[str, Any] | None = None,
    memory_kind: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    conn = _db()
    stamp = float(now if now is not None else time.time())
    stage = use_stage if use_stage in USE_STAGES else "retrieved"
    clean_outcome = outcome if outcome in OUTCOMES else "unknown"
    refs = sorted(_clean_ref(ref) for ref in (evidence_refs or []) if _clean_ref(ref))
    privacy = _privacy(privacy_level)
    expected_kind = memory_kind_for_ref(memory_ref)
    supplied_kind = _clean_ref(memory_kind)
    clean_reason = _clean_ref(reason_code)
    clean_delta = dict(delta or {})
    meta = dict(metadata or {})
    validation = validate_memory_ref(memory_ref)
    storage_kind = expected_kind or supplied_kind or "unknown"

    if supplied_kind and expected_kind and supplied_kind != expected_kind:
        clean_outcome = "unknown"
        clean_reason = "memory_kind_mismatch"
        clean_delta = {}
        meta["supplied_memory_kind"] = supplied_kind
        meta["expected_memory_kind"] = expected_kind
    elif not expected_kind:
        clean_outcome = "unknown"
        if clean_reason:
            meta["producer_reason_code"] = clean_reason
        clean_reason = "unsupported_memory_ref"
        clean_delta = {}
    elif not validation.get("ok"):
        clean_outcome = "unknown"
        if clean_reason:
            meta["producer_reason_code"] = clean_reason
        clean_reason = validation.get("reason") or "missing_ref"
        clean_delta = {}
    elif stage == "not_used":
        clean_outcome = "neutral"
        if clean_reason:
            meta["producer_reason_code"] = clean_reason
        clean_reason = "not_used_no_delta"
        clean_delta = {}
    elif not _has_feedback_evidence(
        outcome=clean_outcome,
        use_stage=stage,
        cited_in_answer=cited_in_answer,
        acted_on=acted_on,
        validated_by_ref=_clean_ref(validated_by_ref),
        evidence_refs=refs,
    ):
        clean_outcome = "unknown"
        if clean_reason:
            meta["producer_reason_code"] = clean_reason
        clean_reason = "insufficient_evidence"
        clean_delta = {}

    preview, redacted = redact_preview(query_text, privacy_level=privacy)
    if stage != use_stage:
        meta["invalid_use_stage"] = use_stage
    if outcome not in OUTCOMES:
        meta["invalid_outcome"] = outcome

    event_uid = use_event_uid_for(
        retrieval_trace_id=retrieval_trace_id,
        route_event_id=route_event_id,
        memory_ref=memory_ref,
        consumer_ref=consumer_ref,
        use_stage=stage,
        outcome=clean_outcome,
        validated_by_ref=validated_by_ref,
        evidence_refs=refs,
        policy_version=policy_version,
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO memory_use_events (
            event_uid, created_at, retrieval_trace_id, route_event_id,
            session_id, conversation_id, project_key, client, consumer_ref,
            memory_ref, memory_kind, source_ref, query_hash,
            query_preview_redacted, context_kind, use_stage, outcome,
            used_in_answer, cited_in_answer, acted_on, validated_by_ref,
            evidence_refs_json, reason_code, delta_json, policy_version,
            confidence, privacy_level, redaction_applied, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_uid,
            stamp,
            _clean_ref(retrieval_trace_id),
            _clean_ref(route_event_id),
            _clean_ref(session_id),
            _clean_ref(conversation_id),
            _clean_ref(project_key),
            _clean_ref(client),
            _clean_ref(consumer_ref),
            _clean_ref(memory_ref),
            storage_kind,
            _clean_ref(source_ref),
            _clean_ref(query_hash) or query_hash_for(query_text),
            preview,
            _clean_ref(context_kind),
            stage,
            clean_outcome,
            _bool_int(used_in_answer),
            _bool_int(cited_in_answer),
            _bool_int(acted_on),
            _clean_ref(validated_by_ref),
            _json(refs),
            clean_reason,
            _json(clean_delta),
            _clean_ref(policy_version) or POLICY_VERSION,
            _clamp(float(confidence or 0.0)),
            privacy,
            _bool_int(redacted),
            _json(meta),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM memory_use_events WHERE event_uid=?", (event_uid,)).fetchone()
    item = _row_to_use_event(row)
    item["ok"] = True
    return item


def list_use_events(
    *,
    memory_ref: str = "",
    policy_version: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    conn = _db()
    params: list[Any] = []
    where: list[str] = []
    if memory_ref:
        where.append("memory_ref=?")
        params.append(memory_ref)
    if policy_version:
        where.append("policy_version=?")
        params.append(policy_version)
    sql = "SELECT * FROM memory_use_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit or 50), 500)))
    return [_row_to_use_event(row) for row in conn.execute(sql, params).fetchall()]


def _row_to_application(row: sqlite3.Row | None) -> dict[str, Any]:
    if not row:
        return {}
    item = dict(row)
    item["event_uids"] = _parse_json(item.pop("event_uids_json", "[]"), [])
    item["metadata"] = _parse_json(item.pop("metadata_json", "{}"), {})
    item["applied"] = bool(item.get("applied"))
    item["rolled_back"] = bool(item.get("rolled_back"))
    return item


def _has_application_for_event(conn, event_uid: str, memory_ref: str, target_field: str, policy_version: str) -> bool:
    return conn.execute(
        """
        SELECT 1 FROM memory_utility_application_events
        WHERE event_uid=? AND memory_ref=? AND target_field=? AND policy_version=?
        LIMIT 1
        """,
        (event_uid, memory_ref, target_field, policy_version),
    ).fetchone() is not None


def _eligible_event(event: dict[str, Any], outcome: str) -> bool:
    if event.get("outcome") != outcome:
        return False
    stage = str(event.get("use_stage") or "")
    evidence_refs = event.get("evidence_refs") or []
    validated_by_ref = str(event.get("validated_by_ref") or "")
    if outcome == "helpful":
        if stage not in {"cited", "acted_on", "validated"} and not (event.get("cited_in_answer") or event.get("acted_on")):
            return False
        return bool(validated_by_ref or evidence_refs)
    if outcome == "harmful":
        if stage not in {"cited", "acted_on", "validated"} and not (event.get("cited_in_answer") or event.get("acted_on")):
            return False
        return bool(validated_by_ref.startswith("correction:") or evidence_refs)
    if outcome == "noise":
        if stage not in {"injected", "cited", "acted_on", "validated"}:
            return False
        return bool(validated_by_ref or evidence_refs)
    return False


def _unused_eligible_events(
    conn,
    *,
    memory_ref: str,
    policy_version: str,
    target_field: str,
    outcome: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM memory_use_events
        WHERE memory_ref=? AND policy_version=?
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (memory_ref, policy_version, max(1, min(int(limit or 50), 500))),
    ).fetchall()
    events = [_row_to_use_event(row) for row in rows]
    return [
        event
        for event in events
        if _eligible_event(event, outcome)
        and not _has_application_for_event(conn, event["event_uid"], memory_ref, target_field, policy_version)
    ]


def _signal_for_target(
    conn,
    *,
    memory_ref: str,
    policy_version: str,
    target_field: str,
    min_samples: int,
) -> tuple[str, list[dict[str, Any]], str]:
    harmful = _unused_eligible_events(
        conn,
        memory_ref=memory_ref,
        policy_version=policy_version,
        target_field=target_field,
        outcome="harmful",
    )
    strong_corrections = [
        event
        for event in harmful
        if str(event.get("validated_by_ref") or "").startswith("correction:")
        or str(event.get("reason_code") or "") == "explicit_correction"
    ]
    if strong_corrections:
        return "harmful", strong_corrections[:1], "explicit_correction"
    if len(harmful) >= max(1, min_samples):
        return "harmful", harmful[: max(1, min_samples)], "harmful_validated"

    helpful = _unused_eligible_events(
        conn,
        memory_ref=memory_ref,
        policy_version=policy_version,
        target_field=target_field,
        outcome="helpful",
    )
    if len(helpful) >= max(1, min_samples):
        return "helpful", helpful[: max(1, min_samples)], "helpful_validated"

    noise = _unused_eligible_events(
        conn,
        memory_ref=memory_ref,
        policy_version=policy_version,
        target_field=target_field,
        outcome="noise",
    )
    if len(noise) >= max(1, min_samples):
        return "noise", noise[: max(1, min_samples)], "noise_validated"
    return "", [], ""


def _target_plan(memory_kind: str, outcome: str) -> dict[str, float]:
    if memory_kind in {"cognitive_stm", "cognitive_ltm"}:
        if outcome == "helpful":
            return {"strength": 0.05, "stability": 0.03, "difficulty": -0.02}
        if outcome == "harmful":
            plan = {"strength": -0.10, "difficulty": 0.05}
            if memory_kind == "cognitive_ltm":
                plan["tags"] = 0.0
            return plan
        if outcome == "noise":
            return {"strength": -0.02}
    if memory_kind == "memory_observation":
        if outcome == "helpful":
            return {"salience": 0.04, "confidence": 0.03, "stability": 0.02}
        if outcome == "harmful":
            return {"confidence": -0.08, "salience": -0.05, "status": 0.0}
        if outcome == "noise":
            return {"salience": -0.02}
    if memory_kind in {"local_context", "causal_edge", "commitment", "learning"}:
        return {"policy": 0.0}
    return {}


def _cognitive_table_for(kind: str) -> str:
    return "stm_memories" if kind == "cognitive_stm" else "ltm_memories"


def _is_protected_memory(memory_ref: str, memory_kind: str) -> bool:
    if memory_kind not in {"cognitive_stm", "cognitive_ltm"}:
        return False
    table = _cognitive_table_for(memory_kind)
    row = _cognitive_db().execute(f"SELECT * FROM {table} WHERE id=?", (_ref_value(memory_ref),)).fetchone()
    if not row:
        return False
    item = dict(row)
    lifecycle = str(item.get("lifecycle_state") or "")
    source_type = str(item.get("source_type") or "")
    return lifecycle == "pinned" or source_type in {"learning", "core_rule"}


def _cooldown_active(conn, *, memory_ref: str, reason_code: str, policy_version: str, now: float, cooldown_seconds: float) -> bool:
    if cooldown_seconds <= 0:
        return False
    row = conn.execute(
        """
        SELECT created_at FROM memory_utility_applications
        WHERE memory_ref=? AND reason_code=? AND policy_version=? AND rolled_back=0
        ORDER BY created_at DESC LIMIT 1
        """,
        (memory_ref, reason_code, policy_version),
    ).fetchone()
    return bool(row and (now - float(row["created_at"] or 0.0)) < cooldown_seconds)


def _remaining_daily_delta(conn, *, memory_ref: str, now: float, max_daily_abs_delta: float) -> float:
    if max_daily_abs_delta <= 0:
        return 0.0
    floor = now - 86400.0
    row = conn.execute(
        """
        SELECT COALESCE(SUM(ABS(delta)), 0) AS total
        FROM memory_utility_applications
        WHERE memory_ref=? AND created_at>=? AND applied=1 AND rolled_back=0
        """,
        (memory_ref, floor),
    ).fetchone()
    used = float(row["total"] or 0.0) if row else 0.0
    return max(0.0, float(max_daily_abs_delta) - used)


def _clamp_delta_to_remaining(delta: float, remaining: float) -> float:
    if remaining <= 0:
        return 0.0
    if abs(delta) <= remaining:
        return delta
    return remaining if delta > 0 else -remaining


def _apply_numeric_target(memory_ref: str, memory_kind: str, target_field: str, delta: float) -> tuple[float | None, float | None, bool, dict[str, Any]]:
    if target_field in {"tags", "status", "policy"}:
        return _apply_special_target(memory_ref, memory_kind, target_field, delta)
    if memory_kind in {"cognitive_stm", "cognitive_ltm"}:
        table = _cognitive_table_for(memory_kind)
        conn = _cognitive_db()
        row = conn.execute(f"SELECT {target_field} FROM {table} WHERE id=?", (_ref_value(memory_ref),)).fetchone()
        if not row:
            return None, None, False, {"reason": "missing_ref"}
        old = float(row[target_field] or 0.0)
        new = _clamp(old + delta)
        conn.execute(f"UPDATE {table} SET {target_field}=? WHERE id=?", (new, _ref_value(memory_ref)))
        conn.commit()
        return old, new, True, {}
    if memory_kind == "memory_observation":
        conn = _db()
        row = conn.execute(
            f"SELECT {target_field} FROM memory_observations WHERE observation_uid=?",
            (_ref_value(memory_ref),),
        ).fetchone()
        if not row:
            return None, None, False, {"reason": "missing_ref"}
        old = float(row[target_field] or 0.0)
        new = _clamp(old + delta)
        conn.execute(
            f"UPDATE memory_observations SET {target_field}=?, updated_at=? WHERE observation_uid=?",
            (new, time.time(), _ref_value(memory_ref)),
        )
        conn.commit()
        return old, new, True, {}
    return None, None, False, {"reason": "unsupported_target"}


def _apply_special_target(memory_ref: str, memory_kind: str, target_field: str, delta: float) -> tuple[float | None, float | None, bool, dict[str, Any]]:
    if memory_kind == "cognitive_ltm" and target_field == "tags":
        conn = _cognitive_db()
        if not _column_exists(conn, "ltm_memories", "tags"):
            return None, None, False, {"reason": "tags_column_unavailable"}
        row = conn.execute("SELECT tags FROM ltm_memories WHERE id=?", (_ref_value(memory_ref),)).fetchone()
        if not row:
            return None, None, False, {"reason": "missing_ref"}
        tags = str(row["tags"] or "")
        if "under_review" not in {tag.strip() for tag in tags.split(",") if tag.strip()}:
            new_tags = f"{tags},under_review".strip(",")
            conn.execute("UPDATE ltm_memories SET tags=? WHERE id=?", (new_tags, _ref_value(memory_ref)))
            conn.commit()
        return None, None, True, {"action": "tag_under_review"}
    if memory_kind == "memory_observation" and target_field == "status":
        conn = _db()
        conn.execute(
            "UPDATE memory_observations SET status='review', updated_at=? WHERE observation_uid=?",
            (time.time(), _ref_value(memory_ref)),
        )
        conn.commit()
        return None, None, True, {"action": "status_review"}
    if memory_kind == "causal_edge":
        return None, None, False, {"route": "causal_graph", "edge_uid": _ref_value(memory_ref)}
    if memory_kind == "local_context":
        return None, None, False, {"route": "local_context_policy", "content_edit": False}
    if memory_kind == "commitment":
        return None, None, False, {"route": "commitment_recall_policy", "status_edit": False}
    if memory_kind == "learning":
        return None, None, False, {"route": "learning_review", "delete": False}
    return None, None, False, {"reason": "unsupported_special_target"}


def _insert_application(
    conn,
    *,
    memory_ref: str,
    memory_kind: str,
    target_field: str,
    policy_version: str,
    reason_code: str,
    events: list[dict[str, Any]],
    old_value: float | None,
    new_value: float | None,
    delta: float,
    applied: bool,
    metadata: dict[str, Any],
    now: float,
) -> dict[str, Any]:
    event_uids = [event["event_uid"] for event in events]
    event_uids_hash = _hash_text(",".join(sorted(event_uids)))
    application_uid = application_uid_for(
        memory_ref=memory_ref,
        target_field=target_field,
        policy_version=policy_version,
        reason_code=reason_code,
        event_uids_hash=event_uids_hash,
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO memory_utility_applications (
            application_uid, created_at, memory_ref, memory_kind, target_field,
            policy_version, reason_code, event_uids_hash, event_uids_json,
            old_value, new_value, delta, applied, rolled_back, rollback_ref,
            metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', ?)
        """,
        (
            application_uid,
            now,
            memory_ref,
            memory_kind,
            target_field,
            policy_version,
            reason_code,
            event_uids_hash,
            _json(event_uids),
            old_value,
            new_value,
            float(delta),
            _bool_int(applied),
            _json(metadata),
        ),
    )
    for event_uid in event_uids:
        conn.execute(
            """
            INSERT OR IGNORE INTO memory_utility_application_events (
                application_uid, event_uid, memory_ref, target_field, policy_version
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (application_uid, event_uid, memory_ref, target_field, policy_version),
        )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM memory_utility_applications WHERE application_uid=?",
        (application_uid,),
    ).fetchone()
    item = _row_to_application(row)
    item["ok"] = True
    return item


def apply_memory_utility(
    *,
    memory_ref: str,
    policy_version: str = POLICY_VERSION,
    min_samples: int = 3,
    cooldown_seconds: float = 86400.0,
    max_daily_abs_delta: float = 0.15,
    now: float | None = None,
    shadow_mode: bool = False,
) -> dict[str, Any]:
    conn = _db()
    stamp = float(now if now is not None else time.time())
    validation = validate_memory_ref(memory_ref)
    if not validation.get("ok"):
        return {"ok": False, "reason": validation.get("reason") or "missing_ref", "applications": [], "suppressed": []}
    memory_kind = validation["memory_kind"]
    applications: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    applied_reason_codes_this_batch: set[str] = set()

    for target_field in sorted(_target_plan(memory_kind, "harmful") | _target_plan(memory_kind, "helpful") | _target_plan(memory_kind, "noise")):
        outcome, events, reason_code = _signal_for_target(
            conn,
            memory_ref=memory_ref,
            policy_version=policy_version,
            target_field=target_field,
            min_samples=min_samples,
        )
        if not outcome:
            suppressed.append({"target_field": target_field, "reason": "insufficient_samples"})
            continue
        plan = _target_plan(memory_kind, outcome)
        if target_field not in plan:
            continue
        if outcome in {"harmful", "noise"} and _is_protected_memory(memory_ref, memory_kind):
            suppressed.append({"target_field": target_field, "reason": "protected_memory"})
            continue
        if reason_code not in applied_reason_codes_this_batch and _cooldown_active(
            conn,
            memory_ref=memory_ref,
            reason_code=reason_code,
            policy_version=policy_version,
            now=stamp,
            cooldown_seconds=cooldown_seconds,
        ):
            suppressed.append({"target_field": target_field, "reason": "cooldown", "reason_code": reason_code})
            continue
        remaining = _remaining_daily_delta(conn, memory_ref=memory_ref, now=stamp, max_daily_abs_delta=max_daily_abs_delta)
        delta = _clamp_delta_to_remaining(float(plan[target_field]), remaining)
        if delta == 0.0 and target_field not in {"tags", "status", "policy"}:
            suppressed.append({"target_field": target_field, "reason": "daily_delta_limit"})
            continue
        if shadow_mode:
            old_value, new_value, applied, meta = None, None, False, {"shadow_mode": True}
        else:
            old_value, new_value, applied, meta = _apply_numeric_target(memory_ref, memory_kind, target_field, delta)
        meta.update({"outcome": outcome, "sample_count": len(events), "policy_version": policy_version})
        applications.append(
            _insert_application(
                conn,
                memory_ref=memory_ref,
                memory_kind=memory_kind,
                target_field=target_field,
                policy_version=policy_version,
                reason_code=reason_code,
                events=events,
                old_value=old_value,
                new_value=new_value,
                delta=delta,
                applied=applied,
                metadata=meta,
                now=stamp,
            )
        )
        applied_reason_codes_this_batch.add(reason_code)

    return {"ok": True, "memory_ref": memory_ref, "memory_kind": memory_kind, "applications": applications, "suppressed": suppressed}


def rollback_applications(*, memory_ref: str, rollback_ref: str, limit: int = 50) -> dict[str, Any]:
    conn = _db()
    rows = conn.execute(
        """
        SELECT * FROM memory_utility_applications
        WHERE memory_ref=? AND applied=1 AND rolled_back=0
        ORDER BY created_at DESC LIMIT ?
        """,
        (memory_ref, max(1, min(int(limit or 50), 500))),
    ).fetchall()
    rolled_back: list[str] = []
    for row in rows:
        item = _row_to_application(row)
        field = str(item["target_field"])
        if field in {"strength", "stability", "difficulty"} and item.get("old_value") is not None:
            table = _cognitive_table_for(item["memory_kind"])
            _cognitive_db().execute(f"UPDATE {table} SET {field}=? WHERE id=?", (float(item["old_value"]), _ref_value(memory_ref)))
            _cognitive_db().commit()
        elif field in {"salience", "confidence", "stability"} and item.get("old_value") is not None:
            _db().execute(
                f"UPDATE memory_observations SET {field}=?, updated_at=? WHERE observation_uid=?",
                (float(item["old_value"]), time.time(), _ref_value(memory_ref)),
            )
            _db().commit()
        conn.execute(
            "UPDATE memory_utility_applications SET rolled_back=1, rollback_ref=? WHERE application_uid=?",
            (_clean_ref(rollback_ref), item["application_uid"]),
        )
        rolled_back.append(item["application_uid"])
    conn.commit()
    return {"ok": True, "memory_ref": memory_ref, "rolled_back": rolled_back}


def explain_score_change(*, memory_ref: str, limit: int = 20) -> dict[str, Any]:
    conn = _db()
    rows = conn.execute(
        """
        SELECT * FROM memory_utility_applications
        WHERE memory_ref=?
        ORDER BY created_at DESC LIMIT ?
        """,
        (memory_ref, max(1, min(int(limit or 20), 100))),
    ).fetchall()
    applications = []
    for row in rows:
        item = _row_to_application(row)
        applications.append(
            {
                "application_uid": item["application_uid"],
                "created_at": item["created_at"],
                "target_field": item["target_field"],
                "reason_code": item["reason_code"],
                "delta": item["delta"],
                "old_value": item["old_value"],
                "new_value": item["new_value"],
                "event_refs": [f"memory_use_event:{uid}" for uid in item["event_uids"]],
                "applied": item["applied"],
                "rolled_back": item["rolled_back"],
            }
        )
    return {"ok": True, "memory_ref": memory_ref, "applications": applications}
