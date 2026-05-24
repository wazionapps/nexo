from __future__ import annotations

"""Memory Fabric release helpers.

This module is the product-owned bridge between existing memory islands:
transcript metadata, historical diary backups, local-context embeddings and the
cognitive knowledge graph. It does not copy raw transcripts into the DB.
"""

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import paths
from db import get_db
from transcript_index import ensure_transcript_index
from transcript_utils import (
    MAX_TRANSCRIPT_HOURS,
    find_claude_session_files,
    find_codex_session_files,
)

HISTORICAL_DIARY_SOURCE = "historical_diary"
HASH_EMBEDDING_MODEL = "nexo-local-hash-embedding"
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def ensure_memory_fabric_schema(conn: sqlite3.Connection | None = None) -> None:
    db = conn or get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS memory_fabric_sources (
            source_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            item_count INTEGER NOT NULL DEFAULT 0,
            last_indexed_at TEXT DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS historical_diary_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_backup_path TEXT NOT NULL,
            source_table TEXT NOT NULL DEFAULT 'session_diary',
            source_row_id INTEGER NOT NULL,
            session_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            decisions TEXT NOT NULL DEFAULT '',
            pending TEXT NOT NULL DEFAULT '',
            context_next TEXT NOT NULL DEFAULT '',
            mental_state TEXT NOT NULL DEFAULT '',
            self_critique TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL UNIQUE,
            indexed_at TEXT DEFAULT (datetime('now')),
            metadata_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(source_backup_path, source_table, source_row_id)
        );

        CREATE INDEX IF NOT EXISTS idx_historical_diary_session
            ON historical_diary_index(session_id);
        CREATE INDEX IF NOT EXISTS idx_historical_diary_created
            ON historical_diary_index(created_at);
        CREATE INDEX IF NOT EXISTS idx_historical_diary_domain
            ON historical_diary_index(domain);
        """
    )
    if conn is None:
        db.commit()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return bool(row)


def _fts_upsert_with_conn(
    conn: sqlite3.Connection,
    source: str,
    source_id: str,
    title: str,
    body: str,
    category: str = "",
) -> None:
    conn.execute("DELETE FROM unified_search WHERE source = ? AND source_id = ?", (source, str(source_id)))
    conn.execute(
        """
        INSERT INTO unified_search(source, source_id, title, body, category, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        """,
        (source, str(source_id), str(title)[:200], body or "", category or ""),
    )


def _row_value(row: sqlite3.Row | dict[str, Any], key: str, default: str = "") -> str:
    try:
        if isinstance(row, sqlite3.Row) and key not in row.keys():
            return default
        value = row[key]
    except Exception:
        return default
    return "" if value is None else str(value)


def _historical_diary_hash(backup_path: Path, row: sqlite3.Row | dict[str, Any]) -> str:
    payload = {
        "id": _row_value(row, "id"),
        "session_id": _row_value(row, "session_id"),
        "created_at": _row_value(row, "created_at"),
        "summary": _row_value(row, "summary"),
        "decisions": _row_value(row, "decisions"),
        "pending": _row_value(row, "pending"),
        "context_next": _row_value(row, "context_next"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _diary_body(row: sqlite3.Row | dict[str, Any]) -> str:
    return " | ".join(
        part
        for part in [
            _row_value(row, "summary"),
            _row_value(row, "decisions"),
            _row_value(row, "pending"),
            _row_value(row, "context_next"),
            _row_value(row, "mental_state"),
            _row_value(row, "self_critique"),
            _row_value(row, "user_signals"),
        ]
        if part
    )


def _link_historical_diary_to_kg(hist: sqlite3.Row, row: sqlite3.Row | dict[str, Any]) -> int:
    try:
        import knowledge_graph as kg

        diary_ref = f"historical_diary:{hist['id']}"
        session_id = _row_value(row, "session_id")
        domain = _row_value(row, "domain") or "general"
        body = _diary_body(row)
        label = _row_value(row, "summary") or session_id or diary_ref
        kg.upsert_node(
            "diary",
            diary_ref,
            label,
            {
                "created_at": _row_value(row, "created_at"),
                "session_id": session_id,
                "source": "backup",
                "backup_path": _row_value(hist, "source_backup_path"),
            },
        )
        edges = 0
        if session_id:
            kg.upsert_node("session", f"session:{session_id}", session_id, {"source": "historical_diary"})
            kg.upsert_edge(
                "diary",
                diary_ref,
                "describes_session",
                "session",
                f"session:{session_id}",
                confidence=0.95,
                source_memory_id=diary_ref,
            )
            edges += 1
        if domain:
            kg.upsert_node("area", f"area:{domain}", domain, {"source": "historical_diary"})
            kg.upsert_edge(
                "diary",
                diary_ref,
                "belongs_to_area",
                "area",
                f"area:{domain}",
                confidence=0.8,
                source_memory_id=diary_ref,
            )
            edges += 1
        for email in sorted(set(EMAIL_RE.findall(body)))[:12]:
            kg.upsert_node("email", f"email:{email.lower()}", email.lower(), {"source": "historical_diary"})
            kg.upsert_edge(
                "diary",
                diary_ref,
                "mentions_email",
                "email",
                f"email:{email.lower()}",
                confidence=0.75,
                source_memory_id=diary_ref,
            )
            edges += 1
        return edges
    except Exception:
        return 0


def _backup_db_paths(backups_root: str | Path | None = None, *, max_files: int = 40) -> list[Path]:
    root = Path(backups_root) if backups_root is not None else paths.backups_dir()
    if not root.exists():
        return []
    candidates: list[Path] = []
    for path in root.rglob("*.db"):
        name = path.name.lower()
        if name.endswith("-wal") or name.endswith("-shm"):
            continue
        candidates.append(path)
    def sort_key(item: Path) -> tuple[int, float]:
        try:
            mtime = item.stat().st_mtime if item.exists() else 0.0
        except OSError:
            mtime = 0.0
        weekly_priority = 1 if item.name.startswith("weekly-") or "weekly" in item.parts else 0
        return (weekly_priority, mtime)

    candidates.sort(key=sort_key, reverse=True)
    return candidates[: max(1, int(max_files or 1))]


def _connect_backup(path: Path) -> sqlite3.Connection | None:
    try:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def _active_diary_keys(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for table in ("session_diary", "diary_archive"):
        if not _table_exists(conn, table):
            continue
        for row in conn.execute(f"SELECT session_id, created_at FROM {table}").fetchall():
            keys.add((str(row["session_id"] or ""), str(row["created_at"] or "")))
    return keys


def reconcile_backup_diaries(
    *,
    backups_root: str | Path | None = None,
    max_backup_files: int = 40,
    limit: int = 5000,
) -> dict[str, Any]:
    """Index missing session diaries from technical backups into active search.

    Rows are copied into a historical index, not into active `session_diary`.
    That keeps provenance intact and avoids overwriting current memory.
    """
    conn = get_db()
    ensure_memory_fabric_schema(conn)
    active_keys = _active_diary_keys(conn)
    scanned_backups = 0
    scanned_rows = 0
    skipped_active = 0
    inserted = 0
    fts_rows = 0
    kg_edges = 0

    for backup_path in _backup_db_paths(backups_root, max_files=max_backup_files):
        if scanned_rows >= limit:
            break
        backup_conn = _connect_backup(backup_path)
        if backup_conn is None:
            continue
        try:
            if not _table_exists(backup_conn, "session_diary"):
                continue
            scanned_backups += 1
            rows = backup_conn.execute(
                "SELECT * FROM session_diary ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit - scanned_rows)),),
            ).fetchall()
            for row in rows:
                scanned_rows += 1
                key = (_row_value(row, "session_id"), _row_value(row, "created_at"))
                if key in active_keys:
                    skipped_active += 1
                    continue
                content_hash = _historical_diary_hash(backup_path, row)
                metadata = {
                    "backup_name": backup_path.name,
                    "quality_tier": _row_value(row, "quality_tier"),
                    "quality_score": _row_value(row, "quality_score"),
                }
                before = conn.total_changes
                conn.execute(
                    """
                    INSERT OR IGNORE INTO historical_diary_index (
                        source_backup_path, source_table, source_row_id,
                        session_id, created_at, domain, summary, decisions,
                        pending, context_next, mental_state, self_critique,
                        source, content_hash, metadata_json
                    )
                    VALUES (?, 'session_diary', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(backup_path),
                        int(_row_value(row, "id", "0") or 0),
                        _row_value(row, "session_id"),
                        _row_value(row, "created_at"),
                        _row_value(row, "domain"),
                        _row_value(row, "summary"),
                        _row_value(row, "decisions"),
                        _row_value(row, "pending"),
                        _row_value(row, "context_next"),
                        _row_value(row, "mental_state"),
                        _row_value(row, "self_critique"),
                        _row_value(row, "source"),
                        content_hash,
                        json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    ),
                )
                if conn.total_changes > before:
                    inserted += 1
                hist = conn.execute(
                    "SELECT id, summary, domain FROM historical_diary_index WHERE content_hash=?",
                    (content_hash,),
                ).fetchone()
                if hist:
                    title = str(hist["summary"] or _row_value(row, "session_id") or "Historical diary")
                    _fts_upsert_with_conn(
                        conn,
                        HISTORICAL_DIARY_SOURCE,
                        str(hist["id"]),
                        title,
                        _diary_body(row),
                        str(hist["domain"] or "backup"),
                    )
                    fts_rows += 1
                    kg_edges += _link_historical_diary_to_kg(hist, row)
        finally:
            backup_conn.close()

    conn.execute(
        """
        INSERT INTO memory_fabric_sources(source_id, source_type, source_ref, status, item_count, last_indexed_at, metadata_json)
        VALUES ('historical_diary_backups', 'backup', ?, 'active', ?, datetime('now'), ?)
        ON CONFLICT(source_id) DO UPDATE SET
            source_ref=excluded.source_ref,
            item_count=excluded.item_count,
            last_indexed_at=excluded.last_indexed_at,
            metadata_json=excluded.metadata_json
        """,
        (
            str(Path(backups_root) if backups_root is not None else paths.backups_dir()),
            int(conn.execute("SELECT COUNT(*) AS total FROM historical_diary_index").fetchone()["total"] or 0),
            json.dumps({"scanned_backups": scanned_backups, "scanned_rows": scanned_rows}, sort_keys=True),
        ),
    )
    conn.commit()
    return {
        "ok": True,
        "scanned_backups": scanned_backups,
        "scanned_rows": scanned_rows,
        "skipped_active": skipped_active,
        "inserted": inserted,
        "fts_rows": fts_rows,
        "kg_edges": kg_edges,
    }


def _count_transcript_files() -> dict[str, int]:
    return {
        "claude_code": len(find_claude_session_files()),
        "codex": len(find_codex_session_files()),
    }


def _local_context_embedding_stats() -> dict[str, Any]:
    try:
        from local_context.db import local_context_db_path

        db_path = local_context_db_path()
        if not db_path.is_file():
            return {"exists": False}
        conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        try:
            if not _table_exists(conn, "local_embeddings"):
                return {"exists": True, "embeddings": 0, "models": {}}
            rows = conn.execute(
                "SELECT model_id, dimension, COUNT(*) AS total FROM local_embeddings GROUP BY model_id, dimension"
            ).fetchall()
            models = {
                f"{row['model_id']}:{row['dimension']}": int(row["total"] or 0)
                for row in rows
            }
            return {
                "exists": True,
                "embeddings": sum(models.values()),
                "models": models,
                "hash_embeddings": sum(
                    total for key, total in models.items() if key.startswith(HASH_EMBEDDING_MODEL + ":")
                ),
            }
        finally:
            conn.close()
    except Exception as exc:
        return {"exists": False, "error": str(exc)}


def _cognitive_kg_stats() -> dict[str, Any]:
    try:
        from cognitive_paths import resolve_cognitive_db

        db_path = resolve_cognitive_db(for_write=False)
        if not db_path.is_file():
            return {"exists": False}
        conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True, timeout=1.0)
        try:
            nodes = conn.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0]
            edges = conn.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0]
            return {"exists": True, "nodes": int(nodes or 0), "edges": int(edges or 0)}
        finally:
            conn.close()
    except Exception as exc:
        return {"exists": False, "error": str(exc)}


def memory_fabric_health(
    *,
    include_backup_scan: bool = True,
    backups_root: str | Path | None = None,
) -> dict[str, Any]:
    ensure_memory_fabric_schema()
    conn = get_db()
    transcript_files = _count_transcript_files()
    transcript_index_count = int(conn.execute("SELECT COUNT(*) AS total FROM transcript_index").fetchone()["total"] or 0)
    historical_count = int(conn.execute("SELECT COUNT(*) AS total FROM historical_diary_index").fetchone()["total"] or 0)
    issues: list[dict[str, str]] = []

    if sum(transcript_files.values()) > 0 and transcript_index_count == 0:
        issues.append({
            "code": "transcript_index_empty",
            "severity": "warn",
            "message": "Transcript files exist but compact transcript_index is empty.",
        })

    backup_rows = 0
    backup_files = 0
    backup_unreconciled = 0
    if include_backup_scan:
        active_keys = _active_diary_keys(conn)
        historical_hashes = {
            str(row["content_hash"] or "")
            for row in conn.execute("SELECT content_hash FROM historical_diary_index").fetchall()
        }
        for backup_path in _backup_db_paths(backups_root, max_files=12):
            backup_conn = _connect_backup(backup_path)
            if backup_conn is None:
                continue
            try:
                if not _table_exists(backup_conn, "session_diary"):
                    continue
                backup_files += 1
                rows = backup_conn.execute("SELECT * FROM session_diary ORDER BY created_at DESC LIMIT 1000").fetchall()
                backup_rows += len(rows)
                for row in rows:
                    key = (_row_value(row, "session_id"), _row_value(row, "created_at"))
                    if key in active_keys:
                        continue
                    if _historical_diary_hash(backup_path, row) in historical_hashes:
                        continue
                    backup_unreconciled += 1
            finally:
                backup_conn.close()
        if backup_unreconciled > 0:
            issues.append({
                "code": "backup_diaries_not_reconciled",
                "severity": "warn",
                "message": "Backup session diaries exist outside active memory and historical index.",
            })

    embeddings = _local_context_embedding_stats()
    if int(embeddings.get("hash_embeddings") or 0) > 0:
        issues.append({
            "code": "hash_embeddings_present",
            "severity": "info",
            "message": "Local context still has deterministic fallback embeddings; re-embedding is recommended.",
        })

    kg = _cognitive_kg_stats()
    if kg.get("exists") and int(kg.get("nodes") or 0) == 0:
        issues.append({
            "code": "kg_empty",
            "severity": "info",
            "message": "Knowledge graph tables exist but have no nodes.",
        })

    return {
        "ok": not any(issue["severity"] == "error" for issue in issues),
        "issues": issues,
        "transcripts": {
            "files": transcript_files,
            "index_rows": transcript_index_count,
        },
        "historical_diaries": {
            "index_rows": historical_count,
            "backup_files_scanned": backup_files,
            "backup_rows_seen": backup_rows,
            "backup_rows_unreconciled": backup_unreconciled,
        },
        "local_context": embeddings,
        "knowledge_graph": kg,
    }


def repair_memory_fabric(
    *,
    transcript_hours: int = MAX_TRANSCRIPT_HOURS,
    transcript_limit: int = 1000,
    backup_limit: int = 5000,
) -> dict[str, Any]:
    transcript_result = ensure_transcript_index(
        hours=transcript_hours,
        limit=transcript_limit,
        min_user_messages=1,
        force=True,
    )
    backup_result = reconcile_backup_diaries(limit=backup_limit)
    health = memory_fabric_health(include_backup_scan=True)
    return {
        "ok": True,
        "transcripts": transcript_result,
        "backups": backup_result,
        "health": health,
    }
