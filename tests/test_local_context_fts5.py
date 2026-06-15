"""Tests for FTS5 keyword recall over local_chunks (migration _m84).

Covers: additive+idempotent schema, triggers mirror local_chunks, incremental
and resumable backfill, dual-read (LIKE until done, FTS after done), privacy
filtering on the FTS path, special-char query fallback, and env rollback.

Vector blob / cosine_blob is intentionally NOT part of this piece (operator cut),
so there are no vector_blob assertions here.
"""

from __future__ import annotations

import sqlite3

from local_context import api
from local_context.db import close_local_context_db, get_local_context_db
from local_context.util import now, stable_id


def _fts_supported(conn) -> bool:
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)"
        )
        conn.execute("DROP TABLE IF EXISTS _fts_probe")
        return True
    except Exception:
        return False


def _seed_asset(conn, asset_id: str, path: str, *, privacy_class: str = "normal", status: str = "active") -> None:
    ts = now()
    conn.execute(
        """
        INSERT OR REPLACE INTO local_assets(
            asset_id, root_id, path, display_path, parent_path, volume_id, file_type,
            extension, size_bytes, depth, depth_reason, phase, status, privacy_class,
            permission_state, first_seen_at, last_seen_at, updated_at
        ) VALUES (?, NULL, ?, ?, '', '', 'file', '.txt', 10, 2, 'default', 'embeddings', ?, ?, 'granted', ?, ?, ?)
        """,
        (asset_id, path, path, status, privacy_class, ts, ts, ts),
    )


def _seed_version(conn, version_id: str, asset_id: str) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO local_asset_versions(
            version_id, asset_id, quick_fingerprint, content_hash, size_bytes,
            modified_at_fs, summary, metadata_json, created_at
        ) VALUES (?, ?, '', '', 10, ?, ?, '{}', ?)
        """,
        (version_id, asset_id, now(), f"summary for {asset_id}", now()),
    )


def _seed_chunk(conn, chunk_id: str, asset_id: str, version_id: str, text: str, *, index: int = 0) -> int:
    conn.execute(
        """
        INSERT OR REPLACE INTO local_chunks(chunk_id, asset_id, version_id, chunk_index, text, token_count, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (chunk_id, asset_id, version_id, index, text, len(text.split()), now()),
    )
    row = conn.execute("SELECT rowid FROM local_chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
    return int(row["rowid"])


def _seed_embedding(conn, asset_id: str, chunk_id: str, vector_json: str = "[0.1, 0.2, 0.3]") -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO local_embeddings(embedding_id, asset_id, chunk_id, model_id, model_revision, dimension, vector_json, created_at)
        VALUES (?, ?, ?, 'm', '1', 3, ?, ?)
        """,
        (stable_id("emb", chunk_id), asset_id, chunk_id, vector_json, now()),
    )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_m84_adds_fts_table_and_bumps_user_version():
    conn = get_local_context_db()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert int(version) >= 84
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE name='local_chunks_fts' AND type IN ('table','view')"
    ).fetchone()
    assert row is not None
    # No vector_blob column was added (operator cut that part).
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(local_embeddings)").fetchall()}
    assert "vector_blob" not in cols


def test_m84_is_idempotent():
    from local_context import db as lcdb

    conn = get_local_context_db()
    # Re-running the schema must not raise (IF NOT EXISTS / column guards).
    lcdb._ensure_schema(conn)
    lcdb._ensure_schema(conn)
    tables = conn.execute(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE name='local_chunks_fts' AND type IN ('table','view')"
    ).fetchone()
    assert int(tables["n"]) == 1


# ---------------------------------------------------------------------------
# Triggers mirror local_chunks
# ---------------------------------------------------------------------------


def test_fts_triggers_mirror_local_chunks():
    conn = get_local_context_db()
    if not _fts_supported(conn):
        return  # host without FTS5: triggers still run against shadow table; skip MATCH-level checks
    _seed_asset(conn, "a1", "/docs/manual.txt", privacy_class="normal")
    _seed_version(conn, "v1", "a1")
    rid = _seed_chunk(conn, "c1", "a1", "v1", "alpha bravo charlie")
    conn.commit()

    fts = conn.execute(
        "SELECT text, privacy_class, asset_status FROM local_chunks_fts WHERE rowid=?", (rid,)
    ).fetchone()
    assert fts is not None
    assert "alpha" in str(fts["text"])
    assert fts["privacy_class"] == "normal"
    assert fts["asset_status"] == "active"

    # Update text -> FTS row mirrors new text.
    conn.execute("UPDATE local_chunks SET text=? WHERE chunk_id=?", ("delta echo foxtrot", "c1"))
    conn.commit()
    fts2 = conn.execute("SELECT text FROM local_chunks_fts WHERE rowid=?", (rid,)).fetchone()
    assert "delta" in str(fts2["text"])
    assert "alpha" not in str(fts2["text"])

    # Delete chunk -> FTS row gone.
    conn.execute("DELETE FROM local_chunks WHERE chunk_id=?", ("c1",))
    conn.commit()
    gone = conn.execute("SELECT 1 FROM local_chunks_fts WHERE rowid=?", (rid,)).fetchone()
    assert gone is None


# ---------------------------------------------------------------------------
# Backfill: incremental + resumable
# ---------------------------------------------------------------------------


def test_backfill_is_incremental_and_resumable():
    conn = get_local_context_db()
    if not _fts_supported(conn):
        return
    _seed_asset(conn, "a1", "/docs/big.txt", privacy_class="normal")
    _seed_version(conn, "v1", "a1")
    # Seed 1200 legacy chunks WITHOUT firing FTS triggers (simulate pre-existing
    # 19GB rows): drop the triggers, seed, then re-create via _ensure_schema.
    conn.executescript(
        "DROP TRIGGER IF EXISTS local_chunks_fts_insert;"
        "DROP TRIGGER IF EXISTS local_chunks_fts_delete;"
        "DROP TRIGGER IF EXISTS local_chunks_fts_update;"
    )
    for i in range(1200):
        cid = f"legacy-{i}"
        conn.execute(
            """
            INSERT INTO local_chunks(chunk_id, asset_id, version_id, chunk_index, text, token_count, created_at)
            VALUES (?, 'a1', 'v1', ?, ?, 3, ?)
            """,
            (cid, i, f"legacy chunk number {i}", now()),
        )
    conn.commit()
    # Re-create triggers so future writes mirror (does not backfill existing).
    from local_context import db as lcdb

    lcdb._m84_local_chunks_fts(conn)
    conn.commit()

    assert conn.execute("SELECT COUNT(*) AS n FROM local_chunks_fts").fetchone()["n"] == 0

    r1 = api._backfill_fts_rows(conn, batch_limit=500)
    assert r1["ok"] is True and r1["done"] is False
    assert conn.execute("SELECT COUNT(*) AS n FROM local_chunks_fts").fetchone()["n"] == 500

    r2 = api._backfill_fts_rows(conn, batch_limit=500)
    assert r2["done"] is False
    assert conn.execute("SELECT COUNT(*) AS n FROM local_chunks_fts").fetchone()["n"] == 1000

    r3 = api._backfill_fts_rows(conn, batch_limit=500)
    assert conn.execute("SELECT COUNT(*) AS n FROM local_chunks_fts").fetchone()["n"] == 1200

    # One more tick finds no rows past the cursor and flips done.
    r4 = api._backfill_fts_rows(conn, batch_limit=500)
    assert api._get_state_conn(conn, api.FTS_MIGRATION_DONE_KEY, "0") == "1"

    # Re-call is a no-op (idempotent).
    r5 = api._backfill_fts_rows(conn, batch_limit=500)
    assert r5.get("skipped") == "already_done"
    assert conn.execute("SELECT COUNT(*) AS n FROM local_chunks_fts").fetchone()["n"] == 1200


def test_backfill_resumes_after_simulated_crash():
    conn = get_local_context_db()
    if not _fts_supported(conn):
        return
    _seed_asset(conn, "a1", "/docs/resume.txt", privacy_class="normal")
    _seed_version(conn, "v1", "a1")
    conn.executescript(
        "DROP TRIGGER IF EXISTS local_chunks_fts_insert;"
        "DROP TRIGGER IF EXISTS local_chunks_fts_delete;"
        "DROP TRIGGER IF EXISTS local_chunks_fts_update;"
    )
    for i in range(800):
        conn.execute(
            """
            INSERT INTO local_chunks(chunk_id, asset_id, version_id, chunk_index, text, token_count, created_at)
            VALUES (?, 'a1', 'v1', ?, ?, 3, ?)
            """,
            (f"r-{i}", i, f"resume chunk {i}", now()),
        )
    conn.commit()
    from local_context import db as lcdb

    lcdb._m84_local_chunks_fts(conn)
    conn.commit()

    api._backfill_fts_rows(conn, batch_limit=500)
    cursor = api._get_state_conn(conn, api.FTS_MIGRATION_CURSOR_KEY, "0")
    assert int(cursor) > 0

    # Simulate crash: drop the in-memory connection and reopen.
    close_local_context_db()
    conn2 = get_local_context_db()
    assert api._get_state_conn(conn2, api.FTS_MIGRATION_CURSOR_KEY, "0") == cursor

    api._backfill_fts_rows(conn2, batch_limit=500)
    api._backfill_fts_rows(conn2, batch_limit=500)
    total = conn2.execute("SELECT COUNT(*) AS n FROM local_chunks_fts").fetchone()["n"]
    distinct = conn2.execute("SELECT COUNT(DISTINCT rowid) AS n FROM local_chunks_fts").fetchone()["n"]
    assert total == 800
    assert total == distinct  # no duplicates


# ---------------------------------------------------------------------------
# New chunks born migrated
# ---------------------------------------------------------------------------


def test_new_chunks_born_migrated_via_triggers():
    conn = get_local_context_db()
    if not _fts_supported(conn):
        return
    api._set_state_conn(conn, api.FTS_MIGRATION_DONE_KEY, "1")
    conn.commit()
    _seed_asset(conn, "a-new", "/docs/new.txt", privacy_class="normal")
    _seed_version(conn, "v-new", "a-new")
    rid = _seed_chunk(conn, "c-new", "a-new", "v-new", "freshly indexed content")
    conn.commit()
    # Without running backfill, the trigger already produced an FTS row.
    fts = conn.execute("SELECT text FROM local_chunks_fts WHERE rowid=?", (rid,)).fetchone()
    assert fts is not None
    assert "freshly" in str(fts["text"])


# ---------------------------------------------------------------------------
# Dual-read
# ---------------------------------------------------------------------------


def _seed_searchable(conn) -> None:
    _seed_asset(conn, "a1", "/docs/widget-guide.txt", privacy_class="normal")
    _seed_version(conn, "v1", "a1")
    cid = "chunk-widget"
    _seed_chunk(conn, cid, "a1", "v1", "the widget assembly instructions are here")
    _seed_embedding(conn, "a1", cid)
    conn.commit()


def test_dual_read_uses_like_path_until_done(monkeypatch):
    conn = get_local_context_db()
    if not _fts_supported(conn):
        return
    _seed_searchable(conn)
    # done flag NOT set -> _fts_ready is False -> legacy LIKE path.
    assert api._fts_ready(conn) is False

    # The FTS branch is the only caller of _fts_match_expr; make it explode if
    # the FTS read path is taken, proving the LIKE path is used mid-migration.
    def boom(*_args, **_kwargs):
        raise AssertionError("FTS match expression built before done=1 (should use LIKE)")

    monkeypatch.setattr(api, "_fts_match_expr", boom)
    rows = api._context_candidate_rows(conn, [], search_query="widget")
    chunk_ids = {r["chunk_id"] for r in rows}
    assert "chunk-widget" in chunk_ids


def test_fts_read_path_after_done_returns_results():
    conn = get_local_context_db()
    if not _fts_supported(conn):
        return
    _seed_searchable(conn)
    api._backfill_fts_rows(conn, batch_limit=500)
    api._backfill_fts_rows(conn, batch_limit=500)  # flip done
    assert api._get_state_conn(conn, api.FTS_MIGRATION_DONE_KEY, "0") == "1"
    assert api._fts_ready(conn) is True

    rows = api._context_candidate_rows(conn, [], search_query="widget")
    chunk_ids = {r["chunk_id"] for r in rows}
    assert "chunk-widget" in chunk_ids


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------


def test_fts_read_path_is_privacy_filtered():
    conn = get_local_context_db()
    if not _fts_supported(conn):
        return
    _seed_asset(conn, "a-normal", "/docs/normal.txt", privacy_class="normal")
    _seed_version(conn, "v-normal", "a-normal")
    _seed_chunk(conn, "c-normal", "a-normal", "v-normal", "secret keyword appears in normal file")
    _seed_embedding(conn, "a-normal", "c-normal")

    _seed_asset(conn, "a-priv", "/docs/private.txt", privacy_class="private_profile_blocked")
    _seed_version(conn, "v-priv", "a-priv")
    _seed_chunk(conn, "c-priv", "a-priv", "v-priv", "secret keyword appears in private file")
    _seed_embedding(conn, "a-priv", "c-priv")
    conn.commit()

    api._backfill_fts_rows(conn, batch_limit=500)
    api._backfill_fts_rows(conn, batch_limit=500)
    assert api._fts_ready(conn) is True

    rows = api._context_candidate_rows(conn, [], search_query="secret keyword")
    chunk_ids = {r["chunk_id"] for r in rows}
    assert "c-normal" in chunk_ids
    assert "c-priv" not in chunk_ids
    # Defense-in-depth: the FTS row for the private asset also carries the
    # private privacy_class snapshot so the SQL prefilter excludes it too.
    priv_fts = conn.execute(
        "SELECT f.privacy_class FROM local_chunks_fts f JOIN local_chunks c ON c.rowid=f.rowid WHERE c.chunk_id='c-priv'"
    ).fetchone()
    assert priv_fts is not None
    assert priv_fts["privacy_class"] == "private_profile_blocked"


# ---------------------------------------------------------------------------
# Special-char query falls back safely
# ---------------------------------------------------------------------------


def test_fts_query_with_special_chars_falls_back_safely():
    conn = get_local_context_db()
    if not _fts_supported(conn):
        return
    _seed_searchable(conn)
    api._backfill_fts_rows(conn, batch_limit=500)
    api._backfill_fts_rows(conn, batch_limit=500)
    assert api._fts_ready(conn) is True

    # FTS operator characters / unbalanced quote must not raise.
    rows = api._context_candidate_rows(conn, [], search_query='widget AND "')
    chunk_ids = {r["chunk_id"] for r in rows}
    # Falls back / still returns the matching chunk.
    assert "chunk-widget" in chunk_ids


# ---------------------------------------------------------------------------
# Rollback via env
# ---------------------------------------------------------------------------


def test_rollback_disables_fts_via_env(monkeypatch):
    conn = get_local_context_db()
    if not _fts_supported(conn):
        return
    _seed_searchable(conn)
    api._backfill_fts_rows(conn, batch_limit=500)
    api._backfill_fts_rows(conn, batch_limit=500)
    assert api._get_state_conn(conn, api.FTS_MIGRATION_DONE_KEY, "0") == "1"

    monkeypatch.setenv("NEXO_LOCAL_CONTEXT_FTS_ENABLED", "0")
    assert api._fts_ready(conn) is False

    # With the flag off, retrieval must use the legacy LIKE path (no FTS branch).
    def boom(*_args, **_kwargs):
        raise AssertionError("FTS match expression built when flag is off (should use LIKE)")

    monkeypatch.setattr(api, "_fts_match_expr", boom)
    rows = api._context_candidate_rows(conn, [], search_query="widget")
    chunk_ids = {r["chunk_id"] for r in rows}
    assert "chunk-widget" in chunk_ids
