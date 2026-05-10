from __future__ import annotations
"""Public MCP helpers for Memory Observations v2."""

import json

from db import (
    backfill_memory_observations,
    list_memory_events,
    list_memory_observations,
    maintain_memory_observations,
    memory_event_stats,
    memory_observation_health,
    memory_observation_stats,
    process_memory_observation_queue,
)
from memory_retrieval import (
    answer_memory_question,
    format_memory_search,
    memory_search,
    memory_timeline,
)


def _format_event(item: dict) -> str:
    paths = item.get("file_paths") or []
    path_note = f" files={', '.join(paths[:4])}" if paths else ""
    tool_note = f" tool={item.get('tool_name')}" if item.get("tool_name") else ""
    source = f"{item.get('source_type') or '?'}:{item.get('source_id') or ''}".rstrip(":")
    return (
        f"- #{item.get('id')} {item.get('event_type')} "
        f"[{source}] sid={item.get('session_id') or '-'} "
        f"ts={item.get('created_at')}{tool_note}{path_note}"
    )


def handle_memory_event_list(
    query: str = "",
    event_type: str = "",
    source_type: str = "",
    source_id: str = "",
    session_id: str = "",
    project_key: str = "",
    limit: int = 20,
) -> str:
    """List raw memory events captured by phase 1."""
    rows = list_memory_events(
        query=query,
        event_type=event_type,
        source_type=source_type,
        source_id=source_id,
        session_id=session_id,
        project_key=project_key,
        limit=limit,
    )
    if not rows:
        return "No memory events found."
    lines = [f"MEMORY EVENTS ({len(rows)})"]
    lines.extend(_format_event(item) for item in rows)
    return "\n".join(lines)


def handle_memory_event_stats(days: int = 7) -> str:
    """Summarize raw memory event volume by type and source."""
    stats = memory_event_stats(days=days)
    return json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True)


def _format_observation(item: dict) -> str:
    refs = item.get("evidence_refs") or []
    ref_note = f" refs={', '.join(refs[:3])}" if refs else ""
    return (
        f"- #{item.get('id')} {item.get('observation_type')} "
        f"[{item.get('subject') or '-'}] score={item.get('salience')} "
        f"ts={item.get('created_at')}: {item.get('summary')}{ref_note}"
    )


def handle_memory_observation_process(limit: int = 25) -> str:
    """Process pending memory events into passive observations."""
    result = process_memory_observation_queue(limit=limit)
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)


def handle_memory_observation_list(
    query: str = "",
    observation_type: str = "",
    session_id: str = "",
    project_key: str = "",
    status: str = "",
    limit: int = 20,
) -> str:
    """List passive memory observations."""
    rows = list_memory_observations(
        query=query,
        observation_type=observation_type,
        session_id=session_id,
        project_key=project_key,
        status=status,
        limit=limit,
    )
    if not rows:
        return "No memory observations found."
    lines = [f"MEMORY OBSERVATIONS ({len(rows)})"]
    lines.extend(_format_observation(item) for item in rows)
    return "\n".join(lines)


def handle_memory_observation_stats(days: int = 7) -> str:
    """Summarize passive memory observations and queue status."""
    stats = memory_observation_stats(days=days)
    return json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True)


def handle_memory_backfill(sources: str = "", limit: int = 100) -> str:
    """Backfill Memory Observations from existing durable Brain tables."""
    requested = [item.strip() for item in (sources or "").split(",") if item.strip()]
    result = backfill_memory_observations(sources=requested or None, limit=limit)
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)


def handle_memory_health() -> str:
    """Return Memory Observations v2 health and table status."""
    return json.dumps(memory_observation_health(), ensure_ascii=False, indent=2, sort_keys=True)


def handle_memory_maintenance(
    process_limit: int = 100,
    retry_failed: bool = True,
    backfill_sources: str = "",
    backfill_limit: int = 0,
) -> str:
    """Run safe Memory Observations v2 queue processing and optional backfill."""
    requested = [item.strip() for item in (backfill_sources or "").split(",") if item.strip()]
    result = maintain_memory_observations(
        process_limit=process_limit,
        retry_failed=retry_failed,
        backfill_sources=requested or None,
        backfill_limit=backfill_limit,
    )
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)


def handle_memory_search(
    query: str,
    project_hint: str = "",
    time_range: str = "",
    depth: str = "brief",
    limit: int = 10,
) -> str:
    """Search Memory Observations v2 with evidence refs."""
    return format_memory_search(
        memory_search(query, project_hint=project_hint, time_range=time_range, depth=depth, limit=limit)
    )


def handle_memory_answer(
    query: str,
    project_hint: str = "",
    time_range: str = "",
    limit: int = 5,
) -> str:
    """Answer a memory question only when evidence exists."""
    return answer_memory_question(query, project_hint=project_hint, time_range=time_range, limit=limit)


def handle_memory_timeline(
    query: str = "",
    project_hint: str = "",
    time_range: str = "",
    limit: int = 20,
) -> str:
    """Return a chronological memory timeline."""
    result = memory_timeline(query, project_hint=project_hint, time_range=time_range, limit=limit)
    candidates = result.get("candidates") or []
    if not candidates:
        return "No hay eventos suficientes para construir timeline."
    lines = [f"MEMORY TIMELINE ({len(candidates)}) — {query or time_range or '(sin query)'}"]
    for item in candidates:
        refs = item.get("evidence_refs") or []
        refs_note = f" refs={', '.join(refs[:3])}" if refs else ""
        lines.append(f"- {item.get('created_at')}: {item.get('summary')}{refs_note}")
    return "\n".join(lines)
