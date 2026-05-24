from __future__ import annotations

"""Structured transcript metadata index for pre-answer continuity.

This index stores compact, redacted metadata and short snippets only. Raw JSONL
transcripts remain a last-resort fallback and are not copied into the database.
"""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from db import get_db
from transcript_utils import (
    DEFAULT_TRANSCRIPT_HOURS,
    MAX_TRANSCRIPT_HOURS,
    _score_text_match,
    _tokenize,
    _truncate,
    find_claude_session_files,
    find_codex_session_files,
    list_recent_transcripts,
)


def _ensure_transcript_index_table() -> None:
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transcript_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_client TEXT NOT NULL,
            conversation_id TEXT DEFAULT '',
            session_id TEXT DEFAULT '',
            message_count INTEGER DEFAULT 0,
            user_message_count INTEGER DEFAULT 0,
            first_user_at TEXT DEFAULT '',
            last_user_at TEXT DEFAULT '',
            path_ref TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            indexed_at TEXT DEFAULT (datetime('now')),
            modified_at TEXT DEFAULT '',
            content_hash TEXT NOT NULL,
            sanitized_summary TEXT DEFAULT '',
            metadata_json TEXT DEFAULT '{}',
            UNIQUE(source_client, path_ref)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_transcript_index_client_modified ON transcript_index(source_client, modified_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_transcript_index_session ON transcript_index(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_transcript_index_conversation ON transcript_index(conversation_id)")
    conn.commit()


def _session_identity(session: dict[str, Any]) -> tuple[str, str, str, str]:
    source_client = str(session.get("client") or "")
    session_id = str(session.get("session_uid") or session.get("session_file") or session.get("display_name") or "")
    conversation_id = str(session.get("conversation_id") or session.get("session_uid") or session_id)
    path_ref = str(session.get("session_path") or session.get("path") or "")
    return source_client, session_id, conversation_id, path_ref


def _session_modified_at(session: dict[str, Any]) -> str:
    modified = str(session.get("modified") or "").strip()
    if modified:
        return modified
    path_ref = str(session.get("session_path") or "").strip()
    if not path_ref:
        return ""
    try:
        return datetime.fromtimestamp(Path(path_ref).stat().st_mtime).isoformat()
    except OSError:
        return ""


def _content_hash(session: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(str(session.get("client") or "").encode())
    digest.update(str(session.get("session_file") or "").encode())
    for message in session.get("messages") or []:
        digest.update(str(message.get("role") or "").encode())
        digest.update(str(message.get("index") or "").encode())
        digest.update(str(message.get("text") or "").encode())
    return digest.hexdigest()


def _sanitized_summary(session: dict[str, Any], *, limit: int = 900) -> str:
    user_snippets: list[str] = []
    assistant_snippets: list[str] = []
    for message in session.get("messages") or []:
        role = str(message.get("role") or "")
        text = _truncate(str(message.get("text") or ""), 180)
        if not text:
            continue
        if role == "user" and len(user_snippets) < 3:
            user_snippets.append(text)
        elif role == "assistant" and len(assistant_snippets) < 2:
            assistant_snippets.append(text)
    parts = []
    if user_snippets:
        parts.append("user: " + " | ".join(user_snippets))
    if assistant_snippets:
        parts.append("assistant: " + " | ".join(assistant_snippets))
    summary = " ".join(parts)
    return _truncate(summary, limit)


def _row_ref_matches(query: str, row: dict[str, Any]) -> bool:
    clean = str(query or "").strip().lower()
    if len(clean) < 6:
        return False
    values = [
        row.get("session_id"),
        row.get("conversation_id"),
        row.get("display_name"),
        row.get("path_ref"),
        Path(str(row.get("path_ref") or "")).name,
        Path(str(row.get("path_ref") or "")).stem,
    ]
    for value in values:
        candidate = str(value or "").strip().lower()
        if not candidate:
            continue
        if candidate.startswith(clean):
            return True
        if candidate.split(":")[-1].startswith(clean):
            return True
    return False


def index_transcript_session(session: dict[str, Any]) -> dict[str, Any]:
    """Upsert a single transcript metadata row and return it."""
    _ensure_transcript_index_table()
    source_client, session_id, conversation_id, path_ref = _session_identity(session)
    if not source_client or not path_ref:
        raise ValueError("transcript session requires client and session_path")

    metadata = {
        "source": session.get("source", ""),
        "cwd": session.get("cwd", ""),
        "originator": session.get("originator", ""),
        "tool_use_count": session.get("tool_use_count", 0),
    }
    conn = get_db()
    conn.execute(
        """
        INSERT INTO transcript_index (
            source_client, conversation_id, session_id, message_count,
            user_message_count, first_user_at, last_user_at, path_ref,
            display_name, indexed_at, modified_at, content_hash,
            sanitized_summary, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, ?)
        ON CONFLICT(source_client, path_ref) DO UPDATE SET
            conversation_id = excluded.conversation_id,
            session_id = excluded.session_id,
            message_count = excluded.message_count,
            user_message_count = excluded.user_message_count,
            first_user_at = excluded.first_user_at,
            last_user_at = excluded.last_user_at,
            display_name = excluded.display_name,
            indexed_at = datetime('now'),
            modified_at = excluded.modified_at,
            content_hash = excluded.content_hash,
            sanitized_summary = excluded.sanitized_summary,
            metadata_json = excluded.metadata_json
        """,
        (
            source_client,
            conversation_id,
            session_id,
            int(session.get("message_count") or len(session.get("messages") or [])),
            int(session.get("user_message_count") or 0),
            str(session.get("first_user_at") or ""),
            str(session.get("last_user_at") or ""),
            path_ref,
            str(session.get("display_name") or ""),
            _session_modified_at(session),
            _content_hash(session),
            _sanitized_summary(session),
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM transcript_index WHERE source_client = ? AND path_ref = ?",
        (source_client, path_ref),
    ).fetchone()
    return dict(row) if row else {}


def index_recent_transcripts(
    *,
    hours: int = DEFAULT_TRANSCRIPT_HOURS,
    client: str = "",
    limit: int = 200,
    min_user_messages: int = 1,
) -> list[dict[str, Any]]:
    rows = list_recent_transcripts(
        hours=hours,
        client=client,
        limit=limit,
        min_user_messages=min_user_messages,
    )
    indexed = []
    for session in rows:
        try:
            indexed.append(index_transcript_session(session))
        except Exception:
            continue
    return indexed


def _latest_source_modified_ts(client: str = "") -> float:
    paths: list[Path] = []
    if not client or client == "claude_code":
        paths.extend(find_claude_session_files())
    if not client or client == "codex":
        paths.extend(find_codex_session_files())
    latest = 0.0
    for path in paths:
        try:
            latest = max(latest, path.stat().st_mtime)
        except OSError:
            continue
    return latest


def _parse_iso_ts(value: str) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except Exception:
        return 0.0


def ensure_transcript_index(
    *,
    hours: int = MAX_TRANSCRIPT_HOURS,
    client: str = "",
    limit: int = 1000,
    min_user_messages: int = 1,
    force: bool = False,
) -> dict[str, Any]:
    """Keep the compact transcript DB index warm enough for fast lookup.

    This is intentionally bounded. Raw JSONL remains the source of truth, but
    normal MCP searches should hit this table before falling back to slow file
    scans.
    """
    _ensure_transcript_index_table()
    conn = get_db()
    params: list[Any] = []
    where = "1=1"
    if client:
        where += " AND source_client = ?"
        params.append(client)
    before = int(conn.execute(f"SELECT COUNT(*) AS total FROM transcript_index WHERE {where}", tuple(params)).fetchone()["total"] or 0)
    latest_indexed = str(conn.execute(
        f"SELECT MAX(modified_at) AS latest FROM transcript_index WHERE {where}",
        tuple(params),
    ).fetchone()["latest"] or "")
    latest_source_ts = _latest_source_modified_ts(client)
    latest_indexed_ts = _parse_iso_ts(latest_indexed)
    stale = bool(latest_source_ts and latest_source_ts > latest_indexed_ts + 1.0)
    should_index = bool(force or before == 0 or stale)
    indexed: list[dict[str, Any]] = []
    if should_index:
        indexed = index_recent_transcripts(
            hours=hours,
            client=client,
            limit=limit,
            min_user_messages=min_user_messages,
        )
    after = int(conn.execute(f"SELECT COUNT(*) AS total FROM transcript_index WHERE {where}", tuple(params)).fetchone()["total"] or 0)
    return {
        "ok": True,
        "before": before,
        "after": after,
        "indexed": len(indexed),
        "forced": bool(force),
        "stale": stale,
        "hours": hours,
        "client": client,
    }


def search_transcript_index(
    query: str = "",
    *,
    hours: int = 72,
    client: str = "",
    limit: int = 10,
) -> list[dict[str, Any]]:
    _ensure_transcript_index_table()
    conn = get_db()
    params: list[Any] = []
    where = "1=1"
    if client:
        where += " AND source_client = ?"
        params.append(client)
    rows = [dict(row) for row in conn.execute(
        f"SELECT * FROM transcript_index WHERE {where} ORDER BY modified_at DESC LIMIT 5000",
        tuple(params),
    ).fetchall()]

    cutoff = datetime.now() - timedelta(hours=max(1, int(hours or 72)))
    query_tokens = _tokenize(query)
    matches = []
    for row in rows:
        modified = str(row.get("modified_at") or "")
        if modified:
            try:
                if datetime.fromisoformat(modified) < cutoff:
                    continue
            except Exception:
                pass
        if not query_tokens:
            row["_score"] = 0.0
            matches.append(row)
            continue
        haystack = " ".join(
            str(row.get(field) or "")
            for field in ("sanitized_summary", "display_name", "session_id", "conversation_id", "path_ref", "metadata_json")
        )
        score = _score_text_match(query_tokens, haystack)
        if _row_ref_matches(query, row):
            score = max(score, 2.0)
        if score <= 0:
            continue
        row["_score"] = round(score, 4)
        matches.append(row)

    matches.sort(key=lambda row: (float(row.get("_score") or 0), str(row.get("modified_at") or "")), reverse=True)
    return matches[: max(1, int(limit or 10))]
