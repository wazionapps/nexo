"""Tests for cognitive engine: embeddings, cosine similarity, search, KG boost, decay."""

import math
import numpy as np


def test_cosine_similarity_identical():
    """Identical vectors should have similarity 1.0."""
    import cognitive
    a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    assert abs(cognitive.cosine_similarity(a, a) - 1.0) < 1e-6


def test_cosine_similarity_orthogonal():
    """Orthogonal vectors should have similarity 0.0."""
    import cognitive
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    assert abs(cognitive.cosine_similarity(a, b)) < 1e-6


def test_cosine_similarity_zero_norm():
    """Zero vector should return 0.0 (not NaN)."""
    import cognitive
    a = np.zeros(3, dtype=np.float32)
    b = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    assert cognitive.cosine_similarity(a, b) == 0.0
    assert cognitive.cosine_similarity(b, a) == 0.0


def test_blob_roundtrip():
    """Array → blob → array should be lossless."""
    import cognitive
    arr = np.random.randn(768).astype(np.float32)
    blob = cognitive._array_to_blob(arr)
    recovered = cognitive._blob_to_array(blob)
    np.testing.assert_array_equal(arr, recovered)


def test_kg_boost_results_no_kg_data():
    """KG boost should be a no-op when there are no KG nodes."""
    import cognitive
    results = [
        {"source_type": "learning", "source_id": "L999", "score": 0.7},
        {"source_type": "sensory", "source_id": "buffer#1", "score": 0.6},
    ]
    boosted = cognitive._kg_boost_results(results)
    # No KG nodes in test DB → no boost applied
    assert boosted[0]["score"] == 0.7
    assert boosted[1]["score"] == 0.6
    assert "kg_boost" not in boosted[0]


def test_kg_boost_results_with_connections():
    """KG boost should increase scores for connected nodes."""
    import cognitive
    import knowledge_graph as kg

    db = cognitive._get_db()
    # Initialize KG tables
    db.executescript("""
        CREATE TABLE IF NOT EXISTS kg_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_type TEXT NOT NULL,
            node_ref TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL DEFAULT '',
            properties TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS kg_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            relation TEXT NOT NULL DEFAULT '',
            weight REAL DEFAULT 1.0,
            confidence REAL DEFAULT 1.0,
            valid_from TEXT,
            valid_until TEXT,
            source_memory_id TEXT DEFAULT '',
            properties TEXT DEFAULT '{}'
        );
    """)

    # Create a learning node with 8 connections
    node_id = db.execute(
        "INSERT INTO kg_nodes (node_type, node_ref, label) VALUES (?, ?, ?)",
        ("learning", "learning:42", "Test Learning")
    ).lastrowid

    for i in range(8):
        file_id = db.execute(
            "INSERT INTO kg_nodes (node_type, node_ref, label) VALUES (?, ?, ?)",
            ("file", f"file:test{i}.py", f"test{i}.py")
        ).lastrowid
        db.execute(
            "INSERT INTO kg_edges (source_id, target_id, relation, weight) VALUES (?, ?, ?, ?)",
            (node_id, file_id, "touched", 1.0)
        )
    db.commit()

    results = [
        {"source_type": "learning", "source_id": "L42", "score": 0.6},
        {"source_type": "sensory", "source_id": "buffer#1", "score": 0.6},
    ]
    boosted = cognitive._kg_boost_results(results)

    # Learning with 8 edges should get boost
    assert boosted[0].get("kg_boost") is not None
    assert boosted[0]["score"] > 0.6
    expected_boost = min(0.08, 0.015 * math.log2(8 + 1))
    assert abs(boosted[0]["kg_boost"] - round(expected_boost, 4)) < 0.001

    # Sensory with no KG node should NOT get boost
    assert "kg_boost" not in boosted[1]


def test_kg_boost_relevance_gate():
    """KG boost should not apply to low-relevance results (score < 0.45)."""
    import cognitive

    db = cognitive._get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS kg_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_type TEXT NOT NULL,
            node_ref TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL DEFAULT '',
            properties TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS kg_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            relation TEXT NOT NULL DEFAULT '',
            weight REAL DEFAULT 1.0,
            confidence REAL DEFAULT 1.0,
            valid_from TEXT,
            valid_until TEXT,
            source_memory_id TEXT DEFAULT '',
            properties TEXT DEFAULT '{}'
        );
    """)

    node_id = db.execute(
        "INSERT INTO kg_nodes (node_type, node_ref, label) VALUES (?, ?, ?)",
        ("learning", "learning:99", "Low Score Learning")
    ).lastrowid
    for i in range(20):
        fid = db.execute(
            "INSERT INTO kg_nodes (node_type, node_ref, label) VALUES (?, ?, ?)",
            ("file", f"file:low{i}.py", f"low{i}.py")
        ).lastrowid
        db.execute(
            "INSERT INTO kg_edges (source_id, target_id, relation, weight) VALUES (?, ?, ?, ?)",
            (node_id, fid, "touched", 1.0)
        )
    db.commit()

    results = [
        {"source_type": "learning", "source_id": "L99", "score": 0.3},
    ]
    boosted = cognitive._kg_boost_results(results)
    # Score below 0.45 gate → no boost
    assert boosted[0]["score"] == 0.3
    assert "kg_boost" not in boosted[0]


def test_decay_formula():
    """Ebbinghaus decay should reduce strength over time (lambda operates on HOURS)."""
    import cognitive
    # STM decay: lambda=0.004126, after 7 days (168h) strength should be ~0.5
    initial = 1.0
    hours_7d = 7 * 24
    decayed = initial * math.exp(-cognitive.LAMBDA_STM * hours_7d)
    assert 0.45 < decayed < 0.55

    # LTM decay: lambda=0.000481, after 60 days (1440h) strength should be ~0.5
    hours_60d = 60 * 24
    decayed_ltm = initial * math.exp(-cognitive.LAMBDA_LTM * hours_60d)
    assert 0.45 < decayed_ltm < 0.55


def test_apply_temporal_boost_historical():
    """Historical queries should get no temporal boost."""
    import cognitive
    results = [
        {"source_type": "learning", "source_id": "L1", "score": 0.7,
         "created_at": "2026-03-28T10:00:00"},
    ]
    boosted = cognitive._apply_temporal_boost(results, "what happened months ago")
    # Historical cue "months" should disable boost
    assert boosted[0]["score"] == 0.7


def test_apply_temporal_boost_operational():
    """Operational queries should get a higher temporal boost."""
    import cognitive
    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    results = [
        {"source_type": "learning", "source_id": "L1", "score": 0.7,
         "created_at": now_str},
    ]
    boosted = cognitive._apply_temporal_boost(results, "active backend issues today")
    # Very recent + operational → should get noticeable boost
    assert boosted[0]["score"] > 0.7


def test_auto_hyde_prefers_conceptual_queries():
    import importlib
    _search = importlib.import_module("cognitive._search")

    assert _search._auto_use_hyde("why does the deploy backend keep drifting after updates") is True
    assert _search._auto_use_hyde("src/server.py line 42 exact error") is False


def test_auto_spreading_depth_stays_off_for_exact_lookups():
    import importlib
    _search = importlib.import_module("cognitive._search")

    assert _search._auto_spreading_depth("how are shopify auth retries related to webhook drift") == 1
    assert _search._auto_spreading_depth("path /Users/test/app.py exact port 6174") == 0


def test_result_confidence_labels():
    import importlib
    _search = importlib.import_module("cognitive._search")

    assert _search._result_confidence(0.9) == "high"
    assert _search._result_confidence(0.7) == "medium"
    assert _search._result_confidence(0.5) == "low"


def test_spreading_activation_keeps_top_k_and_explains_auto_strategy(monkeypatch):
    import importlib

    _search = importlib.import_module("cognitive._search")

    class _Cursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class _DB:
        def execute(self, sql, params=()):
            if "FROM stm_memories" in sql and "SELECT *" in sql:
                return _Cursor([
                    {"id": 1, "embedding": np.array([0.92], dtype=np.float32), "content": "alpha", "source_type": "learning", "source_id": "L1", "source_title": "A", "domain": "nexo", "created_at": "2026-04-05T01:00:00", "strength": 0.9, "access_count": 2, "lifecycle_state": "active"},
                    {"id": 2, "embedding": np.array([0.81], dtype=np.float32), "content": "beta", "source_type": "learning", "source_id": "L2", "source_title": "B", "domain": "nexo", "created_at": "2026-04-05T01:00:00", "strength": 0.8, "access_count": 1, "lifecycle_state": "active"},
                    {"id": 3, "embedding": np.array([0.22], dtype=np.float32), "content": "neighbor", "source_type": "learning", "source_id": "L3", "source_title": "C", "domain": "nexo", "created_at": "2026-04-05T01:00:00", "strength": 0.4, "access_count": 0, "lifecycle_state": "active"},
                ])
            if "FROM ltm_memories" in sql and "SELECT *" in sql:
                return _Cursor([
                    {"id": 10, "embedding": np.array([0.18], dtype=np.float32), "content": "ltm-neighbor", "source_type": "learning", "source_id": "L10", "source_title": "D", "domain": "nexo", "created_at": "2026-04-05T01:00:00", "strength": 0.5, "access_count": 0, "tags": "", "lifecycle_state": "active", "is_dormant": 0},
                ])
            return _Cursor([])

        def commit(self):
            return None

    monkeypatch.setattr(_search, "_get_db", lambda: _DB())
    monkeypatch.setattr(_search, "_blob_to_array", lambda value: value)
    monkeypatch.setattr(_search, "embed", lambda query: np.array([1.0], dtype=np.float32))
    monkeypatch.setattr(_search, "hyde_expand_query", lambda query: np.array([1.0], dtype=np.float32))
    monkeypatch.setattr(_search, "cosine_similarity", lambda query, vec: float(vec[0]))
    monkeypatch.setattr(_search, "_auto_use_hyde", lambda query, source_type_filter="": True)
    monkeypatch.setattr(_search, "_auto_spreading_depth", lambda query, source_type_filter="": 1)
    monkeypatch.setattr(_search, "_apply_temporal_boost", lambda results, query: results)
    monkeypatch.setattr(_search, "_kg_boost_results", lambda results: results)
    monkeypatch.setattr(_search, "_auto_restore_snoozed", lambda db: None)
    monkeypatch.setattr(_search, "_rehearse_results", lambda results, skip_ids=None: None)
    monkeypatch.setattr(_search, "record_co_activation", lambda items: None)
    monkeypatch.setattr(_search, "_get_co_activated_neighbors", lambda ids, depth=1: {
        _search._canonical_co_id("stm", 3): 0.08,
        _search._canonical_co_id("ltm", 10): 0.06,
    })

    results = _search.search(
        "how are these deploy issues related",
        top_k=2,
        min_score=0.1,
        hybrid=False,
        rehearse=False,
    )

    assert len(results) == 2
    assert all("confidence=" in item["explanation"] for item in results)
    assert any("auto_strategy=" in item["explanation"] for item in results)


def test_memory_personalization_changes_decay_rate():
    import cognitive

    easier = cognitive.personalize_decay_rate(
        cognitive.LAMBDA_STM,
        stability=1.8,
        difficulty=0.3,
    )
    harder = cognitive.personalize_decay_rate(
        cognitive.LAMBDA_STM,
        stability=0.8,
        difficulty=0.9,
    )
    assert easier < harder


def test_rehearsal_profile_update_rewards_strong_recall():
    import cognitive

    stable, difficulty = cognitive.rehearsal_profile_update(1.0, 0.6, 0.9)
    assert stable > 1.0
    assert difficulty < 0.6


def test_preview_triggers_does_not_fire_them():
    import cognitive

    trigger_id = cognitive.create_trigger(
        "release",
        "Validate release readiness before launch.",
        "Public release tasks need evidence first.",
    )

    preview = cognitive.preview_triggers("Prepare the release package today")
    armed = cognitive.list_triggers("armed")
    fired = cognitive.list_triggers("fired")

    assert preview
    assert preview[0]["id"] == trigger_id
    assert any(trigger["id"] == trigger_id for trigger in armed)
    assert all(trigger["id"] != trigger_id for trigger in fired)
