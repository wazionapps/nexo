from __future__ import annotations

"""Coverage helpers for transcript fallback visibility."""

import json
from pathlib import Path
from typing import Iterable

from transcript_utils import (
    MIN_USER_MESSAGES,
    extract_claude_session,
    extract_codex_session,
)


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
    except OSError:
        return []
    return rows


def _codex_user_message_count(rows: Iterable[dict]) -> int:
    count = 0
    for payload in rows:
        data = payload.get("payload", {})
        if payload.get("type") == "event_msg" and isinstance(data, dict) and data.get("type") == "user_message":
            content = str(data.get("message", "") or "").strip()
            if content and not content.startswith("<environment_context>"):
                count += 1
    return count


def _claude_user_message_count(rows: Iterable[dict]) -> int:
    count = 0
    for payload in rows:
        if payload.get("type") != "user":
            continue
        content = payload.get("message", {}).get("content", "")
        if isinstance(content, str) and content.strip() and not content.startswith("<system-reminder>"):
            count += 1
    return count


def analyze_transcript_file(path: str | Path, client: str, *, min_user_messages: int = MIN_USER_MESSAGES) -> dict:
    transcript_path = Path(path)
    clean_client = str(client or "").strip()
    rows = _read_jsonl(transcript_path)
    if not rows:
        return {
            "path": str(transcript_path),
            "client": clean_client,
            "covered": False,
            "reason": "unreadable_or_empty",
            "user_message_count": 0,
        }

    if clean_client == "codex":
        user_count = _codex_user_message_count(rows)
        extracted = extract_codex_session(transcript_path)
    elif clean_client == "claude_code":
        user_count = _claude_user_message_count(rows)
        extracted = extract_claude_session(transcript_path)
    else:
        return {
            "path": str(transcript_path),
            "client": clean_client,
            "covered": False,
            "reason": "unknown_client",
            "user_message_count": 0,
        }

    if extracted:
        return {
            "path": str(transcript_path),
            "client": clean_client,
            "covered": True,
            "reason": "covered",
            "session_file": extracted.get("session_file", ""),
            "session_uid": extracted.get("session_uid", ""),
            "user_message_count": user_count,
        }
    if user_count < min_user_messages:
        reason = "below_min_user_messages"
    else:
        reason = "parse_failed"
    return {
        "path": str(transcript_path),
        "client": clean_client,
        "covered": False,
        "reason": reason,
        "user_message_count": user_count,
    }


def _conversation_id(value: dict) -> str:
    return str(value.get("id") or value.get("conversationId") or value.get("conversation_id") or "").strip()


def build_transcript_coverage_report(
    transcript_files: Iterable[tuple[str, str | Path]],
    *,
    desktop_conversations: Iterable[dict] | None = None,
    min_user_messages: int = MIN_USER_MESSAGES,
) -> dict:
    rows = [
        analyze_transcript_file(path, client, min_user_messages=min_user_messages)
        for client, path in transcript_files
    ]
    desktop_ids = {_conversation_id(item) for item in (desktop_conversations or []) if _conversation_id(item)}
    covered_session_refs = {
        str(row.get("session_uid") or row.get("session_file") or "").strip()
        for row in rows
        if row.get("covered")
    }
    desktop_without_transcript = sorted(
        conv_id for conv_id in desktop_ids
        if conv_id not in covered_session_refs
    )
    reasons: dict[str, int] = {}
    for row in rows:
        reason = str(row.get("reason") or "unknown")
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "ok": not desktop_without_transcript and reasons.get("parse_failed", 0) == 0,
        "counts": {
            "files": len(rows),
            "covered": sum(1 for row in rows if row.get("covered")),
            "not_covered": sum(1 for row in rows if not row.get("covered")),
            "desktop_conversations": len(desktop_ids),
            "desktop_without_transcript": len(desktop_without_transcript),
        },
        "reasons": reasons,
        "files": rows,
        "desktop_without_transcript": desktop_without_transcript,
    }
