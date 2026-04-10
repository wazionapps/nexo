from __future__ import annotations
"""Goal Engine v1 — explicit optimization profiles for durable goals and decisions."""

import json

from db._core import get_db
from db._workflow import get_workflow_goal

VALID_SCOPE_TYPES = {"default", "area", "task_type", "goal_id"}
VALID_STATUSES = {"active", "disabled"}
WEIGHT_KEYS = ("impact", "success", "risk", "somatic")
DEFAULT_WEIGHTS = {
    "impact": 0.35,
    "success": 0.30,
    "risk": 0.20,
    "somatic": 0.15,
}
DEFAULT_GOAL_PROFILES = (
    {
        "profile_id": "default_balanced",
        "profile_name": "Balanced default",
        "description": "Balancea impacto, exito, riesgo y huella somatica para decisiones generales.",
        "scope_type": "default",
        "scope_value": "",
        "goal_labels": ["maximise_success", "minimise_risk", "preserve_trust"],
        "weights": DEFAULT_WEIGHTS,
        "status": "active",
        "source": "system",
    },
    {
        "profile_id": "release_safety",
        "profile_name": "Release safety",
        "description": "Favorece decisiones reversibles y verificadas en release, deploy y cambios publicos.",
        "scope_type": "area",
        "scope_value": "release",
        "goal_labels": ["minimise_risk", "preserve_trust", "maximise_success"],
        "weights": {
            "impact": 0.24,
            "success": 0.28,
            "risk": 0.30,
            "somatic": 0.18,
        },
        "status": "active",
        "source": "system",
    },
    {
        "profile_id": "customer_trust",
        "profile_name": "Customer trust",
        "description": "Favorece decisiones que preservan confianza y reducen friccion con clientes.",
        "scope_type": "area",
        "scope_value": "customer",
        "goal_labels": ["preserve_trust", "maximise_success", "minimise_risk"],
        "weights": {
            "impact": 0.25,
            "success": 0.31,
            "risk": 0.26,
            "somatic": 0.18,
        },
        "status": "active",
        "source": "system",
    },
    {
        "profile_id": "ops_efficiency",
        "profile_name": "Operations efficiency",
        "description": "Favorece throughput operativo manteniendo riesgo contenido en ejecucion rutinaria.",
        "scope_type": "task_type",
        "scope_value": "execute",
        "goal_labels": ["maximise_efficiency", "maximise_success", "minimise_risk"],
        "weights": {
            "impact": 0.38,
            "success": 0.28,
            "risk": 0.20,
            "somatic": 0.14,
        },
        "status": "active",
        "source": "system",
    },
    {
        "profile_id": "business_growth",
        "profile_name": "Business growth",
        "description": "Da mas peso a impacto y exito cuando el contexto busca crecimiento o revenue.",
        "scope_type": "area",
        "scope_value": "business",
        "goal_labels": ["maximise_business_impact", "maximise_success"],
        "weights": {
            "impact": 0.56,
            "success": 0.22,
            "risk": 0.14,
            "somatic": 0.08,
        },
        "status": "active",
        "source": "system",
    },
)


def _parse_json(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _normalize_goal_labels(labels) -> list[str]:
    parsed = _parse_json(labels, labels if isinstance(labels, list) else [])
    if not isinstance(parsed, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in parsed:
        clean = str(item or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _normalize_weights(weights) -> dict:
    parsed = _parse_json(weights, weights if isinstance(weights, dict) else {})
    if not isinstance(parsed, dict):
        parsed = {}
    collected: dict[str, float] = {}
    for key in WEIGHT_KEYS:
        try:
            value = float(parsed.get(key, DEFAULT_WEIGHTS[key]))
        except (TypeError, ValueError):
            value = DEFAULT_WEIGHTS[key]
        collected[key] = max(0.01, value)
    total = sum(collected.values())
    if total <= 0:
        collected = dict(DEFAULT_WEIGHTS)
        total = sum(collected.values())
    return {key: round(collected[key] / total, 4) for key in WEIGHT_KEYS}


def _row_to_goal_profile(row, *, resolved_by: str = "") -> dict | None:
    if not row:
        return None
    profile = dict(row)
    profile["goal_labels"] = _normalize_goal_labels(profile.get("goal_labels"))
    profile["weights"] = _normalize_weights(profile.get("weights"))
    if resolved_by:
        profile["resolved_by"] = resolved_by
    return profile


def ensure_default_goal_profiles() -> None:
    conn = get_db()
    for profile in DEFAULT_GOAL_PROFILES:
        conn.execute(
            """INSERT OR IGNORE INTO goal_profiles (
                   profile_id, profile_name, description, scope_type, scope_value,
                   goal_labels, weights, status, source
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                profile["profile_id"],
                profile["profile_name"],
                profile["description"],
                profile["scope_type"],
                profile["scope_value"],
                json.dumps(profile["goal_labels"], ensure_ascii=False),
                json.dumps(_normalize_weights(profile["weights"]), ensure_ascii=False),
                profile["status"],
                profile["source"],
            ),
        )
    conn.commit()


def get_goal_profile(profile_id: str) -> dict | None:
    ensure_default_goal_profiles()
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM goal_profiles WHERE profile_id = ?",
        ((profile_id or "").strip(),),
    ).fetchone()
    return _row_to_goal_profile(row)


def list_goal_profiles(*, scope_type: str = "", status: str = "active", limit: int = 50) -> list[dict]:
    ensure_default_goal_profiles()
    conn = get_db()
    clauses = []
    params: list[object] = []
    clean_scope = (scope_type or "").strip()
    clean_status = (status or "").strip()
    if clean_scope:
        clauses.append("scope_type = ?")
        params.append(clean_scope)
    if clean_status:
        clauses.append("status = ?")
        params.append(clean_status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""SELECT * FROM goal_profiles
            {where}
            ORDER BY
              CASE scope_type
                WHEN 'default' THEN 0
                WHEN 'area' THEN 1
                WHEN 'task_type' THEN 2
                WHEN 'goal_id' THEN 3
                ELSE 9
              END,
              profile_id ASC
            LIMIT ?""",
        params + [max(1, int(limit))],
    ).fetchall()
    return [_row_to_goal_profile(row) for row in rows if row]


def upsert_goal_profile(
    *,
    profile_id: str,
    profile_name: str = "",
    description: str = "",
    scope_type: str = "default",
    scope_value: str = "",
    goal_labels=None,
    weights=None,
    status: str = "active",
    source: str = "manual",
) -> dict:
    ensure_default_goal_profiles()
    clean_id = (profile_id or "").strip()
    if not clean_id:
        raise ValueError("profile_id is required")
    clean_scope = (scope_type or "default").strip()
    if clean_scope not in VALID_SCOPE_TYPES:
        raise ValueError(f"scope_type must be one of: {', '.join(sorted(VALID_SCOPE_TYPES))}")
    clean_status = (status or "active").strip().lower()
    if clean_status not in VALID_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(sorted(VALID_STATUSES))}")

    normalized_weights = _normalize_weights(weights)
    normalized_labels = _normalize_goal_labels(goal_labels)
    conn = get_db()
    existing = conn.execute(
        "SELECT * FROM goal_profiles WHERE profile_id = ?",
        (clean_id,),
    ).fetchone()
    if existing:
        current = dict(existing)
        conn.execute(
            """UPDATE goal_profiles
               SET profile_name = ?,
                   description = ?,
                   scope_type = ?,
                   scope_value = ?,
                   goal_labels = ?,
                   weights = ?,
                   status = ?,
                   source = ?,
                   updated_at = datetime('now')
               WHERE profile_id = ?""",
            (
                (profile_name or current.get("profile_name") or clean_id).strip(),
                (description or current.get("description") or "").strip(),
                clean_scope,
                (scope_value or current.get("scope_value") or "").strip().lower(),
                json.dumps(normalized_labels or _normalize_goal_labels(current.get("goal_labels")), ensure_ascii=False),
                json.dumps(normalized_weights, ensure_ascii=False),
                clean_status,
                (source or current.get("source") or "manual").strip(),
                clean_id,
            ),
        )
    else:
        conn.execute(
            """INSERT INTO goal_profiles (
                   profile_id, profile_name, description, scope_type, scope_value,
                   goal_labels, weights, status, source
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                clean_id,
                (profile_name or clean_id).strip(),
                (description or "").strip(),
                clean_scope,
                (scope_value or "").strip().lower(),
                json.dumps(normalized_labels, ensure_ascii=False),
                json.dumps(normalized_weights, ensure_ascii=False),
                clean_status,
                (source or "manual").strip(),
            ),
        )
    conn.commit()
    return get_goal_profile(clean_id) or {}


def resolve_goal_profile(
    *,
    profile_id: str = "",
    area: str = "",
    task_type: str = "",
    goal_id: str = "",
) -> dict:
    ensure_default_goal_profiles()
    conn = get_db()
    explicit_id = (profile_id or "").strip()
    if explicit_id:
        explicit = get_goal_profile(explicit_id)
        if not explicit:
            raise ValueError(f"Unknown goal profile: {explicit_id}")
        if explicit.get("status") != "active":
            raise ValueError(f"Goal profile {explicit_id} is not active")
        explicit["resolved_by"] = "explicit"
        return explicit

    clean_goal_id = (goal_id or "").strip()
    if clean_goal_id:
        workflow_goal = get_workflow_goal(clean_goal_id)
        if workflow_goal:
            shared_state = workflow_goal.get("shared_state") or {}
            shared_profile_id = str(shared_state.get("goal_profile_id", "")).strip()
            if shared_profile_id:
                linked = get_goal_profile(shared_profile_id)
                if linked and linked.get("status") == "active":
                    linked["resolved_by"] = "workflow_goal.shared_state"
                    return linked
        row = conn.execute(
            """SELECT * FROM goal_profiles
               WHERE scope_type = 'goal_id' AND scope_value = ? AND status = 'active'
               ORDER BY updated_at DESC, profile_id ASC
               LIMIT 1""",
            (clean_goal_id,),
        ).fetchone()
        if row:
            return _row_to_goal_profile(row, resolved_by="goal_id") or {}

    clean_area = (area or "").strip().lower()
    if clean_area:
        row = conn.execute(
            """SELECT * FROM goal_profiles
               WHERE scope_type = 'area' AND scope_value = ? AND status = 'active'
               ORDER BY updated_at DESC, profile_id ASC
               LIMIT 1""",
            (clean_area,),
        ).fetchone()
        if row:
            return _row_to_goal_profile(row, resolved_by="area") or {}

    clean_type = (task_type or "").strip().lower()
    if clean_type:
        row = conn.execute(
            """SELECT * FROM goal_profiles
               WHERE scope_type = 'task_type' AND scope_value = ? AND status = 'active'
               ORDER BY updated_at DESC, profile_id ASC
               LIMIT 1""",
            (clean_type,),
        ).fetchone()
        if row:
            return _row_to_goal_profile(row, resolved_by="task_type") or {}

    row = conn.execute(
        """SELECT * FROM goal_profiles
           WHERE scope_type = 'default' AND status = 'active'
           ORDER BY updated_at DESC, profile_id ASC
           LIMIT 1"""
    ).fetchone()
    return _row_to_goal_profile(row, resolved_by="default") or {
        "profile_id": "default_balanced",
        "profile_name": "Balanced default",
        "description": DEFAULT_GOAL_PROFILES[0]["description"],
        "scope_type": "default",
        "scope_value": "",
        "goal_labels": list(DEFAULT_GOAL_PROFILES[0]["goal_labels"]),
        "weights": dict(DEFAULT_WEIGHTS),
        "status": "active",
        "source": "system",
        "resolved_by": "fallback_default",
    }
