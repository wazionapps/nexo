"""Failure prevention ledger for autopsy candidates and antibody proposals.

This module coordinates existing NEXO owners. It does not create canonical
learnings, outcomes, guard rules, protocol debt, benchmarks, or followups by
itself; it records a validated, redacted case and proposed owner actions.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from db import get_db
from learning_resolver import resolve_learning_candidate


POLICY_VERSION = "failure_prevention.v1"

FAILURE_TYPES = {
    "memory", "identity", "promise", "release", "server", "privacy",
    "security", "test", "tool", "workflow", "communication", "performance",
    "other",
}
SOURCE_TYPES = {
    "francisco_correction", "explicit_instruction", "test_failure",
    "release_gate_failure", "outcome_miss", "protocol_debt",
    "guard_violation", "guard_check", "guardian_telemetry",
    "error_repetition", "somatic_event", "hook_run", "immune_finding",
    "watchdog_finding", "daily_self_audit", "deep_sleep_finding",
    "manual_review",
}
INFERENCE_ONLY_SOURCES = {
    "guardian_telemetry", "immune_finding", "watchdog_finding",
    "daily_self_audit", "deep_sleep_finding",
}
SEVERITIES = {"p0", "p1", "p2", "p3", "p4"}
CASE_STATUSES = {
    "candidate", "analyzing", "action_required", "antibody_pending",
    "verifying", "verified", "resolved", "rejected", "false_positive",
    "expired", "rolled_back", "conflict_review",
}
PRIVACY_LEVELS = {"public", "normal", "private", "sensitive", "secret"}
SURFACES = {"pre_action", "debug_local", "audit", "runtime_internal", "export"}
ACTION_TYPES = {
    "learning_resolve", "test_add", "benchmark_case_add",
    "guard_rule_proposal", "predictive_context_rule", "docs_update",
    "skill_update", "followup_create", "outcome_register",
    "release_gate_update", "immune_check_update", "watchdog_check_update",
}
TARGET_SYSTEMS = {
    "learning_resolver", "learnings", "pytest", "runtime_pack", "guardian",
    "pre_answer_router", "docs", "skills", "followups", "outcomes",
    "release_readiness", "immune", "watchdog",
}
ACTION_STATUSES = {
    "proposed", "approved", "applied", "verifying", "verified", "rejected",
    "expired", "rolled_back", "false_positive",
}
ACTIVATION_POLICIES = {
    "candidate_only", "shadow", "warn", "block_after_verification",
    "manual_approval_required",
}
VERIFICATION_STATUSES = {"missing", "pending", "passed", "failed", "not_applicable"}
NOT_APPLICABLE_ACTIONS = {"docs_update", "followup_create"}
APPROVAL_REF_PREFIXES = {"evidence", "protocol_task", "guard_check", "change_log"}

SOURCE_REF_PREFIXES: dict[str, set[str]] = {
    "francisco_correction": {"session_correction_requirement"},
    "explicit_instruction": {"protocol_task", "evidence"},
    "test_failure": {"test"},
    "release_gate_failure": {"test", "evidence"},
    "outcome_miss": {"outcome"},
    "protocol_debt": {"protocol_debt"},
    "guard_violation": {"protocol_debt", "guardian_rule"},
    "guard_check": {"guard_check"},
    "guardian_telemetry": {"guardian_telemetry"},
    "error_repetition": {"error_repetition"},
    "somatic_event": {"somatic_event"},
    "hook_run": {"hook_run"},
    "immune_finding": {"immune_finding"},
    "watchdog_finding": {"watchdog_finding"},
    "daily_self_audit": {"evidence", "protocol_debt"},
    "deep_sleep_finding": {"evidence"},
    "manual_review": {"evidence"},
}

DB_REF_TABLES: dict[str, tuple[str, str]] = {
    "learning": ("learnings", "id"),
    "error_repetition": ("error_repetitions", "id"),
    "somatic_event": ("somatic_events", "id"),
    "guard_check": ("guard_checks", "id"),
    "protocol_debt": ("protocol_debt", "id"),
    "session_correction_requirement": ("session_correction_requirements", "id"),
    "outcome": ("outcomes", "id"),
    "hook_run": ("hook_runs", "id"),
}

SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|authorization|bearer|credential|cred_ref)\b"
    r"\s*[:=]\s*['\"]?[^'\"\s,;]+"
)
BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ABS_PATH_RE = re.compile(r"(?<![\w.-])/(?:Users|home|var|etc|Volumes)/[^\s,;]+")
RAW_PAYLOAD_MARKER_RE = re.compile(r"(?i)\b(provider_payload|raw_prompt|raw_response|transcript)\b")
SENSITIVE_METADATA_KEY_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|authorization|bearer|credential|cred_ref|"
    r"idempotency[_-]?key|provider_payload|raw_prompt|raw_response|transcript)\b"
)


def _now() -> float:
    return time.time()


def _stable_uid(*parts: object) -> str:
    payload = "\0".join(str(part or "") for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: str, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _normalize_text(value: object) -> str:
    clean = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return clean[:1000]


def _clip(value: str, limit: int = 500) -> str:
    clean = str(value or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    return bool(row)


def _ensure_tables(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "failure_prevention_cases"):
        return
    from db._schema import run_migrations

    run_migrations(conn)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        if not value.strip():
            return []
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:
            pass
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _append_unique(existing_json: str, items: list[str]) -> str:
    existing = _as_list(_load_json(existing_json, []))
    seen = set(existing)
    for item in items:
        clean = str(item or "").strip()
        if clean and clean not in seen:
            existing.append(clean)
            seen.add(clean)
    return _json(existing)


def _normalize_failure_type(value: str) -> str:
    clean = str(value or "other").strip().lower()
    return clean if clean in FAILURE_TYPES else "other"


def _normalize_source_type(value: str) -> str:
    clean = str(value or "").strip().lower()
    return clean if clean in SOURCE_TYPES else ""


def _normalize_severity(value: str) -> str:
    clean = str(value or "p3").strip().lower()
    return clean if clean in SEVERITIES else "p3"


def _normalize_privacy(value: str) -> str:
    clean = str(value or "normal").strip().lower()
    return clean if clean in PRIVACY_LEVELS else "normal"


def _normalize_surface(value: str) -> str:
    clean = str(value or "audit").strip().lower()
    return clean if clean in SURFACES else "audit"


def _normalize_confidence(value: float | int | str) -> float:
    try:
        raw = float(value)
    except Exception:
        raw = 0.5
    return max(0.0, min(1.0, raw))


def redact_value(value: object) -> str:
    """Return a safe, local-only field preview."""
    text = str(value or "")
    if RAW_PAYLOAD_MARKER_RE.search(text):
        return "[redacted_payload]"
    text = SECRET_RE.sub(r"\1=[redacted]", text)
    text = BEARER_RE.sub("Bearer [redacted]", text)
    text = IPV4_RE.sub("[redacted_ip]", text)
    text = ABS_PATH_RE.sub("[redacted_path]", text)
    return _clip(re.sub(r"\s+", " ", text).strip())


def sanitize_metadata(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact metadata before it can reach persistent storage."""
    if _depth > 5:
        return "[redacted_depth]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        return "[redacted_bytes]"
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key or "").strip()
            if SENSITIVE_METADATA_KEY_RE.search(key_text):
                clean[f"redacted:{_stable_uid(key_text)[:12]}"] = "[redacted]"
                continue
            clean_key = redact_value(key_text)[:120] or "field"
            clean[clean_key] = sanitize_metadata(item, _depth=_depth + 1)
        return clean
    if isinstance(value, (list, tuple, set)):
        return [sanitize_metadata(item, _depth=_depth + 1) for item in list(value)[:50]]
    return redact_value(value)


def _idempotency_marker(value: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    return f"sha256:{_stable_uid(POLICY_VERSION, 'idempotency_key', clean)[:24]}"


def field_evidence(
    value: object = "",
    *,
    source_refs: list[str] | None = None,
    confidence: float = 0.5,
    privacy_level: str = "normal",
    value_ref: str = "",
) -> dict[str, Any]:
    return {
        "value_redacted": redact_value(value),
        "value_ref": _sanitize_ref(value_ref, allow_empty=True),
        "source_refs": [_sanitize_ref(ref) for ref in _as_list(source_refs)],
        "confidence": _normalize_confidence(confidence),
        "privacy_level": _normalize_privacy(privacy_level),
    }


def _surface_allowed(surface: str, allowed_surfaces: Any, *, privacy_level: str) -> bool:
    clean_surface = _normalize_surface(surface)
    privacy = _normalize_privacy(privacy_level)
    if privacy == "secret" and clean_surface not in {"audit", "runtime_internal"}:
        return False
    if privacy in {"private", "sensitive"} and clean_surface in {"pre_action", "export"}:
        return False
    allowed = set(_as_list(allowed_surfaces))
    if not allowed:
        allowed = set(_allowed_surfaces(privacy_level=privacy, status="candidate"))
    return clean_surface in allowed


def _safe_refs(value: Any) -> list[str]:
    return [redact_value(ref) for ref in _as_list(value) if redact_value(ref)]


def _safe_field(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    clean = dict(value)
    if "value_redacted" in clean:
        clean["value_redacted"] = redact_value(clean.get("value_redacted"))
    if "value_ref" in clean:
        clean["value_ref"] = redact_value(clean.get("value_ref"))
    if "source_refs" in clean:
        clean["source_refs"] = _safe_refs(clean.get("source_refs"))
    return clean


def _safe_case(case: dict[str, Any], *, surface: str = "audit") -> dict[str, Any]:
    if not case:
        return {}
    if not _surface_allowed(surface, case.get("allowed_surfaces"), privacy_level=str(case.get("privacy_level") or "normal")):
        return {}
    clean = dict(case)
    for key in ("entity_refs", "source_event_refs", "evidence_refs", "antibody_refs"):
        clean[key] = _safe_refs(clean.get(key))
    for key in ("primary_source_ref", "area"):
        clean[key] = redact_value(clean.get(key))
    for key in (
        "symptom", "trigger", "missed_signal", "wrong_assumption",
        "root_cause", "corrective_action",
    ):
        clean[key] = _safe_field(clean.get(key))
    clean["learning_resolution"] = sanitize_metadata(clean.get("learning_resolution") or {})
    clean["metadata"] = sanitize_metadata(clean.get("metadata") or {})
    return clean


def _safe_source_event(event: dict[str, Any]) -> dict[str, Any]:
    if not event:
        return {}
    clean = dict(event)
    clean["source_ref"] = redact_value(clean.get("source_ref"))
    clean["evidence_refs"] = _safe_refs(clean.get("evidence_refs"))
    clean["metadata"] = sanitize_metadata(clean.get("metadata") or {})
    return clean


def _safe_antibody(antibody: dict[str, Any]) -> dict[str, Any]:
    if not antibody:
        return {}
    clean = dict(antibody)
    for key in ("target_ref", "action_payload_ref", "verification_ref", "approved_ref", "rollback_ref"):
        clean[key] = redact_value(clean.get(key))
    clean["metadata"] = sanitize_metadata(clean.get("metadata") or {})
    return clean


def _ref_prefix(ref: str) -> str:
    clean = str(ref or "").strip()
    if clean.startswith("test:"):
        return "test"
    return clean.split(":", 1)[0].strip().lower() if ":" in clean else ""


def _ref_value(ref: str) -> str:
    clean = str(ref or "").strip()
    return clean.split(":", 1)[1].strip() if ":" in clean else ""


def _sanitize_ref(ref: str, *, allow_empty: bool = False) -> str:
    clean = str(ref or "").strip()
    if not clean:
        if allow_empty:
            return ""
        raise ValueError("ref_required")
    lower = clean.lower()
    if SECRET_RE.search(clean) or BEARER_RE.search(clean) or "credential:" in lower or "cred_ref" in lower:
        raise ValueError("ref_contains_secret")
    if IPV4_RE.search(clean):
        raise ValueError("ref_contains_ip")
    if ABS_PATH_RE.search(clean):
        raise ValueError("ref_contains_sensitive_path")
    if RAW_PAYLOAD_MARKER_RE.search(clean):
        raise ValueError("ref_contains_raw_payload_marker")
    return clean[:300]


def _validate_test_ref(source_ref: str) -> tuple[bool, str]:
    value = _ref_value(source_ref)
    path_text = value.split("::", 1)[0].strip()
    if not path_text or Path(path_text).is_absolute() or ".." in Path(path_text).parts:
        return False, "test_ref_must_be_relative"
    return True, "test_ref_format"


def _validate_db_ref(conn: sqlite3.Connection, prefix: str, source_ref: str, *, source_type: str) -> tuple[bool, str]:
    table, column = DB_REF_TABLES[prefix]
    if not _table_exists(conn, table):
        return False, f"{table}_table_missing"
    ref_value = _ref_value(source_ref)
    try:
        if prefix in {"protocol_debt", "session_correction_requirement", "outcome", "hook_run", "guard_check", "error_repetition", "somatic_event", "learning"}:
            lookup_value: object = int(ref_value)
        else:
            lookup_value = ref_value
    except Exception:
        return False, f"{prefix}_id_invalid"
    row = conn.execute(f"SELECT * FROM {table} WHERE {column} = ?", (lookup_value,)).fetchone()
    if not row:
        return False, f"{prefix}_not_found"
    data = dict(row)
    if source_type == "outcome_miss" and str(data.get("status") or "").lower() != "missed":
        return False, "outcome_not_missed"
    if prefix == "hook_run" and str(data.get("status") or "").lower() not in {"error", "timeout", "blocked", "failed", "fail"}:
        return False, "hook_run_not_failed"
    if prefix == "protocol_debt" and str(data.get("status") or "").lower() not in {"open", "resolved", "forgiven"}:
        return False, "protocol_debt_status_invalid"
    if prefix == "session_correction_requirement" and str(data.get("status") or "").lower() not in {"open", "resolved"}:
        return False, "correction_requirement_status_invalid"
    return True, "db_ref_exists"


def validate_source_ref(
    source_type: str,
    source_ref: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Validate a source ref enough to decide whether it may reinforce a case."""
    own_conn = conn is None
    conn = conn or get_db()
    clean_type = _normalize_source_type(source_type)
    if not clean_type:
        return {"validated": False, "validator": "source_type", "validation_error": "source_type_invalid"}
    try:
        clean_ref = _sanitize_ref(source_ref)
    except ValueError as exc:
        return {"validated": False, "validator": "ref_sanitize", "validation_error": str(exc)}

    prefix = _ref_prefix(clean_ref)
    allowed = SOURCE_REF_PREFIXES.get(clean_type, set())
    if prefix not in allowed:
        return {
            "validated": False,
            "validator": "ref_prefix",
            "validation_error": f"ref_prefix_{prefix or 'missing'}_not_allowed_for_{clean_type}",
        }

    try:
        if prefix in DB_REF_TABLES:
            ok, reason = _validate_db_ref(conn, prefix, clean_ref, source_type=clean_type)
            return {"validated": ok, "validator": "db_ref", "validation_error": "" if ok else reason}
        if prefix == "test":
            ok, reason = _validate_test_ref(clean_ref)
            return {"validated": ok, "validator": "test_ref", "validation_error": "" if ok else reason}
        if prefix in {"guardian_rule", "guardian_telemetry", "immune_finding", "watchdog_finding", "benchmark_case", "evidence"}:
            return {"validated": True, "validator": "format_ref", "validation_error": ""}
    finally:
        if own_conn:
            pass

    return {"validated": False, "validator": "ref_prefix", "validation_error": f"unsupported_ref_prefix:{prefix}"}


def _status_for_case(*, source_type: str, severity: str, validated: bool, frequency_count: int) -> str:
    if not validated:
        return "candidate"
    if source_type in INFERENCE_ONLY_SOURCES:
        return "candidate"
    if severity in {"p0", "p1"}:
        return "analyzing"
    if severity == "p2" and frequency_count >= 2:
        return "analyzing"
    if severity == "p4":
        return "rejected"
    return "candidate"


def _allowed_surfaces(*, privacy_level: str, status: str) -> list[str]:
    privacy = _normalize_privacy(privacy_level)
    if privacy == "secret":
        return ["audit"]
    if privacy in {"private", "sensitive"}:
        return ["debug_local", "audit"]
    if status in {"verified", "resolved"}:
        return ["debug_local", "audit", "pre_action"]
    return ["debug_local", "audit"]


def _case_from_row(row: sqlite3.Row | None) -> dict[str, Any]:
    if not row:
        return {}
    data = dict(row)
    for key in (
        "entity_refs_json", "source_event_refs_json", "evidence_refs_json",
        "symptom_json", "trigger_json", "missed_signal_json",
        "wrong_assumption_json", "root_cause_json", "corrective_action_json",
        "learning_resolution_json", "antibody_refs_json",
        "allowed_surfaces_json", "metadata_json",
    ):
        default = [] if key.endswith("_refs_json") or key in {"antibody_refs_json", "allowed_surfaces_json"} else {}
        data[key[:-5] if key.endswith("_json") else key] = _load_json(str(data.pop(key) or ""), default)
    return data


def _source_event_from_row(row: sqlite3.Row | None) -> dict[str, Any]:
    if not row:
        return {}
    data = dict(row)
    data["validated"] = bool(data.get("validated"))
    data["evidence_refs"] = _load_json(str(data.pop("evidence_refs_json") or ""), [])
    data["metadata"] = _load_json(str(data.pop("metadata_json") or ""), {})
    return data


def _learning_resolution_for_source(conn: sqlite3.Connection, source_type: str, source_ref: str) -> dict[str, Any]:
    if source_type != "outcome_miss" or _ref_prefix(source_ref) != "outcome":
        return {"action": "none", "learning_id": None, "resolver_reason": ""}
    try:
        outcome_id = int(_ref_value(source_ref))
    except Exception:
        return {"action": "none", "learning_id": None, "resolver_reason": "outcome_ref_invalid"}
    row = conn.execute("SELECT learning_id FROM outcomes WHERE id = ?", (outcome_id,)).fetchone()
    if row and int(row["learning_id"] or 0) > 0:
        return {
            "action": "merge",
            "learning_id": int(row["learning_id"]),
            "resolver_reason": "outcome_already_linked_learning_no_duplicate",
        }
    return {"action": "none", "learning_id": None, "resolver_reason": "outcome_has_no_learning"}


def ingest_failure(
    *,
    failure_type: str,
    area: str,
    primary_source_type: str,
    primary_source_ref: str,
    symptom: object,
    trigger: object = "",
    missed_signal: object = "",
    wrong_assumption: object = "",
    root_cause: object = "",
    corrective_action: object = "",
    severity: str = "p3",
    confidence: float = 0.5,
    entity_refs: list[str] | str | None = None,
    evidence_refs: list[str] | str | None = None,
    privacy_level: str = "normal",
    observed_at: float | None = None,
    idempotency_key: str = "",
    metadata: dict[str, Any] | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Create or reinforce a redacted failure-prevention case."""
    conn = conn or get_db()
    _ensure_tables(conn)
    now = float(observed_at or _now())
    clean_type = _normalize_failure_type(failure_type)
    clean_source_type = _normalize_source_type(primary_source_type)
    try:
        clean_source_ref = _sanitize_ref(primary_source_ref)
        clean_evidence_refs = [_sanitize_ref(ref) for ref in _as_list(evidence_refs)]
        clean_entity_refs = [_sanitize_ref(ref) for ref in _as_list(entity_refs)]
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    clean_severity = _normalize_severity(severity)
    clean_privacy = _normalize_privacy(privacy_level)
    clean_confidence = _normalize_confidence(confidence)
    clean_area = str(area or "").strip()[:160]
    clean_metadata = sanitize_metadata(metadata or {})
    if not isinstance(clean_metadata, dict):
        clean_metadata = {"value": clean_metadata}

    validation = validate_source_ref(clean_source_type, clean_source_ref, conn=conn)
    validated = bool(validation.get("validated"))
    symptom_field = field_evidence(symptom, source_refs=[clean_source_ref], confidence=clean_confidence, privacy_level=clean_privacy)
    trigger_field = field_evidence(trigger, source_refs=[clean_source_ref], confidence=clean_confidence, privacy_level=clean_privacy)
    missed_signal_field = field_evidence(missed_signal, source_refs=[clean_source_ref], confidence=clean_confidence, privacy_level=clean_privacy)
    wrong_assumption_field = field_evidence(wrong_assumption, source_refs=[clean_source_ref], confidence=clean_confidence, privacy_level=clean_privacy)
    root_cause_field = field_evidence(root_cause, source_refs=[clean_source_ref], confidence=clean_confidence, privacy_level=clean_privacy)
    corrective_action_field = field_evidence(corrective_action, source_refs=[clean_source_ref], confidence=clean_confidence, privacy_level=clean_privacy)
    failure_uid = _stable_uid(
        POLICY_VERSION,
        clean_type,
        clean_area,
        _normalize_text(symptom_field["value_redacted"]),
    )
    source_event_uid = _stable_uid(POLICY_VERSION, failure_uid, clean_source_type, clean_source_ref)
    learning_resolution = _learning_resolution_for_source(conn, clean_source_type, clean_source_ref)
    source_ref_token = f"{clean_source_type}:{clean_source_ref}"

    conn.execute(
        """
        INSERT OR IGNORE INTO failure_prevention_cases (
            failure_uid, policy_version, failure_type, area, entity_refs_json,
            primary_source_type, primary_source_ref, source_event_refs_json,
            evidence_refs_json, symptom_json, trigger_json, missed_signal_json,
            wrong_assumption_json, root_cause_json, corrective_action_json,
            severity, frequency_count, confidence, status, learning_resolution_json,
            antibody_refs_json, privacy_level, allowed_surfaces_json, opened_at,
            updated_at, review_due_at, expires_at, false_positive_count,
            last_triggered_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'candidate', ?, '[]', ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            failure_uid,
            POLICY_VERSION,
            clean_type,
            clean_area,
            _json(clean_entity_refs),
            clean_source_type,
            clean_source_ref,
            _json([source_ref_token]),
            _json(clean_evidence_refs),
            _json(symptom_field),
            _json(trigger_field),
            _json(missed_signal_field),
            _json(wrong_assumption_field),
            _json(root_cause_field),
            _json(corrective_action_field),
            clean_severity,
            clean_confidence,
            _json(learning_resolution),
            clean_privacy,
            _json(_allowed_surfaces(privacy_level=clean_privacy, status="candidate")),
            now,
            now,
            now + 14 * 86400,
            now + 90 * 86400,
            now,
            _json(clean_metadata),
        ),
    )

    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO failure_source_events (
            source_event_uid, failure_uid, policy_version, source_type,
            source_ref, evidence_refs_json, observed_at, validated, validator,
            validation_error, privacy_level, created_at, updated_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_event_uid,
            failure_uid,
            POLICY_VERSION,
            clean_source_type,
            clean_source_ref,
            _json(clean_evidence_refs),
            now,
            1 if validated else 0,
            str(validation.get("validator") or ""),
            str(validation.get("validation_error") or ""),
            clean_privacy,
            now,
            now,
            _json({"idempotency_key_hash": _idempotency_marker(idempotency_key), **clean_metadata}),
        ),
    )
    source_inserted = cursor.rowcount > 0
    if source_inserted and validated:
        conn.execute(
            """
            UPDATE failure_prevention_cases
               SET frequency_count = frequency_count + 1,
                   last_triggered_at = ?,
                   updated_at = ?,
                   source_event_refs_json = ?,
                   evidence_refs_json = ?,
                   learning_resolution_json = ?
             WHERE failure_uid = ?
            """,
            (
                now,
                now,
                _append_unique(
                    conn.execute(
                        "SELECT source_event_refs_json FROM failure_prevention_cases WHERE failure_uid = ?",
                        (failure_uid,),
                    ).fetchone()["source_event_refs_json"],
                    [source_ref_token],
                ),
                _append_unique(
                    conn.execute(
                        "SELECT evidence_refs_json FROM failure_prevention_cases WHERE failure_uid = ?",
                        (failure_uid,),
                    ).fetchone()["evidence_refs_json"],
                    clean_evidence_refs,
                ),
                _json(learning_resolution),
                failure_uid,
            ),
        )
    elif source_inserted:
        clean_metadata["validation_error"] = str(validation.get("validation_error") or "")
        conn.execute(
            "UPDATE failure_prevention_cases SET metadata_json = ?, updated_at = ? WHERE failure_uid = ?",
            (_json(clean_metadata), now, failure_uid),
        )

    row = conn.execute("SELECT * FROM failure_prevention_cases WHERE failure_uid = ?", (failure_uid,)).fetchone()
    frequency_count = int(row["frequency_count"] or 0) if row else 0
    next_status = _status_for_case(
        source_type=clean_source_type,
        severity=clean_severity,
        validated=validated,
        frequency_count=frequency_count,
    )
    if row and str(row["status"] or "") not in {"verified", "resolved", "rolled_back"}:
        conn.execute(
            """
            UPDATE failure_prevention_cases
               SET status = ?, allowed_surfaces_json = ?, updated_at = ?
             WHERE failure_uid = ?
            """,
            (
                next_status,
                _json(_allowed_surfaces(privacy_level=clean_privacy, status=next_status)),
                now,
                failure_uid,
            ),
        )
    conn.commit()
    case_row = conn.execute("SELECT * FROM failure_prevention_cases WHERE failure_uid = ?", (failure_uid,)).fetchone()
    event_row = conn.execute("SELECT * FROM failure_source_events WHERE source_event_uid = ?", (source_event_uid,)).fetchone()
    return {
        "ok": True,
        "failure_uid": failure_uid,
        "source_event_uid": source_event_uid,
        "source_event_inserted": source_inserted,
        "validated": validated,
        "validation_error": str(validation.get("validation_error") or ""),
        "case": _safe_case(_case_from_row(case_row), surface="audit"),
        "source_event": _safe_source_event(_source_event_from_row(event_row)),
    }


def _case_exists(conn: sqlite3.Connection, failure_uid: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM failure_prevention_cases WHERE failure_uid = ?", (failure_uid,)).fetchone())


def _validate_action_ref(ref: str, *, field: str, allow_empty: bool = True) -> str:
    if not ref and allow_empty:
        return ""
    clean = _sanitize_ref(ref, allow_empty=allow_empty)
    return clean


def _validate_antibody_policy(
    *,
    action_type: str,
    target_system: str,
    target_ref: str,
    activation_policy: str,
    verification_ref: str,
    verification_status: str,
    approved_ref: str,
    rollback_ref: str,
    source_type: str,
) -> None:
    if action_type not in ACTION_TYPES:
        raise ValueError("action_type_invalid")
    if target_system not in TARGET_SYSTEMS:
        raise ValueError("target_system_invalid")
    if not target_ref.strip():
        raise ValueError("target_ref_required")
    if activation_policy not in ACTIVATION_POLICIES:
        raise ValueError("activation_policy_invalid")
    if verification_status not in VERIFICATION_STATUSES:
        raise ValueError("verification_status_invalid")
    if source_type in INFERENCE_ONLY_SOURCES and activation_policy != "candidate_only":
        raise ValueError("inference_source_must_remain_candidate_only")
    if verification_status == "not_applicable":
        if action_type not in NOT_APPLICABLE_ACTIONS or activation_policy in {"warn", "block_after_verification"}:
            raise ValueError("not_applicable_verification_not_allowed")
    if activation_policy == "warn" and not verification_ref:
        raise ValueError("warn_requires_verification_ref")
    if activation_policy in {"warn", "block_after_verification"} and verification_ref and not _ref_prefix(verification_ref):
        raise ValueError("verification_ref_must_be_traceable_ref")
    if activation_policy == "block_after_verification":
        if verification_status != "passed" or not verification_ref or not rollback_ref:
            raise ValueError("block_requires_passed_verification_and_rollback")
        if not _ref_prefix(rollback_ref):
            raise ValueError("rollback_ref_must_be_traceable_ref")
    if activation_policy == "manual_approval_required":
        prefix = _ref_prefix(approved_ref)
        if prefix not in APPROVAL_REF_PREFIXES:
            raise ValueError("manual_approval_requires_traceable_approved_ref")


def propose_antibody_action(
    *,
    failure_uid: str,
    action_type: str,
    target_system: str,
    target_ref: str,
    action_payload_ref: str = "",
    activation_policy: str = "candidate_only",
    required_verification: str = "",
    verification_ref: str = "",
    verification_status: str = "missing",
    approved_by: str = "",
    approved_ref: str = "",
    rollback_ref: str = "",
    privacy_level: str = "normal",
    metadata: dict[str, Any] | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Record a proposed owner action without executing it."""
    conn = conn or get_db()
    _ensure_tables(conn)
    clean_failure_uid = str(failure_uid or "").strip()
    if not _case_exists(conn, clean_failure_uid):
        return {"ok": False, "error": "failure_case_not_found"}
    case = conn.execute(
        "SELECT primary_source_type FROM failure_prevention_cases WHERE failure_uid = ?",
        (clean_failure_uid,),
    ).fetchone()
    clean_action = str(action_type or "").strip()
    clean_target_system = str(target_system or "").strip()
    try:
        clean_target_ref = _sanitize_ref(target_ref)
        clean_payload_ref = _validate_action_ref(action_payload_ref, field="action_payload_ref")
        clean_verification_ref = _validate_action_ref(verification_ref, field="verification_ref")
        clean_approved_ref = _validate_action_ref(approved_ref, field="approved_ref")
        clean_rollback_ref = _validate_action_ref(rollback_ref, field="rollback_ref")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    clean_policy = str(activation_policy or "candidate_only").strip()
    clean_verification_status = str(verification_status or "missing").strip()
    clean_privacy = _normalize_privacy(privacy_level)
    source_type = str(case["primary_source_type"] or "") if case else ""

    try:
        _validate_antibody_policy(
            action_type=clean_action,
            target_system=clean_target_system,
            target_ref=clean_target_ref,
            activation_policy=clean_policy,
            verification_ref=clean_verification_ref,
            verification_status=clean_verification_status,
            approved_ref=clean_approved_ref,
            rollback_ref=clean_rollback_ref,
            source_type=source_type,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    clean_metadata = sanitize_metadata(metadata or {})
    if not isinstance(clean_metadata, dict):
        clean_metadata = {"value": clean_metadata}
    learning_resolution = None
    if clean_action == "learning_resolve" and isinstance(clean_metadata.get("learning_candidate"), dict):
        candidate = clean_metadata["learning_candidate"]
        learning_resolution = resolve_learning_candidate(
            category=str(candidate.get("category") or "nexo-ops"),
            title=str(candidate.get("title") or ""),
            content=str(candidate.get("content") or ""),
            reasoning=str(candidate.get("reasoning") or ""),
            prevention=str(candidate.get("prevention") or ""),
            applies_to=str(candidate.get("applies_to") or ""),
            priority=str(candidate.get("priority") or "medium"),
            source_authority=str(candidate.get("source_authority") or "inference"),
            conn=conn,
        )
        clean_metadata["learning_resolution"] = learning_resolution
        conn.execute(
            "UPDATE failure_prevention_cases SET learning_resolution_json = ?, updated_at = ? WHERE failure_uid = ?",
            (_json(learning_resolution), _now(), clean_failure_uid),
        )

    now = _now()
    antibody_uid = _stable_uid(POLICY_VERSION, clean_failure_uid, clean_action, clean_target_system, clean_target_ref)
    status = "approved" if clean_policy == "manual_approval_required" else "proposed"
    if clean_verification_status == "passed":
        status = "verified"
    conn.execute(
        """
        INSERT OR IGNORE INTO antibody_actions (
            antibody_uid, failure_uid, policy_version, action_type, target_system,
            target_ref, action_payload_ref, status, activation_policy,
            required_verification, verification_ref, verification_status,
            approved_by, approved_ref, rollback_ref, review_due_at, expires_at,
            privacy_level, created_at, updated_at, verified_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            antibody_uid,
            clean_failure_uid,
            POLICY_VERSION,
            clean_action,
            clean_target_system,
            clean_target_ref,
            clean_payload_ref,
            status,
            clean_policy,
            redact_value(str(required_verification or "").strip()),
            clean_verification_ref,
            clean_verification_status,
            redact_value(str(approved_by or "").strip())[:160],
            clean_approved_ref,
            clean_rollback_ref,
            now + 14 * 86400,
            now + 90 * 86400,
            clean_privacy,
            now,
            now,
            now if clean_verification_status == "passed" else None,
            _json(clean_metadata),
        ),
    )
    conn.execute(
        "UPDATE failure_prevention_cases SET antibody_refs_json = ?, updated_at = ? WHERE failure_uid = ?",
        (
            _append_unique(
                conn.execute(
                    "SELECT antibody_refs_json FROM failure_prevention_cases WHERE failure_uid = ?",
                    (clean_failure_uid,),
                ).fetchone()["antibody_refs_json"],
                [f"antibody:{antibody_uid}"],
            ),
            now,
            clean_failure_uid,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM antibody_actions WHERE antibody_uid = ?", (antibody_uid,)).fetchone()
    data = dict(row) if row else {}
    if data:
        data["metadata"] = _load_json(str(data.pop("metadata_json") or ""), {})
    return {"ok": True, "antibody_uid": antibody_uid, "antibody": _safe_antibody(data), "learning_resolution": sanitize_metadata(learning_resolution)}


def list_failure_cases(*, status: str = "", limit: int = 20, surface: str = "audit", conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    conn = conn or get_db()
    _ensure_tables(conn)
    clauses = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM failure_prevention_cases {where} ORDER BY updated_at DESC LIMIT ?",
        params + [max(1, int(limit or 20))],
    ).fetchall()
    cases = [_safe_case(_case_from_row(row), surface=surface) for row in rows]
    return [case for case in cases if case]


def get_failure_case(failure_uid: str, *, surface: str = "audit", conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    conn = conn or get_db()
    _ensure_tables(conn)
    row = conn.execute("SELECT * FROM failure_prevention_cases WHERE failure_uid = ?", (failure_uid,)).fetchone()
    return _safe_case(_case_from_row(row), surface=surface)


def mark_false_positive(
    failure_uid: str,
    *,
    antibody_uid: str = "",
    reason: str = "",
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    conn = conn or get_db()
    _ensure_tables(conn)
    clean_failure_uid = str(failure_uid or "").strip()
    if not _case_exists(conn, clean_failure_uid):
        return {"ok": False, "error": "failure_case_not_found"}
    now = _now()
    conn.execute(
        """
        UPDATE failure_prevention_cases
           SET false_positive_count = false_positive_count + 1,
               updated_at = ?
         WHERE failure_uid = ?
        """,
        (now, clean_failure_uid),
    )
    row = conn.execute("SELECT false_positive_count FROM failure_prevention_cases WHERE failure_uid = ?", (clean_failure_uid,)).fetchone()
    count = int(row["false_positive_count"] or 0)
    if count >= 2:
        conn.execute(
            "UPDATE failure_prevention_cases SET status = 'conflict_review', allowed_surfaces_json = ?, updated_at = ? WHERE failure_uid = ?",
            (_json(["debug_local", "audit"]), now, clean_failure_uid),
        )
    if antibody_uid:
        conn.execute(
            """
            UPDATE antibody_actions
               SET status = 'false_positive',
                   activation_policy = 'candidate_only',
                   metadata_json = ?,
                   updated_at = ?
             WHERE antibody_uid = ?
            """,
            (_json({"false_positive_reason": redact_value(reason)}), now, antibody_uid),
        )
    conn.commit()
    return {"ok": True, "false_positive_count": count, "case": get_failure_case(clean_failure_uid, conn=conn)}


def rollback_antibody_action(
    antibody_uid: str,
    *,
    rollback_ref: str,
    reason: str = "",
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    conn = conn or get_db()
    _ensure_tables(conn)
    clean_uid = str(antibody_uid or "").strip()
    try:
        clean_rollback_ref = _validate_action_ref(rollback_ref, field="rollback_ref", allow_empty=False)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    row = conn.execute("SELECT failure_uid FROM antibody_actions WHERE antibody_uid = ?", (clean_uid,)).fetchone()
    if not row:
        return {"ok": False, "error": "antibody_not_found"}
    now = _now()
    conn.execute(
        """
        UPDATE antibody_actions
           SET status = 'rolled_back',
               activation_policy = 'candidate_only',
               rollback_ref = ?,
               metadata_json = ?,
               updated_at = ?
         WHERE antibody_uid = ?
        """,
        (clean_rollback_ref, _json({"rollback_reason": redact_value(reason)}), now, clean_uid),
    )
    conn.execute(
        "UPDATE failure_prevention_cases SET status = 'rolled_back', updated_at = ? WHERE failure_uid = ?",
        (now, row["failure_uid"]),
    )
    conn.commit()
    return {"ok": True, "antibody_uid": clean_uid, "failure_uid": row["failure_uid"]}


__all__ = [
    "POLICY_VERSION",
    "field_evidence",
    "get_failure_case",
    "ingest_failure",
    "list_failure_cases",
    "mark_false_positive",
    "propose_antibody_action",
    "redact_value",
    "rollback_antibody_action",
    "sanitize_metadata",
    "validate_source_ref",
]
