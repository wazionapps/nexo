"""Safety coverage for cognitive embedding model migrations."""

import importlib
import json
import sqlite3

import numpy as np
import pytest


def _setup_conn(_core, db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _core._init_tables(conn)
    _core._migrate_lifecycle(conn)
    _core._migrate_co_activation(conn)
    _core._migrate_memory_personalization(conn)
    _core._ensure_embedding_model_state(conn)
    _core._ensure_embedding_shadow_columns(conn)
    return conn


def test_embedding_migration_resumes_without_reembedding_completed_rows(tmp_path, monkeypatch):
    _core = importlib.import_module("cognitive._core")
    db_path = tmp_path / "cognitive.db"
    conn = _setup_conn(_core, db_path)

    old_vec = np.ones(_core.EMBEDDING_DIM, dtype=np.float32).tobytes()
    ready_vec = np.full(_core.EMBEDDING_DIM, 0.2, dtype=np.float32).tobytes()
    conn.execute(
        "INSERT INTO stm_memories (content, embedding, source_type) VALUES (?, ?, ?)",
        ("already migrated", old_vec, "learning"),
    )
    conn.execute(
        "INSERT INTO stm_memories (content, embedding, source_type) VALUES (?, ?, ?)",
        ("still pending", old_vec, "learning"),
    )
    conn.execute(
        """
        UPDATE stm_memories
        SET embedding_v2 = ?, embedding_v2_model_marker = ?
        WHERE content = 'already migrated'
        """,
        (ready_vec, "new-multilingual-model"),
    )
    conn.execute(
        "INSERT INTO embedding_model_state (key, value, updated_at) VALUES (?, ?, datetime('now'))",
        ("embedding_model_marker", "old-english-model"),
    )
    conn.commit()

    class FakeModel:
        def __init__(self):
            self.texts = []

        def embed(self, texts):
            self.texts.extend(texts)
            for _text in texts:
                yield np.full(_core.EMBEDDING_DIM, 0.5, dtype=np.float32)

    fake = FakeModel()
    monkeypatch.setattr(_core, "COGNITIVE_DB", str(db_path))
    monkeypatch.setattr(_core, "_current_embedding_model_marker", lambda: "new-multilingual-model")
    monkeypatch.setattr(_core, "_get_model", lambda: fake)

    _core._auto_migrate_embeddings(conn)

    assert fake.texts == ["still pending"]
    rows = conn.execute(
        "SELECT content, embedding, embedding_v2, embedding_v2_model_marker FROM stm_memories ORDER BY id"
    ).fetchall()
    assert np.frombuffer(rows[0]["embedding"], dtype=np.float32)[0] == pytest.approx(1.0)
    assert np.frombuffer(rows[0]["embedding_v2"], dtype=np.float32)[0] == pytest.approx(0.2)
    assert np.frombuffer(rows[1]["embedding"], dtype=np.float32)[0] == pytest.approx(1.0)
    assert np.frombuffer(rows[1]["embedding_v2"], dtype=np.float32)[0] == pytest.approx(0.5)
    assert all(row["embedding_v2_model_marker"] == "new-multilingual-model" for row in rows)

    marker = conn.execute(
        "SELECT value FROM embedding_model_state WHERE key = 'embedding_model_marker'"
    ).fetchone()["value"]
    status = conn.execute(
        "SELECT value FROM embedding_model_state WHERE key = 'embedding_migration_status'"
    ).fetchone()["value"]
    assert marker == "new-multilingual-model"
    assert status == "completed"


def test_embedding_migration_completion_removes_persisted_hnsw_indexes(tmp_path, monkeypatch):
    _core = importlib.import_module("cognitive._core")
    hnsw_index = importlib.import_module("hnsw_index")
    db_path = tmp_path / "cognitive.db"
    conn = _setup_conn(_core, db_path)

    marker = "new-multilingual-model"
    vec = np.ones(_core.EMBEDDING_DIM, dtype=np.float32).tobytes()
    conn.execute(
        "INSERT INTO stm_memories (content, embedding, embedding_v2, embedding_v2_model_marker, source_type) VALUES (?, ?, ?, ?, ?)",
        ("already shadowed", vec, vec, marker, "learning"),
    )
    _core._write_embedding_state(conn, "embedding_model_marker", "old-model", commit=False)
    _core._write_embedding_state(conn, "embedding_storage", "legacy", commit=True)

    index_dir = tmp_path / "hnsw_indices"
    index_dir.mkdir()
    for name in ("stm.bin", "stm_ids.npy", "ltm.bin", "ltm_ids.npy"):
        (index_dir / name).write_bytes(b"stale-index")
    hnsw_index._indices["stm"] = object()
    hnsw_index._id_maps["stm"] = {0: 1}

    monkeypatch.setattr(_core, "COGNITIVE_DB", str(db_path))
    monkeypatch.setattr(_core, "_current_embedding_model_marker", lambda: marker)
    monkeypatch.setattr(hnsw_index, "_INDEX_DIR", str(index_dir))
    monkeypatch.setattr(hnsw_index, "is_available", lambda: False)

    _core._auto_migrate_embeddings(conn)

    assert _core.embedding_migration_status(conn)["status"] == "completed"
    for name in ("stm.bin", "stm_ids.npy", "ltm.bin", "ltm_ids.npy"):
        assert not (index_dir / name).exists()
    assert "stm" not in hnsw_index._indices
    assert "stm" not in hnsw_index._id_maps
    assert (
        conn.execute(
            "SELECT value FROM embedding_model_state WHERE key = 'embedding_hnsw_invalidated_marker'"
        ).fetchone()["value"]
        == marker
    )


def test_completed_shadow_migration_still_removes_stale_hnsw_indexes_once(tmp_path, monkeypatch):
    _core = importlib.import_module("cognitive._core")
    hnsw_index = importlib.import_module("hnsw_index")
    db_path = tmp_path / "cognitive.db"
    conn = _setup_conn(_core, db_path)

    marker = "new-multilingual-model"
    vec = np.ones(_core.EMBEDDING_DIM, dtype=np.float32).tobytes()
    conn.execute(
        "INSERT INTO stm_memories (content, embedding, embedding_v2, embedding_v2_model_marker, source_type) VALUES (?, ?, ?, ?, ?)",
        ("already completed", vec, vec, marker, "learning"),
    )
    _core._write_embedding_state(conn, "embedding_model_marker", marker, commit=False)
    _core._write_embedding_state(conn, "embedding_storage", "shadow_v2", commit=False)
    _core._write_embedding_state(conn, "embedding_migration_status", "completed", commit=True)

    index_dir = tmp_path / "hnsw_indices_completed"
    index_dir.mkdir()
    for name in ("stm.bin", "stm_ids.npy", "ltm.bin", "ltm_ids.npy"):
        (index_dir / name).write_bytes(b"stale-index")

    monkeypatch.setattr(_core, "_current_embedding_model_marker", lambda: marker)
    monkeypatch.setattr(hnsw_index, "_INDEX_DIR", str(index_dir))

    _core._auto_migrate_embeddings(conn)

    for name in ("stm.bin", "stm_ids.npy", "ltm.bin", "ltm_ids.npy"):
        assert not (index_dir / name).exists()
    assert (
        conn.execute(
            "SELECT value FROM embedding_model_state WHERE key = 'embedding_hnsw_invalidated_marker'"
        ).fetchone()["value"]
        == marker
    )

    for name in ("stm.bin", "stm_ids.npy", "ltm.bin", "ltm_ids.npy"):
        (index_dir / name).write_bytes(b"fresh-index")

    _core._auto_migrate_embeddings(conn)

    for name in ("stm.bin", "stm_ids.npy", "ltm.bin", "ltm_ids.npy"):
        assert (index_dir / name).exists()


def test_search_reads_shadow_embedding_after_migration_activation(tmp_path, monkeypatch):
    _core = importlib.import_module("cognitive._core")
    _search = importlib.import_module("cognitive._search")
    db_path = tmp_path / "cognitive.db"
    conn = _setup_conn(_core, db_path)

    query_vec = np.ones(_core.EMBEDDING_DIM, dtype=np.float32)
    old_vec = -query_vec
    new_vec = query_vec
    conn.execute(
        "INSERT INTO stm_memories (content, embedding, source_type) VALUES (?, ?, ?)",
        ("shadow vector should win", old_vec.tobytes(), "learning"),
    )
    conn.execute(
        """
        UPDATE stm_memories
        SET embedding_v2 = ?, embedding_v2_model_marker = ?
        WHERE content = 'shadow vector should win'
        """,
        (new_vec.tobytes(), "new-multilingual-model"),
    )
    _core._write_embedding_state(conn, "embedding_storage", "shadow_v2")
    _core._write_embedding_model_marker(conn, "new-multilingual-model")

    monkeypatch.setattr(_core, "COGNITIVE_DB", str(db_path))
    monkeypatch.setattr(_core, "_conn", conn)
    monkeypatch.setattr(_core, "_current_embedding_model_marker", lambda: "new-multilingual-model")
    monkeypatch.setattr(_search, "embed", lambda _text: query_vec)

    results = _search.search(
        "find the migrated vector",
        top_k=3,
        min_score=0.9,
        stores="stm",
        hybrid=False,
        use_hyde=False,
        spreading_depth=0,
        decompose=False,
    )

    assert results
    assert results[0]["content"] == "shadow vector should win"
    assert results[0]["score"] >= 0.95


def test_embedding_migration_status_reports_failed_without_model_warmup(tmp_path, monkeypatch):
    _core = importlib.import_module("cognitive._core")
    db_path = tmp_path / "cognitive.db"
    conn = _setup_conn(_core, db_path)

    old_vec = np.ones(_core.EMBEDDING_DIM, dtype=np.float32).tobytes()
    conn.execute(
        "INSERT INTO stm_memories (content, embedding, source_type) VALUES (?, ?, ?)",
        ("needs migration", old_vec, "learning"),
    )
    _core._write_embedding_state(conn, "embedding_model_marker", "old-model", commit=False)
    _core._write_embedding_state(conn, "embedding_storage", "legacy", commit=False)
    _core._record_embedding_migration_run(
        conn,
        old_marker="old-model",
        new_marker="new-multilingual-model",
        status="failed",
        total_rows=1,
        migrated_rows=0,
        error="fixture failure",
    )

    monkeypatch.setattr(_core, "_current_embedding_model_marker", lambda: "new-multilingual-model")
    monkeypatch.setattr(
        _core,
        "_get_model",
        lambda: (_ for _ in ()).throw(AssertionError("status must not warm up models")),
    )

    status = _core.embedding_migration_status(conn)

    assert status["ok"] is False
    assert status["healthy"] is False
    assert status["status"] == "failed"
    assert status["total_rows"] == 1
    assert status["pending_rows"] == 1
    assert status["error"] == "fixture failure"


def test_embedding_migration_status_reports_completed_shadow_storage(tmp_path, monkeypatch):
    _core = importlib.import_module("cognitive._core")
    db_path = tmp_path / "cognitive.db"
    conn = _setup_conn(_core, db_path)

    vec = np.ones(_core.EMBEDDING_DIM, dtype=np.float32).tobytes()
    conn.execute(
        "INSERT INTO stm_memories (content, embedding, source_type) VALUES (?, ?, ?)",
        ("done", vec, "learning"),
    )
    conn.execute(
        """
        UPDATE stm_memories
        SET embedding_v2 = ?, embedding_v2_model_marker = ?
        WHERE content = 'done'
        """,
        (vec, "new-multilingual-model"),
    )
    _core._write_embedding_state(conn, "embedding_model_marker", "new-multilingual-model", commit=False)
    _core._write_embedding_state(conn, "embedding_storage", "shadow_v2", commit=False)
    _core._write_embedding_state(conn, "embedding_migration_status", "completed", commit=True)
    monkeypatch.setattr(_core, "_current_embedding_model_marker", lambda: "new-multilingual-model")

    status = _core.embedding_migration_status(conn)

    assert status["ok"] is True
    assert status["status"] == "completed"
    assert status["uses_shadow"] is True
    assert status["migrated_rows"] == 1
    assert status["pending_rows"] == 0


def test_embedding_migration_status_does_not_call_legacy_current_completed_without_shadow(tmp_path, monkeypatch):
    _core = importlib.import_module("cognitive._core")
    db_path = tmp_path / "cognitive.db"
    conn = _setup_conn(_core, db_path)

    vec = np.ones(_core.EMBEDDING_DIM, dtype=np.float32).tobytes()
    conn.execute(
        "INSERT INTO stm_memories (content, embedding, source_type) VALUES (?, ?, ?)",
        ("legacy-only row", vec, "learning"),
    )
    _core._write_embedding_state(conn, "embedding_model_marker", "new-multilingual-model", commit=False)
    _core._write_embedding_state(conn, "embedding_storage", "legacy", commit=True)
    _core._write_embedding_state(conn, "embedding_migration_status", "completed", commit=True)
    monkeypatch.setattr(_core, "_current_embedding_model_marker", lambda: "new-multilingual-model")

    status = _core.embedding_migration_status(conn)

    assert status["status"] == "pending"
    assert status["uses_shadow"] is False
    assert status["needs_migration"] is True
    assert status["migrated_rows"] == 0
    assert status["pending_rows"] == 1


def test_process_quarantine_keeps_pending_when_migration_failed_hides_legacy_vector(tmp_path, monkeypatch):
    _core = importlib.import_module("cognitive._core")
    _ingest = importlib.import_module("cognitive._ingest")
    db_path = tmp_path / "cognitive.db"
    conn = _setup_conn(_core, db_path)

    vec = np.ones(_core.EMBEDDING_DIM, dtype=np.float32).tobytes()
    conn.execute(
        "INSERT INTO quarantine (content, embedding, source_type, source) VALUES (?, ?, ?, ?)",
        ("pending during failed migration", vec, "learning", "inferred"),
    )
    _core._write_embedding_state(conn, "embedding_model_marker", "old-model", commit=False)
    _core._write_embedding_state(conn, "embedding_storage", "legacy", commit=False)
    _core._write_embedding_state(conn, "embedding_migration_status", "failed", commit=True)

    monkeypatch.setattr(_core, "_current_embedding_model_marker", lambda: "new-multilingual-model")
    monkeypatch.setattr(_ingest, "_get_db", lambda: conn)

    result = _ingest.process_quarantine()
    row = conn.execute("SELECT status, promotion_checks FROM quarantine").fetchone()

    assert result["rejected"] == 0
    assert result["still_pending"] == 1
    assert row["status"] == "pending"
    assert row["promotion_checks"] == 1


def test_dream_cycle_writes_shadow_embedding_when_shadow_storage_is_active(tmp_path, monkeypatch):
    _core = importlib.import_module("cognitive._core")
    _decay = importlib.import_module("cognitive._decay")
    db_path = tmp_path / "cognitive.db"
    conn = _setup_conn(_core, db_path)

    marker = "new-multilingual-model"
    vec_a = np.zeros(_core.EMBEDDING_DIM, dtype=np.float32)
    vec_a[0] = 1.0
    vec_b = np.zeros(_core.EMBEDDING_DIM, dtype=np.float32)
    vec_b[0] = 0.5
    vec_b[1] = np.sqrt(0.75)
    for content, vec in (("dream source a", vec_a), ("dream source b", vec_b)):
        conn.execute(
            """
            INSERT INTO stm_memories (
                content, embedding, embedding_v2, embedding_v2_model_marker,
                source_type, source_title, domain
            )
            VALUES (?, ?, ?, ?, 'learning', ?, 'tests')
            """,
            (content, vec.tobytes(), vec.tobytes(), marker, content),
        )
    _core._write_embedding_state(conn, "embedding_model_marker", marker, commit=False)
    _core._write_embedding_state(conn, "embedding_storage", "shadow_v2", commit=False)
    _core._write_embedding_state(conn, "embedding_migration_status", "completed", commit=True)

    monkeypatch.setattr(_core, "_current_embedding_model_marker", lambda: marker)
    monkeypatch.setattr(_decay, "_get_db", lambda: conn)

    result = _decay.dream_cycle(max_insights=1)
    row = conn.execute(
        """
        SELECT embedding_v2, embedding_v2_model_marker
        FROM ltm_memories
        WHERE source_type = 'dream_insight'
        """
    ).fetchone()
    status = _core.embedding_migration_status(conn)

    assert result["insights_created"] == 1
    assert row is not None
    assert row["embedding_v2"] is not None
    assert row["embedding_v2_model_marker"] == marker
    assert status["pending_rows"] == 0


def test_embedding_migration_resumes_large_interrupted_run_across_all_tables(tmp_path, monkeypatch):
    _core = importlib.import_module("cognitive._core")
    db_path = tmp_path / "cognitive.db"
    conn = _setup_conn(_core, db_path)
    old_vec = np.ones(_core.EMBEDDING_DIM, dtype=np.float32).tobytes()
    expected_rows = []

    for table, prefix in (
        ("stm_memories", "stm"),
        ("ltm_memories", "ltm"),
        ("quarantine", "quarantine"),
    ):
        for index in range(3):
            content = f"{prefix} row {index}"
            expected_rows.append(content)
            if table == "stm_memories":
                conn.execute(
                    "INSERT INTO stm_memories (content, embedding, source_type) VALUES (?, ?, ?)",
                    (content, old_vec, "learning"),
                )
            elif table == "ltm_memories":
                conn.execute(
                    "INSERT INTO ltm_memories (content, embedding, source_type) VALUES (?, ?, ?)",
                    (content, old_vec, "learning"),
                )
            else:
                conn.execute(
                    "INSERT INTO quarantine (content, embedding, source_type) VALUES (?, ?, ?)",
                    (content, old_vec, "learning"),
                )
    _core._write_embedding_state(conn, "embedding_model_marker", "old-model", commit=True)

    class FlakyModel:
        def __init__(self):
            self.calls = 0
            self.texts = []

        def embed(self, texts):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("simulated power loss")
            self.texts.extend(texts)
            for _text in texts:
                yield np.full(_core.EMBEDDING_DIM, 0.5, dtype=np.float32)

    flaky = FlakyModel()
    monkeypatch.setattr(_core, "COGNITIVE_DB", str(db_path))
    monkeypatch.setattr(_core, "EMBEDDING_MIGRATION_BATCH_SIZE", 2)
    monkeypatch.setattr(_core, "_current_embedding_model_marker", lambda: "new-multilingual-model")
    monkeypatch.setattr(_core, "_get_model", lambda: flaky)

    _core._auto_migrate_embeddings(conn)

    first_status = _core.embedding_migration_status(conn)
    assert first_status["status"] == "failed"
    assert first_status["uses_shadow"] is False
    assert first_status["migrated_rows"] == 2
    assert first_status["pending_rows"] == 7

    class ResumeModel:
        def __init__(self):
            self.texts = []

        def embed(self, texts):
            self.texts.extend(texts)
            for _text in texts:
                yield np.full(_core.EMBEDDING_DIM, 0.8, dtype=np.float32)

    resume = ResumeModel()
    monkeypatch.setattr(_core, "_get_model", lambda: resume)

    _core._auto_migrate_embeddings(conn)

    final_status = _core.embedding_migration_status(conn)
    assert final_status["status"] == "completed"
    assert final_status["uses_shadow"] is True
    assert final_status["migrated_rows"] == 9
    assert final_status["pending_rows"] == 0
    assert final_status["errored_rows"] == 0
    assert resume.texts == expected_rows[2:]
    by_table = final_status["by_table"]
    assert by_table["stm_memories"]["migrated"] == 3
    assert by_table["ltm_memories"]["migrated"] == 3
    assert by_table["quarantine"]["migrated"] == 3


def test_search_does_not_compare_current_query_to_failed_legacy_embeddings(tmp_path, monkeypatch):
    _core = importlib.import_module("cognitive._core")
    _search = importlib.import_module("cognitive._search")
    db_path = tmp_path / "cognitive.db"
    conn = _setup_conn(_core, db_path)

    query_vec = np.ones(_core.EMBEDDING_DIM, dtype=np.float32)
    conn.execute(
        "INSERT INTO stm_memories (content, embedding, source_type) VALUES (?, ?, ?)",
        ("legacy vector must be ignored", query_vec.tobytes(), "learning"),
    )
    _core._write_embedding_state(conn, "embedding_model_marker", "old-model", commit=False)
    _core._write_embedding_state(conn, "embedding_storage", "legacy", commit=False)
    _core._write_embedding_state(conn, "embedding_migration_status", "failed", commit=True)

    monkeypatch.setattr(_core, "COGNITIVE_DB", str(db_path))
    monkeypatch.setattr(_core, "_conn", conn)
    monkeypatch.setattr(_core, "_current_embedding_model_marker", lambda: "new-multilingual-model")
    monkeypatch.setattr(_search, "embed", lambda _text: query_vec)

    results = _search.search(
        "find the old vector",
        top_k=3,
        min_score=0.0,
        stores="stm",
        hybrid=False,
        use_hyde=False,
        spreading_depth=0,
        decompose=False,
    )

    assert results == []


def test_server_embedding_migration_status_tool_returns_schema_without_warmup(monkeypatch):
    server = importlib.import_module("server")
    cognitive = importlib.import_module("cognitive")
    monkeypatch.setattr(
        cognitive,
        "embedding_migration_status",
        lambda: {
            "ok": True,
            "healthy": True,
            "status": "no_database",
            "schema": "nexo.embedding_migration_status.v1",
        },
    )

    payload = json.loads(server.nexo_embedding_migration_status())

    assert payload["ok"] is True
    assert payload["schema"] == "nexo.embedding_migration_status.v1"
