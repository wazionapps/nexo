from __future__ import annotations

"""Pre-compaction auto-flush helpers."""

import json
from pathlib import Path

from db import get_db
from db._hot_context import capture_context_event
from memory_backends import get_backend


def init_tables() -> None:
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS session_auto_flush (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            task TEXT DEFAULT '',
            current_goal TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            next_step TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}',
            source TEXT DEFAULT 'pre-compact-hook',
            backend_key TEXT DEFAULT 'sqlite',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_session_auto_flush_sid ON session_auto_flush(session_id);
        CREATE INDEX IF NOT EXISTS idx_session_auto_flush_created ON session_auto_flush(created_at);
        """
    )
    conn.commit()


def _load_tool_entries(log_file: str = "", last_diary_ts: str = "") -> list[dict]:
    path = Path(log_file).expanduser()
    if not path.is_file():
        return []
    entries: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                item = json.loads(raw_line)
            except Exception:
                continue
            ts = str(item.get("timestamp", "") or "")
            if last_diary_ts and ts and ts < last_diary_ts:
                continue
            entries.append(item)
    return entries


def _derive_bundle(task: str, current_goal: str, entries: list[dict]) -> dict:
    tool_counts: dict[str, int] = {}
    modified_files: list[str] = []
    git_actions: list[str] = []
    last_briefs: list[str] = []
    for entry in entries:
        name = str(entry.get("tool_name", "?") or "?")
        tool_counts[name] = tool_counts.get(name, 0) + 1
        payload = entry.get("tool_input") or {}
        if isinstance(payload, dict):
            file_path = str(payload.get("file_path") or payload.get("path") or "").strip()
            if file_path:
                modified_files.append(file_path.split("/")[-1])
            if name == "Bash":
                cmd = str(payload.get("command") or "").strip()
                if cmd:
                    if "git " in cmd:
                        git_actions.append(cmd[:120])
                    if len(last_briefs) < 5:
                        last_briefs.append(cmd[:120])
            else:
                for _, value in list(payload.items())[:1]:
                    text = str(value).strip()
                    if text:
                        last_briefs.append(text[:120])
                        break
    top_tools = sorted(tool_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    top_tools_str = ", ".join(f"{name} x{count}" for name, count in top_tools) or "no tool activity"
    unique_files = sorted({name for name in modified_files if name})[:12]
    file_str = ", ".join(unique_files) if unique_files else "no file writes detected"
    next_step = (current_goal or "").strip() or (task or "").strip() or "Resume from tool logs and hot context."
    summary = (
        f"Auto-flush captured {len(entries)} tool calls. "
        f"Top tools: {top_tools_str}. "
        f"Files: {file_str}."
    )
    return {
        "summary": summary,
        "next_step": next_step[:400],
        "metadata": {
            "entry_count": len(entries),
            "top_tools": top_tools,
            "modified_files": unique_files,
            "git_actions": git_actions[:10],
            "recent_inputs": last_briefs[:10],
        },
    }


def record_auto_flush(
    *,
    session_id: str,
    task: str = "",
    current_goal: str = "",
    log_file: str = "",
    last_diary_ts: str = "",
    source: str = "pre-compact-hook",
) -> dict:
    init_tables()
    entries = _load_tool_entries(log_file=log_file, last_diary_ts=last_diary_ts)
    if not entries and not task.strip() and not current_goal.strip():
        return {"skipped": True, "reason": "no task and no tool activity"}

    bundle = _derive_bundle(task, current_goal, entries)
    conn = get_db()
    backend = get_backend()
    cursor = conn.execute(
        """
        INSERT INTO session_auto_flush (
            session_id, task, current_goal, summary, next_step, metadata, source, backend_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id.strip() or "unknown",
            task.strip(),
            current_goal.strip(),
            bundle["summary"],
            bundle["next_step"],
            json.dumps(bundle["metadata"], ensure_ascii=True, sort_keys=True),
            source.strip() or "pre-compact-hook",
            backend.key,
        ),
    )
    conn.commit()
    flush_id = int(cursor.lastrowid)

    try:
        capture_context_event(
            event_type="auto_flush",
            title=(task or current_goal or f"auto-flush {session_id}")[:160],
            summary=bundle["summary"][:600],
            body=bundle["next_step"][:1600],
            context_key=f"session:{session_id}",
            context_title=(task or current_goal or session_id)[:160],
            context_summary=bundle["summary"][:600],
            context_type="session",
            state="active",
            actor="system",
            source_type="session",
            source_id=session_id,
            session_id=session_id,
            metadata={"auto_flush_id": flush_id, **bundle["metadata"]},
        )
    except Exception:
        pass

    try:
        import cognitive

        cognitive.ingest(
            f"Auto-flush for session {session_id}. {bundle['summary']} Next step: {bundle['next_step']}",
            "auto_flush",
            f"AF{flush_id}",
            (task or current_goal or f"auto-flush {session_id}")[:120],
            "nexo",
        )
    except Exception:
        pass

    row = conn.execute("SELECT * FROM session_auto_flush WHERE id = ?", (flush_id,)).fetchone()
    result = dict(row) if row else {"id": flush_id}
    try:
        result["metadata"] = json.loads(result.get("metadata") or "{}")
    except Exception:
        result["metadata"] = {}
    return result


def list_auto_flushes(session_id: str = "", limit: int = 20) -> list[dict]:
    init_tables()
    conn = get_db()
    if session_id.strip():
        rows = conn.execute(
            "SELECT * FROM session_auto_flush WHERE session_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (session_id.strip(), max(1, int(limit or 20))),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM session_auto_flush ORDER BY created_at DESC, id DESC LIMIT ?",
            (max(1, int(limit or 20)),),
        ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.get("metadata") or "{}")
        except Exception:
            item["metadata"] = {}
        results.append(item)
    return results


def auto_flush_stats(days: int = 7) -> dict:
    init_tables()
    conn = get_db()
    rows = conn.execute(
        "SELECT source, COUNT(*) AS cnt FROM session_auto_flush WHERE created_at >= datetime('now', ?) GROUP BY source",
        (f"-{max(1, int(days or 7))} days",),
    ).fetchall()
    total = int(
        conn.execute(
            "SELECT COUNT(*) FROM session_auto_flush WHERE created_at >= datetime('now', ?)",
            (f"-{max(1, int(days or 7))} days",),
        ).fetchone()[0]
    )
    return {
        "window_days": max(1, int(days or 7)),
        "total": total,
        "by_source": {row["source"]: row["cnt"] for row in rows},
        "backend": get_backend().key,
    }
