from __future__ import annotations

"""Opportunity Orchestrator MVP.

This module builds a sparse, evidence-backed proactive queue. It is deliberately
not a psychological profile layer: it never ranks proposals from mood labels,
sentiment labels, or personality inferences, and Phase 1 never performs external
actions.
"""

import datetime as _dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any


NORMAL_PROPOSAL_LIMIT = 3
DEFAULT_PROPOSAL_THRESHOLD = 2.45
SAFE_ACTION_CLASSES = {"read_only", "prepare_artifact", "local_reversible"}
VALID_FEEDBACK = {
    "accepted",
    "ignored",
    "snoozed",
    "dismissed",
    "false_positive",
    "useful_but_later",
}
FORBIDDEN_VISIBLE_TERMS = {
    "anxious",
    "depressed",
    "vulnerable",
    "unstable",
    "burnout",
    "manipulable",
    "compliance_likely",
    "frustrated",
    "mood",
    "tension",
}


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def _expires(days: int = 14) -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        + _dt.timedelta(days=max(1, int(days or 14)))
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hash_id(prefix: str, value: str, length: int = 20) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8", errors="ignore")).hexdigest()[:length]
    return f"{prefix}-{digest}"


def _safe_json(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except Exception:
        return "{}"


def _parse_json(value: str | None, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else default
    except Exception:
        return default


def _row(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return dict(row)


def _parse_time(value: Any) -> _dt.datetime | None:
    if value in ("", None):
        return None
    if isinstance(value, (int, float)):
        try:
            return _dt.datetime.fromtimestamp(float(value), tz=_dt.timezone.utc)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = _dt.datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_dt.timezone.utc)
            return parsed
        except Exception:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = _dt.datetime.strptime(text[:19] if fmt != "%Y-%m-%d" else text[:10], fmt)
            return parsed.replace(tzinfo=_dt.timezone.utc)
        except Exception:
            continue
    return None


def _expired(value: Any) -> bool:
    parsed = _parse_time(value)
    return bool(parsed and parsed < _dt.datetime.now(_dt.timezone.utc))


def _clamp(value: Any, default: float = 0.0) -> float:
    try:
        raw = float(value)
    except Exception:
        raw = default
    return max(0.0, min(1.0, raw))


def _sanitize_text(value: Any, limit: int = 360) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    for term in sorted(FORBIDDEN_VISIBLE_TERMS, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(term)}\b", "operational signal", text, flags=re.IGNORECASE)
    if len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "..."
    return text


def _source_hash(payload: Any) -> str:
    return hashlib.sha256(_safe_json(payload).encode("utf-8", errors="ignore")).hexdigest()


def _score(payload: dict[str, Any]) -> float:
    score = (
        float(payload.get("impact") or 0)
        + float(payload.get("urgency") or 0)
        + float(payload.get("confidence") or 0)
        + float(payload.get("readiness") or 0)
        + float(payload.get("user_burden_reduction") or 0)
        + float(payload.get("strategic_alignment") or 0)
        - float(payload.get("risk") or 0)
        - float(payload.get("interruption_cost") or 0)
        - float(payload.get("repetition_penalty") or 0)
    )
    return round(max(0.0, score), 4)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _opportunity_type_from_closure(item: dict[str, Any]) -> str:
    source = str(item.get("source_primary") or "").lower()
    kind = str(item.get("kind") or "").lower()
    if "debt" in source or "blocked" in kind:
        return "remediation"
    if "outcome" in source:
        return "closure"
    if "followup_due" in kind:
        return "deadline"
    if "mcp_write_queue" in source:
        return "remediation"
    if "release" in (str(item.get("title") or "") + " " + str(item.get("summary") or "")).lower():
        return "product"
    return "closure"


def _domain_from_item(item: dict[str, Any]) -> str:
    text = (str(item.get("title") or "") + " " + str(item.get("summary") or "")).lower()
    if "desktop" in text:
        return "desktop"
    if "release" in text or "publish" in text:
        return "release"
    if "email" in text or "correo" in text:
        return "email"
    if "ads" in text or "google" in text:
        return "ads"
    if "nexo" in text or "brain" in text:
        return "brain"
    return "general"


def _authorization_status(action_class: str) -> str:
    clean = str(action_class or "read_only").strip() or "read_only"
    return "not_required" if clean in SAFE_ACTION_CLASSES else "needs_permission"


def _candidate_from_closure(item: dict[str, Any]) -> dict[str, Any]:
    source_id = str(item.get("id") or item.get("dedupe_key") or "")
    source_type = "closure_items"
    title_source = _sanitize_text(item.get("title") or "Operational opportunity", 160)
    opportunity_type = _opportunity_type_from_closure(item)
    domain = _domain_from_item(item)
    impact = _clamp(item.get("impact_score"), 0.55)
    urgency = _clamp(item.get("urgency_score"), 0.45)
    confidence = max(0.45, _clamp(item.get("confidence_score"), 0.75))
    risk = _clamp(item.get("risk_score"), 0.15)
    readiness = 0.72 if str(item.get("evidence_required") or "").strip() else 0.58
    burden = 0.68 if str(item.get("next_action") or "").strip() else 0.52
    strategic = 0.6 if domain in {"release", "desktop", "brain"} else 0.42
    interruption_cost = 0.32
    action_class = "read_only"
    dedupe_key = f"{source_type}:{source_id}"
    opportunity_id = _hash_id("OPP", dedupe_key)
    now = _now_iso()
    source_payload = {
        "source_primary": item.get("source_primary"),
        "kind": item.get("kind"),
        "state": item.get("state"),
        "deadline_at": item.get("deadline_at"),
    }
    score_payload = {
        "impact": impact,
        "urgency": urgency,
        "confidence": confidence,
        "readiness": readiness,
        "user_burden_reduction": burden,
        "strategic_alignment": strategic,
        "risk": risk,
        "interruption_cost": interruption_cost,
        "repetition_penalty": 0.0,
    }
    opportunity = {
        "id": opportunity_id,
        "title": f"Prepared review: {title_source}",
        "hypothesis": "This open operational item has enough evidence for a read-only preparation.",
        "domain": domain,
        "opportunity_type": opportunity_type,
        "dedupe_key": dedupe_key,
        "impact": impact,
        "urgency": urgency,
        "confidence": confidence,
        "risk": risk,
        "effort": 0.25,
        "readiness": readiness,
        "user_burden_reduction": burden,
        "interruption_cost": interruption_cost,
        "strategic_alignment": strategic,
        "repetition_penalty": 0.0,
        "score": _score(score_payload),
        "state": "candidate",
        "owner": "nero",
        "why_now": _sanitize_text(
            item.get("blocker_reason")
            or item.get("evidence_required")
            or "The source remains open and can be reduced to a short review.",
            360,
        ),
        "next_action": _sanitize_text(
            item.get("next_action")
            or "Inspect evidence and choose accept, snooze, or suppress.",
            360,
        ),
        "action_class": action_class,
        "authorization_status": _authorization_status(action_class),
        "created_at": now,
        "updated_at": now,
        "expires_at": item.get("deadline_at") or _expires(14),
        "last_proposed_at": "",
        "source_payload_json": _safe_json(source_payload),
    }
    signal = {
        "id": _hash_id("SIG", f"{dedupe_key}:signal"),
        "source_type": source_type,
        "source_id": source_id,
        "entity_ref": _sanitize_text(domain, 80),
        "summary": _sanitize_text(item.get("title") or item.get("summary") or source_id, 280),
        "signal_kind": _sanitize_text(item.get("kind") or opportunity_type, 80),
        "urgency": urgency,
        "confidence": confidence,
        "privacy_level": "normal",
        "source_hash": _source_hash(source_payload),
        "created_at": now,
        "expires_at": opportunity["expires_at"],
    }
    evidence = {
        "id": _hash_id("OPE", f"{opportunity_id}:{source_type}:{source_id}"),
        "opportunity_id": opportunity_id,
        "source_type": source_type,
        "source_id": source_id,
        "evidence_summary": _sanitize_text(item.get("summary") or item.get("evidence_required") or item.get("title"), 360),
        "confidence": confidence,
        "created_at": now,
    }
    preparation = {
        "id": _hash_id("PREP", f"{opportunity_id}:decision_card"),
        "opportunity_id": opportunity_id,
        "artifact_type": "decision_card",
        "artifact_ref": f"nexo://opportunity/{opportunity_id}",
        "safe_mode": 1,
        "approval_required": 0,
        "status": "ready",
        "created_at": now,
        "expires_at": opportunity["expires_at"],
    }
    return {
        "signal": signal,
        "opportunity": opportunity,
        "evidence": [evidence],
        "preparations": [preparation],
    }


def _collect_from_closure(conn, limit_per_source: int = 250) -> list[dict[str, Any]]:
    from closure_plane import closure_next, refresh_closure_items

    refresh_closure_items(conn, limit_per_adapter=limit_per_source)
    items = closure_next(conn, limit=limit_per_source, include_waiting=True)
    return [_candidate_from_closure(item) for item in items]


def _filtered_sources(sources: str) -> set[str]:
    return {part.strip().lower() for part in str(sources or "").split(",") if part.strip()}


def collect_candidates(conn=None, *, sources: str = "", limit_per_source: int = 250) -> list[dict[str, Any]]:
    if conn is None:
        from db import get_db
        from db._schema import run_migrations

        conn = get_db()
        run_migrations(conn)
    selected = _filtered_sources(sources)
    candidates: list[dict[str, Any]] = []
    if not selected or "closure" in selected or "closure_items" in selected:
        candidates.extend(_collect_from_closure(conn, limit_per_source=limit_per_source))
    return candidates


def _upsert_signal(conn, signal: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO nexo_signals (
            id, source_type, source_id, entity_ref, summary, signal_kind,
            urgency, confidence, privacy_level, source_hash, created_at, expires_at
        ) VALUES (
            :id, :source_type, :source_id, :entity_ref, :summary, :signal_kind,
            :urgency, :confidence, :privacy_level, :source_hash, :created_at, :expires_at
        )
        ON CONFLICT(source_type, source_id, signal_kind) DO UPDATE SET
            entity_ref = excluded.entity_ref,
            summary = excluded.summary,
            urgency = excluded.urgency,
            confidence = excluded.confidence,
            privacy_level = excluded.privacy_level,
            source_hash = excluded.source_hash,
            expires_at = excluded.expires_at
        """,
        signal,
    )


def _upsert_opportunity(conn, opportunity: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO nexo_opportunities (
            id, title, hypothesis, domain, opportunity_type, dedupe_key,
            impact, urgency, confidence, risk, effort, readiness,
            user_burden_reduction, interruption_cost, strategic_alignment,
            repetition_penalty, score, state, owner, why_now, next_action,
            action_class, authorization_status, created_at, updated_at,
            expires_at, last_proposed_at, source_payload_json
        ) VALUES (
            :id, :title, :hypothesis, :domain, :opportunity_type, :dedupe_key,
            :impact, :urgency, :confidence, :risk, :effort, :readiness,
            :user_burden_reduction, :interruption_cost, :strategic_alignment,
            :repetition_penalty, :score, :state, :owner, :why_now, :next_action,
            :action_class, :authorization_status, :created_at, :updated_at,
            :expires_at, :last_proposed_at, :source_payload_json
        )
        ON CONFLICT(dedupe_key) DO UPDATE SET
            title = excluded.title,
            hypothesis = excluded.hypothesis,
            domain = excluded.domain,
            opportunity_type = excluded.opportunity_type,
            impact = excluded.impact,
            urgency = excluded.urgency,
            confidence = excluded.confidence,
            risk = excluded.risk,
            effort = excluded.effort,
            readiness = excluded.readiness,
            user_burden_reduction = excluded.user_burden_reduction,
            interruption_cost = excluded.interruption_cost,
            strategic_alignment = excluded.strategic_alignment,
            score = excluded.score,
            why_now = excluded.why_now,
            next_action = excluded.next_action,
            action_class = excluded.action_class,
            authorization_status = excluded.authorization_status,
            updated_at = excluded.updated_at,
            expires_at = excluded.expires_at,
            source_payload_json = excluded.source_payload_json
        WHERE nexo_opportunities.state NOT IN ('closed', 'discarded', 'suppressed', 'stale')
        """,
        opportunity,
    )


def _upsert_evidence(conn, evidence: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO nexo_opportunity_evidence (
            id, opportunity_id, source_type, source_id, evidence_summary, confidence, created_at
        ) VALUES (
            :id, :opportunity_id, :source_type, :source_id, :evidence_summary, :confidence, :created_at
        )
        ON CONFLICT(opportunity_id, source_type, source_id) DO UPDATE SET
            evidence_summary = excluded.evidence_summary,
            confidence = excluded.confidence
        """,
        evidence,
    )


def _upsert_preparation(conn, preparation: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO nexo_preparations (
            id, opportunity_id, artifact_type, artifact_ref, safe_mode,
            approval_required, status, created_at, expires_at
        ) VALUES (
            :id, :opportunity_id, :artifact_type, :artifact_ref, :safe_mode,
            :approval_required, :status, :created_at, :expires_at
        )
        ON CONFLICT(opportunity_id, artifact_type, artifact_ref) DO UPDATE SET
            safe_mode = excluded.safe_mode,
            approval_required = excluded.approval_required,
            status = excluded.status,
            expires_at = excluded.expires_at
        """,
        preparation,
    )


def _persist_candidates(conn, candidates: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"signals": 0, "opportunities": 0, "evidence": 0, "preparations": 0}
    for candidate in candidates:
        _upsert_signal(conn, candidate["signal"])
        counts["signals"] += 1
        _upsert_opportunity(conn, candidate["opportunity"])
        counts["opportunities"] += 1
        for evidence in candidate.get("evidence") or []:
            _upsert_evidence(conn, evidence)
            counts["evidence"] += 1
        for preparation in candidate.get("preparations") or []:
            _upsert_preparation(conn, preparation)
            counts["preparations"] += 1
    conn.commit()
    return counts


def _active_suppression(conn, scope_type: str, scope_key: str) -> dict[str, Any] | None:
    if not scope_key:
        return None
    rows = conn.execute(
        """
        SELECT *
        FROM nexo_suppression_rules
        WHERE scope_type = ? AND scope_key = ?
        ORDER BY created_at DESC
        """,
        (scope_type, scope_key),
    ).fetchall()
    for raw in rows:
        row = _row(raw)
        if not _expired(row.get("expires_at")):
            return row
    return None


def _opportunity_evidence(conn, opportunity_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM nexo_opportunity_evidence
        WHERE opportunity_id = ?
        ORDER BY confidence DESC, created_at DESC
        """,
        (opportunity_id,),
    ).fetchall()
    return [_row(row) for row in rows]


def _opportunity_preparations(conn, opportunity_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM nexo_preparations
        WHERE opportunity_id = ? AND status NOT IN ('stale', 'deleted')
        ORDER BY created_at DESC
        """,
        (opportunity_id,),
    ).fetchall()
    return [_row(row) for row in rows]


def _proposal_copy(opportunity: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    evidence_count = len(evidence)
    return _sanitize_text(
        f"{opportunity.get('title')}. Evidence refs: {evidence_count}. "
        f"Why now: {opportunity.get('why_now')}",
        520,
    )


def _create_or_update_proposal(conn, opportunity: dict[str, Any], surface: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    now = _now_iso()
    proposal_id = _hash_id("PROP", f"{opportunity['id']}:{surface}")
    payload = {
        "id": proposal_id,
        "opportunity_id": opportunity["id"],
        "surface": surface,
        "copy": _proposal_copy(opportunity, evidence),
        "cta_primary": "Inspect evidence",
        "cta_secondary": "Snooze",
        "shown_at": "",
        "feedback": "",
        "created_at": now,
    }
    conn.execute(
        """
        INSERT INTO nexo_proposals (
            id, opportunity_id, surface, copy, cta_primary, cta_secondary,
            shown_at, feedback, created_at
        ) VALUES (
            :id, :opportunity_id, :surface, :copy, :cta_primary, :cta_secondary,
            :shown_at, :feedback, :created_at
        )
        ON CONFLICT(opportunity_id, surface) DO UPDATE SET
            copy = excluded.copy,
            cta_primary = excluded.cta_primary,
            cta_secondary = excluded.cta_secondary
        """,
        payload,
    )
    conn.execute(
        "UPDATE nexo_opportunities SET state = 'proposed', last_proposed_at = ?, updated_at = ? WHERE id = ?",
        (now, now, opportunity["id"]),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM nexo_proposals WHERE id = ?", (proposal_id,)).fetchone()
    return _row(row)


def _proposal_payload(conn, proposal: dict[str, Any]) -> dict[str, Any]:
    opportunity = get_opportunity(proposal.get("opportunity_id") or "", include_evidence=True, conn=conn)
    item = {
        "id": proposal.get("id"),
        "surface": proposal.get("surface") or "",
        "copy": _sanitize_text(proposal.get("copy"), 600),
        "cta_primary": proposal.get("cta_primary") or "",
        "cta_secondary": proposal.get("cta_secondary") or "",
        "feedback": proposal.get("feedback") or "",
        "created_at": proposal.get("created_at") or "",
        "opportunity": opportunity.get("opportunity"),
    }
    if item["opportunity"]:
        item["confidence"] = item["opportunity"].get("confidence")
        item["evidence_refs"] = [
            f"{ev.get('source_type')}:{ev.get('source_id')}"
            for ev in item["opportunity"].get("evidence", [])
        ]
    else:
        item["confidence"] = 0
        item["evidence_refs"] = []
    return item


def _eligible_opportunities(conn, *, include_snoozed: bool = False) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM nexo_opportunities
        WHERE state IN ('candidate', 'prepared', 'proposed')
          AND score >= ?
          AND authorization_status IN ('not_required', 'needs_permission')
        ORDER BY score DESC, urgency DESC, updated_at DESC
        LIMIT 100
        """,
        (DEFAULT_PROPOSAL_THRESHOLD,),
    ).fetchall()
    results: list[dict[str, Any]] = []
    for raw in rows:
        opportunity = _row(raw)
        if _expired(opportunity.get("expires_at")):
            continue
        if _active_suppression(conn, "opportunity", opportunity.get("id") or ""):
            continue
        if _active_suppression(conn, "domain", opportunity.get("domain") or ""):
            continue
        if _active_suppression(conn, "type", opportunity.get("opportunity_type") or ""):
            continue
        if not include_snoozed and _active_suppression(conn, "snooze", opportunity.get("id") or ""):
            continue
        evidence = _opportunity_evidence(conn, opportunity["id"])
        if not evidence:
            continue
        opportunity["evidence"] = evidence
        results.append(opportunity)
    return results


def opportunity_queue(
    conn=None,
    *,
    surface: str = "home",
    limit: int = NORMAL_PROPOSAL_LIMIT,
    refresh: bool = False,
    include_snoozed: bool = False,
) -> dict[str, Any]:
    if conn is None:
        from db import get_db
        from db._schema import run_migrations

        conn = get_db()
        run_migrations(conn)
    if refresh:
        refresh_opportunities(conn, dry_run=False)
    clean_surface = _sanitize_text(surface or "home", 80) or "home"
    clean_limit = min(NORMAL_PROPOSAL_LIMIT, max(0, int(limit or NORMAL_PROPOSAL_LIMIT)))
    if clean_limit <= 0:
        return {"ok": True, "schema": "nexo.opportunity.queue.v1", "surface": clean_surface, "proposals": []}
    selected = _eligible_opportunities(conn, include_snoozed=include_snoozed)[:clean_limit]
    proposals = [
        _proposal_payload(conn, _create_or_update_proposal(conn, opportunity, clean_surface, opportunity["evidence"]))
        for opportunity in selected
    ]
    return {
        "ok": True,
        "schema": "nexo.opportunity.queue.v1",
        "surface": clean_surface,
        "proposal_limit": NORMAL_PROPOSAL_LIMIT,
        "proposals": proposals,
        "zero_proposals_ok": len(proposals) == 0,
    }


def refresh_opportunities(
    conn=None,
    *,
    dry_run: bool = True,
    sources: str = "",
    limit_per_source: int = 250,
    write_report: bool = False,
) -> dict[str, Any]:
    if conn is None:
        from db import get_db
        from db._schema import run_migrations

        conn = get_db()
        run_migrations(conn)
    clean_limit = max(1, min(int(limit_per_source or 250), 500))
    candidates = collect_candidates(conn, sources=sources, limit_per_source=clean_limit)
    persisted = {"signals": 0, "opportunities": 0, "evidence": 0, "preparations": 0}
    if not dry_run:
        persisted = _persist_candidates(conn, candidates)
    candidate_summary = [
        {
            "opportunity_id": item["opportunity"]["id"],
            "title": item["opportunity"]["title"],
            "score": item["opportunity"]["score"],
            "evidence_refs": [
                f"{ev.get('source_type')}:{ev.get('source_id')}"
                for ev in item.get("evidence", [])
            ],
            "selected": item["opportunity"]["score"] >= DEFAULT_PROPOSAL_THRESHOLD,
        }
        for item in candidates
    ]
    result = {
        "ok": True,
        "schema": "nexo.opportunity.refresh.v1",
        "dry_run": bool(dry_run),
        "sources": sorted(_filtered_sources(sources)) if _filtered_sources(sources) else ["closure_items"],
        "observed_candidates": len(candidates),
        "persisted": persisted,
        "candidates": candidate_summary,
        "zero_proposals_ok": True,
    }
    if write_report:
        result["report"] = write_daily_report(conn, refresh_result=result)
    return result


def get_opportunity(opportunity_id: str, *, include_evidence: bool = True, conn=None) -> dict[str, Any]:
    if conn is None:
        from db import get_db
        from db._schema import run_migrations

        conn = get_db()
        run_migrations(conn)
    clean_id = str(opportunity_id or "").strip()
    if not clean_id:
        return {"ok": False, "error": "opportunity_id is required"}
    row = conn.execute(
        "SELECT * FROM nexo_opportunities WHERE id = ? OR dedupe_key = ?",
        (clean_id, clean_id),
    ).fetchone()
    if not row:
        return {"ok": False, "error": "opportunity not found"}
    opportunity = _row(row)
    opportunity["source_payload"] = _parse_json(opportunity.pop("source_payload_json", "{}"), {})
    if include_evidence:
        opportunity["evidence"] = _opportunity_evidence(conn, opportunity["id"])
        opportunity["preparations"] = _opportunity_preparations(conn, opportunity["id"])
    return {"ok": True, "opportunity": opportunity}


def opportunity_feedback(
    proposal_id: str,
    feedback: str,
    *,
    note: str = "",
    snooze_until: str = "",
    conn=None,
) -> dict[str, Any]:
    if conn is None:
        from db import get_db
        from db._schema import run_migrations

        conn = get_db()
        run_migrations(conn)
    clean_feedback = str(feedback or "").strip()
    if clean_feedback not in VALID_FEEDBACK:
        return {"ok": False, "error": f"invalid feedback: {clean_feedback}"}
    proposal = _row(conn.execute("SELECT * FROM nexo_proposals WHERE id = ?", (str(proposal_id or ""),)).fetchone())
    if not proposal:
        return {"ok": False, "error": "proposal not found"}
    now = _now_iso()
    conn.execute("UPDATE nexo_proposals SET feedback = ? WHERE id = ?", (clean_feedback, proposal["id"]))
    event_id = _hash_id("OPEV", f"{proposal['id']}:{clean_feedback}:{now}:{note}", 24)
    conn.execute(
        """
        INSERT INTO nexo_proposal_events (
            id, proposal_id, event_type, feedback, note, metadata_json, created_at
        ) VALUES (?, ?, 'feedback', ?, ?, ?, ?)
        """,
        (
            event_id,
            proposal["id"],
            clean_feedback,
            _sanitize_text(note, 300),
            _safe_json({"snooze_until": snooze_until}),
            now,
        ),
    )
    suppression = None
    if clean_feedback in {"dismissed", "false_positive", "ignored", "snoozed"}:
        expires_at = snooze_until if clean_feedback == "snoozed" and snooze_until else _expires(7 if clean_feedback != "false_positive" else 30)
        suppression = suppress(
            "snooze" if clean_feedback == "snoozed" else "opportunity",
            proposal["opportunity_id"],
            reason=clean_feedback,
            expires_at=expires_at,
            conn=conn,
        )
    conn.commit()
    return {
        "ok": True,
        "proposal_id": proposal["id"],
        "feedback": clean_feedback,
        "event_id": event_id,
        "suppression": suppression,
    }


def suppress(
    scope_type: str,
    scope_key: str,
    *,
    reason: str = "",
    expires_at: str = "",
    conn=None,
) -> dict[str, Any]:
    if conn is None:
        from db import get_db
        from db._schema import run_migrations

        conn = get_db()
        run_migrations(conn)
    clean_type = _sanitize_text(scope_type, 80)
    clean_key = _sanitize_text(scope_key, 160)
    if not clean_type or not clean_key:
        return {"ok": False, "error": "scope_type and scope_key are required"}
    clean_reason = _sanitize_text(reason or "manual", 240)
    clean_expires = str(expires_at or _expires(14)).strip()
    row_id = _hash_id("OSR", f"{clean_type}:{clean_key}:{clean_reason}", 24)
    conn.execute(
        """
        INSERT INTO nexo_suppression_rules (
            id, scope_type, scope_key, reason, expires_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope_type, scope_key, reason) DO UPDATE SET
            expires_at = excluded.expires_at
        """,
        (row_id, clean_type, clean_key, clean_reason, clean_expires, _now_iso()),
    )
    conn.commit()
    return {
        "ok": True,
        "id": row_id,
        "scope_type": clean_type,
        "scope_key": clean_key,
        "reason": clean_reason,
        "expires_at": clean_expires,
    }


def write_daily_report(conn=None, *, refresh_result: dict[str, Any] | None = None) -> dict[str, Any]:
    if conn is None:
        from db import get_db
        from db._schema import run_migrations

        conn = get_db()
        run_migrations(conn)
    from paths import operations_dir

    queue = opportunity_queue(conn, surface="morning_briefing", limit=NORMAL_PROPOSAL_LIMIT, refresh=False)
    root = operations_dir() / "opportunity-orchestrator"
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / f"{_today()}-opportunities.json"
    md_path = root / f"{_today()}-opportunities.md"
    payload = {
        "schema": "nexo.opportunity.report.v1",
        "generated_at": _now_iso(),
        "refresh": refresh_result or {},
        "queue": queue,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        f"# Opportunity Orchestrator -- {_today()}",
        "",
        f"- Generated at: {payload['generated_at']}",
        f"- Proposals: {len(queue.get('proposals') or [])}",
        "- Zero proposals is valid when evidence is not strong enough.",
        "",
    ]
    for proposal in queue.get("proposals") or []:
        opportunity = proposal.get("opportunity") or {}
        lines.extend([
            f"## {opportunity.get('title', proposal.get('id'))}",
            "",
            f"- Score: {opportunity.get('score')}",
            f"- Confidence: {opportunity.get('confidence')}",
            f"- Why now: {opportunity.get('why_now')}",
            f"- Next action: {opportunity.get('next_action')}",
            f"- Evidence: {', '.join(proposal.get('evidence_refs') or [])}",
            "",
        ])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def handle_opportunity_refresh(
    dry_run: bool = True,
    sources: str = "",
    limit_per_source: int = 250,
    write_report: bool = False,
) -> str:
    return json.dumps(
        refresh_opportunities(
            dry_run=bool(dry_run),
            sources=sources,
            limit_per_source=limit_per_source,
            write_report=bool(write_report),
        ),
        indent=2,
        ensure_ascii=False,
    )


def handle_opportunity_queue(
    surface: str = "home",
    limit: int = NORMAL_PROPOSAL_LIMIT,
    refresh: bool = False,
    include_snoozed: bool = False,
) -> str:
    return json.dumps(
        opportunity_queue(
            surface=surface,
            limit=limit,
            refresh=bool(refresh),
            include_snoozed=bool(include_snoozed),
        ),
        indent=2,
        ensure_ascii=False,
    )


def handle_opportunity_get(opportunity_id: str, include_evidence: bool = True) -> str:
    return json.dumps(
        get_opportunity(opportunity_id, include_evidence=bool(include_evidence)),
        indent=2,
        ensure_ascii=False,
    )


def handle_opportunity_feedback(
    proposal_id: str,
    feedback: str,
    note: str = "",
    snooze_until: str = "",
) -> str:
    return json.dumps(
        opportunity_feedback(proposal_id, feedback, note=note, snooze_until=snooze_until),
        indent=2,
        ensure_ascii=False,
    )


def handle_opportunity_suppress(
    scope_type: str,
    scope_key: str,
    reason: str = "",
    expires_at: str = "",
) -> str:
    return json.dumps(
        suppress(scope_type, scope_key, reason=reason, expires_at=expires_at),
        indent=2,
        ensure_ascii=False,
    )
