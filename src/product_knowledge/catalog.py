from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).with_name("catalog.json")
LEGACY_SYSTEM_CATALOG_NAMES = {
    "nexo_agent_email": [
        ("nexo_email_managed_agent_mailbox", "Email NEXO Managed Agent Mailbox"),
    ],
    "nexo_credits_provider_proxy": [
        ("nexo_provider_proxy", "NEXO Credits Provider Proxy"),
        ("nexo_provider_models", "Provider Model Discovery"),
    ],
    "nexo_managed_cloud_edge": [
        ("nexo_credits_cloud", "NEXO Credits Managed Cloud"),
        ("nexo_edge_cloudflare", "NEXO Edge Cloudflare"),
    ],
}

REQUIRED_CAPABILITY_FIELDS = {
    "id",
    "title",
    "category",
    "layer",
    "status",
    "summary",
    "aliases",
    "source_refs",
    "surfaces",
    "live_state",
    "actions",
    "safety",
    "answer_guidance",
}
REQUIRED_LIVE_STATE_FIELDS = {"source", "max_age", "fallback"}
REQUIRED_ACTION_FIELDS = {"read", "write"}
REQUIRED_SAFETY_FIELDS = {
    "data_touched",
    "data_origin",
    "consent_required",
    "confirmation_required",
    "credential_policy",
    "retention",
    "audit",
    "forbidden_actions",
}
REQUIRED_GUIDANCE_FIELDS = {"must_say", "must_not_say"}


def _normalize(text: Any) -> str:
    return str(text or "").strip().lower()


def _tokens(text: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9._:-]{1,}", _normalize(text))
        if len(token) >= 2
    }


def _haystack(capability: dict[str, Any]) -> str:
    return " ".join(
        [
            str(capability.get("id", "")),
            str(capability.get("title", "")),
            str(capability.get("category", "")),
            str(capability.get("layer", "")),
            str(capability.get("status", "")),
            str(capability.get("summary", "")),
            " ".join(str(item) for item in capability.get("aliases") or []),
            " ".join(str(item) for item in capability.get("source_refs") or []),
            " ".join(str(item) for item in capability.get("surfaces") or []),
        ]
    )


def _score(query: str, capability: dict[str, Any]) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 1.0
    haystack_tokens = _tokens(_haystack(capability))
    overlap = query_tokens & haystack_tokens
    if not overlap:
        return 0.0
    exact_bonus = 0.25 if _normalize(query) in _normalize(_haystack(capability)) else 0.0
    return len(overlap) / max(1, len(query_tokens)) + exact_bonus


@lru_cache(maxsize=1)
def load_product_catalog() -> dict[str, Any]:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    errors = validate_catalog(payload)
    if errors:
        raise ValueError("Invalid product knowledge catalog:\n- " + "\n- ".join(errors))
    return payload


def validate_catalog(catalog: dict[str, Any] | None = None) -> list[str]:
    payload = catalog if catalog is not None else json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["catalog must be a JSON object"]
    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        errors.append("capabilities must be a non-empty list")
        return errors
    seen: set[str] = set()
    for index, capability in enumerate(capabilities):
        prefix = f"capabilities[{index}]"
        if not isinstance(capability, dict):
            errors.append(f"{prefix} must be an object")
            continue
        missing = sorted(REQUIRED_CAPABILITY_FIELDS - capability.keys())
        if missing:
            errors.append(f"{prefix} missing fields: {', '.join(missing)}")
        cap_id = str(capability.get("id", "")).strip()
        if not re.match(r"^[a-z0-9][a-z0-9_:-]{2,}$", cap_id):
            errors.append(f"{prefix}.id is invalid")
        if cap_id in seen:
            errors.append(f"{prefix}.id duplicates {cap_id}")
        seen.add(cap_id)
        for field in ("aliases", "source_refs", "surfaces"):
            if not isinstance(capability.get(field), list) or not capability.get(field):
                errors.append(f"{prefix}.{field} must be a non-empty list")
        live_state = capability.get("live_state") or {}
        if not isinstance(live_state, dict):
            errors.append(f"{prefix}.live_state must be an object")
        else:
            missing_live = sorted(REQUIRED_LIVE_STATE_FIELDS - live_state.keys())
            if missing_live:
                errors.append(f"{prefix}.live_state missing fields: {', '.join(missing_live)}")
        actions = capability.get("actions") or {}
        if not isinstance(actions, dict):
            errors.append(f"{prefix}.actions must be an object")
        else:
            missing_actions = sorted(REQUIRED_ACTION_FIELDS - actions.keys())
            if missing_actions:
                errors.append(f"{prefix}.actions missing fields: {', '.join(missing_actions)}")
            for action_field in REQUIRED_ACTION_FIELDS:
                if not isinstance(actions.get(action_field), list):
                    errors.append(f"{prefix}.actions.{action_field} must be a list")
        safety = capability.get("safety") or {}
        if not isinstance(safety, dict):
            errors.append(f"{prefix}.safety must be an object")
        else:
            missing_safety = sorted(REQUIRED_SAFETY_FIELDS - safety.keys())
            if missing_safety:
                errors.append(f"{prefix}.safety missing fields: {', '.join(missing_safety)}")
            for list_field in ("data_touched", "forbidden_actions"):
                if not isinstance(safety.get(list_field), list) or not safety.get(list_field):
                    errors.append(f"{prefix}.safety.{list_field} must be a non-empty list")
        guidance = capability.get("answer_guidance") or {}
        if not isinstance(guidance, dict):
            errors.append(f"{prefix}.answer_guidance must be an object")
        else:
            missing_guidance = sorted(REQUIRED_GUIDANCE_FIELDS - guidance.keys())
            if missing_guidance:
                errors.append(f"{prefix}.answer_guidance missing fields: {', '.join(missing_guidance)}")
            for list_field in REQUIRED_GUIDANCE_FIELDS:
                if not isinstance(guidance.get(list_field), list):
                    errors.append(f"{prefix}.answer_guidance.{list_field} must be a list")
    return errors


def list_capabilities() -> list[dict[str, Any]]:
    return list(load_product_catalog().get("capabilities") or [])


def find_capabilities(
    query: str = "",
    *,
    category: str = "",
    status: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    category_clean = _normalize(category)
    status_clean = _normalize(status)
    rows: list[tuple[float, dict[str, Any]]] = []
    for capability in list_capabilities():
        if category_clean and _normalize(capability.get("category")) != category_clean:
            continue
        if status_clean and _normalize(capability.get("status")) != status_clean:
            continue
        score = _score(query, capability)
        if query and score <= 0:
            continue
        rows.append((score, capability))
    rows.sort(key=lambda item: (item[0], item[1].get("id", "")), reverse=True)
    return [dict(row) for _, row in rows[: max(1, int(limit or 20))]]


def explain_capability(capability_id: str = "", *, query: str = "") -> dict[str, Any] | None:
    target = _normalize(capability_id)
    if target:
        for capability in list_capabilities():
            keys = [
                capability.get("id"),
                capability.get("title"),
                *(capability.get("aliases") or []),
            ]
            if target in {_normalize(key) for key in keys}:
                return dict(capability)
    matches = find_capabilities(query or capability_id, limit=1)
    return matches[0] if matches else None


def catalog_entries_for_system_catalog() -> list[dict[str, Any]]:
    entries = []
    for capability in list_capabilities():
        base_entry = {
            "kind": "product_capability",
            "name": capability["id"],
            "display_name": capability["title"],
            "category": capability["category"],
            "layer": capability["layer"],
            "status": capability["status"],
            "description": capability["summary"],
            "source": ", ".join(capability.get("source_refs") or []),
            "surfaces": capability.get("surfaces") or [],
            "aliases": capability.get("aliases") or [],
            "live_state": capability.get("live_state") or {},
            "safety": capability.get("safety") or {},
            "answer_guidance": capability.get("answer_guidance") or {},
        }
        entries.append(base_entry)
        for legacy_name, legacy_display in LEGACY_SYSTEM_CATALOG_NAMES.get(capability["id"], []):
            legacy_entry = dict(base_entry)
            legacy_entry["name"] = legacy_name
            legacy_entry["display_name"] = legacy_display
            legacy_entry["canonical_capability_id"] = capability["id"]
            entries.append(legacy_entry)
    return entries


def answer_product_question(question: str, *, locale: str = "es", limit: int = 5) -> str:
    matches = find_capabilities(question, limit=limit)
    if not matches:
        return "No encuentro esa capacidad en el catálogo de producto. La respuesta segura es marcarla como no verificada hasta consultar la fuente viva."
    lines = ["Respuesta basada en el catálogo de producto NEXO:"]
    for capability in matches:
        lines.append(f"- {capability['title']}: {capability['summary']}")
        live_state = capability.get("live_state") or {}
        if live_state.get("source"):
            lines.append(f"  Fuente viva: {live_state['source']}.")
        safety = capability.get("safety") or {}
        if safety.get("confirmation_required"):
            lines.append(f"  Confirmación: {safety['confirmation_required']}.")
    lines.append("Regla: precios, saldos, proveedores invocables, tickets e infraestructura se verifican en backend o runtime vivo antes de prometerlos.")
    return "\n".join(lines)


def surface_status(surface: str = "", *, limit: int = 50) -> dict[str, Any]:
    needle = _normalize(surface)
    capabilities = []
    for capability in list_capabilities():
        surfaces = capability.get("surfaces") or []
        if needle and not any(needle in _normalize(item) for item in surfaces):
            continue
        capabilities.append(
            {
                "id": capability["id"],
                "title": capability["title"],
                "category": capability["category"],
                "status": capability["status"],
                "surfaces": surfaces,
                "live_state_source": (capability.get("live_state") or {}).get("source", ""),
            }
        )
    return {
        "ok": True,
        "schema_version": load_product_catalog().get("schema_version"),
        "count": len(capabilities[: max(1, int(limit or 50))]),
        "capabilities": capabilities[: max(1, int(limit or 50))],
    }
