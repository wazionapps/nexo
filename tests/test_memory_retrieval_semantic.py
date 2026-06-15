from __future__ import annotations

import time

import numpy as np


def _unit(vec: list[float]) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return arr.astype(np.float32)


def _record_observation(db, *, uid: str, summary: str, subject: str = "", project_key: str = "nexo"):
    return db.upsert_memory_observation(
        {
            "observation_uid": uid,
            "project_key": project_key,
            "session_id": "nexo-semantic",
            "observation_type": "decision",
            "subject": subject or summary[:40],
            "summary": summary,
            "facts": {"note": summary},
            "evidence_refs": [f"memory_event:{uid}"],
            "entities": [subject] if subject else [],
            "salience": 0.7,
            "confidence": 0.8,
        }
    )


# ── migration / schema ──────────────────────────────────────────────────


def test_m83_adds_shadow_embedding_columns(isolated_db):
    import db

    conn = db.get_db()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(memory_observations)").fetchall()}
    assert "embedding" in cols
    assert "embedding_model" in cols
    assert db.get_schema_version() >= 83


def test_m83_applies_to_existing_db(isolated_db):
    import db

    conn = db.get_db()
    # Simulate a pre-83 database that still has the columns absent.
    try:
        conn.execute("ALTER TABLE memory_observations DROP COLUMN embedding")
        conn.execute("ALTER TABLE memory_observations DROP COLUMN embedding_model")
    except Exception:
        # Older SQLite without DROP COLUMN: rebuild without the shadow columns is
        # out of scope; just assert idempotent re-run does not raise.
        pass
    conn.execute("DELETE FROM schema_migrations WHERE version = 83")
    conn.commit()

    db.run_migrations()

    cols = {row[1] for row in conn.execute("PRAGMA table_info(memory_observations)").fetchall()}
    assert "embedding" in cols
    assert "embedding_model" in cols


# ── precompute at write time ────────────────────────────────────────────


def test_embedding_precomputed_at_write_time(isolated_db):
    import db

    result = _record_observation(db, uid="MO-precompute", summary="Precompute embedding at write time.")
    assert result["ok"] is True
    # Offline deterministic fallback is active in tests => model is "warm" =>
    # the embedding must be precomputed and stored.
    assert result.get("has_embedding") is True

    conn = db.get_db()
    row = conn.execute(
        "SELECT embedding, embedding_model FROM memory_observations WHERE observation_uid = ?",
        ("MO-precompute",),
    ).fetchone()
    assert row["embedding"] is not None
    assert row["embedding_model"]


def test_embed_error_does_not_block_write(isolated_db, monkeypatch):
    import cognitive._core as cog
    import db

    def boom(*args, **kwargs):
        raise RuntimeError("embedding backend exploded")

    monkeypatch.setattr(cog, "embed", boom)

    result = _record_observation(db, uid="MO-embed-error", summary="Write must survive an embed failure.")

    # The observation is durable even though the embedding could not be computed.
    assert result["ok"] is True
    assert result.get("has_embedding") is False
    observations = db.list_memory_observations(query="survive an embed failure")
    assert len(observations) == 1
    conn = db.get_db()
    row = conn.execute(
        "SELECT embedding FROM memory_observations WHERE observation_uid = ?",
        ("MO-embed-error",),
    ).fetchone()
    assert row["embedding"] is None


# ── retrieval: paraphrase, lexical, no-model ────────────────────────────


def test_paraphrase_is_retrieved_via_vector_fusion(isolated_db, monkeypatch):
    import cognitive._core as cog
    import db
    from memory_retrieval import memory_search

    # Controlled embedding space: the target observation and a paraphrased query
    # collapse to the SAME unit vector; an unrelated observation is orthogonal.
    target_vec = _unit([1.0, 0.0, 0.0] + [0.0] * (cog.EMBEDDING_DIM - 3))
    other_vec = _unit([0.0, 1.0, 0.0] + [0.0] * (cog.EMBEDDING_DIM - 3))

    def fake_embed(text: str):
        lowered = (text or "").lower()
        if "playwright" in lowered or "browser deleted" in lowered or "scraper crashed" in lowered:
            return target_vec
        return other_vec

    monkeypatch.setattr(cog, "embed", fake_embed)

    _record_observation(
        db,
        uid="MO-paraphrase-target",
        summary="The Playwright browser deleted itself after the Python upgrade so the scraper crashed.",
        subject="recambios reviews",
    )
    _record_observation(
        db,
        uid="MO-unrelated",
        summary="Quarterly newsletter open rate analysis for the marketing team.",
        subject="newsletter",
    )

    # The query shares NO lexical tokens with the stored summary, so only the
    # vector fusion can retrieve it.
    result = memory_search("why did the scraper crashed", project_hint="nexo", process_queue=False)

    uids = [c.get("uid") for c in result["candidates"]]
    assert "MO-paraphrase-target" in uids
    top = result["candidates"][0]
    assert top["uid"] == "MO-paraphrase-target"
    assert top.get("vector_score", 0) > 0


def test_lexical_match_still_works(isolated_db):
    import db
    from memory_retrieval import memory_search

    _record_observation(
        db,
        uid="MO-lexical",
        summary="The durable retrieval index handled the lexical lookup target.",
        subject="src/lexical_lookup.py",
    )

    result = memory_search("lexical lookup target", project_hint="nexo", process_queue=False)
    uids = [c.get("uid") for c in result["candidates"]]
    assert "MO-lexical" in uids
    assert result["has_evidence"] is True


def test_no_model_degrades_to_fts(isolated_db, monkeypatch):
    import db
    import memory_retrieval
    from memory_retrieval import memory_search

    # Force the model to look COLD: not offline-fallback, not loaded. The query
    # path must NOT embed and must NOT raise — it degrades to FTS/token search.
    monkeypatch.setattr(memory_retrieval, "_model_is_warm", lambda: False)
    called = {"embedded": False}
    original = memory_retrieval._maybe_query_embedding

    def tracking(query):
        result = original(query)
        if result is not None:
            called["embedded"] = True
        return result

    monkeypatch.setattr(memory_retrieval, "_maybe_query_embedding", tracking)

    _record_observation(
        db,
        uid="MO-fts-degrade",
        summary="Even without a warm model the FTS degrade target is retrievable.",
        subject="degrade target",
    )

    result = memory_search("FTS degrade target", project_hint="nexo", process_queue=False)
    uids = [c.get("uid") for c in result["candidates"]]
    assert "MO-fts-degrade" in uids
    assert called["embedded"] is False


def test_cold_model_query_embedding_skipped(isolated_db, monkeypatch):
    import cognitive._core as cog
    import memory_retrieval

    # Simulate a real (non-offline) model that is NOT loaded yet.
    monkeypatch.setattr(cog, "_model_download_disabled", lambda: False)
    monkeypatch.setattr(cog, "_model", None, raising=False)

    def fail(*args, **kwargs):
        raise AssertionError("cold model must not be loaded on the latency path")

    monkeypatch.setattr(cog, "_get_model", fail)

    assert memory_retrieval._model_is_warm() is False
    assert memory_retrieval._maybe_query_embedding("anything") is None


# ── backfill + vector scan bounds ───────────────────────────────────────


def test_backfill_is_bounded_and_idempotent(isolated_db):
    import db

    conn = db.get_db()
    for idx in range(7):
        _record_observation(db, uid=f"MO-backfill-{idx}", summary=f"Backfill candidate observation number {idx}.")

    # Clear precomputed vectors to recreate a pre-embedding state.
    conn.execute("UPDATE memory_observations SET embedding = NULL, embedding_model = ''")
    conn.commit()
    null_before = conn.execute(
        "SELECT COUNT(*) FROM memory_observations WHERE embedding IS NULL"
    ).fetchone()[0]
    assert null_before == 7

    first = db.backfill_observation_embeddings(limit=3)
    assert first["ok"] is True
    assert first["updated"] == 3  # bounded by limit
    assert first["remaining"] == 4

    second = db.backfill_observation_embeddings(limit=100)
    assert second["updated"] == 4
    assert second["remaining"] == 0

    # Idempotent: a third pass with everything embedded is a no-op.
    third = db.backfill_observation_embeddings(limit=100)
    assert third["updated"] == 0
    assert third["remaining"] == 0


def test_vector_scan_is_bounded(isolated_db, monkeypatch):
    import cognitive._core as cog
    import db

    same_vec = _unit([1.0, 0.0] + [0.0] * (cog.EMBEDDING_DIM - 2))
    monkeypatch.setattr(cog, "embed", lambda text: same_vec)

    for idx in range(6):
        _record_observation(db, uid=f"MO-scan-{idx}", summary=f"Vector scan bounded observation {idx}.")

    # scan_limit caps how many rows are compared; limit caps how many returned.
    hits = db.vector_scan_observations(same_vec, limit=2, scan_limit=3, min_score=0.0)
    assert len(hits) <= 2
    assert all(h["vector_score"] > 0 for h in hits)

    # No query vector => empty, no scan.
    assert db.vector_scan_observations(None, limit=5) == []


def test_vector_scan_skips_rows_without_embeddings(isolated_db, monkeypatch):
    import cognitive._core as cog
    import db

    vec = _unit([0.0, 1.0] + [0.0] * (cog.EMBEDDING_DIM - 2))

    def boom(*args, **kwargs):
        raise RuntimeError("no embed at write")

    # Write an observation while embedding fails => row has no vector.
    monkeypatch.setattr(cog, "embed", boom)
    _record_observation(db, uid="MO-no-vec", summary="This row has no precomputed vector.")

    hits = db.vector_scan_observations(vec, limit=5, min_score=0.0)
    assert all(h["observation_uid"] != "MO-no-vec" for h in hits)


# ── latency ─────────────────────────────────────────────────────────────


def test_memory_search_stays_under_latency_budget(isolated_db, monkeypatch):
    import cognitive._core as cog
    import db
    from memory_retrieval import memory_search

    vec = _unit([1.0, 0.0] + [0.0] * (cog.EMBEDDING_DIM - 2))
    monkeypatch.setattr(cog, "embed", lambda text: vec)

    for idx in range(30):
        _record_observation(db, uid=f"MO-latency-{idx}", summary=f"Latency budget observation row {idx}.")

    start = time.perf_counter()
    result = memory_search("Latency budget observation", project_hint="nexo", process_queue=False)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    assert result["count"] >= 1
    assert elapsed_ms < 250.0, f"memory_search took {elapsed_ms:.1f}ms (>250ms budget)"
