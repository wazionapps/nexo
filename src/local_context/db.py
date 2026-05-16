from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import paths
from db._schema import _m63_local_context_layer, _m64_local_context_live_dirs

LOCAL_CONTEXT_DB_NAME = "local-context.db"
MIGRATION_STATE_KEY = "local_context_db_migrated_from_main"
MIGRATION_SKIPPED_KEY = "local_context_db_migration_skipped"
MAIN_CLEANUP_STATE_KEY = "local_context_main_tables_drained"

LOCAL_CONTEXT_TABLES: tuple[str, ...] = (
    "local_index_roots",
    "local_index_exclusions",
    "local_index_jobs",
    "local_index_checkpoints",
    "local_index_state",
    "local_index_errors",
    "local_index_logs",
    "local_assets",
    "local_asset_versions",
    "local_chunks",
    "local_entities",
    "local_relations",
    "local_embeddings",
    "local_context_queries",
    "local_index_dirs",
)

_CONN: sqlite3.Connection | None = None
_CONN_PATH: Path | None = None
_READY = False
_LAST_MIGRATION_ATTEMPT = 0.0
_MIGRATION_RETRY_INTERVAL_SECONDS = 300.0


def local_context_db_path() -> Path:
    override = os.environ.get("NEXO_LOCAL_CONTEXT_DB", "").strip()
    if override:
        return Path(override).expanduser()
    test_db = os.environ.get("NEXO_TEST_DB", "").strip()
    if test_db:
        return Path(test_db).expanduser().with_name("test_local_context.db")
    return paths.memory_dir() / LOCAL_CONTEXT_DB_NAME


def _main_db_path_for_migration() -> Path:
    override = os.environ.get("NEXO_LOCAL_CONTEXT_MAIN_DB", "").strip()
    if override:
        return Path(override).expanduser()
    test_db = os.environ.get("NEXO_TEST_DB", "").strip()
    if test_db:
        return Path(test_db).expanduser()
    return paths.db_path()


def _busy_timeout_ms() -> int:
    raw = os.environ.get("NEXO_LOCAL_CONTEXT_DB_BUSY_TIMEOUT_MS", "15000")
    try:
        value = int(raw)
    except Exception:
        value = 15000
    return max(1000, min(value, 60000))


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=max(_busy_timeout_ms() / 1000.0, 1.0), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={_busy_timeout_ms()}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def connect_local_context_db_readonly(*, timeout_ms: int = 1200) -> sqlite3.Connection:
    db_path = local_context_db_path()
    if not db_path.is_file():
        raise FileNotFoundError(str(db_path))
    timeout = max(float(timeout_ms) / 1000.0, 0.1)
    uri_path = quote(db_path.resolve().as_posix(), safe="/:")
    uri_params = "mode=ro"
    if not db_path.with_name(db_path.name + "-wal").exists() and not db_path.with_name(db_path.name + "-shm").exists():
        uri_params += "&immutable=1"
    uri = f"file:{uri_path}?{uri_params}"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={max(100, int(timeout_ms))}")
    conn.execute("PRAGMA query_only=ON")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    _m63_local_context_layer(conn)
    _m64_local_context_live_dirs(conn)
    conn.execute("PRAGMA user_version=64")
    conn.commit()


def _table_exists(conn: sqlite3.Connection, table: str, *, schema: str = "main") -> bool:
    row = conn.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return bool(row)


def _table_count(conn: sqlite3.Connection, table: str, *, schema: str = "main") -> int:
    if not _table_exists(conn, table, schema=schema):
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS total FROM {schema}.{_quoted(table)}").fetchone()
    return int(row["total"] or 0)


def _state(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM local_index_state WHERE key=?", (key,)).fetchone()
    return str(row["value"] or "") if row else ""


def _set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO local_index_state(key, value, updated_at)
        VALUES (?, ?, strftime('%s','now'))
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, value),
    )


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_columns(conn: sqlite3.Connection, table: str, *, schema: str = "main") -> list[str]:
    try:
        rows = conn.execute(f"PRAGMA {schema}.table_info({_quoted(table)})").fetchall()
    except sqlite3.OperationalError:
        return []
    return [str(row["name"]) for row in rows if row["name"]]


def _primary_key_columns(conn: sqlite3.Connection, table: str, *, schema: str = "main") -> list[str]:
    try:
        rows = conn.execute(f"PRAGMA {schema}.table_info({_quoted(table)})").fetchall()
    except sqlite3.OperationalError:
        return []
    ordered = sorted(
        (
            (int(row["pk"] or 0), str(row["name"]))
            for row in rows
            if int(row["pk"] or 0) > 0 and row["name"]
        ),
        key=lambda item: item[0],
    )
    return [name for _order, name in ordered]


def _source_rows_missing_in_target(conn: sqlite3.Connection, table: str) -> int:
    target_pk = _primary_key_columns(conn, table)
    source_columns = set(_table_columns(conn, table, schema="source"))
    pk_columns = [column for column in target_pk if column in source_columns]
    if not pk_columns:
        raise RuntimeError(f"cannot verify local context migration for {table}: missing primary key")
    join_sql = " AND ".join(f"s.{_quoted(column)} = t.{_quoted(column)}" for column in pk_columns)
    null_check = f"t.{_quoted(pk_columns[0])} IS NULL"
    row = conn.execute(
        f"SELECT COUNT(*) AS total "
        f"FROM source.{_quoted(table)} s "
        f"LEFT JOIN {_quoted(table)} t ON {join_sql} "
        f"WHERE {null_check}"
    ).fetchone()
    return int(row["total"] or 0)


def _copy_local_tables(conn: sqlite3.Connection, tables: Iterable[str]) -> dict[str, int]:
    copied: dict[str, int] = {}
    for table in tables:
        if not _table_exists(conn, table, schema="source"):
            continue
        target_columns = _table_columns(conn, table)
        source_columns = set(_table_columns(conn, table, schema="source"))
        columns = [column for column in target_columns if column in source_columns]
        if not columns:
            continue
        before = _table_count(conn, table)
        column_sql = ", ".join(_quoted(column) for column in columns)
        conn.execute(
            f"INSERT OR IGNORE INTO {_quoted(table)} ({column_sql}) "
            f"SELECT {column_sql} FROM source.{_quoted(table)}"
        )
        after = _table_count(conn, table)
        copied[table] = max(0, after - before)
    return copied


def _source_table_counts(conn: sqlite3.Connection, tables: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in tables:
        if _table_exists(conn, table, schema="source"):
            counts[table] = _table_count(conn, table, schema="source")
    return counts


def _drain_source_local_tables_if_verified(
    conn: sqlite3.Connection,
    source_counts: dict[str, int],
) -> dict[str, int]:
    """Clear old local-memory rows from main DB after verifying the copy.

    We keep the table schema in ``nexo.db`` for backward compatibility with
    older binaries, but empty the rows after the new DB has at least the same
    number of records per table. This prevents two sources of truth and stops
    the main DB from carrying the huge local-memory payload.
    """
    drained: dict[str, int] = {}
    if not source_counts:
        return drained

    for table, source_count in source_counts.items():
        local_count = _table_count(conn, table)
        missing = _source_rows_missing_in_target(conn, table)
        if local_count < source_count or missing:
            raise RuntimeError(
                f"local context migration verification failed for {table}: "
                f"local={local_count} source={source_count} missing={missing}"
            )

    for table, source_count in source_counts.items():
        if source_count <= 0:
            continue
        conn.execute(f"DELETE FROM source.{_quoted(table)}")
        drained[table] = source_count

    if drained:
        _set_state(conn, MAIN_CLEANUP_STATE_KEY, ",".join(sorted(drained)))
    return drained


def migrate_from_main_if_needed(conn: sqlite3.Connection) -> dict:
    if os.environ.get("NEXO_LOCAL_CONTEXT_DISABLE_MAIN_MIGRATION", "").strip().lower() in {"1", "true", "yes"}:
        return {"ok": True, "skipped": "disabled"}

    main_db = _main_db_path_for_migration()
    if not main_db.is_file():
        _set_state(conn, MIGRATION_SKIPPED_KEY, "main_db_missing")
        conn.commit()
        return {"ok": True, "skipped": "main_db_missing"}

    source = str(main_db).replace("'", "''")
    try:
        conn.execute("PRAGMA busy_timeout=1000")
        conn.execute(f"ATTACH DATABASE '{source}' AS source")
        if not _table_exists(conn, "local_assets", schema="source") and not _table_exists(conn, "local_index_roots", schema="source"):
            _set_state(conn, MIGRATION_SKIPPED_KEY, "main_local_tables_missing")
            if _table_count(conn, "local_assets") > 0 or _table_count(conn, "local_index_jobs") > 0:
                _set_state(conn, MIGRATION_STATE_KEY, "already_has_local_data")
            conn.commit()
            return {"ok": True, "skipped": "main_local_tables_missing"}
        source_counts = _source_table_counts(conn, LOCAL_CONTEXT_TABLES)
        if not any(source_counts.values()):
            _set_state(conn, MIGRATION_STATE_KEY, "main_local_tables_empty")
            _set_state(conn, MAIN_CLEANUP_STATE_KEY, "empty")
            conn.commit()
            return {"ok": True, "skipped": "main_local_tables_empty"}
        copied = _copy_local_tables(conn, LOCAL_CONTEXT_TABLES)
        drained = _drain_source_local_tables_if_verified(conn, source_counts)
        _set_state(conn, MIGRATION_STATE_KEY, str(main_db))
        _set_state(conn, "local_context_db_migrated_rows", str(sum(copied.values())))
        if drained:
            _set_state(conn, "local_context_main_tables_drained_rows", str(sum(drained.values())))
        conn.commit()
        return {"ok": True, "migrated_from": str(main_db), "copied": copied, "drained": drained}
    except sqlite3.OperationalError as exc:
        message = str(exc)
        _set_state(conn, MIGRATION_SKIPPED_KEY, message[:240])
        _set_state(conn, "local_context_db_drain_pending", "main_db_busy_or_unavailable")
        conn.commit()
        return {"ok": True, "skipped": "main_db_busy_or_unavailable", "error": message, "retry_pending": True}
    finally:
        try:
            conn.execute("DETACH DATABASE source")
        except Exception:
            pass
        conn.execute(f"PRAGMA busy_timeout={_busy_timeout_ms()}")


def ensure_local_context_db() -> None:
    global _CONN, _CONN_PATH, _READY, _LAST_MIGRATION_ATTEMPT
    db_path = local_context_db_path()
    if _CONN is not None and _CONN_PATH != db_path:
        close_local_context_db()
    if _CONN is None:
        _CONN = _connect(db_path)
        _CONN_PATH = db_path
    now = time.monotonic()
    if _READY:
        if now - _LAST_MIGRATION_ATTEMPT >= _MIGRATION_RETRY_INTERVAL_SECONDS:
            _LAST_MIGRATION_ATTEMPT = now
            try:
                migrate_from_main_if_needed(_CONN)
            except Exception:
                # Sidecar data is still usable; cleanup from the old main DB is
                # best-effort and will retry on later service cycles.
                pass
        return
    _ensure_schema(_CONN)
    _LAST_MIGRATION_ATTEMPT = now
    migration = migrate_from_main_if_needed(_CONN)
    _READY = True


def get_local_context_db() -> sqlite3.Connection:
    ensure_local_context_db()
    assert _CONN is not None
    return _CONN


def close_local_context_db() -> None:
    global _CONN, _CONN_PATH, _READY, _LAST_MIGRATION_ATTEMPT
    if _CONN is not None:
        try:
            _CONN.close()
        except Exception:
            pass
    _CONN = None
    _CONN_PATH = None
    _READY = False
    _LAST_MIGRATION_ATTEMPT = 0.0
