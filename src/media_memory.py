from __future__ import annotations

"""Lightweight multimodal memory reference layer."""

import json
import mimetypes
import os
from pathlib import Path

from db import get_db
from memory_backends import get_backend


def _safe_json_loads(raw: str | dict | None) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(str(raw))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _serialize_metadata(metadata: dict | None) -> str:
    if not metadata:
        return "{}"
    return json.dumps(metadata, ensure_ascii=True, sort_keys=True)


def _serialize_tags(tags: str | list[str] | tuple[str, ...] | None) -> str:
    if isinstance(tags, str):
        items = [part.strip() for part in tags.replace("\n", ",").split(",")]
    else:
        items = [str(item).strip() for item in (tags or [])]
    clean = sorted({item for item in items if item})
    return ",".join(clean)


def _normalized_path(file_path: str = "") -> str:
    raw = (file_path or "").strip()
    if not raw:
        return ""
    return str(Path(raw).expanduser().resolve())


def _detect_media_type(file_path: str = "", url: str = "", mime_type: str = "") -> str:
    mime_guess = mime_type or mimetypes.guess_type(file_path or url or "")[0] or ""
    lowered = mime_guess.lower()
    if lowered.startswith("image/"):
        return "image"
    if lowered.startswith("audio/"):
        return "audio"
    if lowered.startswith("video/"):
        return "video"
    suffix = (Path(file_path or url).suffix or "").lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
        return "image"
    if suffix in {".mp3", ".wav", ".m4a", ".aac", ".ogg"}:
        return "audio"
    if suffix in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
        return "video"
    if suffix in {".pdf"}:
        return "document"
    return "file"


def init_tables() -> None:
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS media_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT DEFAULT '',
            url TEXT DEFAULT '',
            media_type TEXT NOT NULL DEFAULT 'file',
            mime_type TEXT DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            description TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            domain TEXT DEFAULT '',
            source_type TEXT DEFAULT '',
            source_id TEXT DEFAULT '',
            backend_key TEXT DEFAULT 'sqlite',
            metadata TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_media_memories_type ON media_memories(media_type);
        CREATE INDEX IF NOT EXISTS idx_media_memories_domain ON media_memories(domain);
        CREATE INDEX IF NOT EXISTS idx_media_memories_source ON media_memories(source_type, source_id);
        CREATE INDEX IF NOT EXISTS idx_media_memories_path ON media_memories(file_path);
        CREATE INDEX IF NOT EXISTS idx_media_memories_url ON media_memories(url);
        """
    )
    conn.commit()


def _safe_cognitive_ingest(text: str, source_id: str, title: str, domain: str) -> None:
    try:
        import cognitive

        cognitive.ingest(text, "media_memory", source_id, title[:120], domain)
    except Exception:
        pass


def _row_to_dict(row) -> dict:
    result = dict(row)
    result["metadata"] = _safe_json_loads(result.get("metadata"))
    result["tags_list"] = [item for item in str(result.get("tags", "")).split(",") if item]
    return result


def add_media_memory(
    *,
    file_path: str = "",
    url: str = "",
    title: str = "",
    description: str = "",
    tags: str | list[str] | tuple[str, ...] | None = None,
    domain: str = "",
    source_type: str = "",
    source_id: str = "",
    metadata: str | dict | None = None,
) -> dict:
    init_tables()
    normalized_path = _normalized_path(file_path)
    clean_url = (url or "").strip()
    if not normalized_path and not clean_url:
        return {"error": "file_path or url is required"}

    conn = get_db()
    existing = None
    if normalized_path:
        existing = conn.execute(
            "SELECT * FROM media_memories WHERE file_path = ? LIMIT 1",
            (normalized_path,),
        ).fetchone()
    elif clean_url:
        existing = conn.execute(
            "SELECT * FROM media_memories WHERE url = ? LIMIT 1",
            (clean_url,),
        ).fetchone()

    metadata_dict = _safe_json_loads(metadata)
    if normalized_path and os.path.exists(normalized_path):
        stat = os.stat(normalized_path)
        metadata_dict.setdefault("size_bytes", int(stat.st_size))
        metadata_dict.setdefault("mtime_epoch", float(stat.st_mtime))

    resolved_title = (title or Path(normalized_path or clean_url).name or "media-memory").strip()
    resolved_tags = _serialize_tags(tags)
    media_type = _detect_media_type(normalized_path, clean_url, metadata_dict.get("mime_type", ""))
    mime_type = str(metadata_dict.get("mime_type", mimetypes.guess_type(normalized_path or clean_url)[0] or ""))

    if existing:
        merged_description = description.strip() or existing["description"] or ""
        merged_tags = _serialize_tags(",".join(filter(None, [existing["tags"], resolved_tags])))
        merged_metadata = _safe_json_loads(existing["metadata"])
        merged_metadata.update(metadata_dict)
        conn.execute(
            """
            UPDATE media_memories
               SET title = ?,
                   description = ?,
                   tags = ?,
                   domain = ?,
                   source_type = ?,
                   source_id = ?,
                   media_type = ?,
                   mime_type = ?,
                   metadata = ?,
                   updated_at = datetime('now')
             WHERE id = ?
            """,
            (
                resolved_title or existing["title"],
                merged_description,
                merged_tags,
                domain.strip() or existing["domain"],
                source_type.strip() or existing["source_type"],
                source_id.strip() or existing["source_id"],
                media_type or existing["media_type"],
                mime_type or existing["mime_type"],
                _serialize_metadata(merged_metadata),
                existing["id"],
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM media_memories WHERE id = ?", (existing["id"],)).fetchone()
        return _row_to_dict(row)

    backend = get_backend()
    cursor = conn.execute(
        """
        INSERT INTO media_memories (
            file_path, url, media_type, mime_type, title, description, tags,
            domain, source_type, source_id, backend_key, metadata
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            normalized_path,
            clean_url,
            media_type,
            mime_type,
            resolved_title,
            description.strip(),
            resolved_tags,
            domain.strip(),
            source_type.strip(),
            source_id.strip(),
            backend.key,
            _serialize_metadata(metadata_dict),
        ),
    )
    conn.commit()
    media_id = int(cursor.lastrowid)
    _safe_cognitive_ingest(
        (
            f"Media memory [{media_type}] {resolved_title}. "
            f"Description: {description.strip() or 'n/a'}. "
            f"Tags: {resolved_tags or 'n/a'}."
        ),
        f"MM{media_id}",
        resolved_title,
        domain.strip(),
    )
    row = conn.execute("SELECT * FROM media_memories WHERE id = ?", (media_id,)).fetchone()
    return _row_to_dict(row)


def get_media_memory(media_id: int) -> dict | None:
    init_tables()
    row = get_db().execute("SELECT * FROM media_memories WHERE id = ?", (media_id,)).fetchone()
    return _row_to_dict(row) if row else None


def search_media_memories(
    *,
    query: str = "",
    media_type: str = "",
    domain: str = "",
    tag: str = "",
    limit: int = 20,
) -> list[dict]:
    init_tables()
    conditions = ["1=1"]
    params: list = []
    if media_type.strip():
        conditions.append("media_type = ?")
        params.append(media_type.strip().lower())
    if domain.strip():
        conditions.append("domain = ?")
        params.append(domain.strip())
    if tag.strip():
        conditions.append("LOWER(tags) LIKE ?")
        params.append(f"%{tag.strip().lower()}%")
    if query.strip():
        conditions.append(
            "(LOWER(title) LIKE ? OR LOWER(description) LIKE ? OR LOWER(tags) LIKE ? OR LOWER(file_path) LIKE ? OR LOWER(url) LIKE ?)"
        )
        q = f"%{query.strip().lower()}%"
        params.extend([q, q, q, q, q])
    rows = get_db().execute(
        f"""
        SELECT * FROM media_memories
         WHERE {' AND '.join(conditions)}
         ORDER BY updated_at DESC, created_at DESC
         LIMIT ?
        """,
        params + [max(1, int(limit or 20))],
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def list_media_memories(limit: int = 20) -> list[dict]:
    return search_media_memories(limit=limit)


def media_memory_stats() -> dict:
    init_tables()
    conn = get_db()
    total = int(conn.execute("SELECT COUNT(*) FROM media_memories").fetchone()[0])
    by_type = {
        row["media_type"]: row["cnt"]
        for row in conn.execute(
            "SELECT media_type, COUNT(*) AS cnt FROM media_memories GROUP BY media_type"
        ).fetchall()
    }
    by_domain = {
        row["domain"]: row["cnt"]
        for row in conn.execute(
            "SELECT domain, COUNT(*) AS cnt FROM media_memories WHERE domain != '' GROUP BY domain ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
    }
    return {
        "total": total,
        "by_type": by_type,
        "by_domain": by_domain,
        "backend": get_backend().key,
    }
