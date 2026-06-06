"""MCP handlers for structured NEXO product knowledge."""

from __future__ import annotations

import json
from typing import Any

from product_knowledge import (
    answer_product_question,
    explain_capability,
    find_capabilities,
    list_capabilities,
    surface_status,
    validate_catalog,
)


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def handle_product_capabilities(
    query: str = "",
    category: str = "",
    status: str = "",
    limit: int = 20,
) -> str:
    """Return structured product capabilities from the NEXO catalog."""
    capabilities = find_capabilities(query, category=category, status=status, limit=limit)
    return _json({"ok": True, "count": len(capabilities), "capabilities": capabilities})


def handle_capability_explain(capability_id: str = "", query: str = "", locale: str = "es") -> str:
    """Explain one product capability with safety and source context."""
    capability = explain_capability(capability_id, query=query)
    if not capability:
        return _json({"ok": False, "error": "capability-not-found"})
    return _json({"ok": True, "capability": capability})


def handle_product_answer(question: str, locale: str = "es", limit: int = 5) -> str:
    """Answer a NEXO product question using only the structured product catalog."""
    return answer_product_question(question, locale=locale, limit=limit)


def handle_product_surface_status(surface: str = "", limit: int = 50) -> str:
    """Return which product capabilities are exposed by a surface."""
    return _json(surface_status(surface, limit=limit))


def handle_product_knowledge_validate() -> str:
    """Validate the product knowledge catalog."""
    try:
        errors = validate_catalog()
        capability_count = 0 if errors else len(list_capabilities())
    except Exception as exc:
        errors = [str(exc)]
        capability_count = 0
    return _json({"ok": not errors, "errors": errors, "capability_count": capability_count})
