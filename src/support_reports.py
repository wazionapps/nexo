"""Sanitized support-ticket reports for product improvement signals."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_URL_RE = re.compile(r"\bhttps?://[^\s<>'\"]+", re.IGNORECASE)
_MAC_PATH_RE = re.compile(r"(?<!\w)/(?:Users|Volumes|private|tmp|var)/[^\s<>'\"]+")
_LINUX_HOME_PATH_RE = re.compile(r"(?<!\w)/(?:home|root)/[^\s<>'\"]+")
_WIN_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:Users|ProgramData|Windows|Temp)\\[^\s<>'\"]+")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|bearer|cookie|credential|password|secret|session|token)\b\s*[:=]\s*[^\s,;]+"
)
_SECRET_VALUE_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|pk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|AKIA[0-9A-Z]{12,})\b"
)


def sanitize_product_gap_text(value: object, *, limit: int = 600) -> str:
    """Redact tenant/operator data before sending support reports."""
    text = str(value or "")
    for raw, marker in (
        (str(Path.home()), "[redacted-home]"),
        (str(os.environ.get("NEXO_HOME") or ""), "[redacted-path]"),
        (str(os.environ.get("NEXO_CODE") or ""), "[redacted-path]"),
    ):
        if raw:
            text = text.replace(raw, marker)
    text = _EMAIL_RE.sub("[redacted-email]", text)
    text = _URL_RE.sub("[redacted-url]", text)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}=[redacted-secret]", text)
    text = _SECRET_VALUE_RE.sub("[redacted-secret]", text)
    text = _MAC_PATH_RE.sub("[redacted-path]", text)
    text = _LINUX_HOME_PATH_RE.sub("[redacted-path]", text)
    text = _WIN_PATH_RE.sub("[redacted-path]", text)
    text = re.sub(r"\b(?:AGENTS|CLAUDE|MEMORY)\.md\b", "[redacted-bootstrap]", text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit > 0 and len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def _evidence_examples(action: dict[str, Any], limit: int = 3) -> list[str]:
    examples: list[str] = []
    for entry in action.get("evidence", []) or []:
        if isinstance(entry, dict):
            raw = entry.get("quote") or entry.get("text") or entry.get("summary") or entry.get("evidence") or entry
        else:
            raw = entry
        cleaned = sanitize_product_gap_text(raw, limit=220)
        if cleaned:
            examples.append(cleaned)
        if len(examples) >= limit:
            break
    return examples


def _response_ok(response: object) -> bool:
    text = str(response or "")
    return text.startswith("HTTP 2") or text.startswith("HTTP 201")


def create_product_gap_report(action: dict[str, Any], content: dict[str, Any], dedupe_key: str) -> dict[str, Any]:
    """Create a sanitized NEXO Desktop support ticket for a recurring product gap."""
    try:
        from tools_api_call import handle_support_ticket_create
    except Exception as exc:
        return {"success": False, "error": f"support ticket API unavailable: {exc}"}

    title = sanitize_product_gap_text(content.get("title") or "Deep Sleep product gap", limit=140)
    description = sanitize_product_gap_text(content.get("description") or title, limit=900)
    pattern = sanitize_product_gap_text(content.get("pattern") or "", limit=500)
    deliverable = sanitize_product_gap_text(content.get("deliverable") or "", limit=120)
    sessions_count = content.get("sessions_count", "")
    evidence_count = content.get("evidence_count", len(action.get("evidence", []) or []))
    impact = sanitize_product_gap_text(action.get("impact") or "", limit=80)
    confidence = action.get("confidence", "")
    examples = _evidence_examples(action)

    lines = [
        "Deep Sleep detected a recurring NEXO product gap that should be reviewed by the product team.",
        "",
        f"Impact: {impact or 'unspecified'}",
        f"Confidence: {confidence}",
        f"Suggested deliverable: {deliverable or 'product improvement'}",
        f"Observed sessions: {sessions_count or 'unknown'}",
        f"Evidence items: {evidence_count or 0}",
        "",
        f"Pattern: {pattern or 'No compact pattern supplied.'}",
        f"Requested behavior: {description}",
        "",
        "Privacy: operator/client-specific paths, URLs, emails, bootstrap filenames and secret-looking values were redacted before sending.",
    ]
    if examples:
        lines.extend(["", "Redacted examples:"])
        lines.extend(f"- {example}" for example in examples)

    client_message_id = sanitize_product_gap_text(dedupe_key or "", limit=120) or (
        "product-gap-" + hashlib.md5(description.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    )
    priority = "high" if str(action.get("impact") or "").lower() in {"high", "critical"} else "normal"
    response = handle_support_ticket_create(
        f"[NEXO-PRODUCT-GAP] {title}",
        "\n".join(lines),
        priority=priority,
        client_message_id=client_message_id,
        origin="auto_incident",
    )
    return {
        "success": _response_ok(response),
        "response": response,
        "client_message_id": client_message_id,
        "sanitized": True,
    }


def create_evolution_support_ticket(
    *,
    cycle_num: int,
    analysis: str,
    proposals: list[dict[str, Any]],
    queued_candidates: list[dict[str, Any]] | None = None,
    dedupe_key: str = "",
) -> dict[str, Any]:
    """Create one anonymized support ticket for an Evolution improvement cycle."""
    try:
        from tools_api_call import handle_support_ticket_create
    except Exception as exc:
        return {"success": False, "error": f"support ticket API unavailable: {exc}"}

    queued_candidates = queued_candidates or []
    title_seed = ""
    if proposals:
        title_seed = str(proposals[0].get("action") or proposals[0].get("proposal") or "")
    if not title_seed and queued_candidates:
        title_seed = str(queued_candidates[0].get("title") or "")
    title = sanitize_product_gap_text(title_seed or "Evolution product improvement request", limit=120)

    lines = [
        "Evolution detected NEXO product improvements that should be reviewed by the product team.",
        "",
        f"Cycle: {int(cycle_num)}",
        "Mode: support ticket only; no GitHub branch, push, PR, transcript, local database, or raw private evidence is attached.",
        f"Analysis: {sanitize_product_gap_text(analysis, limit=900) or 'No compact analysis supplied.'}",
        "",
        "Requested improvements:",
    ]

    for index, proposal in enumerate(proposals[:8], 1):
        action = sanitize_product_gap_text(proposal.get("action") or proposal.get("proposal") or "", limit=280)
        reasoning = sanitize_product_gap_text(proposal.get("reasoning") or "", limit=360)
        dimension = sanitize_product_gap_text(proposal.get("dimension") or "other", limit=80)
        scope = sanitize_product_gap_text(proposal.get("scope") or "local", limit=80)
        classification = sanitize_product_gap_text(proposal.get("classification") or "propose", limit=80)
        lines.extend(
            [
                f"{index}. {action or 'Untitled improvement'}",
                f"   Dimension: {dimension}; scope: {scope}; classification: {classification}",
                f"   Reasoning: {reasoning or 'No compact reasoning supplied.'}",
            ]
        )

    if queued_candidates:
        lines.extend(["", "Former public-port queue items now routed to support:"])
        for index, candidate in enumerate(queued_candidates[:5], 1):
            candidate_title = sanitize_product_gap_text(candidate.get("title") or "", limit=240)
            candidate_reasoning = sanitize_product_gap_text(candidate.get("reasoning") or "", limit=360)
            files = [
                sanitize_product_gap_text(path, limit=120)
                for path in (candidate.get("files_changed") or [])[:6]
            ]
            lines.extend(
                [
                    f"{index}. {candidate_title or 'Untitled queued improvement'}",
                    f"   Reasoning: {candidate_reasoning or 'No compact reasoning supplied.'}",
                    f"   Product files: {', '.join(files) if files else 'not supplied'}",
                ]
            )

    lines.extend(
        [
            "",
            "Privacy: all operator/client-specific paths, URLs, emails, bootstrap filenames, tokens and secret-looking values were redacted before sending.",
        ]
    )

    fingerprint_payload = json.dumps(
        {
            "cycle": int(cycle_num),
            "analysis": analysis,
            "proposals": proposals[:8],
            "queued": queued_candidates[:5],
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    client_message_id = sanitize_product_gap_text(dedupe_key or "", limit=140)
    if not client_message_id:
        client_message_id = "evolution-support-" + hashlib.md5(
            fingerprint_payload.encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:16]
    priority = "high" if queued_candidates else "normal"
    if any(str(item.get("impact") or "").strip().lower() in {"high", "critical"} for item in proposals):
        priority = "high"

    response = handle_support_ticket_create(
        f"[NEXO-EVOLUTION] {title}",
        "\n".join(lines),
        priority=priority,
        client_message_id=client_message_id,
        origin="auto_incident",
    )
    return {
        "success": _response_ok(response),
        "response": response,
        "client_message_id": client_message_id,
        "sanitized": True,
    }
