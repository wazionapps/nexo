from __future__ import annotations
"""Shared transcript helpers for Deep Sleep and public MCP fallback tools."""

import json
import os
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

MIN_USER_MESSAGES = 3
DEFAULT_TRANSCRIPT_HOURS = 24
MAX_TRANSCRIPT_HOURS = 30 * 24

_SENSITIVE_PATTERNS = re.compile(
    r'(?:'
    r'sk-ant-[A-Za-z0-9_-]+'
    r'|shpat_[A-Fa-f0-9]+'
    r'|shpss_[A-Fa-f0-9]+'
    r'|sk-[A-Za-z0-9]{20,}'
    r'|ghp_[A-Za-z0-9]{36,}'
    r'|gho_[A-Za-z0-9]{36,}'
    r'|AIza[A-Za-z0-9_-]{35}'
    r'|ya29\.[A-Za-z0-9_-]+'
    r'|xox[bpsa]-[A-Za-z0-9-]+'
    r'|EAAG[A-Za-z0-9]+'
    r'|[Pp]assword\s*[:=]\s*\S+'
    r'|[Ss]ecret\s*[:=]\s*\S+'
    r'|[Tt]oken\s*[:=]\s*\S+'
    r'|[Aa]pi[_-]?[Kk]ey\s*[:=]\s*\S+'
    r')'
)


def _redact_sensitive(text: str) -> str:
    return _SENSITIVE_PATTERNS.sub("[REDACTED]", text)


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


def _truncate(text: str | None, limit: int = 240) -> str:
    if not text:
        return ""
    clean = str(text).strip()
    return clean if len(clean) <= limit else clean[: limit - 3] + "..."


def _session_identifier(client: str, session_file: str) -> str:
    return f"{client}:{session_file}"


def _claude_root() -> Path:
    return Path.home() / ".claude" / "projects"


def _codex_roots() -> list[Path]:
    return [
        Path.home() / ".codex" / "sessions",
        Path.home() / ".codex" / "archived_sessions",
    ]


def clamp_transcript_hours(hours: int | float | str | None) -> int:
    try:
        value = int(float(hours or DEFAULT_TRANSCRIPT_HOURS))
    except Exception:
        value = DEFAULT_TRANSCRIPT_HOURS
    return max(1, min(value, MAX_TRANSCRIPT_HOURS))


def find_claude_session_files() -> list[Path]:
    claude_dir = _claude_root()
    if not claude_dir.exists():
        return []
    return sorted(claude_dir.rglob("*.jsonl"))


def find_codex_session_files() -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for root in _codex_roots():
        if not root.exists():
            continue
        for jsonl in sorted(root.rglob("*.jsonl")):
            key = jsonl.name
            if key in seen:
                continue
            seen.add(key)
            files.append(jsonl)
    return files


def extract_claude_session(jsonl_path: Path) -> dict | None:
    messages = []
    tool_uses = []
    user_msg_count = 0

    try:
        with open(jsonl_path, "r") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = payload.get("type")
                if msg_type == "user":
                    content = payload.get("message", {}).get("content", "")
                    if isinstance(content, str) and content.strip():
                        if content.startswith("<system-reminder>"):
                            continue
                        messages.append(
                            {
                                "role": "user",
                                "index": line_no,
                                "text": _redact_sensitive(content[:5000]),
                                "uuid": payload.get("uuid", ""),
                            }
                        )
                        user_msg_count += 1
                elif msg_type in ("message", "assistant"):
                    msg = payload.get("message", {})
                    content_blocks = msg.get("content", [])
                    text_parts = []
                    for block in content_blocks:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            tool_input = block.get("input", {})
                            raw_file = (
                                tool_input.get("file_path", "")
                                or str(tool_input.get("command", ""))[:100]
                            ) if isinstance(tool_input, dict) else ""
                            tool_uses.append(
                                {
                                    "tool": block.get("name", ""),
                                    "input_keys": list(tool_input.keys()) if isinstance(tool_input, dict) else [],
                                    "file": _redact_sensitive(raw_file),
                                }
                            )
                    combined = "\n".join(part for part in text_parts if part).strip()
                    if combined:
                        messages.append(
                            {
                                "role": "assistant",
                                "index": line_no,
                                "text": _redact_sensitive(combined[:5000]),
                            }
                        )
    except Exception:
        return None

    if user_msg_count < MIN_USER_MESSAGES:
        return None

    return {
        "client": "claude_code",
        "session_file": _session_identifier("claude_code", jsonl_path.name),
        "display_name": jsonl_path.name,
        "session_path": str(jsonl_path),
        "message_count": len(messages),
        "user_message_count": user_msg_count,
        "tool_use_count": len(tool_uses),
        "messages": messages,
        "tool_uses": tool_uses,
        "source": "claude_projects",
    }


def extract_codex_session(jsonl_path: Path) -> dict | None:
    messages = []
    tool_uses = []
    user_msg_count = 0
    session_meta: dict = {}

    try:
        with open(jsonl_path, "r") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue

                item_type = payload.get("type")
                data = payload.get("payload", {})

                if item_type == "session_meta" and isinstance(data, dict):
                    session_meta = data
                    continue

                if item_type == "event_msg" and isinstance(data, dict) and data.get("type") == "user_message":
                    content = str(data.get("message", "") or "").strip()
                    if not content or content.startswith("<environment_context>"):
                        continue
                    messages.append(
                        {
                            "role": "user",
                            "index": line_no,
                            "text": _redact_sensitive(content[:5000]),
                        }
                    )
                    user_msg_count += 1
                    continue

                if item_type == "response_item" and isinstance(data, dict):
                    response_type = data.get("type")
                    role = data.get("role")
                    if response_type == "message" and role == "assistant":
                        text_parts = []
                        for block in data.get("content", []) or []:
                            if isinstance(block, dict) and block.get("type") == "output_text":
                                text_parts.append(str(block.get("text", "")))
                        combined = "\n".join(part for part in text_parts if part).strip()
                        if combined:
                            messages.append(
                                {
                                    "role": "assistant",
                                    "index": line_no,
                                    "text": _redact_sensitive(combined[:5000]),
                                }
                            )
                    elif response_type == "function_call":
                        tool_uses.append(
                            {
                                "tool": data.get("name", ""),
                                "input_keys": [],
                                "file": _redact_sensitive(str(data.get("arguments", ""))[:100]),
                            }
                        )
    except Exception:
        return None

    if user_msg_count < MIN_USER_MESSAGES:
        return None

    return {
        "client": "codex",
        "session_file": _session_identifier("codex", jsonl_path.name),
        "display_name": jsonl_path.name,
        "session_path": str(jsonl_path),
        "message_count": len(messages),
        "user_message_count": user_msg_count,
        "tool_use_count": len(tool_uses),
        "messages": messages,
        "tool_uses": tool_uses,
        "source": session_meta.get("source", "codex"),
        "cwd": session_meta.get("cwd", ""),
        "originator": session_meta.get("originator", ""),
        "session_uid": session_meta.get("id", ""),
    }


def collect_transcripts_since(since_iso: str, until_iso: str = "") -> list[dict]:
    since_dt = datetime.fromisoformat(since_iso)
    until_dt = datetime.fromisoformat(until_iso) if until_iso else datetime.now()
    sessions = []
    transcript_files: list[tuple[str, Path]] = [
        ("claude_code", path) for path in find_claude_session_files()
    ] + [
        ("codex", path) for path in find_codex_session_files()
    ]
    for client, session_file in transcript_files:
        try:
            mtime = datetime.fromtimestamp(session_file.stat().st_mtime)
        except OSError:
            continue
        if not (since_dt < mtime <= until_dt):
            continue
        session = extract_codex_session(session_file) if client == "codex" else extract_claude_session(session_file)
        if session:
            session["modified"] = mtime.isoformat()
            sessions.append(session)
    sessions.sort(key=lambda row: row["modified"])
    return sessions


def list_recent_transcripts(hours: int = DEFAULT_TRANSCRIPT_HOURS, client: str = "", limit: int = 10) -> list[dict]:
    window = clamp_transcript_hours(hours)
    since = datetime.now() - timedelta(hours=window)
    sessions = collect_transcripts_since(since.isoformat())
    filtered = []
    for item in sessions:
        if client and item.get("client") != client:
            continue
        filtered.append(item)
    filtered.sort(key=lambda row: row.get("modified", ""), reverse=True)
    return filtered[: max(1, int(limit or 10))]


def search_transcripts(query: str, *, hours: int = DEFAULT_TRANSCRIPT_HOURS, client: str = "", limit: int = 10) -> list[dict]:
    rows = list_recent_transcripts(hours=hours, client=client, limit=200)
    query_tokens = _tokenize(query)
    if not query_tokens:
        return rows[: max(1, int(limit or 10))]

    matches: list[dict] = []
    cutoff_seconds = clamp_transcript_hours(hours) * 3600
    now = datetime.now().timestamp()
    for item in rows:
        snippets = []
        best_score = 0.0
        for message in item.get("messages") or []:
            text = str(message.get("text", "") or "")
            score = _score_text_match(query_tokens, text)
            if score <= 0:
                continue
            best_score = max(best_score, score)
            snippets.append(
                {
                    "role": message.get("role", ""),
                    "index": message.get("index", 0),
                    "snippet": _truncate(text, 220),
                    "score": round(score, 4),
                }
            )
        meta_text = " ".join(
            [
                str(item.get("display_name", "") or ""),
                str(item.get("session_file", "") or ""),
                str(item.get("source", "") or ""),
                str(item.get("cwd", "") or ""),
            ]
        )
        meta_score = _score_text_match(query_tokens, meta_text)
        best_score = max(best_score, meta_score)
        if best_score <= 0:
            continue
        modified = item.get("modified", "")
        try:
            modified_ts = datetime.fromisoformat(modified).timestamp()
        except Exception:
            modified_ts = now
        recency = max(0.0, 1.0 - ((now - modified_ts) / max(1, cutoff_seconds)))
        item["_score"] = round(best_score + recency * 0.35, 4)
        item["matched_messages"] = sorted(snippets, key=lambda row: row["score"], reverse=True)[:3]
        matches.append(item)

    matches.sort(key=lambda row: (row.get("_score", 0), row.get("modified", "")), reverse=True)
    return matches[: max(1, int(limit or 10))]


def load_transcript(session_ref: str = "", transcript_path: str = "", client: str = "") -> dict | None:
    ref = str(session_ref or "").strip()
    path_ref = str(transcript_path or "").strip()

    transcript_files: list[tuple[str, Path]] = [
        ("claude_code", path) for path in find_claude_session_files()
    ] + [
        ("codex", path) for path in find_codex_session_files()
    ]
    for detected_client, path in transcript_files:
        if client and detected_client != client:
            continue
        if path_ref:
            try:
                if Path(path_ref).expanduser().resolve() != path.resolve():
                    continue
            except Exception:
                continue
        session = extract_codex_session(path) if detected_client == "codex" else extract_claude_session(path)
        if not session:
            continue
        if ref:
            if ref not in {
                str(session.get("session_file", "")),
                str(session.get("display_name", "")),
                str(session.get("session_uid", "")),
                str(path),
            }:
                continue
        try:
            session["modified"] = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
        except OSError:
            session["modified"] = ""
        return session
    return None
