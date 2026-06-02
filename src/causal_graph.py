"""Operational causal graph facade over the existing Knowledge Graph.

Verified causal edges live in ``kg_edges``. Unverified suggestions live in
``causal_edge_candidates`` until a caller explicitly promotes them.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VALID_RELATIONS = {
    "causal:motivated_by",
    "causal:resolved_by",
    "causal:prevented",
    "causal:verified_by",
    "causal:depends_on",
    "causal:blocked_by",
    "causal:superseded_by",
    "causal:regressed_by",
    "causal:reverted_by",
    "ops:contains",
    "ops:produced",
    "ops:reviewed_by",
    "ops:approved_by",
}
ACTIVE_EDGE_STATUSES = {"active", "verified", "stale", "contradicted", "superseded", "retracted"}
CANDIDATE_STATUSES = {"proposed", "review", "approved", "promoted", "rejected", "expired", "superseded"}
PRIVACY_LEVELS = {"public", "normal", "private", "sensitive", "secret"}
PRIVACY_ALIASES = {"internal": "normal", "confidential": "sensitive"}
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


def _db():
    import db

    return db.get_db()


def _kg():
    import knowledge_graph

    return knowledge_graph


def _kg_db():
    import cognitive

    return cognitive._get_db()


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone() is not None
    except Exception:
        return False


def _parse_json(value: str, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else default
    except Exception:
        return default


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _privacy(value: str) -> str:
    clean = _normalize(value)
    clean = PRIVACY_ALIASES.get(clean, clean)
    return clean if clean in PRIVACY_LEVELS else "private"


def redact_reason(reason: str, *, privacy_level: str = "normal", max_chars: int = 240) -> tuple[str, bool]:
    privacy = _privacy(privacy_level)
    if privacy == "secret":
        return "", bool(reason)
    text = str(reason or "").strip()
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    if len(redacted) > max_chars:
        redacted = redacted[: max(0, max_chars - 3)].rstrip() + "..."
    return redacted, redacted != text


def edge_uid_for(
    *,
    source_type: str,
    source_ref: str,
    relation: str,
    target_type: str,
    target_ref: str,
    evidence_refs: list[str] | tuple[str, ...] | None,
) -> str:
    seed = "|".join(
        [
            _normalize(source_type),
            _normalize(source_ref),
            relation.strip(),
            _normalize(target_type),
            _normalize(target_ref),
            ",".join(sorted(str(ref).strip() for ref in (evidence_refs or []) if str(ref).strip())),
        ]
    )
    return hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()


def candidate_uid_for(
    *,
    project_key: str = "",
    source_type: str,
    source_ref: str,
    relation: str,
    target_type: str,
    target_ref: str,
    producer: str,
    source_event_uid: str = "",
    evidence_refs: list[str] | tuple[str, ...] | None,
) -> str:
    seed = "|".join(
        [
            _normalize(project_key),
            _normalize(source_type),
            _normalize(source_ref),
            relation.strip(),
            _normalize(target_type),
            _normalize(target_ref),
            _normalize(producer),
            _normalize(source_event_uid),
            ",".join(sorted(str(ref).strip() for ref in (evidence_refs or []) if str(ref).strip())),
        ]
    )
    return hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()


def ensure_kg_indexes() -> None:
    conn = _kg_db()
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_edges_source_relation_active ON kg_edges(source_id, relation, valid_until)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_edges_target_relation_active ON kg_edges(target_id, relation, valid_until)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_edges_relation_active ON kg_edges(relation, valid_until)")
    conn.commit()


def _query_exists(conn, table: str, column: str, value: str) -> bool:
    if not _table_exists(conn, table):
        return False
    row = conn.execute(f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1", (value,)).fetchone()
    return row is not None


def _path_exists(ref: str, *, repo_root: Path) -> bool:
    raw = str(ref or "").strip()
    if not raw:
        return False
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = repo_root / raw
    try:
        return path.exists()
    except Exception:
        return False


def _git_commit_exists(ref: str, *, repo_root: Path) -> bool:
    clean = str(ref or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", clean):
        return False
    if not (repo_root / ".git").exists():
        return True
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{clean}^{{commit}}"],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def validate_ref(
    ref_type: str,
    ref: str,
    *,
    evidence_refs: list[str] | None = None,
    repo_root: Path = ROOT,
) -> tuple[bool, str]:
    clean_type = _normalize(ref_type)
    clean_ref = str(ref or "").strip()
    if not clean_type or not clean_ref:
        return False, "missing_ref"

    conn = _db()
    if clean_type == "protocol_task":
        return (_query_exists(conn, "protocol_tasks", "task_id", clean_ref), "missing_ref")
    if clean_type in {"workflow", "workflow_run"}:
        return (_query_exists(conn, "workflow_runs", "run_id", clean_ref), "missing_ref")
    if clean_type == "workflow_checkpoint":
        return (_query_exists(conn, "workflow_checkpoints", "id", clean_ref), "missing_ref")
    if clean_type == "commitment":
        return (_query_exists(conn, "commitments", "id", clean_ref), "missing_ref")
    if clean_type in {"change_log", "change"}:
        return (_query_exists(conn, "change_log", "id", clean_ref), "missing_ref")
    if clean_type == "memory_event":
        return (_query_exists(conn, "memory_events", "event_uid", clean_ref), "missing_ref")
    if clean_type in {"file", "artifact", "spec", "audit", "test"}:
        if _path_exists(clean_ref, repo_root=repo_root):
            return True, ""
        return (bool(evidence_refs), "missing_ref")
    if clean_type == "release":
        return (bool(re.fullmatch(r"v?\d+\.\d+(?:\.\d+)?(?:[-+][a-zA-Z0-9_.-]+)?", clean_ref)), "missing_ref")
    if clean_type == "commit":
        return (_git_commit_exists(clean_ref, repo_root=repo_root), "missing_ref")
    if clean_type == "risk":
        return (clean_ref.startswith("risk:") and bool(evidence_refs), "missing_ref")
    if clean_type == "finding":
        return (clean_ref.startswith("finding:") and bool(evidence_refs), "missing_ref")
    return False, "unsupported_ref_type"


def _candidate_row(row: sqlite3.Row | None) -> dict[str, Any]:
    if not row:
        return {}
    item = dict(row)
    item["evidence_refs"] = _parse_json(item.pop("evidence_refs_json", "[]"), [])
    item["metadata"] = _parse_json(item.pop("metadata_json", "{}"), {})
    return item


def propose_candidate(
    *,
    source_type: str,
    source_ref: str,
    relation: str,
    target_type: str,
    target_ref: str,
    reason_public: str = "",
    evidence_refs: list[str] | None = None,
    source_event_uid: str = "",
    producer: str = "manual",
    project_key: str = "",
    privacy_level: str = "normal",
    confidence: float = 0.5,
    status: str = "proposed",
    metadata: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    conn = _db()
    stamp = float(now if now is not None else time.time())
    refs = [str(ref).strip() for ref in (evidence_refs or []) if str(ref).strip()]
    privacy = _privacy(privacy_level)
    clean_reason, redacted = redact_reason(reason_public, privacy_level=privacy)
    clean_status = status if status in CANDIDATE_STATUSES else "review"
    review_reason = ""
    if relation not in VALID_RELATIONS:
        clean_status = "review"
        review_reason = "unknown_relation"
    elif privacy == "secret":
        clean_status = "review"
        review_reason = "secret_reference_only"
    elif not refs:
        clean_status = "review"
        review_reason = "missing_evidence"
    candidate_uid = candidate_uid_for(
        project_key=project_key,
        source_type=source_type,
        source_ref=source_ref,
        relation=relation,
        target_type=target_type,
        target_ref=target_ref,
        producer=producer,
        source_event_uid=source_event_uid,
        evidence_refs=refs,
    )
    meta = dict(metadata or {})
    if redacted:
        meta["redaction_applied"] = True
    conn.execute(
        """
        INSERT INTO causal_edge_candidates (
            candidate_uid, created_at, updated_at, source_type, source_ref,
            relation, target_type, target_ref, reason_public, evidence_refs_json,
            source_event_uid, producer, project_key, privacy_level, confidence,
            status, review_reason, promoted_edge_uid, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)
        ON CONFLICT(candidate_uid) DO UPDATE SET
            updated_at = excluded.updated_at,
            reason_public = excluded.reason_public,
            confidence = MAX(causal_edge_candidates.confidence, excluded.confidence),
            status = CASE
                WHEN causal_edge_candidates.status = 'promoted' THEN 'promoted'
                ELSE excluded.status
            END,
            review_reason = excluded.review_reason,
            metadata_json = excluded.metadata_json
        """,
        (
            candidate_uid,
            stamp,
            stamp,
            _normalize(source_type),
            str(source_ref).strip(),
            relation,
            _normalize(target_type),
            str(target_ref).strip(),
            clean_reason,
            _json(refs),
            str(source_event_uid or "").strip(),
            _normalize(producer) or "manual",
            str(project_key or "").strip(),
            privacy,
            max(0.0, min(1.0, float(confidence or 0.0))),
            clean_status,
            review_reason,
            _json(meta),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM causal_edge_candidates WHERE candidate_uid=?", (candidate_uid,)).fetchone()
    item = _candidate_row(row)
    item["ok"] = True
    return item


def list_candidates(*, status: str = "", limit: int = 20) -> list[dict[str, Any]]:
    conn = _db()
    if status:
        rows = conn.execute(
            "SELECT * FROM causal_edge_candidates WHERE status=? ORDER BY updated_at DESC LIMIT ?",
            (status, max(1, int(limit or 20))),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM causal_edge_candidates ORDER BY updated_at DESC LIMIT ?",
            (max(1, int(limit or 20)),),
        ).fetchall()
    return [_candidate_row(row) for row in rows]


def _active_edge_by_uid(edge_uid: str) -> dict[str, Any]:
    conn = _kg_db()
    rows = conn.execute(
        "SELECT * FROM kg_edges WHERE valid_until IS NULL AND properties LIKE ?",
        (f'%"{edge_uid}"%',),
    ).fetchall()
    for row in rows:
        item = dict(row)
        props = _parse_json(item.get("properties") or "{}", {})
        if props.get("edge_uid") == edge_uid:
            item["properties_dict"] = props
            return item
    return {}


def upsert_active_edge(
    *,
    source_type: str,
    source_ref: str,
    relation: str,
    target_type: str,
    target_ref: str,
    reason_public: str,
    evidence_refs: list[str],
    source_event_uid: str = "",
    producer: str = "manual",
    project_key: str = "",
    privacy_level: str = "normal",
    confidence: float = 0.8,
    status: str = "",
    repo_root: Path = ROOT,
) -> dict[str, Any]:
    refs = [str(ref).strip() for ref in (evidence_refs or []) if str(ref).strip()]
    if relation not in VALID_RELATIONS:
        return {"ok": False, "status": "rejected", "review_reason": "unknown_relation"}
    if not refs:
        return {"ok": False, "status": "review", "review_reason": "missing_evidence"}
    privacy = _privacy(privacy_level)
    if privacy == "secret":
        return {"ok": False, "status": "review", "review_reason": "secret_reference_only"}
    source_ok, source_reason = validate_ref(source_type, source_ref, evidence_refs=refs, repo_root=repo_root)
    if not source_ok:
        return {"ok": False, "status": "review", "review_reason": f"source_{source_reason}"}
    target_ok, target_reason = validate_ref(target_type, target_ref, evidence_refs=refs, repo_root=repo_root)
    if not target_ok:
        return {"ok": False, "status": "review", "review_reason": f"target_{target_reason}"}

    edge_uid = edge_uid_for(
        source_type=source_type,
        source_ref=source_ref,
        relation=relation,
        target_type=target_type,
        target_ref=target_ref,
        evidence_refs=refs,
    )
    existing = _active_edge_by_uid(edge_uid)
    if existing:
        return {"ok": True, "action": "NOOP", "edge_id": existing["id"], "edge_uid": edge_uid}

    clean_reason, redacted = redact_reason(reason_public, privacy_level=privacy)
    edge_status = status if status in ACTIVE_EDGE_STATUSES else ("verified" if confidence >= 0.9 else "active")
    props = {
        "schema_version": 1,
        "edge_uid": edge_uid,
        "status": edge_status,
        "project_key": str(project_key or "").strip(),
        "reason_public": clean_reason,
        "evidence_refs": refs,
        "source_event_uid": str(source_event_uid or "").strip(),
        "producer": _normalize(producer) or "manual",
        "privacy_level": privacy,
        "redaction_applied": bool(redacted),
        "created_by": "causal_graph",
    }
    ensure_kg_indexes()
    result = _kg().upsert_edge(
        source_type=_normalize(source_type),
        source_ref=str(source_ref).strip(),
        relation=relation,
        target_type=_normalize(target_type),
        target_ref=str(target_ref).strip(),
        weight=1.0,
        confidence=max(0.0, min(1.0, float(confidence or 0.0))),
        source_memory_id=str(source_event_uid or "").strip(),
        properties=props,
    )
    result.update({"ok": True, "edge_uid": edge_uid, "properties": props})
    return result


def promote_candidate(candidate_uid: str) -> dict[str, Any]:
    conn = _db()
    row = conn.execute("SELECT * FROM causal_edge_candidates WHERE candidate_uid=?", (candidate_uid,)).fetchone()
    if not row:
        return {"ok": False, "error": "candidate_not_found"}
    candidate = _candidate_row(row)
    if candidate.get("status") not in {"approved", "proposed"}:
        return {"ok": False, "error": f"candidate_status_not_promotable:{candidate.get('status')}"}
    result = upsert_active_edge(
        source_type=candidate["source_type"],
        source_ref=candidate["source_ref"],
        relation=candidate["relation"],
        target_type=candidate["target_type"],
        target_ref=candidate["target_ref"],
        reason_public=candidate.get("reason_public") or "",
        evidence_refs=candidate.get("evidence_refs") or [],
        source_event_uid=candidate.get("source_event_uid") or "",
        producer=candidate.get("producer") or "candidate",
        project_key=candidate.get("project_key") or "",
        privacy_level=candidate.get("privacy_level") or "normal",
        confidence=float(candidate.get("confidence") or 0.0),
    )
    if not result.get("ok"):
        conn.execute(
            "UPDATE causal_edge_candidates SET status='review', review_reason=?, updated_at=? WHERE candidate_uid=?",
            (result.get("review_reason") or result.get("error") or "promotion_failed", time.time(), candidate_uid),
        )
        conn.commit()
        return result
    conn.execute(
        "UPDATE causal_edge_candidates SET status='promoted', promoted_edge_uid=?, updated_at=? WHERE candidate_uid=?",
        (result.get("edge_uid") or "", time.time(), candidate_uid),
    )
    conn.commit()
    result["candidate_uid"] = candidate_uid
    return result


def record_task_close_edges(
    *,
    task_id: str,
    change_log_id: str | int = "",
    test_refs: list[str] | None = None,
    risk_ref: str = "",
    evidence_refs: list[str] | None = None,
    project_key: str = "",
    reason_public: str = "",
) -> list[dict[str, Any]]:
    refs = [str(ref).strip() for ref in (evidence_refs or []) if str(ref).strip()]
    if not refs:
        refs = [f"protocol_task:{task_id}"]
    results: list[dict[str, Any]] = []
    if change_log_id not in ("", None):
        change_ref = str(change_log_id)
        results.append(
            upsert_active_edge(
                source_type="protocol_task",
                source_ref=task_id,
                relation="ops:produced",
                target_type="change_log",
                target_ref=change_ref,
                reason_public=reason_public or "Task produced a change log entry.",
                evidence_refs=refs,
                producer="task_close",
                project_key=project_key,
                confidence=0.9,
            )
        )
        results.append(
            upsert_active_edge(
                source_type="change_log",
                source_ref=change_ref,
                relation="causal:motivated_by",
                target_type="protocol_task",
                target_ref=task_id,
                reason_public=reason_public or "Change was motivated by the closed task.",
                evidence_refs=refs,
                producer="task_close",
                project_key=project_key,
                confidence=0.85,
            )
        )
    for test_ref in test_refs or []:
        results.append(
            upsert_active_edge(
                source_type="protocol_task",
                source_ref=task_id,
                relation="causal:verified_by",
                target_type="test",
                target_ref=test_ref,
                reason_public=reason_public or "Task was verified by test evidence.",
                evidence_refs=[*refs, f"test:{test_ref}"],
                producer="task_close",
                project_key=project_key,
                confidence=0.92,
            )
        )
    if risk_ref:
        results.append(
            upsert_active_edge(
                source_type="change_log",
                source_ref=str(change_log_id),
                relation="causal:prevented",
                target_type="risk",
                target_ref=risk_ref,
                reason_public=reason_public or "Change prevented a documented risk.",
                evidence_refs=refs,
                producer="task_close",
                project_key=project_key,
                confidence=0.8,
            )
        )
    return results


def record_commitment_resolution_edges(commitment_id: str) -> list[dict[str, Any]]:
    conn = _db()
    if not _table_exists(conn, "commitments"):
        return []
    row = conn.execute("SELECT * FROM commitments WHERE id=?", (commitment_id,)).fetchone()
    if not row:
        return []
    item = dict(row)
    if item.get("status") not in {"fulfilled"}:
        return []
    action_ref_type = str(item.get("action_ref_type") or "").strip()
    action_ref_id = str(item.get("action_ref_id") or "").strip()
    evidence_ref = str(item.get("evidence_ref") or "").strip()
    refs = [ref for ref in [evidence_ref, f"commitment:{commitment_id}"] if ref]
    results: list[dict[str, Any]] = []
    if action_ref_type and action_ref_id:
        results.append(
            upsert_active_edge(
                source_type="commitment",
                source_ref=commitment_id,
                relation="causal:resolved_by",
                target_type=action_ref_type,
                target_ref=action_ref_id,
                reason_public="Commitment was fulfilled by the linked action.",
                evidence_refs=refs,
                producer="commitment",
                project_key=str(item.get("project_key") or ""),
                confidence=float(item.get("confidence") or 0.8),
            )
        )
    if evidence_ref:
        results.append(
            upsert_active_edge(
                source_type="commitment",
                source_ref=commitment_id,
                relation="causal:verified_by",
                target_type="artifact",
                target_ref=evidence_ref,
                reason_public="Commitment resolution has explicit evidence.",
                evidence_refs=refs,
                producer="commitment",
                project_key=str(item.get("project_key") or ""),
                confidence=float(item.get("confidence") or 0.8),
            )
        )
    return results


def propose_from_memory_executive(event: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    if str(decision.get("decision_kind") or "") != "proposed_causal_edge":
        return {"ok": False, "error": "decision_not_causal"}
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    edge = metadata.get("causal_edge") if isinstance(metadata.get("causal_edge"), dict) else {}
    if not edge:
        return {"ok": False, "error": "missing_causal_edge_payload"}
    return propose_candidate(
        source_type=str(edge.get("source_type") or ""),
        source_ref=str(edge.get("source_ref") or ""),
        relation=str(edge.get("relation") or ""),
        target_type=str(edge.get("target_type") or ""),
        target_ref=str(edge.get("target_ref") or ""),
        reason_public=str(edge.get("reason_public") or decision.get("reason") or ""),
        evidence_refs=[str(ref) for ref in edge.get("evidence_refs") or event.get("evidence_refs") or []],
        source_event_uid=str(event.get("event_uid") or ""),
        producer="memory_executive",
        project_key=str(event.get("project_key") or ""),
        privacy_level=str(edge.get("privacy_level") or event.get("privacy_level") or "normal"),
        confidence=float(edge.get("confidence") or decision.get("confidence") or 0.5),
        status="proposed",
        metadata={"memory_decision": decision.get("dedupe_key") or ""},
    )


def approve_candidate(candidate_uid: str) -> dict[str, Any]:
    conn = _db()
    row = conn.execute("SELECT * FROM causal_edge_candidates WHERE candidate_uid=?", (candidate_uid,)).fetchone()
    if not row:
        return {"ok": False, "error": "candidate_not_found"}
    conn.execute(
        "UPDATE causal_edge_candidates SET status='approved', review_reason='', updated_at=? WHERE candidate_uid=?",
        (time.time(), candidate_uid),
    )
    conn.commit()
    return {"ok": True, "candidate_uid": candidate_uid, "status": "approved"}


def _node_id(ref_type: str, ref: str) -> int | None:
    node = _kg().get_node(_normalize(ref_type), str(ref).strip())
    if not node:
        return None
    return int(node["id"])


def query_edges(
    *,
    ref_type: str,
    ref: str,
    project_key: str = "",
    include_historical: bool = False,
    limit: int = 8,
) -> dict[str, Any]:
    node_id = _node_id(ref_type, ref)
    if node_id is None:
        return {"ok": True, "has_evidence": False, "edges": [], "message": "no tengo evidencia suficiente"}
    conn = _kg_db()
    conditions = ["(e.source_id=? OR e.target_id=?)"]
    params: list[Any] = [node_id, node_id]
    if not include_historical:
        conditions.append("e.valid_until IS NULL")
    rows = conn.execute(
        f"""
        SELECT e.*, src.node_type AS source_type, src.node_ref AS source_ref,
               tgt.node_type AS target_type, tgt.node_ref AS target_ref
        FROM kg_edges e
        JOIN kg_nodes src ON src.id=e.source_id
        JOIN kg_nodes tgt ON tgt.id=e.target_id
        WHERE {' AND '.join(conditions)}
        ORDER BY e.confidence DESC, e.id DESC
        LIMIT ?
        """,
        [*params, max(1, min(int(limit or 8), 50))],
    ).fetchall()
    edges: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        relation = str(item.get("relation") or "")
        if not (relation.startswith("causal:") or relation.startswith("ops:")):
            continue
        props = _parse_json(item.get("properties") or "{}", {})
        status = str(props.get("status") or "active")
        privacy = _privacy(props.get("privacy_level") or "normal")
        if not include_historical and status not in {"active", "verified"}:
            continue
        if project_key and props.get("project_key") and props.get("project_key") != project_key:
            continue
        if privacy == "secret":
            continue
        if privacy == "sensitive":
            item["renderable_reason"] = "Tengo una relacion causal con evidencia privada; puedo revisarla si me das permiso para usar ese contexto."
        else:
            item["renderable_reason"] = props.get("reason_public") or ""
        item["properties_dict"] = props
        edges.append(item)
    return {
        "ok": True,
        "has_evidence": bool(edges),
        "edges": edges[: max(1, min(int(limit or 8), 50))],
        "message": "" if edges else "no tengo evidencia suficiente",
    }


def render_query_result(result: dict[str, Any], *, max_chars: int = 1200) -> str:
    edges = result.get("edges") or []
    if not edges:
        return "No tengo evidencia suficiente."
    lines = ["Causal evidence:"]
    for edge in edges:
        props = edge.get("properties_dict") or {}
        refs = props.get("evidence_refs") or []
        reason = edge.get("renderable_reason") or props.get("reason_public") or ""
        lines.append(
            f"- {edge.get('source_type')}:{edge.get('source_ref')} {edge.get('relation')} "
            f"{edge.get('target_type')}:{edge.get('target_ref')} - {reason} "
            f"(evidence: {', '.join(refs) or 'none'})"
        )
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max(0, max_chars - 3)].rstrip() + "..."


__all__ = [
    "ACTIVE_EDGE_STATUSES",
    "CANDIDATE_STATUSES",
    "VALID_RELATIONS",
    "approve_candidate",
    "candidate_uid_for",
    "edge_uid_for",
    "ensure_kg_indexes",
    "list_candidates",
    "promote_candidate",
    "propose_from_memory_executive",
    "propose_candidate",
    "query_edges",
    "record_commitment_resolution_edges",
    "record_task_close_edges",
    "redact_reason",
    "render_query_result",
    "upsert_active_edge",
    "validate_ref",
]
