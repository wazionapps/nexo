from __future__ import annotations
"""NEXO DB — recent events + hot context for 24h operational continuity."""

import json
import re
import unicodedata
from typing import Any

from db._core import get_db, now_epoch

DEFAULT_CONTEXT_TTL_HOURS = 24
MAX_CONTEXT_TTL_HOURS = 7 * 24
ACTIVE_CONTEXT_STATES = {"active", "waiting_user", "waiting_third_party", "blocked"}


def _serialize_metadata(metadata: dict[str, Any] | None) -> str:
    if not metadata:
        return "{}"
    try:
        return json.dumps(metadata, ensure_ascii=True, sort_keys=True)
    except Exception:
        return "{}"


def _truncate(text: str | None, limit: int = 240) -> str:
    if not text:
        return ""
    clean = str(text).strip()
    return clean if len(clean) <= limit else clean[: limit - 3] + "..."


def _normalize_text(text: str | None) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", str(text))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return ascii_text.lower()


def _tokenize(text: str | None) -> set[str]:
    normalized = _normalize_text(text)
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9._:-]{1,}", normalized)
        if len(token) >= 3
    }


def _slugify(text: str | None) -> str:
    normalized = _normalize_text(text)
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return slug[:80]


def clamp_ttl_hours(ttl_hours: int | float | str | None) -> int:
    try:
        value = int(float(ttl_hours or DEFAULT_CONTEXT_TTL_HOURS))
    except Exception:
        value = DEFAULT_CONTEXT_TTL_HOURS
    return max(1, min(value, MAX_CONTEXT_TTL_HOURS))


def derive_context_key(
    *,
    context_key: str = "",
    topic: str = "",
    title: str = "",
    source_type: str = "",
    source_id: str = "",
) -> str:
    explicit = (context_key or "").strip()
    if explicit:
        return explicit
    if source_type and source_id:
        return f"{source_type.strip()}:{source_id.strip()}"
    if topic.strip():
        slug = _slugify(topic)
        if slug:
            return f"topic:{slug}"
    slug = _slugify(title)
    if slug:
        return f"topic:{slug}"
    return ""


def cleanup_expired_hot_context(now: float | None = None) -> dict[str, int]:
    conn = get_db()
    ts = now if now is not None else now_epoch()
    deleted_events = conn.execute(
        "DELETE FROM recent_events WHERE expires_at < ?",
        (ts,),
    ).rowcount
    deleted_contexts = conn.execute(
        "DELETE FROM hot_context WHERE expires_at < ?",
        (ts,),
    ).rowcount
    conn.commit()
    return {
        "deleted_events": int(deleted_events or 0),
        "deleted_contexts": int(deleted_contexts or 0),
    }


def remember_hot_context(
    *,
    context_key: str,
    title: str,
    summary: str = "",
    context_type: str = "topic",
    state: str = "active",
    owner: str = "",
    source_type: str = "",
    source_id: str = "",
    session_id: str = "",
    metadata: dict[str, Any] | None = None,
    ttl_hours: int | float | str | None = DEFAULT_CONTEXT_TTL_HOURS,
    last_event_at: float | None = None,
) -> dict:
    clean_key = (context_key or "").strip()
    clean_title = _truncate(title or summary or clean_key, 160)
    if not clean_key:
        return {"error": "context_key is required"}
    if not clean_title:
        return {"error": "title is required"}

    conn = get_db()
    now = now_epoch()
    event_ts = float(last_event_at if last_event_at is not None else now)
    ttl = clamp_ttl_hours(ttl_hours)
    expires_at = max(event_ts, now) + ttl * 3600
    clean_state = (state or "active").strip().lower()

    existing = conn.execute(
        "SELECT * FROM hot_context WHERE context_key = ?",
        (clean_key,),
    ).fetchone()
    if existing:
        first_seen_at = float(existing["first_seen_at"] or event_ts)
        summary_value = _truncate(summary, 600) if summary else (existing["summary"] or "")
        metadata_value = _serialize_metadata(metadata) if metadata is not None else (existing["metadata"] or "{}")
        conn.execute(
            """
            UPDATE hot_context
               SET title = ?,
                   summary = ?,
                   context_type = ?,
                   state = ?,
                   owner = ?,
                   source_type = ?,
                   source_id = ?,
                   session_id = ?,
                   metadata = ?,
                   last_event_at = ?,
                   expires_at = ?,
                   updated_at = ?
             WHERE context_key = ?
            """,
            (
                clean_title,
                summary_value,
                (context_type or existing["context_type"] or "topic").strip().lower(),
                clean_state,
                (owner or existing["owner"] or "").strip(),
                (source_type or existing["source_type"] or "").strip(),
                (source_id or existing["source_id"] or "").strip(),
                (session_id or existing["session_id"] or "").strip(),
                metadata_value,
                event_ts,
                expires_at,
                now,
                clean_key,
            ),
        )
    else:
        first_seen_at = event_ts
        conn.execute(
            """
            INSERT INTO hot_context (
                context_key, title, summary, context_type, state, owner,
                source_type, source_id, session_id, metadata,
                first_seen_at, last_event_at, expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_key,
                clean_title,
                _truncate(summary, 600),
                (context_type or "topic").strip().lower(),
                clean_state,
                (owner or "").strip(),
                (source_type or "").strip(),
                (source_id or "").strip(),
                (session_id or "").strip(),
                _serialize_metadata(metadata),
                first_seen_at,
                event_ts,
                expires_at,
                now,
                now,
            ),
        )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM hot_context WHERE context_key = ?",
        (clean_key,),
    ).fetchone()
    return dict(row) if row else {"error": f"hot_context {clean_key} not found after upsert"}


def record_recent_event(
    *,
    event_type: str,
    title: str = "",
    summary: str = "",
    body: str = "",
    context_key: str = "",
    actor: str = "system",
    source_type: str = "",
    source_id: str = "",
    session_id: str = "",
    metadata: dict[str, Any] | None = None,
    ttl_hours: int | float | str | None = DEFAULT_CONTEXT_TTL_HOURS,
    created_at: float | None = None,
) -> dict:
    clean_event = (event_type or "").strip().lower()
    if not clean_event:
        return {"error": "event_type is required"}

    conn = get_db()
    now = now_epoch()
    event_ts = float(created_at if created_at is not None else now)
    ttl = clamp_ttl_hours(ttl_hours)
    expires_at = max(event_ts, now) + ttl * 3600
    conn.execute(
        """
        INSERT INTO recent_events (
            context_key, event_type, title, summary, body, actor,
            source_type, source_id, session_id, metadata, created_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (context_key or "").strip(),
            clean_event,
            _truncate(title, 160),
            _truncate(summary, 600),
            _truncate(body, 1600),
            (actor or "system").strip(),
            (source_type or "").strip(),
            (source_id or "").strip(),
            (session_id or "").strip(),
            _serialize_metadata(metadata),
            event_ts,
            expires_at,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM recent_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else {"error": "recent_event insert failed"}


def capture_context_event(
    *,
    event_type: str,
    title: str = "",
    summary: str = "",
    body: str = "",
    context_key: str = "",
    topic: str = "",
    context_title: str = "",
    context_summary: str = "",
    context_type: str = "topic",
    state: str = "active",
    owner: str = "",
    actor: str = "system",
    source_type: str = "",
    source_id: str = "",
    session_id: str = "",
    metadata: dict[str, Any] | None = None,
    ttl_hours: int | float | str | None = DEFAULT_CONTEXT_TTL_HOURS,
    created_at: float | None = None,
) -> dict:
    cleanup_expired_hot_context()
    clean_key = derive_context_key(
        context_key=context_key,
        topic=topic,
        title=context_title or title or summary,
        source_type=source_type,
        source_id=source_id,
    )
    context = None
    if clean_key:
        context = remember_hot_context(
            context_key=clean_key,
            title=context_title or title or summary or clean_key,
            summary=context_summary or summary or body,
            context_type=context_type,
            state=state,
            owner=owner,
            source_type=source_type,
            source_id=source_id,
            session_id=session_id,
            metadata=metadata,
            ttl_hours=ttl_hours,
            last_event_at=created_at,
        )
    event = record_recent_event(
        event_type=event_type,
        title=title or context_title,
        summary=summary or context_summary,
        body=body,
        context_key=clean_key,
        actor=actor,
        source_type=source_type,
        source_id=source_id,
        session_id=session_id,
        metadata=metadata,
        ttl_hours=ttl_hours,
        created_at=created_at,
    )
    return {"context_key": clean_key, "context": context, "event": event}


def get_hot_context(context_key: str, include_events: bool = False, limit: int = 10) -> dict | None:
    cleanup_expired_hot_context()
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM hot_context WHERE context_key = ?",
        ((context_key or "").strip(),),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    if include_events:
        result["events"] = [
            dict(item)
            for item in conn.execute(
                """
                SELECT * FROM recent_events
                 WHERE context_key = ?
                 ORDER BY created_at DESC
                 LIMIT ?
                """,
                ((context_key or "").strip(), max(1, int(limit or 10))),
            ).fetchall()
        ]
    return result


def _score_text_match(query_tokens: set[str], haystack: str) -> float:
    if not query_tokens:
        return 0.0
    haystack_tokens = _tokenize(haystack)
    if not haystack_tokens:
        return 0.0
    intersection = query_tokens & haystack_tokens
    if not intersection:
        return 0.0
    smaller = min(len(query_tokens), len(haystack_tokens))
    return len(intersection) / max(1, smaller)


def search_hot_context(query: str = "", *, hours: int = DEFAULT_CONTEXT_TTL_HOURS, limit: int = 10, state: str = "") -> list[dict]:
    cleanup_expired_hot_context()
    conn = get_db()
    ts = now_epoch() - clamp_ttl_hours(hours) * 3600
    if state.strip():
        rows = conn.execute(
            "SELECT * FROM hot_context WHERE last_event_at >= ? AND state = ? ORDER BY last_event_at DESC LIMIT 200",
            (ts, state.strip().lower()),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM hot_context WHERE last_event_at >= ? ORDER BY last_event_at DESC LIMIT 200",
            (ts,),
        ).fetchall()
    query_tokens = _tokenize(query)
    scored: list[dict] = []
    for row in rows:
        item = dict(row)
        combined = " ".join(
            [
                item.get("context_key") or "",
                item.get("title") or "",
                item.get("summary") or "",
                item.get("source_type") or "",
                item.get("source_id") or "",
            ]
        )
        score = _score_text_match(query_tokens, combined) if query_tokens else 0.5
        if query_tokens and score <= 0:
            continue
        recency_boost = max(0.0, 1.0 - ((now_epoch() - float(item.get("last_event_at") or now_epoch())) / (clamp_ttl_hours(hours) * 3600)))
        item["_score"] = round(score + recency_boost * 0.35, 4)
        scored.append(item)
    scored.sort(key=lambda item: (item["_score"], item.get("last_event_at", 0)), reverse=True)
    return scored[: max(1, int(limit or 10))]


def search_recent_events(
    query: str = "",
    *,
    hours: int = DEFAULT_CONTEXT_TTL_HOURS,
    limit: int = 10,
    context_keys: list[str] | None = None,
    session_id: str = "",
    exclude_source: tuple[str, str] | None = None,
) -> list[dict]:
    cleanup_expired_hot_context()
    conn = get_db()
    ts = now_epoch() - clamp_ttl_hours(hours) * 3600
    rows = conn.execute(
        "SELECT * FROM recent_events WHERE created_at >= ? ORDER BY created_at DESC LIMIT 400",
        (ts,),
    ).fetchall()
    query_tokens = _tokenize(query)
    context_filter = {item for item in (context_keys or []) if item}
    scored: list[dict] = []
    for row in rows:
        item = dict(row)
        if exclude_source and (
            item.get("source_type") == exclude_source[0]
            and item.get("source_id") == exclude_source[1]
        ):
            continue
        if session_id and item.get("session_id") == session_id:
            session_bonus = 0.25
        else:
            session_bonus = 0.0
        if context_filter and item.get("context_key") not in context_filter:
            if not query_tokens:
                continue
        combined = " ".join(
            [
                item.get("context_key") or "",
                item.get("title") or "",
                item.get("summary") or "",
                item.get("body") or "",
                item.get("source_type") or "",
                item.get("source_id") or "",
            ]
        )
        score = _score_text_match(query_tokens, combined) if query_tokens else 0.35
        if query_tokens and score <= 0 and item.get("context_key") not in context_filter:
            continue
        recency_boost = max(0.0, 1.0 - ((now_epoch() - float(item.get("created_at") or now_epoch())) / (clamp_ttl_hours(hours) * 3600)))
        item["_score"] = round(score + recency_boost * 0.45 + session_bonus, 4)
        scored.append(item)
    scored.sort(key=lambda item: (item["_score"], item.get("created_at", 0)), reverse=True)
    return scored[: max(1, int(limit or 10))]


def _find_related_items(table: str, query: str, *, hours: int = DEFAULT_CONTEXT_TTL_HOURS, limit: int = 5) -> list[dict]:
    conn = get_db()
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []
    rows = conn.execute(
        f"SELECT * FROM {table} ORDER BY updated_at DESC LIMIT 200"
    ).fetchall()
    items: list[dict] = []
    for row in rows:
        item = dict(row)
        combined = " ".join(
            [
                item.get("id") or "",
                item.get("description") or "",
                item.get("verification") or "",
                item.get("reasoning") or "",
                item.get("category") or "",
                item.get("status") or "",
            ]
        )
        score = _score_text_match(query_tokens, combined)
        if score <= 0:
            continue
        item["_score"] = round(score, 4)
        items.append(item)
    items.sort(key=lambda item: (item["_score"], item.get("updated_at", 0)), reverse=True)
    return items[: max(1, int(limit or 5))]


def build_pre_action_context(
    *,
    query: str = "",
    context_key: str = "",
    session_id: str = "",
    hours: int = DEFAULT_CONTEXT_TTL_HOURS,
    limit: int = 6,
) -> dict:
    cleanup_expired_hot_context()
    clean_query = (query or "").strip()
    clean_key = (context_key or "").strip()
    contexts: list[dict] = []
    if clean_key:
        exact = get_hot_context(clean_key, include_events=False)
        if exact:
            exact["_score"] = 1.0
            contexts.append(exact)
    searched = search_hot_context(clean_query, hours=hours, limit=limit, state="")
    seen_keys = {item.get("context_key") for item in contexts}
    for item in searched:
        if item.get("context_key") not in seen_keys:
            contexts.append(item)
            seen_keys.add(item.get("context_key"))
    contexts = contexts[: max(1, int(limit or 6))]

    context_keys = [item.get("context_key") for item in contexts if item.get("context_key")]
    events = search_recent_events(
        clean_query,
        hours=hours,
        limit=max(4, int(limit or 6) * 2),
        context_keys=context_keys,
        session_id=session_id,
    )

    reminders = _find_related_items("reminders", clean_query, hours=hours, limit=4) if clean_query else []
    followups = _find_related_items("followups", clean_query, hours=hours, limit=4) if clean_query else []

    return {
        "query": clean_query,
        "context_key": clean_key,
        "hours": clamp_ttl_hours(hours),
        "contexts": contexts,
        "events": events,
        "reminders": reminders,
        "followups": followups,
        "has_matches": bool(contexts or events or reminders or followups),
    }


def format_pre_action_context_bundle(bundle: dict, *, compact: bool = False) -> str:
    if not bundle or not bundle.get("has_matches"):
        return "No recent context."

    lines: list[str] = []
    header = "RECENT CONTEXT (24h)"
    if bundle.get("query"):
        header += f" — query: {bundle['query'][:120]}"
    lines.append(header)

    contexts = bundle.get("contexts") or []
    if contexts:
        lines.append("Contexts:")
        for item in contexts[: (3 if compact else 5)]:
            state = item.get("state") or "active"
            last_event = item.get("last_event_at") or "?"
            summary = _truncate(item.get("summary") or "", 140)
            suffix = f" — {summary}" if summary else ""
            lines.append(f"- {item.get('context_key')}: [{state}] {item.get('title') or ''} ({last_event}){suffix}")

    events = bundle.get("events") or []
    if events:
        lines.append("Events:")
        for event in events[: (3 if compact else 6)]:
            note = _truncate(event.get("summary") or event.get("body") or "", 140)
            suffix = f" — {note}" if note else ""
            lines.append(
                f"- {event.get('created_at')} [{event.get('event_type')}] {event.get('title') or event.get('context_key') or '(event)'}{suffix}"
            )

    if not compact:
        reminders = bundle.get("reminders") or []
        if reminders:
            lines.append("Related reminders:")
            for item in reminders[:3]:
                lines.append(f"- {item.get('id')}: {item.get('description') or ''} [{item.get('status') or '—'}]")
        followups = bundle.get("followups") or []
        if followups:
            lines.append("Related followups:")
            for item in followups[:3]:
                lines.append(f"- {item.get('id')}: {item.get('description') or ''} [{item.get('status') or '—'}]")

    return "\n".join(lines)


def resolve_hot_context(
    *,
    context_key: str,
    resolution: str = "",
    actor: str = "system",
    session_id: str = "",
    source_type: str = "",
    source_id: str = "",
    ttl_hours: int | float | str | None = DEFAULT_CONTEXT_TTL_HOURS,
) -> dict:
    existing = get_hot_context(context_key, include_events=False)
    if not existing:
        return {"error": f"Unknown context_key: {context_key}"}
    return capture_context_event(
        event_type="resolved",
        title=existing.get("title") or context_key,
        summary=resolution or existing.get("summary") or "Context resolved.",
        body=resolution,
        context_key=context_key,
        context_title=existing.get("title") or context_key,
        context_summary=existing.get("summary") or resolution or "",
        context_type=existing.get("context_type") or "topic",
        state="resolved",
        owner=existing.get("owner") or "",
        actor=actor,
        source_type=source_type or existing.get("source_type") or "",
        source_id=source_id or existing.get("source_id") or "",
        session_id=session_id or existing.get("session_id") or "",
        metadata=None,
        ttl_hours=ttl_hours,
    )
