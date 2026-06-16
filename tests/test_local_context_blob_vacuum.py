"""local-context.db disk-leak fix: float32 BLOB embeddings + VACUUM reclaim.

Covers the 5-part fix:
- compact float32 vector_blob round-trips equal to the legacy JSON within float
  tolerance, and the decoder prefers BLOB but falls back to JSON;
- the incremental TEXT->BLOB backfill is resumable and converts every row;
- clear_index() actually shrinks the file on disk (DELETE+VACUUM, not just
  free-list);
- an existing auto_vacuum=NONE db is converted to INCREMENTAL once.
"""

from __future__ import annotations

import math
import sqlite3
import struct
from pathlib import Path

from local_context import api
from local_context import db as lcdb
from local_context.db import close_local_context_db, get_local_context_db, local_context_db_path


def _row(vector_json="", dimension=0, vector_blob=None):
    return {"vector_json": vector_json, "dimension": dimension, "vector_blob": vector_blob}


# --- round-trip + decode preference ----------------------------------------

def test_embedding_blob_roundtrip_within_tolerance():
    for dim in (128, 384):
        vec = [math.sin(i) / math.sqrt(dim) for i in range(dim)]
        blob = api._encode_embedding_blob(vec)
        assert blob is not None and len(blob) == dim * 4
        decoded = api._decode_embedding(_row(dimension=dim, vector_blob=blob))
        assert len(decoded) == dim
        # float32 drift only — cosine self-similarity must stay ~1.0.
        from local_context import embeddings
        assert math.isclose(embeddings.cosine(vec, decoded), 1.0, abs_tol=1e-5)
        assert max(abs(a - b) for a, b in zip(vec, decoded)) < 1e-5


def test_decode_prefers_blob_then_falls_back_to_json():
    vec = [0.25, 0.75]
    blob = api._encode_embedding_blob(vec)
    # BLOB present + correct length → used.
    assert api._decode_embedding(_row(vector_json="[9, 9]", dimension=2, vector_blob=blob)) == [0.25, 0.75]
    # No BLOB → JSON fallback.
    assert api._decode_embedding(_row(vector_json="[0.25, 0.75]", dimension=2, vector_blob=None)) == [0.25, 0.75]
    # Wrong-length BLOB (corrupt) must be ignored and fall back to JSON.
    assert api._decode_embedding(_row(vector_json="[0.25, 0.75]", dimension=2, vector_blob=b"\x00\x01")) == [0.25, 0.75]


def test_encode_respects_write_flag(monkeypatch):
    monkeypatch.setattr(api, "EMB_BLOB_WRITE_ENABLED", False)
    assert api._encode_embedding_blob([0.1, 0.2]) is None
    monkeypatch.setattr(api, "EMB_BLOB_WRITE_ENABLED", True)
    assert api._encode_embedding_blob([0.1, 0.2]) is not None
    assert api._encode_embedding_blob([]) is None


# --- resumable backfill -----------------------------------------------------

def _reset_emb_state(conn):
    for key in (api.EMB_BLOB_CURSOR_KEY, api.EMB_BLOB_DONE_KEY, api.EMB_BLOB_TOTAL_KEY):
        conn.execute("DELETE FROM local_index_state WHERE key=?", (key,))
    conn.commit()


def test_backfill_embedding_blobs_resumable():
    conn = get_local_context_db()
    conn.execute("DELETE FROM local_embeddings")
    _reset_emb_state(conn)
    n = 7
    for i in range(n):
        vec = [float(i), float(i) + 0.5]
        conn.execute(
            "INSERT INTO local_embeddings(embedding_id, asset_id, chunk_id, model_id, model_revision, dimension, vector_json, vector_blob, created_at) "
            "VALUES (?, 'a', ?, 'm', '1', 2, ?, NULL, 0)",
            (f"e{i}", f"c{i}", api.json_dumps(vec)),
        )
    conn.commit()

    # Drive it two rows at a time; it must resume from the cursor and finish.
    seen_done = False
    for _ in range(10):
        res = api._backfill_embedding_blobs(conn, batch_limit=2)
        if res.get("done"):
            seen_done = True
            break
    assert seen_done
    # Every row now has a BLOB equal to its JSON within tolerance.
    rows = conn.execute("SELECT dimension, vector_json, vector_blob FROM local_embeddings").fetchall()
    assert len(rows) == n
    for r in rows:
        assert r["vector_blob"] is not None
        dim = int(r["dimension"])
        decoded = list(struct.unpack(f"<{dim}f", r["vector_blob"]))
        original = api.json_loads(r["vector_json"], [])
        assert max(abs(a - b) for a, b in zip(original, decoded)) < 1e-5
    # Idempotent: a further call short-circuits as already_done.
    assert api._backfill_embedding_blobs(conn, batch_limit=2).get("done")


def test_backfill_respects_disabled_batch():
    conn = get_local_context_db()
    res = api._backfill_embedding_blobs(conn, batch_limit=0)
    assert res.get("skipped") == "disabled"


# --- clear_index shrinks the file ------------------------------------------

def test_clear_index_vacuums_and_shrinks():
    conn = get_local_context_db()
    conn.execute("DELETE FROM local_embeddings")
    conn.commit()
    # Write enough bytes that a VACUUM is observable.
    big = api.json_dumps([0.123456789] * 384)
    for i in range(3000):
        conn.execute(
            "INSERT INTO local_embeddings(embedding_id, asset_id, chunk_id, model_id, model_revision, dimension, vector_json, created_at) "
            "VALUES (?, 'a', ?, 'm', '1', 384, ?, 0)",
            (f"big{i}", f"c{i}", big),
        )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db_path = local_context_db_path()
    before = db_path.stat().st_size

    api.clear_index()

    after = db_path.stat().st_size
    assert after < before, f"expected shrink, before={before} after={after}"
    # Free-list fully reclaimed by the VACUUM.
    assert int(conn.execute("PRAGMA freelist_count").fetchone()[0]) == 0


# --- auto_vacuum conversion of an existing NONE-mode db --------------------

def test_existing_db_converts_to_incremental_auto_vacuum(tmp_path):
    db_path = Path(tmp_path) / "legacy.db"
    raw = sqlite3.connect(str(db_path))
    try:
        assert int(raw.execute("PRAGMA auto_vacuum").fetchone()[0]) == 0  # NONE default
        raw.execute("CREATE TABLE local_index_state (key TEXT PRIMARY KEY, value TEXT, updated_at REAL)")
        raw.execute("CREATE TABLE t (x BLOB)")
        for i in range(200):
            raw.execute("INSERT INTO t(x) VALUES (?)", (b"\x00" * 4096,))
        raw.commit()
    finally:
        raw.close()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        lcdb._convert_auto_vacuum_once(conn, db_path)
        assert int(conn.execute("PRAGMA auto_vacuum").fetchone()[0]) == 2  # INCREMENTAL
        assert lcdb._state(conn, lcdb.AUTO_VACUUM_CONVERTED_KEY) == "1"
        # Second call is a no-op (flag already set) — must not raise.
        lcdb._convert_auto_vacuum_once(conn, db_path)
    finally:
        conn.close()
    close_local_context_db()
