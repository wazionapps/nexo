"""Release A / A8 — chunk_id must be stable on (version_id, index), not content.

chunk_text() enumerates chunks, so chunk_index is always unique within a batch;
that alone already prevents a within-batch PK collision. The real defect is that
the old id hashed the chunk TEXT (chunk[:80]) into the primary key, so the id
churned on every content edit at a given position. Two consequences:

  * entity_facts / local_entity_aliases dedup on UNIQUE(..., source_chunk_id)
    (db.py:182,196). A churning source_chunk_id defeats that dedup across
    re-indexing, feeding the entity_facts blow-up (learning #830).
  * the embedding-refresh join on chunk_id (api.py) and any future incremental
    UPSERT can never match a chunk to its prior row.

A position-stable id (stable_id('chunk', f'{version_id}:{index}')) makes
re-indexing idempotent and keeps source_chunk_id stable.
"""

from pathlib import Path

from local_context import api
from local_context import db as lcdb
from local_context.util import stable_id


def _schema_conn(tmp_path):
    conn = lcdb._connect(Path(tmp_path) / "a8.db")
    lcdb._ensure_schema(conn)
    return conn


def _chunk_ids(conn, version_id):
    return [
        row["chunk_id"]
        for row in conn.execute(
            "SELECT chunk_id FROM local_chunks WHERE version_id=? ORDER BY chunk_index",
            (version_id,),
        ).fetchall()
    ]


def test_chunk_id_is_position_stable_independent_of_content(tmp_path):
    conn = _schema_conn(tmp_path)
    try:
        # Equal-length texts → identical chunk count / indices, different content.
        api._replace_chunks(conn, "asset-1", "ver-A", "x" * 2500)
        ids_first = _chunk_ids(conn, "ver-A")
        api._replace_chunks(conn, "asset-1", "ver-A", "y" * 2500)
        ids_second = _chunk_ids(conn, "ver-A")
        assert ids_first, "expected at least one chunk"
        assert ids_first == ids_second, (
            "chunk_id must depend only on (version_id, index); it changed when "
            "only the chunk TEXT changed"
        )
    finally:
        conn.close()


def test_chunk_id_matches_stable_id_of_version_and_index(tmp_path):
    conn = _schema_conn(tmp_path)
    try:
        api._replace_chunks(conn, "asset-2", "ver-B", "z" * 2500)
        rows = conn.execute(
            "SELECT chunk_id, chunk_index FROM local_chunks "
            "WHERE version_id='ver-B' ORDER BY chunk_index"
        ).fetchall()
        assert rows, "expected at least one chunk"
        for row in rows:
            expected = stable_id("chunk", f"ver-B:{row['chunk_index']}")
            assert row["chunk_id"] == expected, (
                f"chunk {row['chunk_index']} id {row['chunk_id']} != {expected}"
            )
    finally:
        conn.close()


def test_no_pk_collision_on_repetitive_content(tmp_path):
    conn = _schema_conn(tmp_path)
    try:
        # Heavy boilerplate: identical 80-char prefixes across many chunks must
        # still yield unique PKs because chunk_index disambiguates them.
        text = ("HEADER-BOILERPLATE " + "q" * 200 + "\n") * 60
        api._replace_chunks(conn, "asset-3", "ver-C", text)
        ids = _chunk_ids(conn, "ver-C")
        assert ids, "expected at least one chunk"
        assert len(ids) == len(set(ids)), "chunk_index must keep PKs unique"
    finally:
        conn.close()
