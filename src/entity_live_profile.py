from __future__ import annotations

"""EntityLiveProfile facade.

This module composes existing authoritative stores into a redacted, cacheable
profile. It must never become the owner of identity, artifacts, paths, facts,
relations, commitments, or evidence.
"""

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import db

try:
    from local_context.extractors import canonical_entity_key, normalize_entity_alias
except Exception:  # pragma: no cover - local_context may be unavailable in tiny runtimes
    def normalize_entity_alias(value: str) -> str:
        return " ".join(str(value or "").lower().split())

    def canonical_entity_key(value: str) -> str:
        clean = normalize_entity_alias(value)
        return f"alias:{clean}" if clean else ""


PROFILE_VERSION = "entity_live_profile.v1"
DEFAULT_SURFACES = ("pre_answer", "pre_action", "debug_local", "audit")
BLOCKED_PUBLIC_SURFACES = {"export", "release_public"}
HEAVY_SOURCES = {"local_context", "entity_dossier", "memory", "transcripts", "cognitive", "remote_llm"}
LIGHT_BUDGET_TIERS = {"instant", "quick"}
PRIVACY_RANK = {"public": 0, "normal": 1, "private": 2, "sensitive": 3, "secret": 4}

_SECRET_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-~+/]{12,}\b", re.I),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat|glpat|xoxb|xoxp|shpat)_[A-Za-z0-9_]{16,}\b", re.I),
    re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16,}\b"),
    re.compile(r"\b(?:password|passwd|pwd|token|secret|api[_-]?key)\s*[:=]\s*['\"]?[^'\"\s,;]{8,}", re.I),
)
_PATH_PATTERN = re.compile(
    r"(?<![\w])(?:~|/Users|/home|/var|/srv|/www|/etc|/opt|/tmp|/Volumes)"
    r"(?:/[^\s,;:'\")\]}]+)+"
)
_GENERIC_ABS_PATH_PATTERN = re.compile(r"(?<![\w:])/[A-Za-z0-9._@+-]+(?:/[A-Za-z0-9._@+-]+)+")
_IP_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_DOCROOT_PATTERN = re.compile(r"\b(?:docroot|document_root|root_path|vhost)\s*[:=]\s*[^\s,;]+", re.I)


def _conn():
    return db.get_db()


def _now() -> float:
    try:
        return float(db.now_epoch())
    except Exception:
        return time.time()


def _table_exists(conn, table: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone() is not None
    except Exception:
        return False


def _ensure_table(conn, table: str) -> None:
    if _table_exists(conn, table):
        return
    from db._schema import run_migrations

    run_migrations(conn)


def _json(value: Any, default: Any) -> str:
    if value in (None, ""):
        value = default
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return json.dumps(default, ensure_ascii=False, sort_keys=True)


def _parse_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value))
        return parsed if parsed is not None else default
    except Exception:
        return default


def _hash(value: Any, *, length: int = 24) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()[:length]


def _safe_event_uid(idempotency_key: str, fallback_parts: Any) -> str:
    clean = str(idempotency_key or "").strip()
    if clean:
        redacted = redact_entity_value(clean)
        if redacted == clean and re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", clean):
            return clean
    return f"ACU-{_hash(fallback_parts, length=32)}"


def _unique(values: list[Any] | tuple[Any, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        clean = str(raw or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def redact_entity_value(value: Any) -> str:
    """Return a compact value safe for normal profile surfaces."""
    if isinstance(value, (dict, list, tuple)):
        text = _json(value, {})
    else:
        text = str(value or "")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED:secret]", text)
    text = _DOCROOT_PATTERN.sub("[REDACTED:docroot]", text)
    text = _PATH_PATTERN.sub("[REDACTED:path]", text)
    text = _GENERIC_ABS_PATH_PATTERN.sub("[REDACTED:path]", text)
    text = _IP_PATTERN.sub("[REDACTED:ip]", text)
    return text[:1200]


def sanitize_refs(refs: Any) -> list[str]:
    if isinstance(refs, str):
        raw_items = [refs]
    elif isinstance(refs, (list, tuple, set)):
        raw_items = list(refs)
    else:
        raw_items = []
    return _unique([redact_entity_value(item) for item in raw_items])[:50]


def normalize_privacy_level(value: str | None) -> str:
    clean = str(value or "normal").strip().lower()
    return clean if clean in PRIVACY_RANK else "normal"


def _max_privacy(*levels: str) -> str:
    best = "public"
    for level in levels:
        clean = normalize_privacy_level(level)
        if PRIVACY_RANK[clean] > PRIVACY_RANK[best]:
            best = clean
    return best


def _surfaces_for_privacy(privacy_level: str, *, allow_public_release: bool = False) -> list[str]:
    privacy = normalize_privacy_level(privacy_level)
    if privacy == "secret":
        return ["audit"]
    if privacy in {"private", "sensitive"}:
        return ["pre_action", "debug_local", "audit"]
    surfaces = list(DEFAULT_SURFACES)
    if allow_public_release:
        surfaces.extend(["export", "release_public"])
    return surfaces


def _surface_allowed(surface: str, allowed: list[str] | tuple[str, ...]) -> bool:
    clean = str(surface or "").strip()
    if clean in BLOCKED_PUBLIC_SURFACES:
        return clean in allowed
    return clean in allowed


def _field(
    *,
    name: str,
    value: Any,
    owner_source: str,
    source_refs: list[str] | tuple[str, ...],
    privacy_level: str = "normal",
    write_policy: str = "owner_only",
    last_verified_at: float | None = None,
    expires_at: float | None = None,
    conflict_state: str = "none",
    allowed_surfaces: list[str] | None = None,
) -> dict[str, Any]:
    privacy = normalize_privacy_level(privacy_level)
    return {
        "name": name,
        "value_redacted": redact_entity_value(value),
        "owner_source": owner_source,
        "source_refs": sanitize_refs(source_refs),
        "privacy_level": privacy,
        "allowed_surfaces": allowed_surfaces or _surfaces_for_privacy(privacy),
        "write_policy": write_policy,
        "last_verified_at": last_verified_at,
        "expires_at": expires_at,
        "stale_status": _stale_status(last_verified_at, expires_at),
        "conflict_state": conflict_state,
    }


def _stale_status(last_verified_at: float | None, expires_at: float | None) -> str:
    now = _now()
    if expires_at is not None and float(expires_at or 0) <= now:
        return "expired"
    if not last_verified_at:
        return "unknown"
    return "fresh"


def _ttl_seconds(kind: str, surface: str) -> int:
    clean_kind = str(kind or "").lower()
    clean_surface = str(surface or "").lower()
    pre_action = clean_surface == "pre_action"
    if clean_kind in {"person", "client", "contact", "host"}:
        return 24 * 3600 if pre_action else 7 * 24 * 3600
    if clean_kind in {"project", "repo", "artifact", "managed_asset", "service", "dashboard"}:
        return 4 * 3600 if pre_action else 24 * 3600
    if clean_kind in {"server", "domain", "release", "campaign"}:
        return 3600 if pre_action else 6 * 3600
    return 24 * 3600


def _entity_aliases(row: dict[str, Any]) -> list[str]:
    aliases: list[str] = [row.get("name") or ""]
    aliases.extend(_parse_json(row.get("aliases"), []))
    metadata = _parse_json(row.get("metadata"), {})
    value_json = _parse_json(row.get("value"), {})
    for source in (metadata, value_json):
        if isinstance(source, dict):
            aliases.extend(source.get("aliases") or [])
            alias = source.get("alias")
            aliases.extend(alias if isinstance(alias, list) else [alias or ""])
    return _unique(aliases)


def _row_access_mode(row: dict[str, Any]) -> str:
    metadata = _parse_json(row.get("metadata"), {})
    value_json = _parse_json(row.get("value"), {})
    for candidate in (
        row.get("access_mode"),
        metadata.get("access_mode") if isinstance(metadata, dict) else "",
        value_json.get("access_mode") if isinstance(value_json, dict) else "",
    ):
        clean = str(candidate or "").strip().lower()
        if clean:
            return clean
    return "unknown"


def _row_privacy(row: dict[str, Any]) -> str:
    metadata = _parse_json(row.get("metadata"), {})
    if isinstance(metadata, dict):
        return normalize_privacy_level(metadata.get("privacy_level"))
    return "normal"


def _entity_candidate_score(query: str, row: dict[str, Any]) -> float:
    clean_query = normalize_entity_alias(query)
    if not clean_query:
        return 0.0
    if str(row.get("id") or "") == clean_query or f"entity:{row.get('id')}" == clean_query:
        return 1.0
    aliases = [normalize_entity_alias(item) for item in _entity_aliases(row)]
    if clean_query in aliases:
        return 0.99
    if any(clean_query and clean_query in alias for alias in aliases):
        return 0.88
    terms = set(clean_query.split())
    if not terms:
        return 0.0
    best = 0.0
    for alias in aliases:
        alias_terms = set(alias.split())
        overlap = terms & alias_terms
        if overlap:
            best = max(best, 0.35 + len(overlap) / max(len(alias_terms), 1) * 0.45)
    return min(0.82, best)


def resolve_entity(query: str, *, conn=None, limit: int = 8) -> dict[str, Any]:
    """Resolve a query against the canonical entities table."""
    connection = conn or _conn()
    clean_query = str(query or "").strip()
    if not clean_query or not _table_exists(connection, "entities"):
        return {"ok": True, "status": "not_found", "query": clean_query, "candidates": [], "needs_disambiguation": False}
    rows = [dict(row) for row in connection.execute("SELECT * FROM entities").fetchall()]
    scored = []
    for row in rows:
        score = _entity_candidate_score(clean_query, row)
        if score <= 0:
            continue
        scored.append((score, row))
    scored.sort(key=lambda item: (item[0], float(item[1].get("confidence") or 0.0)), reverse=True)
    candidates = [
        {
            "entity_key": f"entity:{row.get('id')}",
            "entity_id": int(row.get("id") or 0),
            "display_name": row.get("name") or "",
            "canonical_kind": row.get("type") or "entity",
            "score": round(float(score), 4),
            "aliases": _entity_aliases(row)[:12],
            "source_ref": f"entity:{row.get('id')}",
        }
        for score, row in scored[: max(1, int(limit))]
    ]
    if not candidates:
        return {"ok": True, "status": "not_found", "query": clean_query, "candidates": [], "needs_disambiguation": False}
    needs_disambiguation = (
        len(candidates) > 1
        and candidates[0]["score"] < 1.0
        and candidates[1]["score"] >= candidates[0]["score"] - 0.08
    )
    if needs_disambiguation:
        return {
            "ok": True,
            "status": "ambiguous",
            "query": clean_query,
            "candidates": candidates,
            "needs_disambiguation": True,
        }
    best = scored[0][1]
    return {
        "ok": True,
        "status": "resolved",
        "query": clean_query,
        "entity_key": f"entity:{best.get('id')}",
        "entity": best,
        "confidence": candidates[0]["score"],
        "candidates": candidates,
        "needs_disambiguation": False,
    }


def _project_atlas_path() -> Path:
    return Path("~/.nexo/brain/project-atlas.json").expanduser()


def _atlas_projects(atlas: dict[str, Any]) -> dict[str, Any]:
    if isinstance(atlas.get("projects"), dict):
        return atlas["projects"]
    return {k: v for k, v in atlas.items() if isinstance(v, dict) and not str(k).startswith("_")}


def _load_atlas(atlas: dict[str, Any] | None = None, atlas_path: str | Path | None = None) -> dict[str, Any]:
    if isinstance(atlas, dict):
        return atlas
    path = Path(atlas_path).expanduser() if atlas_path else _project_atlas_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _match_atlas_projects(atlas: dict[str, Any], aliases: list[str]) -> list[dict[str, Any]]:
    projects = _atlas_projects(atlas)
    normalized_aliases = {normalize_entity_alias(alias) for alias in aliases if alias}
    matches: list[dict[str, Any]] = []
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        haystack = [str(key), str(entry.get("description") or "")]
        haystack.extend(str(alias) for alias in (entry.get("aliases") or []))
        normalized = {normalize_entity_alias(item) for item in haystack if item}
        if normalized_aliases & normalized:
            matches.append({"project_key": str(key), **entry})
            continue
        if any(a and any(a in h or h in a for h in normalized) for a in normalized_aliases):
            matches.append({"project_key": str(key), **entry})
    return matches[:5]


def _artifact_rows(conn, aliases: list[str], project_keys: list[str]) -> list[dict[str, Any]]:
    if not _table_exists(conn, "artifact_registry"):
        return []
    terms = _unique([*aliases, *project_keys])[:12]
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for term in terms:
        clean = f"%{term.lower()}%"
        found = conn.execute(
            """
            SELECT DISTINCT r.*
            FROM artifact_registry r
            LEFT JOIN artifact_aliases a ON a.artifact_id = r.id
            WHERE LOWER(r.canonical_name) LIKE ?
               OR LOWER(r.domain) LIKE ?
               OR LOWER(r.description) LIKE ?
               OR LOWER(COALESCE(a.phrase, '')) LIKE ?
            ORDER BY r.last_touched_at DESC
            LIMIT 8
            """,
            (clean, clean, clean, clean),
        ).fetchall()
        for row in found:
            artifact_id = int(row["id"])
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            rows.append(dict(row))
    return rows[:12]


def _collect_open_items(conn, aliases: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    terms = [alias for alias in aliases if alias][:8]
    if not terms:
        return items
    tables = [
        ("commitments", "id", "description", "status", "status NOT IN ('done','cancelled','failed','closed')"),
        ("followups", "id", "description", "status", "status NOT IN ('done','cancelled','completed','closed')"),
        ("workflow_runs", "run_id", "goal", "status", "status NOT IN ('done','cancelled','failed','closed','completed')"),
        ("protocol_tasks", "task_id", "goal", "status", "status NOT IN ('done','cancelled','failed','closed')"),
    ]
    for table, id_col, text_col, status_col, status_filter in tables:
        if not _table_exists(conn, table):
            continue
        clauses = " OR ".join(f"LOWER({text_col}) LIKE ?" for _ in terms)
        params = [f"%{term.lower()}%" for term in terms]
        try:
            rows = conn.execute(
                f"""
                SELECT {id_col} AS item_id, {text_col} AS summary, {status_col} AS status
                FROM {table}
                WHERE ({clauses}) AND {status_filter}
                ORDER BY rowid DESC
                LIMIT 5
                """,
                params,
            ).fetchall()
        except Exception:
            continue
        for row in rows:
            items.append(
                {
                    "item_ref": f"{table}:{row['item_id']}",
                    "owner_source": table,
                    "status": row["status"],
                    "summary_redacted": redact_entity_value(row["summary"]),
                    "source_refs": [f"{table}:{row['item_id']}"],
                    "privacy_level": "normal",
                    "allowed_surfaces": list(DEFAULT_SURFACES),
                    "write_policy": "owner_only",
                }
            )
    return items[:20]


def _atlas_artifact_conflicts(projects: list[dict[str, Any]], artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    atlas_paths: dict[str, set[str]] = {}
    for project in projects:
        key = str(project.get("project_key") or "")
        locations = project.get("locations") if isinstance(project.get("locations"), dict) else {}
        atlas_paths[key] = {str(value) for value in locations.values() if str(value or "").strip()}
    for artifact in artifacts:
        domain = str(artifact.get("domain") or "")
        if domain not in atlas_paths or not atlas_paths[domain]:
            continue
        artifact_paths = _parse_json(artifact.get("paths"), [])
        for path in artifact_paths:
            clean_path = str(path or "")
            if clean_path and clean_path not in atlas_paths[domain]:
                conflicts.append(
                    {
                        "conflict_type": "authority_conflict",
                        "field": "location",
                        "winner": "project_atlas",
                        "loser": "artifact_registry",
                        "reason": "Project Atlas is authoritative for project/action locations.",
                        "source_refs": [f"project_atlas:{domain}", f"artifact_registry:{artifact.get('id')}"],
                        "value_redacted": redact_entity_value(clean_path),
                    }
                )
    return conflicts[:10]


def _local_dossier_fields(query: str, *, budget_tier: str, surface: str, max_chars: int = 6000) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    if budget_tier in LIGHT_BUDGET_TIERS or surface == "pre_answer":
        return [], [], {"skipped": True, "reason": "budget_or_surface"}
    try:
        from local_context import api as local_context_api
    except Exception as exc:
        return [], [], {"skipped": True, "reason": f"unavailable:{exc}"}
    try:
        payload = local_context_api.entity_dossier(query, max_assets=24, max_chunks=0, max_facts=80, max_chars=max_chars)
    except Exception as exc:
        return [], [], {"skipped": True, "reason": f"dossier_error:{exc}"}
    if payload.get("needs_disambiguation"):
        return [], [], {"needs_disambiguation": True, "candidates": payload.get("candidates") or []}
    fields: list[dict[str, Any]] = []
    refs: list[str] = sanitize_refs(payload.get("evidence_refs") or [])
    for fact in (payload.get("facts") or [])[:40]:
        value = fact.get("value") or ""
        if not value:
            continue
        fields.append(
            _field(
                name=f"local_fact:{fact.get('predicate') or 'fact'}",
                value=value,
                owner_source="local_context.entity_dossier",
                source_refs=[f"local_asset:{fact.get('source_asset_id')}#chunk:{fact.get('source_chunk_id')}"],
                privacy_level="private",
                write_policy="candidate_only",
                allowed_surfaces=["debug_local", "audit"],
            )
        )
    return fields, refs, {"skipped": False, "facts_returned": len(fields)}


def build_entity_profile(
    query: str,
    *,
    surface: str = "pre_answer",
    budget_tier: str = "standard",
    atlas: dict[str, Any] | None = None,
    atlas_path: str | Path | None = None,
    include_local_context: bool | None = None,
    cache: bool = False,
    conn=None,
) -> dict[str, Any]:
    """Build a redacted live profile from canonical stores."""
    connection = conn or _conn()
    now = _now()
    resolution = resolve_entity(query, conn=connection)
    used_sources: list[str] = ["entities"]
    missing_required_sources: list[str] = []
    fields: list[dict[str, Any]] = []
    open_items: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    source_refs: list[str] = []
    privacy = "normal"
    aliases: list[str] = []
    canonical_kind = ""
    canonical_name = ""
    entity_key = canonical_entity_key(query)
    action_blocked = False

    if resolution["status"] == "ambiguous":
        action_blocked = True
        profile = _profile_base(
            query=query,
            entity_key=entity_key,
            canonical_kind="entity",
            canonical_name=str(query or ""),
            aliases=[],
            resolution=resolution,
            fields=[],
            relations=[],
            open_items=[],
            conflicts=[{
                "conflict_type": "needs_disambiguation",
                "reason": "Several entities match; choose one before acting or writing.",
                "source_refs": [candidate["source_ref"] for candidate in resolution.get("candidates") or []],
            }],
            source_refs=[],
            privacy_level=privacy,
            surface=surface,
            budget_tier=budget_tier,
            used_sources=used_sources,
            missing_required_sources=[],
            created_at=now,
            action_blocked=action_blocked,
        )
        return _maybe_cache(profile, cache=cache, conn=connection)

    if resolution["status"] == "resolved":
        row = resolution["entity"]
        entity_key = resolution["entity_key"]
        canonical_kind = row.get("type") or "entity"
        canonical_name = row.get("name") or ""
        aliases = _entity_aliases(row)
        source_refs.append(entity_key)
        last_verified = float(row.get("updated_at") or row.get("created_at") or now)
        expires = last_verified + _ttl_seconds(canonical_kind, surface)
        privacy = _row_privacy(row)
        access_mode = _row_access_mode(row)
        write_policy = "read_only" if access_mode == "read_only" else "owner_only"
        fields.extend(
            [
                _field(name="canonical_name", value=canonical_name, owner_source="entities", source_refs=[entity_key], privacy_level=privacy, last_verified_at=last_verified, expires_at=expires, write_policy=write_policy),
                _field(name="canonical_kind", value=canonical_kind, owner_source="entities", source_refs=[entity_key], privacy_level="normal", last_verified_at=last_verified, expires_at=expires, write_policy=write_policy),
                _field(name="aliases", value=aliases, owner_source="entities", source_refs=[entity_key], privacy_level=privacy, last_verified_at=last_verified, expires_at=expires, write_policy=write_policy),
                _field(name="access_mode", value=access_mode, owner_source="entities", source_refs=[entity_key], privacy_level="normal", last_verified_at=last_verified, expires_at=expires, write_policy=write_policy),
                _field(name="entity_value", value=row.get("value") or "", owner_source="entities", source_refs=[entity_key], privacy_level=privacy, last_verified_at=last_verified, expires_at=expires, write_policy=write_policy),
            ]
        )
        if access_mode == "read_only":
            action_blocked = True
    else:
        missing_required_sources.append("entities")
        canonical_kind = "entity"
        canonical_name = str(query or "")
        aliases = [canonical_name] if canonical_name else []

    atlas_payload = _load_atlas(atlas=atlas, atlas_path=atlas_path)
    atlas_projects = _match_atlas_projects(atlas_payload, aliases or [query])
    if atlas_projects:
        used_sources.append("project_atlas")
        for project in atlas_projects:
            project_key = str(project.get("project_key") or "")
            source_refs.append(f"project_atlas:{project_key}")
            fields.append(
                _field(
                    name="project_key",
                    value=project_key,
                    owner_source="project_atlas",
                    source_refs=[f"project_atlas:{project_key}"],
                    privacy_level="normal",
                    write_policy="read_only",
                    last_verified_at=now,
                    expires_at=now + _ttl_seconds("project", surface),
                )
            )

    artifacts = _artifact_rows(connection, aliases or [query], [p.get("project_key", "") for p in atlas_projects])
    if artifacts:
        used_sources.append("artifact_registry")
        for artifact in artifacts[:8]:
            source_refs.append(f"artifact_registry:{artifact.get('id')}")
        fields.append(
            _field(
                name="artifact_refs",
                value=[f"artifact_registry:{artifact.get('id')}" for artifact in artifacts[:8]],
                owner_source="artifact_registry",
                source_refs=[f"artifact_registry:{artifact.get('id')}" for artifact in artifacts[:8]],
                privacy_level="normal",
                write_policy="owner_only",
                last_verified_at=now,
                expires_at=now + _ttl_seconds("artifact", surface),
            )
        )
    conflicts.extend(_atlas_artifact_conflicts(atlas_projects, artifacts))
    if conflicts:
        action_blocked = True

    open_items = _collect_open_items(connection, aliases or [query])
    if open_items:
        used_sources.extend(_unique([item["owner_source"] for item in open_items]))
        for item in open_items:
            source_refs.extend(item.get("source_refs") or [])

    local_requested = include_local_context
    if local_requested is None:
        local_requested = budget_tier not in LIGHT_BUDGET_TIERS and surface in {"pre_action", "debug_local", "audit"}
    if local_requested:
        local_fields, local_refs, local_meta = _local_dossier_fields(query, budget_tier=budget_tier, surface=surface)
        if local_meta.get("needs_disambiguation"):
            action_blocked = True
            conflicts.append({
                "conflict_type": "needs_disambiguation",
                "reason": "Local entity dossier needs disambiguation before use.",
                "source_refs": [],
                "candidates": local_meta.get("candidates") or [],
            })
        if local_fields:
            used_sources.append("local_context")
            fields.extend(local_fields)
            source_refs.extend(local_refs)

    profile = _profile_base(
        query=query,
        entity_key=entity_key,
        canonical_kind=canonical_kind,
        canonical_name=canonical_name,
        aliases=aliases,
        resolution=resolution,
        fields=fields,
        relations=relations,
        open_items=open_items,
        conflicts=conflicts,
        source_refs=source_refs,
        privacy_level=_max_privacy(privacy, *(field.get("privacy_level") or "normal" for field in fields)),
        surface=surface,
        budget_tier=budget_tier,
        used_sources=used_sources,
        missing_required_sources=missing_required_sources,
        created_at=now,
        action_blocked=action_blocked,
    )
    return _maybe_cache(profile, cache=cache, conn=connection)


def _profile_base(
    *,
    query: str,
    entity_key: str,
    canonical_kind: str,
    canonical_name: str,
    aliases: list[str],
    resolution: dict[str, Any],
    fields: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    open_items: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    source_refs: list[str],
    privacy_level: str,
    surface: str,
    budget_tier: str,
    used_sources: list[str],
    missing_required_sources: list[str],
    created_at: float,
    action_blocked: bool,
) -> dict[str, Any]:
    allowed_surfaces = _surfaces_for_privacy(privacy_level)
    clean_resolution = _redacted_resolution(resolution)
    filtered_fields = [
        field for field in fields
        if _surface_allowed(surface, field.get("allowed_surfaces") or [])
    ]
    filtered_open_items = [
        item for item in open_items
        if _surface_allowed(surface, item.get("allowed_surfaces") or [])
    ]
    clean_refs = sanitize_refs(source_refs)
    input_hash = _hash({"query": query, "surface": surface, "budget_tier": budget_tier, "entity_key": entity_key})
    refs_hash = _hash(clean_refs)
    expires_values = [field.get("expires_at") for field in filtered_fields if field.get("expires_at")]
    expires_at = min(expires_values) if expires_values else created_at + _ttl_seconds(canonical_kind, surface)
    last_values = [field.get("last_verified_at") for field in filtered_fields if field.get("last_verified_at")]
    last_verified_at = max(last_values) if last_values else None
    stale_status = "conflict" if conflicts else _stale_status(last_verified_at, expires_at)
    uid = f"ELP-{_hash([PROFILE_VERSION, entity_key, refs_hash, input_hash], length=32)}"
    heavy_used = sorted(HEAVY_SOURCES & set(used_sources))
    budget = {
        "budget_tier": budget_tier,
        "used_sources": _unique(used_sources),
        "heavy_sources_used": heavy_used,
        "degraded": bool(missing_required_sources) or (budget_tier in LIGHT_BUDGET_TIERS and bool(heavy_used)),
        "missing_required_sources": _unique(missing_required_sources),
    }
    if budget_tier in LIGHT_BUDGET_TIERS and heavy_used:
        action_blocked = True
        conflicts.append({
            "conflict_type": "budget_violation",
            "reason": "Instant/quick profiles cannot use heavy sources.",
            "source_refs": heavy_used,
        })
    if str(surface or "") in BLOCKED_PUBLIC_SURFACES and str(surface or "") not in allowed_surfaces:
        action_blocked = True
    return {
        "profile_uid": uid,
        "profile_version": PROFILE_VERSION,
        "entity_key": entity_key,
        "canonical_kind": canonical_kind,
        "canonical_name": redact_entity_value(canonical_name),
        "aliases": [redact_entity_value(alias) for alias in aliases[:20]],
        "resolution": {
            **clean_resolution,
            "action_blocked": bool(action_blocked),
        },
        "authority": {
            "non_authoritative_cache": True,
            "identity_owner": "entities",
            "project_owner": "project_atlas",
            "artifact_owner": "artifact_registry",
            "local_fact_owner": "local_context.entity_dossier",
            "relation_owner": "kg_edges",
            "history_owner": "memory_events/evidence_ledger/change_log",
        },
        "fields": filtered_fields,
        "relations": relations,
        "open_items": filtered_open_items,
        "conflicts": conflicts,
        "stale_status": stale_status,
        "last_verified_at": last_verified_at,
        "expires_at": expires_at,
        "source_refs": clean_refs,
        "source_refs_hash": refs_hash,
        "input_hash": input_hash,
        "privacy_level": normalize_privacy_level(privacy_level),
        "allowed_surfaces": allowed_surfaces,
        "surface": surface,
        "budget": budget,
    }


def _redacted_resolution(resolution: dict[str, Any]) -> dict[str, Any]:
    clean = {
        key: value
        for key, value in (resolution or {}).items()
        if key not in {"entity"}
    }
    candidates = []
    for candidate in clean.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        candidates.append({
            "entity_key": candidate.get("entity_key") or "",
            "entity_id": candidate.get("entity_id") or 0,
            "display_name": redact_entity_value(candidate.get("display_name") or ""),
            "canonical_kind": redact_entity_value(candidate.get("canonical_kind") or ""),
            "score": candidate.get("score") or 0.0,
            "aliases": [redact_entity_value(alias) for alias in (candidate.get("aliases") or [])[:12]],
            "source_ref": candidate.get("source_ref") or "",
        })
    clean["candidates"] = candidates
    return clean


def _maybe_cache(profile: dict[str, Any], *, cache: bool, conn=None) -> dict[str, Any]:
    if cache:
        stored = store_entity_profile(profile, conn=conn)
        profile["cache"] = stored
    return profile


def store_entity_profile(profile: dict[str, Any], *, conn=None) -> dict[str, Any]:
    connection = conn or _conn()
    _ensure_table(connection, "entity_profile_cache")
    now = _now()
    payload = json.loads(_json(profile, {}))
    connection.execute(
        """
        INSERT INTO entity_profile_cache (
            profile_uid, profile_version, entity_key, canonical_kind, canonical_name,
            source_refs_hash, input_hash, profile_redacted_json, source_refs_json,
            stale_status, privacy_level, allowed_surfaces_json, last_verified_at,
            expires_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(profile_uid) DO UPDATE SET
            profile_redacted_json=excluded.profile_redacted_json,
            source_refs_json=excluded.source_refs_json,
            stale_status=excluded.stale_status,
            privacy_level=excluded.privacy_level,
            allowed_surfaces_json=excluded.allowed_surfaces_json,
            last_verified_at=excluded.last_verified_at,
            expires_at=excluded.expires_at,
            updated_at=excluded.updated_at
        """,
        (
            payload["profile_uid"],
            payload.get("profile_version") or PROFILE_VERSION,
            payload.get("entity_key") or "",
            payload.get("canonical_kind") or "",
            payload.get("canonical_name") or "",
            payload.get("source_refs_hash") or "",
            payload.get("input_hash") or "",
            _json(payload, {}),
            _json(payload.get("source_refs") or [], []),
            payload.get("stale_status") or "unknown",
            payload.get("privacy_level") or "normal",
            _json(payload.get("allowed_surfaces") or [], []),
            payload.get("last_verified_at"),
            payload.get("expires_at"),
            now,
            now,
        ),
    )
    connection.commit()
    return {"ok": True, "profile_uid": payload["profile_uid"]}


def load_cached_entity_profile(profile_uid: str, *, conn=None) -> dict[str, Any] | None:
    connection = conn or _conn()
    if not _table_exists(connection, "entity_profile_cache"):
        return None
    row = connection.execute(
        "SELECT profile_redacted_json FROM entity_profile_cache WHERE profile_uid=?",
        (profile_uid,),
    ).fetchone()
    if not row:
        return None
    payload = _parse_json(row["profile_redacted_json"], {})
    return payload if isinstance(payload, dict) else None


def upsert_managed_asset(
    *,
    entity_key: str,
    artifact_id: int | None = None,
    project_key: str = "",
    asset_kind: str = "other",
    provider_ref: str = "",
    external_ref: str = "",
    status: str = "planned",
    source_refs: list[str] | tuple[str, ...] | None = None,
    privacy_level: str = "normal",
    metadata: dict[str, Any] | None = None,
    conn=None,
) -> dict[str, Any]:
    """Create/update a managed asset bridge without creating artifacts."""
    connection = conn or _conn()
    _ensure_table(connection, "nexo_managed_assets")
    if artifact_id is not None:
        found = connection.execute("SELECT id FROM artifact_registry WHERE id=?", (int(artifact_id),)).fetchone()
        if not found:
            return {"ok": False, "error": "artifact_id_not_found", "artifact_id": int(artifact_id)}
    external_hash = hashlib.sha256(str(external_ref or "").encode("utf-8", errors="ignore")).hexdigest() if external_ref else ""
    asset_uid = _managed_asset_uid(entity_key=entity_key, artifact_id=artifact_id, provider_ref=provider_ref, external_ref_hash=external_hash, project_key=project_key, asset_kind=asset_kind)
    now = _now()
    connection.execute(
        """
        INSERT INTO nexo_managed_assets (
            asset_uid, artifact_id, entity_key, project_key, asset_kind,
            provider_ref, provider_redacted, external_ref_hash, status,
            source_refs_json, privacy_level, last_verified_at, created_at,
            updated_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(asset_uid) DO UPDATE SET
            artifact_id=excluded.artifact_id,
            project_key=excluded.project_key,
            asset_kind=excluded.asset_kind,
            provider_ref=excluded.provider_ref,
            provider_redacted=excluded.provider_redacted,
            external_ref_hash=excluded.external_ref_hash,
            status=excluded.status,
            source_refs_json=excluded.source_refs_json,
            privacy_level=excluded.privacy_level,
            last_verified_at=excluded.last_verified_at,
            updated_at=excluded.updated_at,
            metadata_json=excluded.metadata_json
        """,
        (
            asset_uid,
            int(artifact_id) if artifact_id is not None else None,
            entity_key,
            project_key or "",
            asset_kind or "other",
            provider_ref or "",
            redact_entity_value(provider_ref),
            external_hash,
            status or "planned",
            _json(sanitize_refs(source_refs or []), []),
            normalize_privacy_level(privacy_level),
            now,
            now,
            now,
            _json(_sanitize_metadata(metadata or {}), {}),
        ),
    )
    connection.commit()
    return {"ok": True, "asset_uid": asset_uid, "artifact_id": artifact_id, "external_ref_hash": external_hash}


def _managed_asset_uid(
    *,
    entity_key: str,
    artifact_id: int | None,
    provider_ref: str,
    external_ref_hash: str,
    project_key: str,
    asset_kind: str,
) -> str:
    if artifact_id is not None:
        return f"managed_asset:artifact:{int(artifact_id)}"
    if provider_ref and external_ref_hash:
        return f"managed_asset:provider:{_hash([provider_ref, external_ref_hash], length=24)}"
    return f"managed_asset:{_hash([entity_key, project_key, asset_kind], length=24)}"


def _sanitize_metadata(value: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, item in (value or {}).items():
        lower = str(key).lower()
        if lower in {"payload", "raw", "body", "content", "secret", "token", "password", "provider_payload"}:
            clean[str(key)] = "[REDACTED]"
        elif isinstance(item, dict):
            clean[str(key)] = _sanitize_metadata(item)
        elif isinstance(item, list):
            clean[str(key)] = [redact_entity_value(part) for part in item[:20]]
        else:
            clean[str(key)] = redact_entity_value(item)
    return clean


def record_asset_context_updated(
    *,
    entity_key: str,
    asset_uid: str,
    change_type: str,
    artifact_id: int | None = None,
    project_key: str = "",
    source_refs: list[str] | tuple[str, ...] | None = None,
    privacy_level: str = "normal",
    session_id: str = "",
    idempotency_key: str = "",
    metadata: dict[str, Any] | None = None,
    conn=None,
) -> dict[str, Any]:
    """Record an idempotent asset context update and mirror it to memory_events."""
    connection = conn or _conn()
    _ensure_table(connection, "asset_context_updated")
    refs = sanitize_refs(source_refs or [])
    clean_change = redact_entity_value(change_type)
    event_uid = _safe_event_uid(
        idempotency_key,
        [entity_key, asset_uid, artifact_id, project_key, clean_change, refs],
    )
    now = _now()
    connection.execute(
        """
        INSERT OR IGNORE INTO asset_context_updated (
            event_uid, entity_key, asset_uid, artifact_id, project_key,
            change_type, source_refs_json, privacy_level, redaction_applied,
            created_at, memory_event_uid
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, '')
        """,
        (
            event_uid,
            entity_key,
            asset_uid,
            int(artifact_id) if artifact_id is not None else None,
            project_key or "",
            clean_change,
            _json(refs, []),
            normalize_privacy_level(privacy_level),
            now,
        ),
    )
    connection.commit()
    memory_event = db.record_memory_event(
        event_type="asset_context_updated",
        source_type="asset_context_updated",
        source_id=asset_uid,
        session_id=session_id,
        project_key=project_key,
        actor="nexo",
        raw_ref=event_uid,
        privacy_level=normalize_privacy_level(privacy_level),
        metadata={
            "entity_key": entity_key,
            "asset_uid": asset_uid,
            "artifact_id": artifact_id,
            "project_key": project_key,
            "change_type": clean_change,
            "source_refs": refs,
            **_sanitize_metadata(metadata or {}),
        },
        event_uid=event_uid,
        idempotency_key=event_uid,
    )
    memory_uid = str(memory_event.get("event_uid") or event_uid)
    connection.execute(
        "UPDATE asset_context_updated SET memory_event_uid=? WHERE event_uid=? AND COALESCE(memory_event_uid, '')=''",
        (memory_uid, event_uid),
    )
    connection.commit()
    row = connection.execute("SELECT * FROM asset_context_updated WHERE event_uid=?", (event_uid,)).fetchone()
    return {"ok": True, "event_uid": event_uid, "memory_event_uid": memory_uid, "inserted": bool(memory_event.get("inserted")), "row": dict(row) if row else {}}


__all__ = [
    "PROFILE_VERSION",
    "build_entity_profile",
    "load_cached_entity_profile",
    "record_asset_context_updated",
    "redact_entity_value",
    "resolve_entity",
    "sanitize_refs",
    "store_entity_profile",
    "upsert_managed_asset",
]
