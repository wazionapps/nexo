"""Ola 4 — SCHEMA-ABSTRACTION plugin tools.

Thin MCP surface over ``schema_abstraction`` (the distiller). Templates are
non-authoritative GUIDANCE: they prime a complete diagnosis when a recurring
incident archetype reappears; they never block. Precision-first / anti-noise:
a template is minted only from a genuinely recurring cluster (>= 3 distinct
incidents of the same archetype, high confidence).
"""

from __future__ import annotations

import json

import schema_abstraction as sa


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def handle_schema_abstraction_distill() -> str:
    """Run the distillation pass: cluster recurring incidents → mint diagnostic
    templates (idempotent). Safe to re-run; returns a report including anti-noise
    skips (clusters below the recurrence/confidence threshold)."""
    return _dump(sa.distill_templates())


def handle_schema_abstraction_templates(status: str = "active", limit: int = 50) -> str:
    """List distilled diagnostic templates."""
    return _dump({"templates": sa.list_templates(status=status, limit=limit)})


def handle_schema_abstraction_match(query: str = "", area: str = "", limit: int = 1) -> str:
    """Return the diagnostic template(s) whose archetype clearly matches an
    action (the same match used to prime pre-action context)."""
    return _dump({"matches": sa.match_templates_for_action(query=query, area=area, limit=limit)})


def handle_schema_abstraction_retire(template_uid: str, reason: str = "") -> str:
    """Retire a diagnostic template (lifecycle). Guidance-only; never deletes
    the underlying incidents."""
    return _dump(sa.retire_template(template_uid, reason=reason))


TOOLS = [
    (
        handle_schema_abstraction_distill,
        "nexo_schema_abstraction_distill",
        "Distill recurring incident archetypes into reusable diagnostic templates (idempotent clustering pass)",
    ),
    (
        handle_schema_abstraction_templates,
        "nexo_schema_abstraction_templates",
        "List distilled diagnostic templates",
    ),
    (
        handle_schema_abstraction_match,
        "nexo_schema_abstraction_match",
        "Match an action against diagnostic-template archetypes (primed diagnosis)",
    ),
    (
        handle_schema_abstraction_retire,
        "nexo_schema_abstraction_retire",
        "Retire a diagnostic template (lifecycle, guidance-only)",
    ),
]
