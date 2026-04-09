from __future__ import annotations
"""NEXO Drive/Curiosity — autonomous investigation signals.

Public MCP tool handlers + internal detection logic that feeds from
heartbeat, task_close, and diary consolidation.
"""

import json
import re
from db import (
    create_drive_signal, reinforce_drive_signal, get_drive_signals,
    get_drive_signal, update_drive_signal_status, decay_drive_signals,
    find_similar_drive_signal, drive_signal_stats,
)


# ── Heuristic detection keywords ─────────────────────────────────────

_ANOMALY_PATTERNS = [
    re.compile(r"\b(subió|bajó|cayó|subi[oó]|baj[oó]|dropped|spiked|jumped)\b.*\b\d+%", re.I),
    re.compile(r"\b(inesperado|unexpected|anomal|raro|weird|strange)\b", re.I),
    re.compile(r"\b(error rate|tasa de error|failure|fallo)\b.*\b(subi|increas|grew)\b", re.I),
]

_PATTERN_INDICATORS = [
    re.compile(r"\b(otra vez|again|de nuevo|siempre pasa|keeps happening|recurring)\b", re.I),
    re.compile(r"\b(cada vez que|every time|whenever)\b", re.I),
    re.compile(r"\b(mismo (problema|error|issue)|same (problem|error|issue))\b", re.I),
]

_GAP_INDICATORS = [
    re.compile(r"\b(no sé cómo|don'?t know how|no entiendo|unclear how)\b", re.I),
    re.compile(r"\b(falta documentación|missing docs|undocumented)\b", re.I),
]

_OPPORTUNITY_INDICATORS = [
    re.compile(r"\b(benchmark|media del sector|industry average)\b.*\b(bajo|low|por debajo|below)\b", re.I),
    re.compile(r"\b(podríamos|could|se podría|we could|opportunity)\b.*\b(automatiz|improve|mejorar|optimiz)\b", re.I),
]


def _classify_signal(text: str) -> str | None:
    """Classify text into a signal type, or None if nothing interesting."""
    for pattern in _ANOMALY_PATTERNS:
        if pattern.search(text):
            return "anomaly"
    for pattern in _PATTERN_INDICATORS:
        if pattern.search(text):
            return "pattern"
    for pattern in _GAP_INDICATORS:
        if pattern.search(text):
            return "gap"
    for pattern in _OPPORTUNITY_INDICATORS:
        if pattern.search(text):
            return "opportunity"
    return None


def _infer_area(text: str) -> str:
    """Infer operational area from text keywords."""
    text_lower = text.lower()
    area_keywords = {
        "shopify": ["shopify", "tienda", "pedido", "producto", "sku"],
        "google-ads": ["google ads", "campaña", "campaign", "cpc", "pmax", "roas", "gads"],
        "meta-ads": ["meta ads", "facebook", "instagram", "pixel", "capi"],
        "wazion": ["wazion", "whatsapp", "wa ", "baileys"],
        "nexo": ["nexo", "brain", "mcp", "cognitive"],
        "canaririural": ["canarirural", "canari", "reserva", "hospedaje", "alojamiento", "propietario"],
        "seo": ["seo", "search console", "indexación", "ranking"],
        "email": ["email", "correo", "inbox", "smtp"],
    }
    for area, keywords in area_keywords.items():
        for kw in keywords:
            if kw in text_lower:
                return area
    return ""


def detect_drive_signal(
    context_hint: str,
    source: str,
    source_id: str = "",
    area: str = "",
) -> dict | None:
    """Analyze text for interesting signals. Creates or reinforces.

    Called internally from heartbeat and task_close. Not a public MCP tool.
    Returns the signal dict if created/reinforced, None otherwise.
    """
    if not context_hint or len(context_hint.strip()) < 15:
        return None

    signal_type = _classify_signal(context_hint)
    if not signal_type:
        return None

    inferred_area = area or _infer_area(context_hint)

    # Check for similar existing signal
    existing = find_similar_drive_signal(context_hint, inferred_area)
    if existing:
        result = reinforce_drive_signal(existing["id"], context_hint[:500])
        return result if result.get("ok") else None

    # Create new
    result = create_drive_signal(
        signal_type=signal_type,
        source=source,
        source_id=source_id,
        area=inferred_area,
        summary=context_hint[:300],
    )
    return result if result.get("ok") else None


# ── Public MCP tool handlers ─────────────────────────────────────────

def handle_drive_signals(
    status: str = "",
    area: str = "",
    limit: int = 20,
) -> str:
    """List drive signals, optionally filtered by status and area."""
    signals = get_drive_signals(
        status=status or None,
        area=area or None,
        limit=limit,
    )
    if not signals:
        return "No drive signals found."

    stats = drive_signal_stats()
    lines = [
        f"DRIVE SIGNALS ({len(signals)} shown, {stats['total']} total):",
        f"  By status: {json.dumps(stats.get('by_status', {}), ensure_ascii=False)}",
        "",
    ]
    for s in signals:
        evidence_count = 0
        try:
            evidence_count = len(json.loads(s.get("evidence") or "[]"))
        except (json.JSONDecodeError, TypeError):
            pass
        tension_bar = "█" * int(float(s.get("tension", 0)) * 10)
        lines.append(
            f"  [{s['id']}] {s['status'].upper()} {tension_bar} "
            f"t={s['tension']:.2f} ({s['signal_type']}) "
            f"{'[' + s['area'] + '] ' if s.get('area') else ''}"
            f"{s['summary'][:80]}"
            f" ({evidence_count} obs, decay={s.get('decay_rate', 0.05):.2f})"
        )
    return "\n".join(lines)


def handle_drive_reinforce(signal_id: int, observation: str) -> str:
    """Manually reinforce a drive signal with a new observation."""
    if not observation.strip():
        return "ERROR: observation cannot be empty"
    result = reinforce_drive_signal(signal_id, observation)
    if not result.get("ok"):
        return f"ERROR: {result.get('error', 'unknown')}"
    return (
        f"Signal #{signal_id} reinforced: "
        f"tension {result['old_tension']:.2f} → {result['new_tension']:.2f}, "
        f"status {result['old_status']} → {result['new_status']}, "
        f"{result['evidence_count']} observations total"
    )


def handle_drive_act(signal_id: int, outcome: str) -> str:
    """Mark a drive signal as investigated with an outcome."""
    if not outcome.strip():
        return "ERROR: outcome cannot be empty"
    result = update_drive_signal_status(signal_id, "acted", outcome)
    if not result.get("ok"):
        return f"ERROR: {result.get('error', 'unknown')}"
    return f"Signal #{signal_id} marked as ACTED. Outcome recorded."


def handle_drive_dismiss(signal_id: int, reason: str) -> str:
    """Dismiss a drive signal with a reason (archived, not deleted)."""
    if not reason.strip():
        return "ERROR: reason cannot be empty"
    result = update_drive_signal_status(signal_id, "dismissed", reason)
    if not result.get("ok"):
        return f"ERROR: {result.get('error', 'unknown')}"
    return f"Signal #{signal_id} dismissed. Reason: {reason}"
