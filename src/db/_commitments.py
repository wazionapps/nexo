"""Durable commitment ledger for future-action promises.

The ledger is an index over promises and their linked action artifacts. It is
not a scheduler: followups, workflows, outcomes, and protocol tasks remain the
systems that execute work.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from db._core import get_db, now_epoch


ACTIVE_STATUSES = {"active", "in_progress", "pending"}
CLOSED_STATUSES = {"fulfilled", "missed", "cancelled", "superseded"}
VALID_OWNERS = {"agent", "user", "shared", "waiting"}
VALID_STATUSES = ACTIVE_STATUSES | CLOSED_STATUSES
_WORD_RE = re.compile(r"[a-z0-9_]+")


def _clean_text(value: Any, *, max_chars: int = 1200) -> str:
    text = str(value or "").strip()
    if len(text) > max_chars:
        return text[: max(0, max_chars - 3)].rstrip() + "..."
    return text


def _status(value: str) -> str:
    clean = str(value or "active").strip().lower()
    return clean if clean in VALID_STATUSES else "active"


def _owner(value: str) -> str:
    clean = str(value or "agent").strip().lower()
    return clean if clean in VALID_OWNERS else "agent"


def _json(value: dict[str, Any] | None) -> str:
    try:
        return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return "{}"


def _tokens(value: str) -> set[str]:
    return {item for item in _WORD_RE.findall(str(value or "").lower()) if len(item) >= 3}


def _dedupe_key(*, source_type: str, source_id: str, session_id: str, statement: str) -> str:
    seed = "|".join(
        [
            _clean_text(source_type, max_chars=80).lower(),
            _clean_text(source_id, max_chars=160).lower(),
            _clean_text(session_id, max_chars=160).lower(),
            _clean_text(statement, max_chars=600).lower(),
        ]
    )
    return hashlib.sha1(seed.encode("utf-8", errors="ignore"), usedforsecurity=False).hexdigest()


def _commitment_id(dedupe_key: str) -> str:
    return f"CM-{dedupe_key[:16].upper()}"


def _row_dict(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    data = dict(row)
    try:
        data["metadata"] = json.loads(data.get("metadata_json") or "{}")
    except Exception:
        data["metadata"] = {}
    return data


def create_commitment(
    *,
    statement: str,
    source_type: str = "",
    source_id: str = "",
    memory_event_uid: str = "",
    session_id: str = "",
    conversation_id: str = "",
    project_key: str = "",
    owner: str = "agent",
    deadline: str = "",
    status: str = "active",
    confidence: float = 0.5,
    action_ref_type: str = "",
    action_ref_id: str = "",
    outcome_id: int | None = None,
    evidence_ref: str = "",
    dedupe_key: str = "",
    metadata: dict[str, Any] | None = None,
    created_at: float | None = None,
) -> dict[str, Any]:
    clean_statement = _clean_text(statement)
    if not clean_statement:
        return {"ok": False, "error": "statement_required"}
    stamp = float(created_at if created_at is not None else now_epoch())
    clean_source_type = _clean_text(source_type, max_chars=80)
    clean_source_id = _clean_text(source_id, max_chars=180)
    clean_session_id = _clean_text(session_id, max_chars=180)
    key = _clean_text(dedupe_key, max_chars=80) or _dedupe_key(
        source_type=clean_source_type,
        source_id=clean_source_id,
        session_id=clean_session_id,
        statement=clean_statement,
    )
    commitment_id = _commitment_id(key)
    conn = get_db()
    existing = conn.execute("SELECT * FROM commitments WHERE dedupe_key = ? LIMIT 1", (key,)).fetchone()
    if existing:
        result = _row_dict(existing)
        result.update({"ok": True, "created": False})
        return result
    conn.execute(
        """
        INSERT INTO commitments (
            id, created_at, updated_at, closed_at, source_type, source_id,
            memory_event_uid, session_id, conversation_id, project_key,
            statement, owner, deadline, status, confidence, action_ref_type,
            action_ref_id, outcome_id, evidence_ref, dedupe_key, metadata_json
        )
        VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            commitment_id,
            stamp,
            stamp,
            clean_source_type,
            clean_source_id,
            _clean_text(memory_event_uid, max_chars=180),
            clean_session_id,
            _clean_text(conversation_id, max_chars=180),
            _clean_text(project_key, max_chars=120),
            clean_statement,
            _owner(owner),
            _clean_text(deadline, max_chars=80),
            _status(status),
            max(0.0, min(1.0, float(confidence or 0.5))),
            _clean_text(action_ref_type, max_chars=80),
            _clean_text(action_ref_id, max_chars=180),
            int(outcome_id) if outcome_id is not None else None,
            _clean_text(evidence_ref, max_chars=240),
            key,
            _json(metadata),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM commitments WHERE id = ?", (commitment_id,)).fetchone()
    result = _row_dict(row)
    result.update({"ok": True, "created": True})
    return result


def list_commitments(
    *,
    query: str = "",
    status: str = "",
    session_id: str = "",
    project_key: str = "",
    owner: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if status:
        clean_status = status.strip().lower()
        if clean_status in {"open", "active"}:
            clauses.append("status IN ('active','in_progress','pending')")
        elif clean_status in {"closed", "resolved"}:
            clauses.append("status IN ('fulfilled','missed','cancelled','superseded')")
        else:
            clauses.append("status = ?")
            params.append(_status(clean_status))
    if session_id.strip():
        clauses.append("session_id = ?")
        params.append(session_id.strip())
    if project_key.strip():
        clauses.append("project_key = ?")
        params.append(project_key.strip())
    if owner.strip():
        clauses.append("owner = ?")
        params.append(_owner(owner))
    max_items = max(1, min(int(limit or 20), 100))
    terms = _tokens(query)
    # Query filtering is semantic-ish and happens in Python over multiple
    # fields. Fetch a larger bounded window first so older relevant open
    # commitments do not disappear merely because the caller requested a
    # small result limit.
    query_window = 500 if terms else max_items * 3
    rows = get_db().execute(
        f"""
        SELECT * FROM commitments
         WHERE {' AND '.join(clauses)}
         ORDER BY
            CASE status WHEN 'active' THEN 0 WHEN 'in_progress' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END,
            COALESCE(deadline, '') ASC,
            updated_at DESC
         LIMIT ?
        """,
        [*params, query_window],
    ).fetchall()
    items = [_row_dict(row) for row in rows]
    if terms:
        filtered = []
        for item in items:
            haystack = _tokens(
                " ".join(
                    str(item.get(field) or "")
                    for field in (
                        "id",
                        "statement",
                        "source_type",
                        "source_id",
                        "session_id",
                        "project_key",
                        "action_ref_type",
                        "action_ref_id",
                        "evidence_ref",
                    )
                )
            )
            if terms & haystack:
                filtered.append(item)
        items = filtered
    return items[:max_items]


def update_commitment_status(
    commitment_id: str,
    *,
    status: str,
    evidence_ref: str = "",
    action_ref_type: str = "",
    action_ref_id: str = "",
    outcome_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    clean_status = _status(status)
    stamp = float(now if now is not None else now_epoch())
    closed_at = stamp if clean_status in CLOSED_STATUSES else None
    conn = get_db()
    row = conn.execute("SELECT * FROM commitments WHERE id = ?", (commitment_id.strip(),)).fetchone()
    if not row:
        return {"ok": False, "error": f"commitment_not_found:{commitment_id}"}
    merged_metadata = _row_dict(row).get("metadata") or {}
    merged_metadata.update(metadata or {})
    conn.execute(
        """
        UPDATE commitments
           SET status = ?,
               updated_at = ?,
               closed_at = ?,
               evidence_ref = COALESCE(NULLIF(?, ''), evidence_ref),
               action_ref_type = COALESCE(NULLIF(?, ''), action_ref_type),
               action_ref_id = COALESCE(NULLIF(?, ''), action_ref_id),
               outcome_id = COALESCE(?, outcome_id),
               metadata_json = ?
         WHERE id = ?
        """,
        (
            clean_status,
            stamp,
            closed_at,
            _clean_text(evidence_ref, max_chars=240),
            _clean_text(action_ref_type, max_chars=80),
            _clean_text(action_ref_id, max_chars=180),
            int(outcome_id) if outcome_id is not None else None,
            _json(merged_metadata),
            commitment_id.strip(),
        ),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM commitments WHERE id = ?", (commitment_id.strip(),)).fetchone()
    result = _row_dict(updated)
    result["ok"] = True
    return result


def resolve_matching_commitments(
    *,
    session_id: str = "",
    evidence_text: str = "",
    action_ref_type: str = "",
    action_ref_id: str = "",
    evidence_ref: str = "",
    status: str = "fulfilled",
    limit: int = 5,
) -> dict[str, Any]:
    """Close active commitments when completion evidence overlaps enough."""
    terms = _tokens(evidence_text)
    if not terms and not (action_ref_type and action_ref_id):
        return {"ok": True, "resolved": 0, "items": [], "reason": "no_matching_signal"}
    candidates = list_commitments(session_id=session_id, status="open", limit=max(1, min(limit, 20)))
    resolved: list[dict[str, Any]] = []
    for item in candidates:
        action_match = (
            bool(action_ref_type and action_ref_id)
            and item.get("action_ref_type") == action_ref_type
            and item.get("action_ref_id") == action_ref_id
        )
        statement_terms = _tokens(str(item.get("statement") or ""))
        matched_terms = terms & statement_terms
        overlap = len(matched_terms) / max(1, len(statement_terms))
        required_matches = max(4, math.ceil(len(statement_terms) * 0.55))
        strong_text_match = bool(
            len(matched_terms) >= required_matches
            and overlap >= 0.65
        )
        if not action_match and not strong_text_match:
            continue
        resolved.append(
            update_commitment_status(
                str(item.get("id")),
                status=status,
                evidence_ref=evidence_ref,
                action_ref_type=action_ref_type,
                action_ref_id=action_ref_id,
                metadata={
                    "resolved_by": "action_ref" if action_match else "strong_matching_evidence",
                    "overlap": round(overlap, 4),
                    "matched_terms": sorted(matched_terms)[:12],
                },
            )
        )
        if len(resolved) >= limit:
            break
    return {"ok": True, "resolved": len(resolved), "items": resolved}


__all__ = [
    "create_commitment",
    "list_commitments",
    "update_commitment_status",
    "resolve_matching_commitments",
]
