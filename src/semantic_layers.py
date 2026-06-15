"""SemanticLayers cache for compact, source-backed continuity.

This module creates derived, redacted views over existing NEXO sources. It is
not an owner of truth: diary, workflows, tasks, evidence, memory and transcript
index remain canonical.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from typing import Any

import db


POLICY_VERSION = "semantic_layers_v1"
GENERATOR_VERSION = "continuity_layer_builder_v1"

SCOPE_TYPES = {
    "session", "conversation", "workflow", "workflow_goal", "protocol_task",
    "release", "project_entity",
}
LAYER_KINDS = {
    "headline", "brief", "timeline", "decisions", "commitments", "files",
    "evidence", "risks", "next_action", "semantic_tags", "source_map",
}
STATUSES = {"fresh", "stale", "expired", "invalid"}
QUALITY_STATES = {"complete", "partial", "degraded", "conflicted", "source_missing", "invalid"}
PRIVACY_LEVELS = {"public", "normal", "private", "sensitive", "secret"}
PRIVACY_RANK = {"public": 0, "normal": 1, "private": 2, "sensitive": 3, "secret": 4}
SURFACES = {
    "task_open", "pre_answer", "pre_action", "context_packet",
    "portable_context", "audit", "debug_local", "runtime_internal",
    "release_public", "export",
}
SURFACE_ALIASES = {"debug": "debug_local", "public": "release_public"}
PRIVACY_ALIASES = {"internal": "normal", "confidential": "sensitive"}

CANONICAL_SOURCE_PREFIXES = {
    "guard", "workflow_run", "workflow_step", "workflow_checkpoint",
    "workflow_goal", "protocol_task", "commitment", "followup", "reminder",
    "release", "commit", "risk", "spec", "audit", "finding", "change_log",
    "evidence", "memory_event", "memory_observation", "cognitive_stm",
    "cognitive_ltm", "hot_context", "recent_event", "session_diary",
    "session_diary_draft", "diary_archive", "historical_diary",
    "transcript_index", "continuity_snapshot", "entity", "entity_profile",
    "artifact_registry", "artifact_alias", "project_atlas", "local_asset",
    "managed_asset", "causal_node", "causal_edge", "causal_edge_candidate",
    "doc", "test", "local_context", "protocol_debt", "immune_finding",
    "watchdog_finding", "guardian_telemetry", "outcome", "learning",
    "correction", "trust_score", "sentiment_log", "adaptive_log",
    "somatic_event", "somatic_marker", "memory_correction",
    "cortex_evaluation", "predictive_context", "preference",
    "error_repetition", "guard_check", "session_correction_requirement",
    "hook_run", "guardian_rule", "benchmark_case",
}
SOURCE_ALIASES = {
    "atlas": "project_atlas",
    "workflow": "workflow_run",
    "task": "protocol_task",
    "kg_edge": "causal_edge",
    "kg_node": "causal_node",
    "cognitive:stm": "cognitive_stm",
    "cognitive:ltm": "cognitive_ltm",
    "diary": "session_diary",
    "watchdog": "watchdog_finding",
}

SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|authorization|bearer|credential|cred_ref)\b"
    r"\s*[:=]\s*['\"]?[^'\"\s,;]+"
)
BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}")
TOKEN_RE = re.compile(r"\b(?:sk|ghp|gho|ghu|ghs|github_pat|glpat|xoxb|xoxp|shpat)[-_][a-z0-9_]{16,}\b", re.I)
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ABS_PATH_RE = re.compile(r"(?<![\w:])/(?:Users|home|var|etc|Volumes|srv|opt|tmp)/[^\s,;]+")
RAW_PAYLOAD_RE = re.compile(r"(?i)\b(provider_payload|raw_prompt|raw_response|transcript)\b")


def _conn() -> sqlite3.Connection:
    return db.get_db()


def _now() -> float:
    try:
        return float(db.now_epoch())
    except Exception:
        return time.time()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value))
        return parsed if parsed is not None else default
    except Exception:
        return default


def _hash(value: Any, *, length: int = 64) -> str:
    digest = hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()
    return digest[:length] if length else digest


def _stable_uid(*parts: Any) -> str:
    return "SL-" + _hash([str(part or "") for part in parts], length=32)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone() is not None
    except Exception:
        return False


def _ensure_schema(conn: sqlite3.Connection, *, migrate: bool = True) -> bool:
    if _table_exists(conn, "semantic_layers") and _table_exists(conn, "semantic_layer_source_refs"):
        return True
    if not migrate:
        return False
    from db._schema import run_migrations

    run_migrations(conn)
    return _table_exists(conn, "semantic_layers") and _table_exists(conn, "semantic_layer_source_refs")


def redact_value(value: Any, *, max_chars: int = 4000) -> str:
    text = str(value or "")
    if RAW_PAYLOAD_RE.search(text):
        return "[redacted_payload]"
    for pattern, replacement in (
        (BEARER_RE, "[redacted_secret]"),
        (TOKEN_RE, "[redacted_secret]"),
        (SECRET_RE, r"\1=[redacted_secret]"),
        (IPV4_RE, "[redacted_ip]"),
        (ABS_PATH_RE, "[redacted_path]"),
    ):
        text = pattern.sub(replacement, text)
    if max_chars and len(text) > max_chars:
        return text[: max(0, max_chars - 1)].rstrip() + "..."
    return text


def _sensitive_ref(ref: str) -> bool:
    return bool(BEARER_RE.search(ref) or TOKEN_RE.search(ref) or SECRET_RE.search(ref) or IPV4_RE.search(ref) or ABS_PATH_RE.search(ref) or RAW_PAYLOAD_RE.search(ref))


def _normalize_privacy(value: str) -> str:
    clean = str(value or "normal").strip().lower()
    clean = PRIVACY_ALIASES.get(clean, clean)
    return clean if clean in PRIVACY_LEVELS else "normal"


def _max_privacy(values: list[str] | tuple[str, ...]) -> str:
    best = "public"
    for value in values:
        clean = _normalize_privacy(value)
        if PRIVACY_RANK[clean] > PRIVACY_RANK[best]:
            best = clean
    return best


def _normalize_surface(value: str) -> str:
    clean = str(value or "").strip().lower()
    clean = SURFACE_ALIASES.get(clean, clean)
    return clean if clean in SURFACES else ""


def _normalize_surfaces(values: Any, *, privacy_level: str, allow_public: bool = False) -> list[str]:
    if isinstance(values, str):
        parsed = _load_json(values, None)
        values = parsed if isinstance(parsed, list) else [item.strip() for item in values.split(",")]
    if not isinstance(values, (list, tuple, set)) or not values:
        privacy = _normalize_privacy(privacy_level)
        if privacy == "secret":
            values = ["audit", "runtime_internal"]
        elif privacy in {"private", "sensitive"}:
            values = ["audit", "debug_local", "runtime_internal"]
        else:
            values = ["pre_answer", "pre_action", "context_packet", "portable_context", "audit", "debug_local", "runtime_internal"]

    normalized: list[str] = []
    privacy = _normalize_privacy(privacy_level)
    for value in values:
        surface = _normalize_surface(str(value or ""))
        if not surface:
            continue
        if privacy == "secret" and surface not in {"audit", "runtime_internal"}:
            continue
        if privacy in {"private", "sensitive"} and surface in {"pre_answer", "context_packet", "portable_context", "export", "release_public"}:
            continue
        if surface in {"export", "release_public"} and not allow_public:
            continue
        if surface in {"export", "release_public"} and privacy != "public":
            continue
        if surface not in normalized:
            normalized.append(surface)
    return normalized


def _surface_allowed(surface: str, allowed_surfaces: Any, *, privacy_level: str) -> bool:
    clean_surface = _normalize_surface(surface)
    if not clean_surface:
        return False
    privacy = _normalize_privacy(privacy_level)
    if privacy == "secret" and clean_surface not in {"audit", "runtime_internal"}:
        return False
    if privacy in {"private", "sensitive"} and clean_surface in {"pre_answer", "context_packet", "portable_context", "export", "release_public"}:
        return False
    allowed = _normalize_surfaces(allowed_surfaces, privacy_level=privacy)
    return clean_surface in allowed


def _split_ref(source_ref: str) -> tuple[str, str]:
    raw = str(source_ref or "").strip()
    if raw.startswith("cognitive:stm:"):
        return "cognitive_stm", raw.split(":", 2)[2]
    if raw.startswith("cognitive:ltm:"):
        return "cognitive_ltm", raw.split(":", 2)[2]
    if raw.startswith("watchdog:"):
        return "watchdog_finding", raw.split(":", 1)[1]
    if ":" not in raw:
        return "", raw
    prefix, value = raw.split(":", 1)
    return SOURCE_ALIASES.get(prefix, prefix), value


def normalize_source_ref(source_ref: str) -> str:
    raw = str(source_ref or "").strip()
    if not raw:
        return ""
    prefix, value = _split_ref(raw)
    if not prefix or not value:
        return raw
    return f"{prefix}:{value}"


def validate_source_ref_namespace(source_ref: str) -> dict[str, Any]:
    clean_ref = normalize_source_ref(source_ref)
    prefix, value = _split_ref(clean_ref)
    if not clean_ref or not prefix or not value:
        return {"ok": False, "source_ref": clean_ref, "source_kind": prefix, "validation_status": "invalid", "validation_error": "invalid_source_ref"}
    if _sensitive_ref(clean_ref):
        return {"ok": False, "source_ref": clean_ref, "source_kind": prefix, "validation_status": "invalid", "validation_error": "sensitive_source_ref"}
    if prefix not in CANONICAL_SOURCE_PREFIXES:
        return {"ok": False, "source_ref": clean_ref, "source_kind": prefix, "validation_status": "unsupported", "validation_error": "unsupported_source_ref"}
    return {"ok": True, "source_ref": clean_ref, "source_kind": prefix, "validation_status": "ok", "validation_error": ""}


def _row_by(conn: sqlite3.Connection, table: str, column: str, value: Any) -> sqlite3.Row | None:
    if not _table_exists(conn, table):
        return None
    return conn.execute(f"SELECT * FROM {table} WHERE {column}=? LIMIT 1", (value,)).fetchone()


def _evidence_row(conn: sqlite3.Connection, ref_value: str) -> sqlite3.Row | None:
    if not _table_exists(conn, "memory_events"):
        return None
    value = str(ref_value or "").strip()
    if value.startswith("evidence_record:"):
        value = value.split(":", 1)[1]
    return conn.execute(
        """
        SELECT * FROM memory_events
         WHERE (event_uid=? OR source_id=? OR raw_ref=?)
           AND (source_type='evidence_ledger' OR event_type LIKE 'evidence_%')
         ORDER BY created_at DESC
         LIMIT 1
        """,
        (value, value, value),
    ).fetchone()


def _row_version(row: sqlite3.Row, fields: list[str], *, extra: Any = None) -> str:
    data = dict(row)
    payload = {field: data.get(field) for field in fields}
    if extra is not None:
        payload["extra"] = extra
    return _hash(payload)


def _privacy_for_source(conn: sqlite3.Connection, source_kind: str, ref_value: str) -> str:
    specs = {
        "memory_event": ("memory_events", "event_uid", "privacy_level"),
        "entity_profile": ("entity_profile_cache", "profile_uid", "privacy_level"),
        "managed_asset": ("nexo_managed_assets", "asset_uid", "privacy_level"),
        "local_asset": ("local_assets", "asset_id", "privacy_class"),
        "causal_edge_candidate": ("causal_edge_candidates", "candidate_uid", "privacy_level"),
    }
    spec = specs.get(source_kind)
    if not spec:
        if source_kind == "evidence":
            row = _evidence_row(conn, ref_value)
            return _normalize_privacy(row["privacy_level"] if row else "normal")
        return "normal"
    table, column, privacy_column = spec
    lookup_value = ref_value.split("#", 1)[0] if source_kind == "local_asset" else ref_value
    row = _row_by(conn, table, column, lookup_value)
    if not row:
        return "normal"
    data = dict(row)
    return _normalize_privacy(data.get(privacy_column) or "normal")


def _version_from_db(conn: sqlite3.Connection, source_kind: str, ref_value: str) -> dict[str, Any] | None:
    if source_kind == "evidence":
        row = _evidence_row(conn, ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["event_uid", "input_hash", "output_digest", "metadata_json"]), "updated_at": str(row["created_at"] or "")}
    if source_kind == "workflow_run":
        row = _row_by(conn, "workflow_runs", "run_id", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["updated_at", "status", "next_action", "current_step_key"]), "updated_at": str(row["updated_at"] or "")}
    if source_kind == "workflow_step":
        parts = ref_value.split(":", 1)
        if len(parts) != 2 or not _table_exists(conn, "workflow_steps"):
            return None
        row = conn.execute("SELECT * FROM workflow_steps WHERE run_id=? AND step_key=? LIMIT 1", (parts[0], parts[1])).fetchone()
        if not row:
            return None
        return {"version": _row_version(row, ["updated_at", "status", "last_summary", "last_evidence"]), "updated_at": str(row["updated_at"] or "")}
    if source_kind == "workflow_checkpoint":
        row = _row_by(conn, "workflow_checkpoints", "id", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["id", "created_at", "step_key", "summary", "evidence"]), "updated_at": str(row["created_at"] or "")}
    if source_kind == "protocol_task":
        row = _row_by(conn, "protocol_tasks", "task_id", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["opened_at", "closed_at", "status", "goal", "files", "files_changed", "close_evidence", "outcome_notes"]), "updated_at": str(row["closed_at"] or row["opened_at"] or "")}
    if source_kind == "workflow_goal":
        row = _row_by(conn, "workflow_goals", "goal_id", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["updated_at", "status", "next_action"]), "updated_at": str(row["updated_at"] or "")}
    if source_kind == "followup":
        row = _row_by(conn, "followups", "id", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["status", "updated_at", "date", "verification"]), "updated_at": str(row["updated_at"] or "")}
    if source_kind == "reminder":
        row = _row_by(conn, "reminders", "id", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["status", "updated_at", "date", "description", "category"]), "updated_at": str(row["updated_at"] or "")}
    if source_kind == "commitment":
        row = _row_by(conn, "commitments", "id", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["status", "updated_at", "deadline", "evidence_ref", "action_ref_type", "action_ref_id", "outcome_id", "metadata_json"]), "updated_at": str(row["updated_at"] or "")}
    if source_kind == "session_diary":
        row = _row_by(conn, "session_diary", "id", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["id", "created_at", "quality_tier", "quality_score", "summary"]), "updated_at": str(row["created_at"] or "")}
    if source_kind == "session_diary_draft":
        row = _row_by(conn, "session_diary_draft", "sid", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["sid", "updated_at", "summary_draft", "heartbeat_count"]), "updated_at": str(row["updated_at"] or "")}
    if source_kind == "change_log":
        row = _row_by(conn, "change_log", "id", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["id", "created_at", "files", "what_changed", "why"]), "updated_at": str(row["created_at"] or "")}
    if source_kind == "memory_event":
        row = _row_by(conn, "memory_events", "event_uid", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["event_uid", "input_hash", "output_digest", "metadata_json"]), "updated_at": str(row["created_at"] or "")}
    if source_kind == "memory_observation":
        row = _row_by(conn, "memory_observations", "observation_uid", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["observation_uid", "updated_at", "source_hash", "status"]), "updated_at": str(row["updated_at"] or "")}
    if source_kind == "hot_context":
        row = _row_by(conn, "hot_context", "context_key", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["context_key", "last_event_at", "state", "source_id"]), "updated_at": str(row["last_event_at"] or "")}
    if source_kind == "recent_event":
        row = _row_by(conn, "recent_events", "id", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["id", "created_at", "event_type", "source_id"]), "updated_at": str(row["created_at"] or "")}
    if source_kind == "transcript_index":
        row = _row_by(conn, "transcript_index", "id", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["content_hash", "modified_at", "message_count"]), "updated_at": str(row["modified_at"] or row["indexed_at"] or "")}
    if source_kind == "continuity_snapshot":
        row = _row_by(conn, "continuity_snapshots", "id", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["id", "updated_at", "idempotency_key", "trace_id"]), "updated_at": str(row["updated_at"] or "")}
    if source_kind == "entity":
        row = _row_by(conn, "entities", "id", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["id", "updated_at", "name", "type", "aliases", "metadata"], extra=_hash(str(row["value"] or ""))), "updated_at": str(row["updated_at"] or "")}
    if source_kind == "entity_profile":
        row = _row_by(conn, "entity_profile_cache", "profile_uid", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["profile_uid", "source_refs_hash", "input_hash", "stale_status", "last_verified_at", "expires_at"]), "updated_at": str(row["updated_at"] or "")}
    if source_kind == "artifact_registry":
        row = _row_by(conn, "artifact_registry", "id", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["id", "state", "last_touched_at", "last_verified_at", "metadata", "uri", "paths"]), "updated_at": str(row["last_touched_at"] or row["created_at"] or "")}
    if source_kind == "artifact_alias":
        row = _row_by(conn, "artifact_aliases", "id", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["id", "artifact_id", "phrase", "source", "confidence", "created_at"]), "updated_at": str(row["created_at"] or "")}
    if source_kind == "managed_asset":
        row = _row_by(conn, "nexo_managed_assets", "asset_uid", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["asset_uid", "source_refs_json", "updated_at", "status"]), "updated_at": str(row["updated_at"] or "")}
    if source_kind == "causal_edge_candidate":
        row = _row_by(conn, "causal_edge_candidates", "candidate_uid", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["candidate_uid", "updated_at", "privacy_level", "status", "confidence"]), "updated_at": str(row["updated_at"] or "")}
    # ── kinds added for the resolution cache's anti-stale fingerprint ──────
    # These are mutated by tools that do NOT write change_log (learning_add /
    # update, set_preference, the local indexer), so without a real per-row
    # version they would fall through to a CONSTANT validator digest and a
    # superseded learning / changed preference / re-indexed file would be
    # invisible to the cache (stale-serve). Versioning them from their DB row
    # makes the fingerprint react to the actual content change.
    if source_kind == "learning":
        row = _row_by(conn, "learnings", "id", ref_value)
        if not row:
            return None
        data = dict(row)
        fields = [f for f in ("id", "updated_at", "status", "title", "content", "reasoning", "category") if f in data]
        return {"version": _row_version(row, fields), "updated_at": str(data.get("updated_at") or "")}
    if source_kind == "preference":
        row = _row_by(conn, "preferences", "key", ref_value)
        if not row:
            return None
        return {"version": _row_version(row, ["key", "value", "category", "updated_at"]), "updated_at": str(row["updated_at"] or "")}
    if source_kind == "local_asset":
        # ``local_asset:<asset_id>[#<version>]`` — the asset row carries the
        # current fingerprint/mtime; that is what changes on a re-index.
        asset_id = ref_value.split("#", 1)[0]
        row = _row_by(conn, "local_assets", "asset_id", asset_id)
        if not row:
            return None
        return {"version": _row_version(row, ["asset_id", "updated_at", "quick_fingerprint", "modified_at_fs", "size_bytes", "status"]), "updated_at": str(row["updated_at"] or "")}
    return None


def source_version_for(source_ref: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    conn = conn or _conn()
    validation = validate_source_ref_namespace(source_ref)
    if not validation.get("ok"):
        return {
            **validation,
            "source_version": "",
            "source_updated_at": "",
            "privacy_level": "normal",
        }
    clean_ref = validation["source_ref"]
    source_kind = validation["source_kind"]
    _, ref_value = _split_ref(clean_ref)
    version = _version_from_db(conn, source_kind, ref_value)
    if version is None:
        db_backed = source_kind in {
            "workflow_run", "workflow_step", "workflow_checkpoint", "protocol_task",
            "workflow_goal", "followup", "reminder", "commitment", "session_diary",
            "session_diary_draft", "change_log", "evidence", "memory_event",
            "memory_observation", "hot_context", "recent_event", "transcript_index",
            "continuity_snapshot", "entity", "entity_profile", "artifact_registry",
            "artifact_alias", "managed_asset", "causal_edge_candidate",
            # Added for the resolution-cache fingerprint: a missing row here is a
            # real "source gone" signal (distinct marker), not a stable digest.
            "learning", "preference", "local_asset",
        }
        if db_backed:
            return {
                **validation,
                "ok": False,
                "validation_status": "missing",
                "validation_error": "source_missing",
                "source_version": "",
                "source_updated_at": "",
                "privacy_level": "normal",
            }
        digest = _hash({"source_ref": clean_ref, "validator": "format_ref"})
        return {
            **validation,
            "source_version": f"validator_digest:{digest}",
            "source_updated_at": "",
            "privacy_level": "normal",
        }
    return {
        **validation,
        "source_version": str(version["version"]),
        "source_updated_at": str(version.get("updated_at") or ""),
        "privacy_level": _privacy_for_source(conn, source_kind, ref_value),
    }


def _source_fingerprint(source_versions: list[dict[str, Any]]) -> str:
    items = sorted(
        f"{item.get('source_ref','')}@{item.get('source_version','')}"
        for item in source_versions
    )
    return _hash({"policy_version": POLICY_VERSION, "sources": items})


def _content_hash(value_redacted: str, source_refs: list[str], evidence_refs: list[str]) -> str:
    return _hash({"value_redacted": value_redacted, "source_refs": sorted(source_refs), "evidence_refs": sorted(evidence_refs)})


def _parse_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        loaded = _load_json(value, None)
        if isinstance(loaded, list):
            value = loaded
        else:
            value = [item.strip() for item in value.split(",")]
    if not isinstance(value, (list, tuple, set)):
        return []
    result = []
    for item in value:
        clean = str(item or "").strip()
        if clean and clean not in result:
            result.append(clean)
    return result


def _normalize_source_refs(source_refs: Any) -> list[str]:
    refs = []
    for ref in _parse_list(source_refs):
        clean = normalize_source_ref(ref)
        if clean and clean not in refs:
            refs.append(clean)
    return refs


def _validate_ref_list(refs: list[str], *, field: str) -> tuple[list[str], list[dict[str, Any]]]:
    clean_refs: list[str] = []
    errors: list[dict[str, Any]] = []
    for ref in refs:
        validation = validate_source_ref_namespace(ref)
        if not validation.get("ok"):
            errors.append({
                "field": field,
                "source_ref": validation.get("source_ref", ref),
                "source_kind": validation.get("source_kind", ""),
                "validation_status": validation.get("validation_status", "invalid"),
                "validation_error": validation.get("validation_error", "invalid_source_ref"),
            })
            continue
        clean_ref = validation["source_ref"]
        if clean_ref not in clean_refs:
            clean_refs.append(clean_ref)
    return clean_refs, errors


def _normalize_scope(scope_type: str, scope_id: str) -> tuple[str, str]:
    clean_scope = str(scope_type or "").strip().lower()
    clean_id = str(scope_id or "").strip()
    if clean_scope not in SCOPE_TYPES:
        raise ValueError(f"invalid_scope_type:{clean_scope or 'missing'}")
    if not clean_id:
        raise ValueError("scope_id_required")
    return clean_scope, clean_id


def _normalize_layer_kind(layer_kind: str) -> str:
    clean = str(layer_kind or "").strip().lower()
    if clean not in LAYER_KINDS:
        raise ValueError(f"invalid_layer_kind:{clean or 'missing'}")
    return clean


def _quality_for_sources(source_versions: list[dict[str, Any]], requested: str) -> str:
    if requested in QUALITY_STATES and requested not in {"complete"}:
        return requested
    if any(item.get("validation_status") == "missing" for item in source_versions):
        return "source_missing"
    if any(not item.get("ok") for item in source_versions):
        return "invalid"
    return "complete"


def _default_source_refs_for_scope(conn: sqlite3.Connection, scope_type: str, scope_id: str) -> list[str]:
    refs: list[str] = []
    if scope_type == "workflow":
        if _row_by(conn, "workflow_runs", "run_id", scope_id):
            refs.append(f"workflow_run:{scope_id}")
        for row in conn.execute(
            "SELECT id FROM workflow_checkpoints WHERE run_id=? ORDER BY id DESC LIMIT 5",
            (scope_id,),
        ).fetchall():
            refs.append(f"workflow_checkpoint:{row['id']}")
    elif scope_type == "protocol_task":
        if _row_by(conn, "protocol_tasks", "task_id", scope_id):
            refs.append(f"protocol_task:{scope_id}")
    elif scope_type == "workflow_goal":
        if _row_by(conn, "workflow_goals", "goal_id", scope_id):
            refs.append(f"workflow_goal:{scope_id}")
    elif scope_type == "session":
        for row in conn.execute(
            "SELECT id FROM session_diary WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (scope_id,),
        ).fetchall():
            refs.append(f"session_diary:{row['id']}")
        if _row_by(conn, "session_diary_draft", "sid", scope_id):
            refs.append(f"session_diary_draft:{scope_id}")
        for row in conn.execute(
            "SELECT task_id FROM protocol_tasks WHERE session_id=? ORDER BY opened_at DESC LIMIT 5",
            (scope_id,),
        ).fetchall():
            refs.append(f"protocol_task:{row['task_id']}")
        for row in conn.execute(
            "SELECT run_id FROM workflow_runs WHERE session_id=? ORDER BY updated_at DESC LIMIT 5",
            (scope_id,),
        ).fetchall():
            refs.append(f"workflow_run:{row['run_id']}")
    elif scope_type == "conversation":
        for row in conn.execute(
            "SELECT id FROM continuity_snapshots WHERE conversation_id=? ORDER BY id DESC LIMIT 5",
            (scope_id,),
        ).fetchall():
            refs.append(f"continuity_snapshot:{row['id']}")
    elif scope_type == "project_entity":
        row = conn.execute(
            "SELECT profile_uid FROM entity_profile_cache WHERE entity_key=? ORDER BY updated_at DESC LIMIT 1",
            (scope_id,),
        ).fetchone()
        if row:
            refs.append(f"entity_profile:{row['profile_uid']}")
    elif scope_type == "release":
        refs.append(f"release:{scope_id}")
    return refs


def _latest_checkpoint(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM workflow_checkpoints WHERE run_id=? ORDER BY id DESC LIMIT 1",
        (run_id,),
    ).fetchone()


def _default_values_for_scope(conn: sqlite3.Connection, scope_type: str, scope_id: str, layers: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    if scope_type == "workflow":
        run = _row_by(conn, "workflow_runs", "run_id", scope_id)
        checkpoint = _latest_checkpoint(conn, scope_id)
        if run:
            values["headline"] = f"{run['status']}: {run['goal']}"
            values["next_action"] = str(run["next_action"] or (checkpoint["next_action"] if checkpoint else "") or "")
            brief_parts = [str(run["goal"] or "")]
            if checkpoint and checkpoint["summary"]:
                brief_parts.append(str(checkpoint["summary"]))
            values["brief"] = "\n".join(part for part in brief_parts if part)
            risks = []
            if run["status"] in {"blocked", "waiting_approval"}:
                risks.append(f"workflow status: {run['status']}")
            if checkpoint and checkpoint["requires_approval"]:
                risks.append("requires approval")
            if checkpoint and checkpoint["compensation_note"]:
                risks.append(str(checkpoint["compensation_note"]))
            values["risks"] = "\n".join(risks)
    elif scope_type == "protocol_task":
        task = _row_by(conn, "protocol_tasks", "task_id", scope_id)
        if task:
            values["headline"] = f"{task['status']}: {task['goal']}"
            values["next_action"] = str(task["verification_step"] or task["outcome_notes"] or "")
            values["brief"] = str(task["close_evidence"] or task["goal"] or "")
            unknowns = _load_json(task["unknowns"], [])
            risks = _load_json(task["constraints"], [])
            values["risks"] = "\n".join(str(item) for item in (unknowns + risks)[:8])
    elif scope_type == "session":
        session = _row_by(conn, "sessions", "sid", scope_id)
        diary = conn.execute(
            "SELECT * FROM session_diary WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (scope_id,),
        ).fetchone()
        draft = _row_by(conn, "session_diary_draft", "sid", scope_id)
        if session:
            values["headline"] = str(session["task"] or "active session")
        if diary:
            values["brief"] = str(diary["summary"] or "")
            values["next_action"] = str(diary["context_next"] or diary["pending"] or "")
            if str(diary["quality_tier"] or "") in {"fallback_minimal", "auto_close_minimal"}:
                values["risks"] = "diary quality is minimal; verify original sources before sensitive action"
        elif draft:
            values["brief"] = str(draft["summary_draft"] or "")
            values["next_action"] = str(draft["last_context_hint"] or "")
            values["risks"] = "only diary draft available"
    elif scope_type == "conversation":
        snap = conn.execute(
            "SELECT * FROM continuity_snapshots WHERE conversation_id=? ORDER BY id DESC LIMIT 1",
            (scope_id,),
        ).fetchone()
        if snap:
            payload = _load_json(snap["payload_json"], {})
            values["headline"] = redact_value(payload.get("headline") or payload.get("summary") or snap["event_type"])
            values["brief"] = redact_value(payload.get("summary") or payload.get("context") or "")
            values["next_action"] = redact_value(payload.get("next_action") or "")
    if "source_map" in layers:
        values["source_map"] = "Sources: " + ", ".join(_default_source_refs_for_scope(conn, scope_type, scope_id)[:12])
    if "semantic_tags" in layers:
        refs = _default_source_refs_for_scope(conn, scope_type, scope_id)
        tags = sorted({ref.split(":", 1)[0] for ref in refs if ":" in ref})
        values["semantic_tags"] = _json({"source_kinds": tags})
    return {kind: value for kind, value in values.items() if kind in layers and str(value or "").strip()}


def _row_to_layer(row: sqlite3.Row | None) -> dict[str, Any]:
    if not row:
        return {}
    data = dict(row)
    for key, default in (
        ("source_refs_json", []),
        ("evidence_refs_json", []),
        ("allowed_surfaces_json", []),
        ("metadata_json", {}),
    ):
        data[key[:-5] if key.endswith("_json") else key] = _load_json(data.get(key), default)
    return data


def build_semantic_layers(
    scope_type: str,
    scope_id: str,
    layers: list[str] | tuple[str, ...] | None = None,
    *,
    producer: str = "manual",
    budget_tier: str = "standard",
    source_refs: list[str] | tuple[str, ...] | None = None,
    evidence_refs: list[str] | tuple[str, ...] | None = None,
    values: dict[str, Any] | None = None,
    privacy_level: str = "normal",
    allowed_surfaces: list[str] | tuple[str, ...] | None = None,
    quality_state: str = "complete",
    confidence: float = 0.8,
    coverage: float = 1.0,
    metadata: dict[str, Any] | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Build or refresh deterministic semantic layers for a concrete scope."""
    conn = conn or _conn()
    _ensure_schema(conn)
    try:
        clean_scope, clean_id = _normalize_scope(scope_type, scope_id)
        clean_layers = [_normalize_layer_kind(item) for item in (layers or ["headline", "brief", "next_action", "risks", "source_map"])]
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "layers": []}

    refs = _normalize_source_refs(source_refs) if source_refs is not None else _default_source_refs_for_scope(conn, clean_scope, clean_id)
    refs, source_errors = _validate_ref_list(refs, field="source_refs")
    if source_errors:
        return {"ok": False, "error": "invalid_source_refs", "errors": source_errors, "layers": []}
    if not refs:
        return {"ok": False, "error": "scope_sources_missing", "layers": []}

    source_versions = [source_version_for(ref, conn=conn) for ref in refs]
    fingerprint = _source_fingerprint(source_versions)
    evidence, evidence_errors = _validate_ref_list(_normalize_source_refs(evidence_refs or []), field="evidence_refs")
    if evidence_errors:
        return {"ok": False, "error": "invalid_evidence_refs", "errors": evidence_errors, "layers": []}
    evidence_versions = [source_version_for(ref, conn=conn) for ref in evidence]
    evidence_version_errors = [
        {
            "field": "evidence_refs",
            "source_ref": item.get("source_ref", ""),
            "source_kind": item.get("source_kind", ""),
            "validation_status": item.get("validation_status", "invalid"),
            "validation_error": item.get("validation_error", "invalid_source_ref"),
        }
        for item in evidence_versions
        if not item.get("ok")
    ]
    if evidence_version_errors:
        return {"ok": False, "error": "invalid_evidence_refs", "errors": evidence_version_errors, "layers": []}
    privacy = _max_privacy([privacy_level] + [item.get("privacy_level", "normal") for item in source_versions + evidence_versions])
    allow_public = bool(metadata or {}) and bool((metadata or {}).get("allow_public_surfaces"))
    surfaces = _normalize_surfaces(allowed_surfaces, privacy_level=privacy, allow_public=allow_public)
    requested_quality = quality_state if quality_state in QUALITY_STATES else "complete"
    resolved_quality = _quality_for_sources(source_versions, requested_quality)
    status = "fresh" if resolved_quality not in {"invalid", "source_missing"} else "invalid"
    now = _now()
    source_max_updated_at = max([str(item.get("source_updated_at") or "") for item in source_versions] or [""])
    value_map = {str(k): v for k, v in (values or {}).items()}
    default_values = _default_values_for_scope(conn, clean_scope, clean_id, clean_layers)
    value_map = {**default_values, **value_map}

    built: list[dict[str, Any]] = []
    for layer_kind in clean_layers:
        raw_value = value_map.get(layer_kind)
        if raw_value in (None, "") and layer_kind != "source_map":
            continue
        if layer_kind == "source_map" and raw_value in (None, ""):
            raw_value = "Sources: " + ", ".join(refs)
        value_redacted = redact_value(raw_value)
        layer_uid = _stable_uid(clean_scope, clean_id, layer_kind, fingerprint, POLICY_VERSION)
        content_hash = _content_hash(value_redacted, refs, evidence)
        conn.execute(
            """
            UPDATE semantic_layers
               SET status='stale', stale_at=?, stale_reason='source_fingerprint_replaced', updated_at=?
             WHERE scope_type=? AND scope_id=? AND layer_kind=? AND policy_version=?
               AND status='fresh' AND source_fingerprint<>?
            """,
            (now, now, clean_scope, clean_id, layer_kind, POLICY_VERSION, fingerprint),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO semantic_layers (
                layer_uid, scope_type, scope_id, layer_kind, policy_version,
                status, quality_state, value_redacted, value_ref, token_size,
                source_refs_json, evidence_refs_json, source_fingerprint,
                content_hash, privacy_level, allowed_surfaces_json, confidence,
                coverage, generated_by, generator_version, generated_at,
                updated_at, source_max_updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                layer_uid, clean_scope, clean_id, layer_kind, POLICY_VERSION,
                status, resolved_quality, value_redacted, len(value_redacted.split()),
                _json(refs), _json(evidence), fingerprint, content_hash, privacy,
                _json(surfaces), max(0.0, min(1.0, float(confidence))),
                max(0.0, min(1.0, float(coverage))), str(producer or "manual"),
                GENERATOR_VERSION, now, now, source_max_updated_at, _json(metadata or {}),
            ),
        )
        conn.execute(
            """
            UPDATE semantic_layers
               SET status=?, quality_state=?, value_redacted=?, token_size=?,
                   source_refs_json=?, evidence_refs_json=?, content_hash=?,
                   privacy_level=?, allowed_surfaces_json=?, confidence=?,
                   coverage=?, generated_by=?, generator_version=?,
                   updated_at=?, source_max_updated_at=?, stale_at=0,
                   stale_reason='', metadata_json=?
             WHERE layer_uid=?
            """,
            (
                status, resolved_quality, value_redacted, len(value_redacted.split()),
                _json(refs), _json(evidence), content_hash, privacy, _json(surfaces),
                max(0.0, min(1.0, float(confidence))),
                max(0.0, min(1.0, float(coverage))), str(producer or "manual"),
                GENERATOR_VERSION, now, source_max_updated_at, _json(metadata or {}),
                layer_uid,
            ),
        )
        tracked_versions: list[tuple[dict[str, Any], int]] = [(item, 1) for item in source_versions]
        tracked_versions.extend((item, 0) for item in evidence_versions)
        for item, required_for_layer in tracked_versions:
            conn.execute(
                """
                INSERT OR IGNORE INTO semantic_layer_source_refs (
                    layer_uid, source_ref, source_kind, source_version,
                    source_updated_at, privacy_level, required_for_layer,
                    validation_status, validation_error, created_at, updated_at,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')
                """,
                (
                    layer_uid,
                    item.get("source_ref", ""),
                    item.get("source_kind", ""),
                    item.get("source_version", ""),
                    item.get("source_updated_at", ""),
                    _normalize_privacy(item.get("privacy_level", "normal")),
                    required_for_layer,
                    item.get("validation_status", "ok"),
                    item.get("validation_error", ""),
                    now,
                    now,
                ),
            )
        row = conn.execute("SELECT * FROM semantic_layers WHERE layer_uid=?", (layer_uid,)).fetchone()
        built.append(_row_to_layer(row))
    conn.commit()
    return {"ok": True, "scope_type": clean_scope, "scope_id": clean_id, "source_fingerprint": fingerprint, "layers": built}


def validate_semantic_layer_sources(
    layer_uid: str,
    *,
    conn: sqlite3.Connection | None = None,
    mark_stale: bool = True,
) -> dict[str, Any]:
    conn = conn or _conn()
    if not _ensure_schema(conn, migrate=False):
        return {"ok": False, "error": "schema_missing", "layer_uid": str(layer_uid or "").strip()}
    clean_uid = str(layer_uid or "").strip()
    row = conn.execute("SELECT * FROM semantic_layers WHERE layer_uid=?", (clean_uid,)).fetchone()
    if not row:
        return {"ok": False, "error": "layer_not_found", "layer_uid": clean_uid}
    refs = conn.execute(
        "SELECT * FROM semantic_layer_source_refs WHERE layer_uid=? ORDER BY source_ref",
        (clean_uid,),
    ).fetchall()
    checked: list[dict[str, Any]] = []
    changed = False
    missing = False
    invalid = False
    for source in refs:
        current = source_version_for(source["source_ref"], conn=conn)
        status = "ok"
        error = ""
        if not current.get("ok"):
            status = current.get("validation_status") or "invalid"
            error = current.get("validation_error") or "source_invalid"
            missing = status == "missing"
            invalid = invalid or status != "missing"
        elif str(current.get("source_version") or "") != str(source["source_version"] or ""):
            status = "changed"
            error = "source_version_changed"
            changed = True
        elif _normalize_privacy(current.get("privacy_level", "normal")) != _normalize_privacy(source["privacy_level"]):
            status = "changed"
            error = "source_privacy_changed"
            changed = True
        checked.append({**current, "expected_version": source["source_version"], "validation_status": status, "validation_error": error})
    now = _now()
    if mark_stale and (changed or missing or invalid):
        new_quality = "source_missing" if missing else ("invalid" if invalid else row["quality_state"])
        conn.execute(
            """
            UPDATE semantic_layers
               SET status='stale', quality_state=?, stale_at=?, stale_reason=?, updated_at=?
             WHERE layer_uid=?
            """,
            (new_quality, now, "source_validation_changed" if changed else "source_validation_failed", now, clean_uid),
        )
        conn.commit()
    return {
        "ok": not (changed or missing or invalid),
        "layer_uid": clean_uid,
        "changed": changed,
        "missing": missing,
        "invalid": invalid,
        "sources": checked,
    }


def get_semantic_layer(
    scope_type: str,
    scope_id: str,
    layer_kind: str,
    surface: str,
    *,
    budget_tier: str = "quick",
    allow_stale: bool = False,
    mark_stale_on_read: bool = True,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    conn = conn or _conn()
    if not _ensure_schema(conn, migrate=False):
        return {"ok": False, "error": "schema_missing"}
    try:
        clean_scope, clean_id = _normalize_scope(scope_type, scope_id)
        clean_kind = _normalize_layer_kind(layer_kind)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    row = conn.execute(
        """
        SELECT * FROM semantic_layers
         WHERE scope_type=? AND scope_id=? AND layer_kind=? AND policy_version=?
         ORDER BY CASE status WHEN 'fresh' THEN 0 ELSE 1 END, updated_at DESC
         LIMIT 1
        """,
        (clean_scope, clean_id, clean_kind, POLICY_VERSION),
    ).fetchone()
    if not row:
        return {"ok": False, "error": "layer_missing"}
    layer = _row_to_layer(row)
    if not _surface_allowed(surface, layer.get("allowed_surfaces"), privacy_level=layer.get("privacy_level", "normal")):
        return {"ok": False, "error": "surface_not_allowed", "layer_uid": layer.get("layer_uid", "")}
    if layer.get("status") != "fresh" and not allow_stale:
        return {"ok": False, "error": "layer_stale", "layer_uid": layer.get("layer_uid", ""), "decision_signal": "defer" if budget_tier == "critical" else ""}
    if layer.get("quality_state") in {"conflicted", "source_missing", "invalid"}:
        return {"ok": False, "error": f"layer_{layer.get('quality_state')}", "layer_uid": layer.get("layer_uid", ""), "decision_signal": "defer" if budget_tier == "critical" else ""}
    validation = validate_semantic_layer_sources(str(layer["layer_uid"]), conn=conn, mark_stale=mark_stale_on_read)
    if not validation.get("ok") and not allow_stale:
        return {"ok": False, "error": "layer_stale", "layer_uid": layer.get("layer_uid", ""), "validation": validation, "decision_signal": "defer" if budget_tier == "critical" else ""}
    return {"ok": True, "layer": layer, "validation": validation}


def _layers_for_intent(intent_kind: str, budget_tier: str) -> list[str]:
    tier = str(budget_tier or "quick").strip().lower()
    intent = str(intent_kind or "").strip().lower()
    if tier == "instant":
        return ["headline", "next_action"]
    if intent == "schedule_commitment":
        base = ["commitments", "next_action", "risks"]
    elif intent in {"prior_work", "identity_authorship"}:
        base = ["headline", "brief", "next_action", "risks", "evidence", "files"]
    else:
        base = ["brief", "next_action", "commitments", "risks"]
    if tier in {"standard", "deep", "critical"}:
        base.extend(["timeline", "decisions", "files", "evidence", "source_map"])
    return [kind for kind in dict.fromkeys(base) if kind in LAYER_KINDS]


def select_semantic_layers(
    query: str = "",
    intent_bundle: dict[str, Any] | None = None,
    budget_policy: dict[str, Any] | None = None,
    surface: str = "pre_answer",
    scope_hint: dict[str, Any] | None = None,
    requested_layers: list[str] | tuple[str, ...] | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    conn = conn or _conn()
    if not _ensure_schema(conn, migrate=False):
        return {"ok": True, "layers": [], "rendered": "", "reason": "schema_missing"}
    scope_hint = dict(scope_hint or {})
    budget_policy = dict(budget_policy or {})
    intent_bundle = dict(intent_bundle or {})
    scope_type = str(scope_hint.get("scope_type") or "").strip()
    scope_id = str(scope_hint.get("scope_id") or "").strip()
    if not scope_type or not scope_id:
        return {"ok": True, "layers": [], "rendered": "", "reason": "scope_hint_required"}
    budget_tier = str(budget_policy.get("budget_tier") or intent_bundle.get("budget_tier") or "quick").strip().lower()
    intent_kind = str(intent_bundle.get("intent_kind") or budget_policy.get("intent") or "").strip().lower()
    layer_names = [_normalize_layer_kind(item) for item in requested_layers] if requested_layers else _layers_for_intent(intent_kind, budget_tier)
    selected: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for kind in layer_names:
        result = get_semantic_layer(
            scope_type,
            scope_id,
            kind,
            surface,
            budget_tier=budget_tier,
            mark_stale_on_read=False,
            conn=conn,
        )
        if result.get("ok"):
            selected.append(result["layer"])
        elif result.get("error") not in {"layer_missing", "surface_not_allowed"}:
            errors.append({"layer_kind": kind, "error": result.get("error"), "decision_signal": result.get("decision_signal", "")})
    rendered = format_semantic_layers(selected, max_chars=int(budget_policy.get("max_rendered_chars") or 1400))
    return {
        "ok": True,
        "query_hash": _hash(str(query or "")),
        "scope_type": scope_type,
        "scope_id": scope_id,
        "surface": _normalize_surface(surface),
        "budget_tier": budget_tier,
        "layers": selected,
        "errors": errors,
        "rendered": rendered,
    }


def mark_semantic_layers_stale(source_ref: str, reason: str = "source_changed", *, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    conn = conn or _conn()
    _ensure_schema(conn)
    clean_ref = normalize_source_ref(source_ref)
    now = _now()
    rows = conn.execute(
        """
        SELECT DISTINCT layer_uid FROM semantic_layer_source_refs
         WHERE source_ref=?
        """,
        (clean_ref,),
    ).fetchall()
    layer_uids = [row["layer_uid"] for row in rows]
    if layer_uids:
        conn.executemany(
            "UPDATE semantic_layers SET status='stale', stale_at=?, stale_reason=?, updated_at=? WHERE layer_uid=? AND status='fresh'",
            [(now, str(reason or "source_changed"), now, uid) for uid in layer_uids],
        )
        conn.commit()
    return {"ok": True, "source_ref": clean_ref, "stale_count": len(layer_uids), "layer_uids": layer_uids}


def list_semantic_layers(
    scope_type: str = "",
    scope_id: str = "",
    status: str = "fresh",
    limit: int = 20,
    surface: str = "pre_answer",
    *,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    conn = conn or _conn()
    if not _ensure_schema(conn, migrate=False):
        return []
    clauses: list[str] = []
    params: list[Any] = []
    if scope_type:
        clauses.append("scope_type=?")
        params.append(scope_type)
    if scope_id:
        clauses.append("scope_id=?")
        params.append(scope_id)
    if status:
        clauses.append("status=?")
        params.append(status)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM semantic_layers {where} ORDER BY updated_at DESC LIMIT ?",
        (*params, max(1, int(limit or 20))),
    ).fetchall()
    layers = [_row_to_layer(row) for row in rows]
    clean_surface = _normalize_surface(surface)
    if not clean_surface:
        return []
    visible: list[dict[str, Any]] = []
    for layer in layers:
        if not _surface_allowed(clean_surface, layer.get("allowed_surfaces"), privacy_level=layer.get("privacy_level", "normal")):
            continue
        validation = validate_semantic_layer_sources(str(layer.get("layer_uid") or ""), conn=conn, mark_stale=False)
        if not validation.get("ok"):
            continue
        layer["validation"] = validation
        visible.append(layer)
    return visible


def format_semantic_layers(layers: list[dict[str, Any]], *, max_chars: int = 1400) -> str:
    if not layers:
        return ""
    lines = ["Semantic layers:"]
    for layer in layers:
        value = redact_value(layer.get("value_redacted", ""), max_chars=600).strip()
        if not value:
            continue
        refs = layer.get("source_refs") or []
        ref_note = f" refs={len(refs)}" if refs else ""
        lines.append(f"- {layer.get('layer_kind')}: {value}{ref_note}")
    rendered = "\n".join(lines)
    if max_chars and len(rendered) > max_chars:
        return rendered[: max(0, max_chars - 1)].rstrip() + "..."
    return rendered


__all__ = [
    "POLICY_VERSION",
    "build_semantic_layers",
    "format_semantic_layers",
    "get_semantic_layer",
    "list_semantic_layers",
    "mark_semantic_layers_stale",
    "normalize_source_ref",
    "redact_value",
    "select_semantic_layers",
    "source_version_for",
    "validate_semantic_layer_sources",
    "validate_source_ref_namespace",
]
