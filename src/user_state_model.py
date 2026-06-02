from __future__ import annotations

"""Inspectable user-state model built from multiple NEXO signals."""

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import cognitive
from db import get_db
from db._hot_context import search_hot_context
from memory_backends import get_backend


OPERATIONAL_POLICY_VERSION = "operational_state_v1"
CANONICAL_AREAS = {
    "brain",
    "desktop",
    "release",
    "server",
    "email",
    "ads",
    "legal",
    "personal",
    "billing",
    "external_publication",
    "general",
}
AREA_ALIASES = {
    "nexo": "brain",
    "nexo brain": "brain",
    "nexo desktop": "desktop",
    "deploy": "release",
    "deployment": "release",
    "publish": "external_publication",
    "publication": "external_publication",
    "public": "external_publication",
    "finance": "billing",
    "payments": "billing",
    "payment": "billing",
    "mail": "email",
    "google ads": "ads",
}
CAUTION_LEVELS = {"fluid", "normal", "cautious", "max_caution"}
COMMUNICATION_MODES = {"ultra_concise", "concise", "normal", "expanded"}
DETAIL_MODES = {"terse", "normal", "expanded"}
VERIFICATION_REQUIREMENTS = {"none", "single", "double", "external", "release_gate"}
AUTONOMY_LIMITS = {"act", "propose", "ask", "defer"}
AREA_RISKS = {"low", "medium", "high", "critical"}
PRIVACY_LEVELS = {"public", "normal", "private", "sensitive", "secret"}
PRIVACY_ORDER = {"public": 0, "normal": 1, "private": 2, "sensitive": 3, "secret": 4}
GLOBAL_SOURCE_PREFIXES = {"trust_score", "sentiment_log", "adaptive_log"}
AREA_SOURCE_PREFIXES = {
    "somatic_event",
    "memory_correction",
    "protocol_task",
    "cortex_evaluation",
    "outcome",
    "predictive_context",
    "workflow_run",
    "learning",
    "preference",
}
OPERATIONAL_SOURCE_PREFIXES = GLOBAL_SOURCE_PREFIXES | AREA_SOURCE_PREFIXES


def init_tables() -> None:
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_state_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            state_label TEXT NOT NULL,
            confidence REAL DEFAULT 0.0,
            guidance TEXT DEFAULT '',
            signals TEXT DEFAULT '{}',
            backend_key TEXT DEFAULT 'sqlite',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_user_state_snapshots_created ON user_state_snapshots(created_at);
        CREATE INDEX IF NOT EXISTS idx_user_state_snapshots_label ON user_state_snapshots(state_label);
        CREATE TABLE IF NOT EXISTS operational_state_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_uid TEXT NOT NULL UNIQUE,
            policy_version TEXT NOT NULL,
            created_at REAL NOT NULL,
            area_key TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            task_type TEXT DEFAULT '',
            caution_level TEXT NOT NULL,
            communication_mode TEXT NOT NULL,
            detail_mode TEXT NOT NULL,
            verification_requirement TEXT NOT NULL,
            autonomy_limit TEXT NOT NULL,
            area_risk TEXT NOT NULL,
            reason_codes_json TEXT NOT NULL DEFAULT '[]',
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            privacy_level TEXT NOT NULL DEFAULT 'normal',
            input_hash TEXT NOT NULL,
            expires_at REAL NOT NULL,
            decay_policy_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_operational_state_area_created
            ON operational_state_snapshots(area_key, created_at);
        CREATE INDEX IF NOT EXISTS idx_operational_state_scope
            ON operational_state_snapshots(scope_key, created_at);
        CREATE INDEX IF NOT EXISTS idx_operational_state_expires
            ON operational_state_snapshots(expires_at);
        """
    )
    conn.commit()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _parse_json(value: str | None, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else default
    except Exception:
        return default


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8", errors="ignore")).hexdigest()


def _table_exists(conn, table_name: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone() is not None
    except Exception:
        return False


def _query_exists(conn, table: str, column: str, value: str) -> bool:
    if not _table_exists(conn, table):
        return False
    return conn.execute(f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1", (value,)).fetchone() is not None


def canonical_area(area: str | None) -> str:
    clean = _normalize(area)
    if not clean:
        return "general"
    if clean in AREA_ALIASES:
        return AREA_ALIASES[clean]
    if clean in CANONICAL_AREAS:
        return clean
    for alias, canonical in AREA_ALIASES.items():
        if alias in clean:
            return canonical
    for candidate in ("release", "desktop", "server", "email", "ads", "legal", "billing", "personal", "brain"):
        if candidate in clean:
            return candidate
    return "general"


def _areas_for_text(value: str | None) -> set[str]:
    clean = _normalize(value)
    if not clean:
        return {"general"}
    areas = {canonical_area(clean)}
    if "desktop" in clean and "release" in clean:
        areas.update({"desktop", "release"})
    if "public" in clean or "publish" in clean:
        areas.add("external_publication")
    return {area for area in areas if area in CANONICAL_AREAS}


def _privacy(value: str | None) -> str:
    clean = _normalize(value)
    if clean == "internal":
        clean = "normal"
    if clean == "confidential":
        clean = "sensitive"
    return clean if clean in PRIVACY_LEVELS else "normal"


def _max_privacy(*levels: str) -> str:
    current = "public"
    for level in levels:
        clean = _privacy(level)
        if PRIVACY_ORDER[clean] > PRIVACY_ORDER[current]:
            current = clean
    return current


def _parse_ts(value: Any) -> float:
    if value in ("", None):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    return 0.0


def _recent_enough(value: Any, *, now: float, hours: float) -> bool:
    ts = _parse_ts(value)
    if not ts:
        return False
    return now - ts <= hours * 3600


def _decayed_delta(delta: float, created_at: float, *, now: float, half_life_hours: float) -> float:
    if delta <= 0:
        return delta
    if not created_at or now <= created_at:
        return delta
    age_hours = max(0.0, (now - created_at) / 3600.0)
    if half_life_hours <= 0:
        return delta
    return delta * (0.5 ** (age_hours / half_life_hours))


def _source_prefix(ref: str) -> tuple[str, str]:
    prefix, sep, value = str(ref or "").strip().partition(":")
    if not sep:
        return "", ""
    return prefix.strip(), value.strip()


def _row_dict(row) -> dict:
    return dict(row) if row else {}


def _append_source_ref(refs: list[str], seen: set[str], prefix: str, value: Any) -> None:
    clean_value = str(value or "").strip()
    if not prefix or not clean_value:
        return
    ref = f"{prefix}:{clean_value}"
    if ref not in seen:
        refs.append(ref)
        seen.add(ref)


def collect_operational_source_refs(
    *,
    area: str = "general",
    task_type: str = "",
    response_contract: dict[str, Any] | None = None,
    now: float | None = None,
    limit: int = 16,
) -> list[str]:
    """Collect recent canonical signal refs without copying private payloads."""
    stamp = float(now if now is not None else time.time())
    area_key = canonical_area(area)
    refs: list[str] = []
    seen: set[str] = set()
    conn = get_db()
    cdb = cognitive._get_db()

    if _table_exists(cdb, "trust_score"):
        rows = cdb.execute(
            """
            SELECT id, score, event, delta, created_at
            FROM trust_score
            ORDER BY id DESC
            LIMIT 8
            """
        ).fetchall()
        for row in rows:
            item = _row_dict(row)
            score = float(item.get("score") or 50.0)
            delta = float(item.get("delta") or 0.0)
            event = _normalize(item.get("event"))
            if item.get("id") and _recent_enough(item.get("created_at"), now=stamp, hours=24 * 7):
                if score < 40 or delta > 0 or event in {"correction", "repeated_error", "explicit_thanks", "delegation"}:
                    _append_source_ref(refs, seen, "trust_score", item["id"])

    if _table_exists(cdb, "sentiment_log"):
        rows = cdb.execute(
            """
            SELECT id, sentiment, intensity, created_at
            FROM sentiment_log
            ORDER BY id DESC
            LIMIT 8
            """
        ).fetchall()
        for row in rows:
            item = _row_dict(row)
            sentiment = _normalize(item.get("sentiment"))
            if sentiment in {"urgent", "negative"} and _recent_enough(item.get("created_at"), now=stamp, hours=24):
                _append_source_ref(refs, seen, "sentiment_log", item.get("id"))

    if _table_exists(conn, "adaptive_log"):
        rows = conn.execute(
            """
            SELECT id, mode, tension_score, timestamp
            FROM adaptive_log
            ORDER BY id DESC
            LIMIT 8
            """
        ).fetchall()
        for row in rows:
            item = _row_dict(row)
            mode = _normalize(item.get("mode")).upper()
            tension_score = float(item.get("tension_score") or 0.0)
            if (mode == "TENSION" or tension_score >= 0.55) and _recent_enough(item.get("timestamp"), now=stamp, hours=24):
                _append_source_ref(refs, seen, "adaptive_log", item.get("id"))

    if _table_exists(conn, "somatic_events"):
        rows = conn.execute(
            """
            SELECT id, timestamp, target, target_type, delta
            FROM somatic_events
            ORDER BY id DESC
            LIMIT 32
            """
        ).fetchall()
        for row in rows:
            item = _row_dict(row)
            if float(item.get("delta") or 0.0) <= 0:
                continue
            target_type = _normalize(item.get("target_type"))
            areas = {"global"} if target_type == "global" else _areas_for_text(item.get("target"))
            if ("global" in areas or area_key in areas) and _recent_enough(item.get("timestamp"), now=stamp, hours=24 * 30):
                _append_source_ref(refs, seen, "somatic_event", item.get("id"))

    if _table_exists(cdb, "memory_corrections"):
        rows = cdb.execute(
            """
            SELECT id, context, created_at
            FROM memory_corrections
            ORDER BY id DESC
            LIMIT 32
            """
        ).fetchall()
        for row in rows:
            item = _row_dict(row)
            context = str(item.get("context") or "")
            inferred_area = context.split(":", 1)[0] if ":" in context else context
            if area_key in _areas_for_text(inferred_area) and _recent_enough(item.get("created_at"), now=stamp, hours=24 * 7):
                _append_source_ref(refs, seen, "memory_correction", item.get("id"))

    if _table_exists(conn, "protocol_tasks"):
        rows = conn.execute(
            """
            SELECT task_id, area, status, guard_has_blocking, response_high_stakes, opened_at
            FROM protocol_tasks
            ORDER BY opened_at DESC
            LIMIT 32
            """
        ).fetchall()
        for row in rows:
            item = _row_dict(row)
            status = _normalize(item.get("status"))
            high_stakes = bool(item.get("response_high_stakes"))
            guard_blocking = bool(item.get("guard_has_blocking"))
            if area_key in _areas_for_text(item.get("area")) and _recent_enough(item.get("opened_at"), now=stamp, hours=24 * 7):
                if high_stakes or guard_blocking or status in {"open", "active", "blocked"}:
                    _append_source_ref(refs, seen, "protocol_task", item.get("task_id"))

    if _table_exists(conn, "cortex_evaluations"):
        rows = conn.execute(
            """
            SELECT id, area, impact_level, created_at
            FROM cortex_evaluations
            ORDER BY created_at DESC, id DESC
            LIMIT 32
            """
        ).fetchall()
        for row in rows:
            item = _row_dict(row)
            impact = _normalize(item.get("impact_level"))
            if area_key in _areas_for_text(item.get("area")) and impact in {"high", "critical"}:
                if _recent_enough(item.get("created_at"), now=stamp, hours=24 * 7):
                    _append_source_ref(refs, seen, "cortex_evaluation", item.get("id"))

    if _table_exists(conn, "outcomes"):
        rows = conn.execute(
            """
            SELECT id, action_type, status, created_at, updated_at
            FROM outcomes
            ORDER BY updated_at DESC, id DESC
            LIMIT 32
            """
        ).fetchall()
        for row in rows:
            item = _row_dict(row)
            status = _normalize(item.get("status"))
            status_relevant = status in {"failed", "missed", "blocked", "cancelled", "met", "success", "succeeded", "done", "fulfilled"}
            if area_key in _areas_for_text(item.get("action_type")) and status_relevant:
                if _recent_enough(item.get("updated_at") or item.get("created_at"), now=stamp, hours=24 * 7):
                    _append_source_ref(refs, seen, "outcome", item.get("id"))

    if _table_exists(conn, "workflow_runs"):
        rows = conn.execute(
            """
            SELECT run_id, goal, workflow_kind, status, opened_at, updated_at
            FROM workflow_runs
            ORDER BY updated_at DESC
            LIMIT 32
            """
        ).fetchall()
        for row in rows:
            item = _row_dict(row)
            status = _normalize(item.get("status"))
            areas = _areas_for_text(f"{item.get('workflow_kind') or ''} {item.get('goal') or ''}")
            if area_key in areas and status in {"blocked", "waiting_approval"}:
                if _recent_enough(item.get("updated_at") or item.get("opened_at"), now=stamp, hours=24 * 7):
                    _append_source_ref(refs, seen, "workflow_run", item.get("run_id"))

    if _table_exists(conn, "predictive_context_events"):
        rows = conn.execute(
            """
            SELECT event_uid, area_key, risk_level, created_at
            FROM predictive_context_events
            ORDER BY created_at DESC
            LIMIT 32
            """
        ).fetchall()
        for row in rows:
            item = _row_dict(row)
            risk = _normalize(item.get("risk_level"))
            if canonical_area(item.get("area_key")) == area_key and risk in {"high", "critical"}:
                if _recent_enough(item.get("created_at"), now=stamp, hours=24 * 7):
                    _append_source_ref(refs, seen, "predictive_context", item.get("event_uid"))

    if bool((response_contract or {}).get("high_stakes")) or _normalize(task_type) in {"edit", "execute", "delegate"}:
        # Keep room for explicit refs while prioritizing recent operational signals.
        limit = max(limit, 8)
    return refs[: max(1, min(int(limit or 16), 64))]


def validate_operational_source_ref(source_ref: str) -> dict[str, Any]:
    prefix, value = _source_prefix(source_ref)
    if not prefix or not value:
        return {"ok": False, "source_ref": source_ref, "reason": "invalid_source_ref"}

    conn = get_db()
    cdb = cognitive._get_db()
    if prefix == "trust_score":
        row = cdb.execute("SELECT id, score, event, delta, created_at FROM trust_score WHERE id=?", (value,)).fetchone()
        if not row:
            return {"ok": False, "source_ref": source_ref, "reason": "missing_ref"}
        item = _row_dict(row)
        tension = 0.15 if float(item.get("score") or 50.0) < 40 else 0.0
        relief = -0.05 if float(item.get("delta") or 0.0) > 0 else 0.0
        return {
            "ok": True,
            "source_ref": source_ref,
            "family": prefix,
            "areas": ["global"],
            "privacy_level": "normal",
            "created_at": _parse_ts(item.get("created_at")),
            "delta": tension + relief,
            "reason_code": "trust_low" if tension else ("positive_trust_weak" if relief else "trust_reference"),
        }
    if prefix == "sentiment_log":
        row = cdb.execute("SELECT id, sentiment, intensity, created_at FROM sentiment_log WHERE id=?", (value,)).fetchone()
        if not row:
            return {"ok": False, "source_ref": source_ref, "reason": "missing_ref"}
        item = _row_dict(row)
        sentiment = str(item.get("sentiment") or "")
        intensity = float(item.get("intensity") or 0.5)
        delta = 0.15 * intensity if sentiment == "urgent" else 0.10 * intensity if sentiment == "negative" else 0.0
        return {
            "ok": True,
            "source_ref": source_ref,
            "family": prefix,
            "areas": ["global"],
            "privacy_level": "normal",
            "created_at": _parse_ts(item.get("created_at")),
            "delta": delta,
            "reason_code": "urgent_signal" if sentiment == "urgent" else "sentiment_pressure" if delta else "sentiment_reference",
        }
    if prefix == "adaptive_log":
        row = conn.execute("SELECT id, mode, tension_score, timestamp FROM adaptive_log WHERE id=?", (value,)).fetchone()
        if not row:
            return {"ok": False, "source_ref": source_ref, "reason": "missing_ref"}
        item = _row_dict(row)
        mode = str(item.get("mode") or "").upper()
        tension_score = max(0.0, min(1.0, float(item.get("tension_score") or 0.0)))
        return {
            "ok": True,
            "source_ref": source_ref,
            "family": prefix,
            "areas": ["global"],
            "privacy_level": "normal",
            "created_at": _parse_ts(item.get("timestamp")),
            "delta": 0.10 if mode == "TENSION" else max(0.0, min(0.05, tension_score * 0.05)),
            "reason_code": "adaptive_tension" if mode == "TENSION" else "adaptive_reference",
        }
    if prefix == "somatic_event":
        row = conn.execute("SELECT id, timestamp, target, target_type, event_type, delta, source FROM somatic_events WHERE id=?", (value,)).fetchone()
        if not row:
            return {"ok": False, "source_ref": source_ref, "reason": "missing_ref"}
        item = _row_dict(row)
        target_type = _normalize(item.get("target_type"))
        target = _normalize(item.get("target"))
        areas = ["global"] if target_type == "global" else sorted(_areas_for_text(target))
        return {
            "ok": True,
            "source_ref": source_ref,
            "family": prefix,
            "areas": areas,
            "privacy_level": "normal",
            "created_at": _parse_ts(item.get("timestamp")),
            "delta": max(0.0, min(0.25, float(item.get("delta") or 0.0))),
            "reason_code": "area_somatic_risk",
        }
    if prefix == "memory_correction":
        row = cdb.execute("SELECT id, memory_id, store, correction_type, context, created_at FROM memory_corrections WHERE id=?", (value,)).fetchone()
        if not row:
            return {"ok": False, "source_ref": source_ref, "reason": "missing_ref"}
        item = _row_dict(row)
        context = str(item.get("context") or "")
        inferred_area = context.split(":", 1)[0] if ":" in context else context
        return {
            "ok": True,
            "source_ref": source_ref,
            "family": prefix,
            "areas": sorted(_areas_for_text(inferred_area)),
            "privacy_level": "private",
            "created_at": _parse_ts(item.get("created_at")),
            "delta": 0.25,
            "reason_code": "area_correction",
        }
    if prefix == "protocol_task":
        row = conn.execute("SELECT task_id, area, task_type, guard_has_blocking, response_high_stakes, opened_at FROM protocol_tasks WHERE task_id=?", (value,)).fetchone()
        if not row:
            return {"ok": False, "source_ref": source_ref, "reason": "missing_ref"}
        item = _row_dict(row)
        high_stakes = bool(item.get("response_high_stakes"))
        guard_blocking = bool(item.get("guard_has_blocking"))
        return {
            "ok": True,
            "source_ref": source_ref,
            "family": prefix,
            "areas": sorted(_areas_for_text(item.get("area") or "")),
            "privacy_level": "normal",
            "created_at": _parse_ts(item.get("opened_at")),
            "delta": (0.15 if high_stakes else 0.0) + (0.20 if guard_blocking else 0.0),
            "reason_code": "protocol_high_stakes" if high_stakes else "protocol_guard_blocking" if guard_blocking else "protocol_reference",
            "task_type": item.get("task_type") or "",
        }
    if prefix == "cortex_evaluation":
        row = conn.execute("SELECT id, area, impact_level, created_at FROM cortex_evaluations WHERE id=?", (value,)).fetchone()
        if not row:
            return {"ok": False, "source_ref": source_ref, "reason": "missing_ref"}
        item = _row_dict(row)
        impact = _normalize(item.get("impact_level"))
        return {
            "ok": True,
            "source_ref": source_ref,
            "family": prefix,
            "areas": sorted(_areas_for_text(item.get("area") or "")),
            "privacy_level": "normal",
            "created_at": _parse_ts(item.get("created_at")),
            "delta": 0.15 if impact == "critical" else 0.10 if impact == "high" else 0.05,
            "reason_code": "cortex_high_impact",
        }
    if prefix == "outcome":
        row = conn.execute("SELECT id, action_type, status, created_at, updated_at FROM outcomes WHERE id=?", (value,)).fetchone()
        if not row:
            return {"ok": False, "source_ref": source_ref, "reason": "missing_ref"}
        item = _row_dict(row)
        status = _normalize(item.get("status"))
        negative = status in {"failed", "missed", "blocked", "cancelled"}
        positive = status in {"met", "success", "succeeded", "done", "fulfilled"}
        return {
            "ok": True,
            "source_ref": source_ref,
            "family": prefix,
            "areas": sorted(_areas_for_text(item.get("action_type") or "")),
            "privacy_level": "normal",
            "created_at": _parse_ts(item.get("updated_at") or item.get("created_at")),
            "delta": 0.15 if negative else -0.10 if positive else 0.0,
            "reason_code": "area_outcome_negative" if negative else "area_outcome_verified_success" if positive else "outcome_reference",
        }
    if prefix == "workflow_run":
        row = conn.execute("SELECT run_id, goal, workflow_kind, status, opened_at, updated_at FROM workflow_runs WHERE run_id=?", (value,)).fetchone()
        if not row:
            return {"ok": False, "source_ref": source_ref, "reason": "missing_ref"}
        item = _row_dict(row)
        status = _normalize(item.get("status"))
        return {
            "ok": True,
            "source_ref": source_ref,
            "family": prefix,
            "areas": sorted(_areas_for_text(f"{item.get('workflow_kind') or ''} {item.get('goal') or ''}")),
            "privacy_level": "normal",
            "created_at": _parse_ts(item.get("updated_at") or item.get("opened_at")),
            "delta": 0.15 if status in {"blocked", "waiting_approval"} else 0.0,
            "reason_code": "workflow_blocked" if status in {"blocked", "waiting_approval"} else "workflow_reference",
        }
    if prefix == "learning":
        row = conn.execute("SELECT id, category, priority, created_at FROM learnings WHERE id=?", (value,)).fetchone()
        if not row:
            return {"ok": False, "source_ref": source_ref, "reason": "missing_ref"}
        item = _row_dict(row)
        priority = _normalize(item.get("priority"))
        return {
            "ok": True,
            "source_ref": source_ref,
            "family": prefix,
            "areas": sorted(_areas_for_text(item.get("category") or "")),
            "privacy_level": "normal",
            "created_at": _parse_ts(item.get("created_at")),
            "delta": 0.05 if priority in {"high", "critical"} else 0.0,
            "reason_code": "area_learning_guard",
        }
    if prefix == "preference":
        if not _query_exists(conn, "preferences", "key", value):
            return {"ok": False, "source_ref": source_ref, "reason": "missing_ref"}
        return {
            "ok": True,
            "source_ref": source_ref,
            "family": prefix,
            "areas": ["global"],
            "privacy_level": "normal",
            "created_at": time.time(),
            "delta": 0.0,
            "reason_code": "explicit_preference",
        }
    if prefix == "predictive_context":
        if not _table_exists(conn, "predictive_context_events"):
            return {"ok": False, "source_ref": source_ref, "reason": "predictive_context_unavailable"}
        row = conn.execute("SELECT event_uid, area_key, risk_level, created_at FROM predictive_context_events WHERE event_uid=?", (value,)).fetchone()
        if not row:
            return {"ok": False, "source_ref": source_ref, "reason": "missing_ref"}
        item = _row_dict(row)
        risk = _normalize(item.get("risk_level"))
        return {
            "ok": True,
            "source_ref": source_ref,
            "family": prefix,
            "areas": sorted(_areas_for_text(item.get("area_key") or "")),
            "privacy_level": "normal",
            "created_at": _parse_ts(item.get("created_at")),
            "delta": 0.15 if risk in {"high", "critical"} else 0.0,
            "reason_code": "predictive_context_high_risk" if risk in {"high", "critical"} else "predictive_context_reference",
        }
    return {"ok": False, "source_ref": source_ref, "reason": "unsupported_source_ref"}


def _source_applies(source: dict[str, Any], area_key: str) -> bool:
    areas = set(source.get("areas") or [])
    if "global" in areas:
        return True
    return area_key in areas


def _source_decay_hours(source: dict[str, Any]) -> float:
    family = source.get("family")
    if family in {"somatic_event", "memory_correction"}:
        return 24.0
    if family in {"protocol_task", "cortex_evaluation", "outcome", "workflow_run"}:
        return 24.0
    return 12.0


def _risk_from_tension(tension: float, *, high_risk: bool = False, critical_boundary: bool = False) -> str:
    if critical_boundary or tension >= 0.70:
        return "critical"
    if high_risk or tension >= 0.45:
        return "high"
    if tension >= 0.20:
        return "medium"
    return "low"


def _unique_reason_codes(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        clean = _normalize(value).replace(" ", "_")
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _policy_uid_for(*, area_key: str, scope_key: str, task_type: str, input_hash: str, policy_version: str) -> str:
    seed = "|".join([policy_version, area_key, scope_key, task_type, input_hash])
    return hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()


def _row_to_operational_snapshot(row, *, visible_guidance: str, applied_overrides: list[str], ttl_seconds: int) -> dict[str, Any]:
    item = dict(row)
    item["reason_codes"] = _parse_json(item.pop("reason_codes_json", "[]"), [])
    item["source_refs"] = _parse_json(item.pop("source_refs_json", "[]"), [])
    item["decay_policy"] = _parse_json(item.pop("decay_policy_json", "{}"), {})
    item["visible_guidance"] = visible_guidance
    item["applied_overrides"] = applied_overrides
    item["ttl_seconds"] = ttl_seconds
    item["ok"] = True
    return item


def _visible_guidance(
    *,
    verification_requirement: str,
    autonomy_limit: str,
    task_type: str,
    area_key: str,
    reason_codes: list[str],
) -> str:
    if verification_requirement == "release_gate":
        return "Verifico doble porque esto toca release."
    if verification_requirement == "external":
        return "Verifico con evidencia externa antes de ejecutar."
    if autonomy_limit in {"propose", "ask"}:
        return "Voy a proponer antes de ejecutar porque falta evidencia."
    if task_type == "answer" and area_key == "general" and not reason_codes:
        return "Respuesta corta: la pregunta no requiere contexto."
    if verification_requirement == "double":
        return "Verifico dos fuentes porque el riesgo operativo es alto."
    if verification_requirement == "single":
        return "Verifico una vez antes de avanzar."
    return "Actuo con la verificacion normal de esta tarea."


def build_operational_state_policy(
    *,
    area: str = "general",
    task_type: str = "",
    task_id: str = "",
    workflow_run_id: str = "",
    scope_key: str = "",
    source_refs: list[str] | None = None,
    response_contract: dict[str, Any] | None = None,
    current_instruction: str = "",
    explicit_autonomy: str = "",
    persist: bool = True,
    auto_collect: bool | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    init_tables()
    stamp = float(now if now is not None else time.time())
    area_key = canonical_area(area)
    response_contract = dict(response_contract or {})
    clean_task_type = _normalize(task_type)
    if clean_task_type not in {"answer", "analyze", "edit", "execute", "delegate", ""}:
        clean_task_type = ""
    if not scope_key:
        if task_id:
            scope_key = f"protocol_task:{task_id}"
        elif workflow_run_id:
            scope_key = f"workflow_run:{workflow_run_id}"
        else:
            scope_key = f"area:{area_key}"

    explicit_refs = [str(ref).strip() for ref in (source_refs or []) if str(ref).strip()]
    should_auto_collect = source_refs is None if auto_collect is None else bool(auto_collect)
    auto_refs = (
        collect_operational_source_refs(
            area=area_key,
            task_type=clean_task_type,
            response_contract=response_contract,
            now=stamp,
        )
        if should_auto_collect
        else []
    )
    refs = sorted({*explicit_refs, *auto_refs})
    validated = [validate_operational_source_ref(ref) for ref in refs]
    valid_sources = [source for source in validated if source.get("ok") and _source_applies(source, area_key)]
    invalid_refs = [source["source_ref"] for source in validated if not source.get("ok")]

    reason_codes: list[str] = []
    privacy = "normal"
    positive_success = False
    has_area_correction = False
    independent_positive_families: set[str] = set()
    tension = 0.0
    for source in valid_sources:
        delta = float(source.get("delta") or 0.0)
        decayed = _decayed_delta(
            delta,
            float(source.get("created_at") or stamp),
            now=stamp,
            half_life_hours=_source_decay_hours(source),
        )
        if source.get("family") in GLOBAL_SOURCE_PREFIXES:
            decayed = min(decayed, 0.15)
        tension += decayed
        if decayed > 0.01:
            independent_positive_families.add(str(source.get("family") or ""))
        if source.get("reason_code"):
            reason_codes.append(str(source["reason_code"]))
        privacy = _max_privacy(privacy, str(source.get("privacy_level") or "normal"))
        if source.get("reason_code") == "area_outcome_verified_success":
            positive_success = True
        if source.get("family") == "memory_correction" and decayed > 0.01:
            has_area_correction = True

    response_high_stakes = bool(response_contract.get("high_stakes"))
    response_mode = str(response_contract.get("mode") or "")
    if response_high_stakes:
        tension += 0.15
        reason_codes.append("protocol_high_stakes")
        independent_positive_families.add("response_contract")
    if invalid_refs:
        reason_codes.append("invalid_source_ref_ignored")

    explicit = _normalize(explicit_autonomy or current_instruction)
    release_boundary = area_key == "release" or "release" in _normalize(area)
    critical_boundary = area_key in {"legal", "billing", "external_publication"} and response_high_stakes
    high_risk = release_boundary and (response_high_stakes or clean_task_type in {"edit", "execute", "delegate"})
    tension = max(0.0, min(1.0, tension))

    area_risk = _risk_from_tension(tension, high_risk=high_risk, critical_boundary=critical_boundary)
    if area_risk == "critical" and (len(independent_positive_families) < 2 and not critical_boundary):
        area_risk = "high"
    if area_risk == "critical":
        caution_level = "max_caution"
    elif area_risk == "high":
        caution_level = "cautious"
    elif tension <= 0.15 and positive_success and not has_area_correction:
        caution_level = "fluid"
    else:
        caution_level = "normal"

    if release_boundary and clean_task_type in {"edit", "execute", "delegate"}:
        verification_requirement = "release_gate"
    elif area_key in {"external_publication", "legal", "billing"} and response_high_stakes:
        verification_requirement = "external"
    elif caution_level == "max_caution":
        verification_requirement = "double"
    elif caution_level == "cautious" or response_high_stakes:
        verification_requirement = "single"
    else:
        verification_requirement = "none"

    if response_mode == "defer":
        autonomy_limit = "defer"
    elif response_mode == "ask":
        autonomy_limit = "ask"
    elif verification_requirement in {"release_gate", "external", "double"}:
        autonomy_limit = "propose"
    elif explicit in {"act", "execute", "hazlo", "adelante", "sigue"}:
        autonomy_limit = "act"
        reason_codes.append("explicit_instruction_current")
    elif caution_level == "cautious":
        autonomy_limit = "propose"
    else:
        autonomy_limit = "act"

    if clean_task_type == "answer" and area_key == "general" and not valid_sources and not response_high_stakes:
        communication_mode = "ultra_concise"
        detail_mode = "terse"
    elif caution_level in {"cautious", "max_caution"}:
        communication_mode = "concise"
        detail_mode = "normal"
    else:
        communication_mode = "normal"
        detail_mode = "normal"

    reason_codes = _unique_reason_codes(reason_codes)
    visible_guidance = _visible_guidance(
        verification_requirement=verification_requirement,
        autonomy_limit=autonomy_limit,
        task_type=clean_task_type,
        area_key=area_key,
        reason_codes=reason_codes,
    )
    decay_policy = {
        "low_half_life_hours": 12,
        "medium_half_life_hours": 12,
        "high_half_life_hours": 24,
        "critical_half_life_hours": 72,
        "source_count": len(valid_sources),
        "invalid_source_refs": invalid_refs,
    }
    ttl_seconds = 43200 if area_risk in {"low", "medium"} else 86400 if area_risk == "high" else 259200
    expires_at = stamp + ttl_seconds
    input_payload = {
        "area_key": area_key,
        "scope_key": scope_key,
        "task_type": clean_task_type,
        "source_refs": refs,
        "response_mode": response_mode,
        "response_high_stakes": response_high_stakes,
        "explicit": explicit,
        "policy_version": OPERATIONAL_POLICY_VERSION,
    }
    input_hash = _hash_payload(input_payload)
    policy_uid = _policy_uid_for(
        area_key=area_key,
        scope_key=scope_key,
        task_type=clean_task_type,
        input_hash=input_hash,
        policy_version=OPERATIONAL_POLICY_VERSION,
    )
    applied_overrides = ["explicit_instruction_current"] if "explicit_instruction_current" in reason_codes else []
    conn = get_db()
    if persist:
        conn.execute(
            """
            INSERT OR IGNORE INTO operational_state_snapshots (
                policy_uid, policy_version, created_at, area_key, scope_key,
                task_type, caution_level, communication_mode, detail_mode,
                verification_requirement, autonomy_limit, area_risk,
                reason_codes_json, source_refs_json, privacy_level, input_hash,
                expires_at, decay_policy_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                policy_uid,
                OPERATIONAL_POLICY_VERSION,
                stamp,
                area_key,
                scope_key,
                clean_task_type,
                caution_level,
                communication_mode,
                detail_mode,
                verification_requirement,
                autonomy_limit,
                area_risk,
                _json(reason_codes),
                _json([source["source_ref"] for source in valid_sources]),
                privacy,
                input_hash,
                expires_at,
                _json(decay_policy),
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM operational_state_snapshots WHERE policy_uid=?", (policy_uid,)).fetchone()
    else:
        row = {
            "id": None,
            "policy_uid": policy_uid,
            "policy_version": OPERATIONAL_POLICY_VERSION,
            "created_at": stamp,
            "area_key": area_key,
            "scope_key": scope_key,
            "task_type": clean_task_type,
            "caution_level": caution_level,
            "communication_mode": communication_mode,
            "detail_mode": detail_mode,
            "verification_requirement": verification_requirement,
            "autonomy_limit": autonomy_limit,
            "area_risk": area_risk,
            "reason_codes_json": _json(reason_codes),
            "source_refs_json": _json([source["source_ref"] for source in valid_sources]),
            "privacy_level": privacy,
            "input_hash": input_hash,
            "expires_at": expires_at,
            "decay_policy_json": _json(decay_policy),
        }
    return _row_to_operational_snapshot(
        row,
        visible_guidance=visible_guidance,
        applied_overrides=applied_overrides,
        ttl_seconds=ttl_seconds,
    )


def list_operational_state_snapshots(*, area_key: str = "", scope_key: str = "", limit: int = 20) -> list[dict[str, Any]]:
    init_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    if area_key:
        clauses.append("area_key=?")
        params.append(canonical_area(area_key))
    if scope_key:
        clauses.append("scope_key=?")
        params.append(scope_key)
    rows = get_db().execute(
        f"""
        SELECT * FROM operational_state_snapshots
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        [*params, max(1, min(int(limit or 20), 100))],
    ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        item["reason_codes"] = _parse_json(item.pop("reason_codes_json", "[]"), [])
        item["source_refs"] = _parse_json(item.pop("source_refs_json", "[]"), [])
        item["decay_policy"] = _parse_json(item.pop("decay_policy_json", "{}"), {})
        results.append(item)
    return results


def _recent_correction_count(days: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    row = cognitive._get_db().execute(
        "SELECT COUNT(*) FROM memory_corrections WHERE created_at >= ?",
        (cutoff,),
    ).fetchone()
    return int((row[0] if row else 0) or 0)


def _recent_trust_event_count(days: int, event_name: str) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    row = cognitive._get_db().execute(
        "SELECT COUNT(*) FROM trust_score WHERE created_at >= ? AND event = ?",
        (cutoff, event_name),
    ).fetchone()
    return int((row[0] if row else 0) or 0)


def _recent_diary_signal_count(days: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    row = get_db().execute(
        "SELECT COUNT(*) FROM session_diary WHERE created_at >= ? AND user_signals != ''",
        (cutoff,),
    ).fetchone()
    return int((row[0] if row else 0) or 0)


def build_user_state(days: int = 7, *, persist: bool = False) -> dict:
    init_tables()
    trust = float(cognitive.get_trust_score())
    history = cognitive.get_trust_history(days=days)
    sentiments = history.get("sentiment_distribution", {})
    negative = int((sentiments.get("negative") or {}).get("count", 0) or 0)
    urgent = int((sentiments.get("urgent") or {}).get("count", 0) or 0)
    positive = int((sentiments.get("positive") or {}).get("count", 0) or 0)
    corrections = _recent_correction_count(days)
    repeated_errors = _recent_trust_event_count(days, "repeated_error")
    productive_sessions = _recent_trust_event_count(days, "session_productive")
    delegation_events = _recent_trust_event_count(days, "delegation")
    diaries_with_signals = _recent_diary_signal_count(days)
    active_contexts = len(search_hot_context("", hours=min(max(days, 1) * 24, 168), limit=50, state="active"))
    waiting_contexts = len(search_hot_context("", hours=min(max(days, 1) * 24, 168), limit=50, state="waiting_user"))
    blocked_contexts = len(search_hot_context("", hours=min(max(days, 1) * 24, 168), limit=50, state="blocked"))

    frustration_score = negative * 1.5 + corrections * 0.8 + repeated_errors * 1.2 + (1 if trust < 45 else 0)
    flow_score = positive * 1.2 + productive_sessions * 1.0 + delegation_events * 0.8 + (1 if trust > 60 else 0)
    urgency_score = urgent * 2.0 + blocked_contexts * 0.6

    if urgency_score >= max(2.0, frustration_score, flow_score):
        label = "urgent"
        guidance = "Immediate execution. Keep answers short. Avoid speculative detours."
        confidence = min(0.98, 0.45 + urgency_score * 0.12)
    elif frustration_score >= max(2.0, flow_score):
        label = "frustrated"
        guidance = "Ultra-concise mode. Show concrete progress and avoid avoidable questions."
        confidence = min(0.98, 0.4 + frustration_score * 0.1)
    elif flow_score >= 2.5:
        label = "in_flow"
        guidance = "Keep momentum. Bias toward execution and only interrupt for real blockers."
        confidence = min(0.98, 0.4 + flow_score * 0.09)
    elif waiting_contexts > 0 or active_contexts > 6:
        label = "loaded"
        guidance = "Prefer batching, tight summaries, and explicit next actions."
        confidence = 0.68
    else:
        label = "stable"
        guidance = "Normal mode. Clear, direct execution with selective initiative."
        confidence = 0.6

    snapshot = {
        "state_label": label,
        "confidence": round(confidence, 2),
        "guidance": guidance,
        "trust_score": round(trust, 1),
        "signals": {
            "negative_sentiment": negative,
            "urgent_sentiment": urgent,
            "positive_sentiment": positive,
            "recent_corrections": corrections,
            "repeated_errors": repeated_errors,
            "productive_sessions": productive_sessions,
            "delegation_events": delegation_events,
            "diaries_with_user_signals": diaries_with_signals,
            "active_contexts": active_contexts,
            "waiting_contexts": waiting_contexts,
            "blocked_contexts": blocked_contexts,
        },
        "backend": get_backend().key,
    }

    if persist:
        conn = get_db()
        conn.execute(
            "INSERT INTO user_state_snapshots (state_label, confidence, guidance, signals, backend_key) VALUES (?, ?, ?, ?, ?)",
            (
                snapshot["state_label"],
                snapshot["confidence"],
                snapshot["guidance"],
                json.dumps(snapshot["signals"], ensure_ascii=True, sort_keys=True),
                snapshot["backend"],
            ),
        )
        conn.commit()

    return snapshot


def list_user_state_snapshots(limit: int = 20) -> list[dict]:
    init_tables()
    rows = get_db().execute(
        "SELECT * FROM user_state_snapshots ORDER BY created_at DESC, id DESC LIMIT ?",
        (max(1, int(limit or 20)),),
    ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        try:
            item["signals"] = json.loads(item.get("signals") or "{}")
        except Exception:
            item["signals"] = {}
        results.append(item)
    return results


def user_state_stats(days: int = 30) -> dict:
    init_tables()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = get_db().execute(
        "SELECT state_label, COUNT(*) AS cnt FROM user_state_snapshots WHERE created_at >= ? GROUP BY state_label",
        (cutoff,),
    ).fetchall()
    return {
        "window_days": days,
        "snapshots": sum(int(row["cnt"]) for row in rows),
        "by_state": {row["state_label"]: row["cnt"] for row in rows},
        "backend": get_backend().key,
    }
