"""Public MCP tools for transcript fallback access."""

from __future__ import annotations

from transcript_utils import (
    clamp_transcript_hours,
    list_recent_transcripts,
    load_transcript,
    search_transcripts,
)


def handle_transcript_search(query: str = "", hours: int = 24, client: str = "", limit: int = 10) -> str:
    """Search recent Claude Code / Codex transcripts as a fallback when memory is insufficient."""
    window = clamp_transcript_hours(hours)
    rows = search_transcripts(query or "", hours=window, client=(client or "").strip(), limit=limit)
    if not rows:
        scope = f"query='{query}'" if query else "recent transcripts"
        return f"No transcript matches for {scope} in the last {window}h."

    lines = [f"TRANSCRIPTS ({len(rows)}) — last {window}h"]
    for item in rows:
        lines.append(
            f"- {item.get('session_file')}: [{item.get('client')}] {item.get('display_name')} "
            f"(modified={item.get('modified')}, messages={item.get('message_count')}, user={item.get('user_message_count')})"
        )
        if item.get("cwd"):
            lines.append(f"  cwd: {item['cwd']}")
        if item.get("session_uid"):
            lines.append(f"  session_uid: {item['session_uid']}")
        for snippet in item.get("matched_messages") or []:
            lines.append(
                f"  [{snippet.get('role')}#{snippet.get('index')}] {snippet.get('snippet')}"
            )
    return "\n".join(lines)


def handle_transcript_recent(hours: int = 24, client: str = "", limit: int = 10) -> str:
    """List recent transcripts without searching full text."""
    window = clamp_transcript_hours(hours)
    rows = list_recent_transcripts(hours=window, client=(client or "").strip(), limit=limit)
    if not rows:
        return f"No transcripts found in the last {window}h."

    lines = [f"RECENT TRANSCRIPTS ({len(rows)}) — last {window}h"]
    for item in rows:
        lines.append(
            f"- {item.get('session_file')}: [{item.get('client')}] {item.get('display_name')} "
            f"(modified={item.get('modified')}, messages={item.get('message_count')}, user={item.get('user_message_count')})"
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
    )
    if not transcript:
        target = session_ref or transcript_path or "(empty ref)"
        return f"Transcript not found for {target}."

    limit = max(1, min(int(max_messages or 80), 200))
    messages = transcript.get("messages") or []
    truncated = len(messages) > limit
    visible = messages[-limit:] if truncated else messages

    lines = [
        f"TRANSCRIPT {transcript.get('session_file')}",
        f"Client: {transcript.get('client')}",
        f"Display: {transcript.get('display_name')}",
        f"Path: {transcript.get('session_path')}",
        f"Modified: {transcript.get('modified')}",
        f"Messages: {transcript.get('message_count')} (user={transcript.get('user_message_count')}, tools={transcript.get('tool_use_count')})",
    ]
    if transcript.get("cwd"):
        lines.append(f"CWD: {transcript.get('cwd')}")
    if transcript.get("session_uid"):
        lines.append(f"Session UID: {transcript.get('session_uid')}")
    if truncated:
        lines.append(f"Showing last {limit} messages.")

    for message in visible:
        role = str(message.get("role") or "?").upper()
        index = message.get("index", "?")
        text = str(message.get("text") or "").strip()
        lines.append("")
        lines.append(f"[{role} #{index}]")
        lines.append(text)

    return "\n".join(lines)
