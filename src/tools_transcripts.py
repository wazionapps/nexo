"""Public MCP tools for transcript fallback access."""

from __future__ import annotations

from transcript_utils import (
    clamp_transcript_hours,
    list_recent_transcripts,
    load_transcript,
    search_transcripts,
)
from transcript_index import ensure_transcript_index, search_transcript_index

try:
    from semantic_layers import redact_value as _redact_value
except Exception:  # pragma: no cover - bootstrap fallback
    def _redact_value(value, *, max_chars=4000):
        return str(value or "")[:max_chars]


def _safe(value: object, *, max_chars: int = 500) -> str:
    return _redact_value(value, max_chars=max_chars)


def _safe_message_preview(value: object) -> str:
    text = _safe(value, max_chars=700).strip()
    return text or "[empty]"


def handle_transcript_search(query: str = "", hours: int = 24, client: str = "", limit: int = 10) -> str:
    """Search recent Claude Code / Codex transcripts as a fallback when memory is insufficient."""
    window = clamp_transcript_hours(hours)
    clean_client = (client or "").strip()
    ensure_transcript_index(
        hours=window,
        client=clean_client,
        limit=max(200, min(2000, int(limit or 10) * 50)),
        min_user_messages=1,
    )
    rows = search_transcript_index(query or "", hours=window, client=clean_client, limit=limit)
    source = "index"
    if not rows:
        rows = search_transcripts(
            query or "",
            hours=window,
            client=clean_client,
            limit=limit,
            min_user_messages=1,
        )
        source = "raw"
    if not rows:
        scope = f"query='{query}'" if query else "recent transcripts"
        return f"No transcript matches for {scope} in the last {window}h."

    lines = [f"TRANSCRIPTS ({len(rows)}) — last {window}h ({source})"]
    for item in rows:
        session_file = _safe(item.get("session_file") or item.get("session_id") or item.get("display_name"))
        display_name = _safe(item.get("display_name") or item.get("path_ref") or item.get("session_path"))
        modified = _safe(item.get("modified") or item.get("modified_at"))
        lines.append(
            f"- {session_file}: [{_safe(item.get('client') or item.get('source_client'))}] {display_name} "
            f"(modified={modified}, messages={item.get('message_count')}, user={item.get('user_message_count')})"
        )
        if item.get("cwd"):
            lines.append(f"  cwd: {_safe(item['cwd'])}")
        if item.get("session_uid"):
            lines.append(f"  session_uid: {_safe(item['session_uid'])}")
        if item.get("conversation_id") and item.get("conversation_id") != item.get("session_id"):
            lines.append(f"  conversation_id: {_safe(item['conversation_id'])}")
        if item.get("path_ref"):
            lines.append(f"  path: {_safe(item['path_ref'])}")
        if item.get("sanitized_summary"):
            lines.append(f"  summary: {_safe(item['sanitized_summary'], max_chars=700)}")
        for snippet in item.get("matched_messages") or []:
            lines.append(
                f"  [{_safe(snippet.get('role'), max_chars=40)}#{snippet.get('index')}] "
                f"{_safe(snippet.get('snippet'), max_chars=700)}"
            )
    return "\n".join(lines)


def handle_transcript_recent(hours: int = 24, client: str = "", limit: int = 10) -> str:
    """List recent transcripts without searching full text."""
    window = clamp_transcript_hours(hours)
    clean_client = (client or "").strip()
    ensure_transcript_index(
        hours=window,
        client=clean_client,
        limit=max(200, min(2000, int(limit or 10) * 50)),
        min_user_messages=1,
    )
    rows = search_transcript_index("", hours=window, client=clean_client, limit=limit)
    source = "index"
    if not rows:
        rows = list_recent_transcripts(hours=window, client=clean_client, limit=limit, min_user_messages=1)
        source = "raw"
    if not rows:
        return f"No transcripts found in the last {window}h."

    lines = [f"RECENT TRANSCRIPTS ({len(rows)}) — last {window}h ({source})"]
    for item in rows:
        session_file = _safe(item.get("session_file") or item.get("session_id") or item.get("display_name"))
        display_name = _safe(item.get("display_name") or item.get("path_ref") or item.get("session_path"))
        modified = _safe(item.get("modified") or item.get("modified_at"))
        lines.append(
            f"- {session_file}: [{_safe(item.get('client') or item.get('source_client'))}] {display_name} "
            f"(modified={modified}, messages={item.get('message_count')}, user={item.get('user_message_count')})"
        )
    return "\n".join(lines)


def handle_transcript_read(
    session_ref: str = "",
    transcript_path: str = "",
    client: str = "",
    max_messages: int = 80,
) -> str:
    """Read a transcript in fallback mode. Accepts session_file, display name, session_uid or exact path."""
    transcript = load_transcript(
        session_ref=(session_ref or "").strip(),
        transcript_path=(transcript_path or "").strip(),
        client=(client or "").strip(),
        min_user_messages=1,
    )
    if not transcript:
        target = session_ref or transcript_path or "(empty ref)"
        return f"Transcript not found for {target}."

    limit = max(1, min(int(max_messages or 80), 200))
    messages = transcript.get("messages") or []
    truncated = len(messages) > limit
    visible = messages[-limit:] if truncated else messages

    lines = [
        f"TRANSCRIPT {_safe(transcript.get('session_file'))}",
        f"Client: {_safe(transcript.get('client'))}",
        f"Display: {_safe(transcript.get('display_name'))}",
        f"Path: {_safe(transcript.get('session_path'))}",
        f"Modified: {_safe(transcript.get('modified'))}",
        f"Messages: {transcript.get('message_count')} (user={transcript.get('user_message_count')}, tools={transcript.get('tool_use_count')})",
    ]
    if transcript.get("cwd"):
        lines.append(f"CWD: {_safe(transcript.get('cwd'))}")
    if transcript.get("session_uid"):
        lines.append(f"Session UID: {_safe(transcript.get('session_uid'))}")
    if truncated:
        lines.append(f"Showing last {limit} messages.")

    for message in visible:
        role = str(message.get("role") or "?").upper()
        index = message.get("index", "?")
        text = _safe_message_preview(message.get("text"))
        lines.append("")
        lines.append(f"[{role} #{index}]")
        lines.append(text)

    return "\n".join(lines)
