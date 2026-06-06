from __future__ import annotations

"""Operational Closure Plane MVP.

Read-only adapters discover unfinished operational work and project it into a
canonical closure_items table. Verification/close calls only mutate closure
metadata; they never execute the source action.
"""

import datetime as _dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any


OPEN_STATES = {"open", "waiting", "verified"}
FINAL_STATES = {"closed", "rejected", "stale"}


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def _hash_id(prefix: str, value: str, length: int = 16) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}-{digest}"


def _as_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return dict(row)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _safe_json(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except Exception:
        return "{}"


def _parse_time(value: Any) -> _dt.datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return _dt.datetime.fromtimestamp(float(value), tz=_dt.timezone.utc)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.replace(".", "", 1).isdigit():
        try:
            return _dt.datetime.fromtimestamp(float(text), tz=_dt.timezone.utc)
        except Exception:
            return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = _dt.datetime.strptime(text[:19] if fmt != "%Y-%m-%d" else text[:10], fmt)
            return parsed.replace(tzinfo=_dt.timezone.utc)
        except Exception:
            continue
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _age_days(value: Any) -> float:
    parsed = _parse_time(value)
    if not parsed:
        return 0.0
    return max(0.0, (_dt.datetime.now(_dt.timezone.utc) - parsed).total_seconds() / 86400)


def _deadline_urgency(value: Any, default: float = 0.35) -> float:
    parsed = _parse_time(value)
    if not parsed:
        return default
    delta_days = (parsed - _dt.datetime.now(_dt.timezone.utc)).total_seconds() / 86400
    if delta_days <= 0:
        return 1.0
    if delta_days <= 1:
        return 0.85
    if delta_days <= 3:
        return 0.7
    if delta_days <= 7:
        return 0.55
    return 0.3


def _priority(impact: float, urgency: float, risk: float, confidence: float = 0.8) -> float:
    score = (impact * 0.45) + (urgency * 0.35) + (confidence * 0.15) - (risk * 0.1)
    return round(max(0.0, min(1.0, score)), 4)


def _candidate(
    *,
    source_primary: str,
    source_key: str,
    kind: str,
    title: str,
    summary: str = "",
    state: str = "open",
    impact: float = 0.6,
    urgency: float = 0.4,
    risk: float = 0.15,
    confidence: float = 0.8,
    safety_class: str = "normal",
    capability_required: str = "",
    capability_status: str = "unknown",
    next_action: str = "",
    blocker_reason: str = "",
    evidence_required: str = "",
    deadline_at: str = "",
    source_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_source = str(source_primary)
    clean_key = str(source_key)
    dedupe_key = f"{clean_source}:{kind}:{clean_key}"
    now = _now_iso()
    return {
        "id": _hash_id("CI", dedupe_key),
        "title": str(title or clean_key)[:240],
        "summary": str(summary or "")[:1200],
        "kind": kind,
        "state": state if state in OPEN_STATES else "open",
        "source_primary": clean_source,
        "source_key": clean_key,
        "dedupe_key": dedupe_key,
        "impact_score": round(impact, 4),
        "urgency_score": round(urgency, 4),
        "risk_score": round(risk, 4),
        "confidence_score": round(confidence, 4),
        "priority_score": _priority(impact, urgency, risk, confidence),
        "safety_class": safety_class,
        "capability_required": capability_required,
        "capability_status": capability_status,
        "owner": "nero",
        "next_action": next_action,
        "blocker_reason": blocker_reason,
        "evidence_required": evidence_required,
        "deadline_at": str(deadline_at or ""),
        "first_seen_at": now,
        "last_seen_at": now,
        "source_payload_json": _safe_json(source_payload or {}),
    }


def _upsert_candidate(conn, item: dict[str, Any]) -> bool:
    existing = conn.execute(
        "SELECT id, state FROM closure_items WHERE dedupe_key = ?",
        (item["dedupe_key"],),
    ).fetchone()
    was_new = existing is None
    conn.execute(
        """
        INSERT INTO closure_items (
            id, title, summary, kind, state, source_primary, source_key,
            dedupe_key, impact_score, urgency_score, risk_score,
            confidence_score, priority_score, safety_class,
            capability_required, capability_status, owner, next_action,
            blocker_reason, evidence_required, deadline_at,
            first_seen_at, last_seen_at, source_payload_json, updated_at
        ) VALUES (
            :id, :title, :summary, :kind, :state, :source_primary, :source_key,
            :dedupe_key, :impact_score, :urgency_score, :risk_score,
            :confidence_score, :priority_score, :safety_class,
            :capability_required, :capability_status, :owner, :next_action,
            :blocker_reason, :evidence_required, :deadline_at,
            :first_seen_at, :last_seen_at, :source_payload_json, :last_seen_at
        )
        ON CONFLICT(dedupe_key) DO UPDATE SET
            title = excluded.title,
            summary = excluded.summary,
            kind = excluded.kind,
            source_primary = excluded.source_primary,
            source_key = excluded.source_key,
            impact_score = excluded.impact_score,
            urgency_score = excluded.urgency_score,
            risk_score = excluded.risk_score,
            confidence_score = excluded.confidence_score,
            priority_score = excluded.priority_score,
            safety_class = excluded.safety_class,
            capability_required = excluded.capability_required,
            capability_status = excluded.capability_status,
            next_action = excluded.next_action,
            blocker_reason = excluded.blocker_reason,
            evidence_required = excluded.evidence_required,
            deadline_at = excluded.deadline_at,
            last_seen_at = excluded.last_seen_at,
            source_payload_json = excluded.source_payload_json,
            updated_at = excluded.last_seen_at
        WHERE closure_items.state NOT IN ('closed', 'rejected', 'stale')
        """,
        item,
    )
    source_id = _hash_id("CIS", f"{item['id']}:{item['source_primary']}:{item['source_key']}", 20)
    conn.execute(
        """
        INSERT OR REPLACE INTO closure_item_sources (
            id, closure_item_id, source_type, source_id, source_status,
            source_payload_json, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            item["id"],
            item["source_primary"],
            item["source_key"],
            item["state"],
            item["source_payload_json"],
            item["last_seen_at"],
        ),
    )
    if was_new:
        _record_event(conn, item["id"], "discovered", "", item["state"], "Closure item discovered from source.")
    return was_new


def _record_event(conn, item_id: str, event_type: str, from_state: str, to_state: str, note: str, evidence: str = "") -> None:
    event_id = _hash_id("CIE", f"{item_id}:{event_type}:{from_state}:{to_state}:{_now_iso()}:{note}", 24)
    conn.execute(
        """
        INSERT INTO closure_item_events (
            id, closure_item_id, event_type, from_state, to_state, note, evidence, actor
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'nexo')
        """,
        (event_id, item_id, event_type, from_state, to_state, note, evidence),
    )


def _protocol_task_candidates(conn, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "protocol_tasks"):
        return []
    rows = conn.execute(
        """
        SELECT task_id, session_id, goal, task_type, area, status, opened_at,
               close_evidence, outcome_notes, verification_step
        FROM protocol_tasks
        WHERE lower(status) IN ('open', 'partial', 'blocked')
        ORDER BY opened_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for raw in rows:
        row = _as_dict(raw)
        status = str(row.get("status") or "open").lower()
        state = "waiting" if status == "blocked" else "open"
        urgency = min(1.0, 0.35 + (_age_days(row.get("opened_at")) / 14.0))
        kind = f"protocol_task_{status}"
        next_action = "Finish the task and close it with concrete evidence."
        if status == "partial":
            next_action = "Supply missing verification or convert the residual work into an explicit followup."
        elif status == "blocked":
            next_action = "Resolve the blocker or get an operator decision before continuing."
        items.append(_candidate(
            source_primary="protocol_tasks",
            source_key=str(row.get("task_id") or ""),
            kind=kind,
            title=str(row.get("goal") or row.get("task_id") or "Open protocol task"),
            summary=str(row.get("outcome_notes") or row.get("verification_step") or ""),
            state=state,
            impact=0.75,
            urgency=urgency,
            risk=0.15,
            confidence=0.9,
            next_action=next_action,
            blocker_reason="status=blocked" if status == "blocked" else "",
            evidence_required=str(row.get("verification_step") or "Evidence before closure."),
            source_payload={
                "status": row.get("status"),
                "session_id": row.get("session_id"),
                "task_type": row.get("task_type"),
                "area": row.get("area"),
            },
        ))
    return items


def _followup_candidates(conn, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "followups"):
        return []
    rows = conn.execute(
        """
        SELECT id, date, description, verification, status, impact_score, created_at, updated_at
        FROM followups
        WHERE lower(status) NOT IN ('done', 'complete', 'completed', 'deleted', 'archived', 'cancelled', 'canceled')
        ORDER BY COALESCE(date, ''), updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for raw in rows:
        row = _as_dict(raw)
        due = row.get("date") or ""
        urgency = _deadline_urgency(due, default=0.45)
        overdue = bool(_parse_time(due) and urgency >= 1.0)
        kind = "followup_due" if overdue else "followup_pending"
        items.append(_candidate(
            source_primary="followups",
            source_key=str(row.get("id") or ""),
            kind=kind,
            title=str(row.get("description") or row.get("id") or "Pending followup"),
            state="open",
            impact=max(0.45, min(1.0, float(row.get("impact_score") or 0) / 100.0 if row.get("impact_score") else 0.55)),
            urgency=urgency,
            risk=0.1,
            confidence=0.85,
            next_action="Complete, update, or explicitly reschedule this followup.",
            evidence_required=str(row.get("verification") or "Result note or explicit reschedule evidence."),
            deadline_at=str(due or ""),
            source_payload={"status": row.get("status"), "date": due},
        ))
    return items


def _protocol_debt_candidates(conn, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "protocol_debt"):
        return []
    rows = conn.execute(
        """
        SELECT id, session_id, task_id, debt_type, severity, status, evidence, created_at
        FROM protocol_debt
        WHERE lower(status) = 'open'
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    items: list[dict[str, Any]] = []
    severity_impact = {"block": 0.85, "error": 0.8, "warn": 0.6, "info": 0.35}
    for raw in rows:
        row = _as_dict(raw)
        severity = str(row.get("severity") or "warn").lower()
        impact = severity_impact.get(severity, 0.6)
        items.append(_candidate(
            source_primary="protocol_debt",
            source_key=str(row.get("id") or ""),
            kind="protocol_debt_open",
            title=f"Open protocol debt: {row.get('debt_type') or row.get('id')}",
            summary=str(row.get("evidence") or ""),
            state="waiting" if severity in {"block", "error"} else "open",
            impact=impact,
            urgency=min(1.0, 0.4 + (_age_days(row.get("created_at")) / 21.0)),
            risk=0.2,
            confidence=0.9,
            next_action="Resolve the debt with evidence or mark it as intentionally superseded.",
            blocker_reason="blocking protocol debt" if severity in {"block", "error"} else "",
            evidence_required="Resolution note tied to the source task/session.",
            source_payload={
                "severity": severity,
                "task_id": row.get("task_id"),
                "session_id": row.get("session_id"),
            },
        ))
    return items


def _outcome_candidates(conn, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "outcomes"):
        return []
    rows = conn.execute(
        """
        SELECT id, action_type, action_id, session_id, description, expected_result,
               status, deadline, checked_at, notes
        FROM outcomes
        WHERE lower(status) IN ('pending', 'missed', 'failed')
        ORDER BY deadline ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for raw in rows:
        row = _as_dict(raw)
        status = str(row.get("status") or "pending").lower()
        items.append(_candidate(
            source_primary="outcomes",
            source_key=str(row.get("id") or ""),
            kind=f"outcome_{status}",
            title=str(row.get("description") or row.get("expected_result") or f"Outcome {row.get('id')}"),
            summary=str(row.get("notes") or row.get("expected_result") or ""),
            state="open",
            impact=0.7 if status == "pending" else 0.8,
            urgency=_deadline_urgency(row.get("deadline"), default=0.45),
            risk=0.15,
            confidence=0.85,
            next_action="Verify the expected result and record the outcome.",
            evidence_required=str(row.get("expected_result") or "Measured result or explicit miss reason."),
            deadline_at=str(row.get("deadline") or ""),
            source_payload={
                "status": status,
                "action_type": row.get("action_type"),
                "action_id": row.get("action_id"),
                "session_id": row.get("session_id"),
            },
        ))
    return items


def _mcp_write_queue_candidates(limit: int) -> list[dict[str, Any]]:
    nexo_home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))).expanduser()
    root = nexo_home / "runtime" / "operations" / "mcp-write-queue"
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    for state in ("dead_letter", "failed", "retrying", "queued"):
        for path in sorted((root / state).glob("*.json"))[: max(1, limit)]:
            if len(items) >= limit:
                return items
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            write_id = str(payload.get("writeId") or path.stem)
            status = str(payload.get("status") or state)
            created = payload.get("created_at")
            items.append(_candidate(
                source_primary="mcp_write_queue",
                source_key=write_id,
                kind=f"mcp_write_queue_{status}",
                title=f"MCP write queue item {write_id}",
                summary=str(payload.get("kind") or ""),
                state="waiting" if status in {"failed", "dead_letter"} else "open",
                impact=0.65,
                urgency=min(1.0, 0.35 + (_age_days(created) / 3.0)),
                risk=0.2,
                confidence=0.8,
                next_action="Inspect the queued write and let the queue worker retry or resolve it manually.",
                blocker_reason=status if status in {"failed", "dead_letter"} else "",
                evidence_required="Committed queue status or explicit dead-letter resolution.",
                source_payload={
                    "status": status,
                    "kind": payload.get("kind"),
                    "attempts": payload.get("attempts"),
                    "last_error": str(payload.get("last_error") or "")[:240],
                },
            ))
    return items


def refresh_closure_items(conn=None, *, limit_per_adapter: int = 250) -> dict[str, Any]:
    if conn is None:
        from db import get_db
        from db._schema import run_migrations

        conn = get_db()
        run_migrations(conn)
    candidates: list[dict[str, Any]] = []
    adapter_counts: dict[str, int] = {}
    adapters = [
        ("protocol_tasks", lambda: _protocol_task_candidates(conn, limit_per_adapter)),
        ("followups", lambda: _followup_candidates(conn, limit_per_adapter)),
        ("protocol_debt", lambda: _protocol_debt_candidates(conn, limit_per_adapter)),
        ("outcomes", lambda: _outcome_candidates(conn, limit_per_adapter)),
        ("mcp_write_queue", lambda: _mcp_write_queue_candidates(limit_per_adapter)),
    ]
    for name, adapter in adapters:
        try:
            produced = adapter()
        except Exception:
            produced = []
        adapter_counts[name] = len(produced)
        candidates.extend(produced)

    created = 0
    for item in candidates:
        if _upsert_candidate(conn, item):
            created += 1
    conn.commit()
    _write_daily_snapshot(conn)
    return {
        "ok": True,
        "adapters": adapter_counts,
        "observed": len(candidates),
        "created": created,
    }


def closure_next(conn=None, *, limit: int = 10, include_waiting: bool = False, source: str = "", kind: str = "") -> list[dict[str, Any]]:
    if conn is None:
        from db import get_db

        conn = get_db()
    states = ("open", "verified", "waiting") if include_waiting else ("open", "verified")
    clauses = [f"state IN ({','.join('?' for _ in states)})"]
    params: list[Any] = list(states)
    if source:
        clauses.append("source_primary = ?")
        params.append(source)
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    params.append(max(1, min(int(limit or 10), 100)))
    rows = conn.execute(
        f"""
        SELECT *
        FROM closure_items
        WHERE {' AND '.join(clauses)}
        ORDER BY priority_score DESC, urgency_score DESC, updated_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_as_dict(row) for row in rows]


def closure_status(conn=None, *, refresh: bool = True, limit: int = 10) -> dict[str, Any]:
    if conn is None:
        from db import get_db
        from db._schema import run_migrations

        conn = get_db()
        run_migrations(conn)
    refresh_result = refresh_closure_items(conn, limit_per_adapter=250) if refresh else {"ok": True}
    counts = {
        row["state"]: row["n"]
        for row in conn.execute("SELECT state, COUNT(*) AS n FROM closure_items GROUP BY state").fetchall()
    }
    by_kind = {
        row["kind"]: row["n"]
        for row in conn.execute("SELECT kind, COUNT(*) AS n FROM closure_items WHERE state IN ('open', 'waiting', 'verified') GROUP BY kind").fetchall()
    }
    return {
        "ok": True,
        "schema": "nexo.closure.status.v1",
        "refreshed": refresh_result,
        "counts": counts,
        "open_total": sum(int(counts.get(state, 0)) for state in OPEN_STATES),
        "by_kind": by_kind,
        "next": closure_next(conn, limit=limit, include_waiting=True),
    }


def closure_item_get(item_id: str, conn=None) -> dict[str, Any] | None:
    if conn is None:
        from db import get_db

        conn = get_db()
    clean_id = str(item_id or "").strip()
    if not clean_id:
        return None
    item = conn.execute(
        "SELECT * FROM closure_items WHERE id = ? OR dedupe_key = ?",
        (clean_id, clean_id),
    ).fetchone()
    if not item:
        return None
    payload = _as_dict(item)
    sources = conn.execute(
        "SELECT * FROM closure_item_sources WHERE closure_item_id = ? ORDER BY observed_at DESC",
        (payload["id"],),
    ).fetchall()
    events = conn.execute(
        "SELECT * FROM closure_item_events WHERE closure_item_id = ? ORDER BY created_at DESC LIMIT 50",
        (payload["id"],),
    ).fetchall()
    payload["sources"] = [_as_dict(row) for row in sources]
    payload["events"] = [_as_dict(row) for row in events]
    return payload


def closure_verify_item(item_id: str, evidence: str, conn=None) -> dict[str, Any]:
    if conn is None:
        from db import get_db

        conn = get_db()
    clean_evidence = str(evidence or "").strip()
    if not clean_evidence:
        return {"ok": False, "error": "evidence is required"}
    item = closure_item_get(item_id, conn)
    if not item:
        return {"ok": False, "error": "closure item not found"}
    if item["state"] in FINAL_STATES:
        return {"ok": False, "error": f"closure item is already {item['state']}"}
    now = _now_iso()
    conn.execute(
        """
        UPDATE closure_items
        SET state = 'verified',
            evidence_observed = ?,
            last_progress_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (clean_evidence, now, now, item["id"]),
    )
    _record_event(conn, item["id"], "verified", item["state"], "verified", "Closure evidence recorded.", clean_evidence)
    conn.commit()
    return {"ok": True, "id": item["id"], "state": "verified"}


def closure_close_item(item_id: str, *, reason: str = "completed", conn=None) -> dict[str, Any]:
    if conn is None:
        from db import get_db

        conn = get_db()
    item = closure_item_get(item_id, conn)
    if not item:
        return {"ok": False, "error": "closure item not found"}
    if item["state"] in FINAL_STATES:
        return {"ok": True, "id": item["id"], "state": item["state"], "already_final": True}
    if not str(item.get("evidence_observed") or "").strip() and str(reason or "").strip() not in {"rejected", "stale"}:
        return {"ok": False, "error": "verification evidence is required before close"}
    final_state = "rejected" if str(reason or "").strip() == "rejected" else "stale" if str(reason or "").strip() == "stale" else "closed"
    now = _now_iso()
    conn.execute(
        """
        UPDATE closure_items
        SET state = ?,
            closed_at = ?,
            close_reason = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (final_state, now, str(reason or final_state), now, item["id"]),
    )
    _record_event(conn, item["id"], "closed", item["state"], final_state, str(reason or final_state), item.get("evidence_observed") or "")
    conn.commit()
    return {"ok": True, "id": item["id"], "state": final_state}


def _write_daily_snapshot(conn) -> None:
    counts = {
        row["state"]: row["n"]
        for row in conn.execute("SELECT state, COUNT(*) AS n FROM closure_items GROUP BY state").fetchall()
    }
    top = closure_next(conn, limit=10, include_waiting=True)
    conn.execute(
        """
        INSERT OR REPLACE INTO closure_daily_snapshots (
            snapshot_date, total_open, total_verified, total_waiting, total_closed,
            top_items_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _today(),
            int(counts.get("open", 0)),
            int(counts.get("verified", 0)),
            int(counts.get("waiting", 0)),
            int(counts.get("closed", 0)),
            _safe_json([
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "priority_score": item.get("priority_score"),
                    "state": item.get("state"),
                }
                for item in top
            ]),
            _now_iso(),
        ),
    )
    conn.commit()


def handle_closure_status(refresh: bool = True, limit: int = 10) -> str:
    return json.dumps(closure_status(refresh=refresh, limit=limit), indent=2, ensure_ascii=False)


def handle_closure_next(limit: int = 10, include_waiting: bool = False, source: str = "", kind: str = "") -> str:
    from db import get_db
    from db._schema import run_migrations

    conn = get_db()
    run_migrations(conn)
    refresh_closure_items(conn)
    return json.dumps({
        "ok": True,
        "items": closure_next(conn, limit=limit, include_waiting=include_waiting, source=source, kind=kind),
    }, indent=2, ensure_ascii=False)


def handle_closure_item_get(item_id: str) -> str:
    from db import get_db
    from db._schema import run_migrations

    conn = get_db()
    run_migrations(conn)
    item = closure_item_get(item_id, conn)
    return json.dumps({"ok": bool(item), "item": item}, indent=2, ensure_ascii=False)


def handle_closure_verify(item_id: str, evidence: str) -> str:
    from db import get_db
    from db._schema import run_migrations

    conn = get_db()
    run_migrations(conn)
    return json.dumps(closure_verify_item(item_id, evidence, conn), indent=2, ensure_ascii=False)


def handle_closure_close(item_id: str, reason: str = "completed") -> str:
    from db import get_db
    from db._schema import run_migrations

    conn = get_db()
    run_migrations(conn)
    return json.dumps(closure_close_item(item_id, reason=reason, conn=conn), indent=2, ensure_ascii=False)
