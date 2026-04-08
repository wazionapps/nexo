"""Tools for NEXO hot context / recent 24h memory."""

from __future__ import annotations

import json

from db import (
    derive_context_key,
    capture_context_event,
    get_hot_context,
    build_pre_action_context,
    format_pre_action_context_bundle,
    resolve_hot_context,
    search_hot_context,
)


def _parse_metadata(metadata: str = "") -> dict:
    if not metadata or not metadata.strip():
        return {}
    try:
        parsed = json.loads(metadata)
    except Exception:
        return {"raw": metadata.strip()}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def handle_recent_context_capture(
    title: str,
    summary: str = "",
    details: str = "",
    topic: str = "",
    context_key: str = "",
    state: str = "active",
    owner: str = "",
    source_type: str = "",
    source_id: str = "",
    session_id: str = "",
    actor: str = "nexo",
    ttl_hours: int = 24,
    metadata: str = "",
) -> str:
    """Capture/update a recent context item and append an event."""
    clean_title = (title or "").strip()
    if not clean_title and not summary.strip():
        return "ERROR: title or summary is required."
    resolved_key = derive_context_key(
        context_key=context_key,
        topic=topic,
        title=clean_title or summary,
        source_type=source_type,
        source_id=source_id,
    )
    result = capture_context_event(
        event_type="context_capture",
        title=clean_title or summary,
        summary=(summary or clean_title)[:600],
        body=details[:1600] if details else "",
        context_key=resolved_key,
        topic=topic,
        context_title=clean_title or summary or resolved_key,
        context_summary=summary or details or clean_title,
        context_type="topic",
        state=state or "active",
        owner=owner or "",
        actor=actor or "nexo",
        source_type=source_type or "",
        source_id=source_id or "",
        session_id=session_id or "",
        metadata=_parse_metadata(metadata),
        ttl_hours=ttl_hours,
    )
    event = result.get("event") or {}
    return (
        f"Recent context captured: {result.get('context_key') or resolved_key}\n"
        f"Title: {clean_title or summary}\n"
        f"State: {(result.get('context') or {}).get('state', state or 'active')}\n"
        f"Event: {event.get('event_type', 'context_capture')}"
    )


def handle_recent_context(query: str = "", context_key: str = "", hours: int = 24, limit: int = 8) -> str:
    """Search hot context items and show their recent continuity."""
    if context_key.strip():
        item = get_hot_context(context_key.strip(), include_events=True, limit=max(4, int(limit or 8)))
        if not item:
            return f"No hot context found for {context_key.strip()}."
        bundle = {
            "query": query.strip(),
            "context_key": context_key.strip(),
            "hours": hours,
            "contexts": [item],
            "events": item.get("events") or [],
            "reminders": [],
            "followups": [],
            "has_matches": True,
        }
        return format_pre_action_context_bundle(bundle)

    bundle = build_pre_action_context(query=query, context_key="", session_id="", hours=hours, limit=limit)
    return format_pre_action_context_bundle(bundle)


def handle_pre_action_context(query: str = "", context_key: str = "", session_id: str = "", hours: int = 24, limit: int = 8) -> str:
    """Build the recent 24h bundle that agents/scripts should consult before acting."""
    bundle = build_pre_action_context(
        query=query,
        context_key=context_key,
        session_id=session_id,
        hours=hours,
        limit=limit,
    )
    return format_pre_action_context_bundle(bundle)


def handle_recent_context_resolve(
    context_key: str = "",
    topic: str = "",
    resolution: str = "",
    actor: str = "nexo",
    session_id: str = "",
    source_type: str = "",
    source_id: str = "",
    ttl_hours: int = 24,
) -> str:
    """Resolve a hot-context item and append a resolution event."""
    resolved_key = derive_context_key(
        context_key=context_key,
        topic=topic,
        title=topic,
        source_type=source_type,
        source_id=source_id,
    )
    if not resolved_key:
        return "ERROR: context_key or topic is required."
    result = resolve_hot_context(
        context_key=resolved_key,
        resolution=resolution or "Context resolved.",
        actor=actor or "nexo",
        session_id=session_id or "",
        source_type=source_type or "",
        source_id=source_id or "",
        ttl_hours=ttl_hours,
    )
    if "error" in result:
        return f"ERROR: {result['error']}"
    return f"Hot context resolved: {resolved_key}"


def handle_hot_context_list(hours: int = 24, limit: int = 10, state: str = "") -> str:
    """List active hot context items without the full event bundle."""
    rows = search_hot_context("", hours=hours, limit=limit, state=state)
    if not rows:
        return "No hot context items."
    lines = [f"HOT CONTEXT ({len(rows)}):"]
    for item in rows:
        summary = (item.get("summary") or "").strip()
        suffix = f" — {summary[:120]}" if summary else ""
        lines.append(
            f"- {item.get('context_key')}: [{item.get('state')}] {item.get('title')} "
            f"(last_event={item.get('last_event_at')}){suffix}"
        )
    return "\n".join(lines)
