"""NEXO HNSW Vector Index — Optional acceleration for cognitive search.

When memory count exceeds THRESHOLD (default 10_000), this module builds and
maintains an HNSW index for approximate nearest neighbor search. Falls back
gracefully to brute-force when hnswlib is not available or index is cold.

Usage in cognitive.search():
    from hnsw_index import hnsw_search
    candidates = hnsw_search(query_vec, store="stm", top_k=50)
    # candidates is a list of (memory_id, distance) or None if not available
"""

import os
import sqlite3
import threading
import numpy as np
from pathlib import Path
from typing import Optional

try:
    import hnswlib
    HNSWLIB_AVAILABLE = True
except ImportError:
    HNSWLIB_AVAILABLE = False

# When to activate HNSW (below this, brute force is fine)
ACTIVATION_THRESHOLD = int(os.environ.get("NEXO_HNSW_THRESHOLD", "10000"))

# Index params
EMBEDDING_DIM = 768
EF_CONSTRUCTION = 200  # Higher = better recall during build, slower
M = 16                 # Connections per node (16 is good for 768-dim)
EF_SEARCH = 50         # Higher = better recall during search

# Index file paths
_INDEX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hnsw_indices")

# In-memory indices (one per store)
_indices: dict = {}  # {"stm": hnswlib.Index, "ltm": hnswlib.Index}
_index_lock = threading.Lock()
_id_maps: dict = {}  # {"stm": {internal_id: db_id}, "ltm": {internal_id: db_id}}


def is_available() -> bool:
    """Check if HNSW is available and should be used."""
    return HNSWLIB_AVAILABLE


def _index_path(store: str) -> str:
    return os.path.join(_INDEX_DIR, f"{store}.bin")


def _id_map_path(store: str) -> str:
    return os.path.join(_INDEX_DIR, f"{store}_ids.npy")


def should_activate(store: str = "both") -> bool:
    """Check if memory count exceeds threshold, making HNSW worthwhile."""
    if not HNSWLIB_AVAILABLE:
        return False
    try:
        import cognitive
        db = cognitive._get_db()
        total = 0
        if store in ("both", "stm"):
            total += db.execute("SELECT COUNT(*) FROM stm_memories WHERE promoted_to_ltm = 0").fetchone()[0]
        if store in ("both", "ltm"):
            total += db.execute("SELECT COUNT(*) FROM ltm_memories WHERE is_dormant = 0").fetchone()[0]
        return total >= ACTIVATION_THRESHOLD
    except Exception:
        return False


def build_index(store: str) -> dict:
    """Build HNSW index from all active memories in the given store.

    Args:
        store: "stm" or "ltm"

    Returns:
        {"count": N, "store": store, "status": "built"} or error dict
    """
    if not HNSWLIB_AVAILABLE:
        return {"error": "hnswlib not installed"}

    try:
        import cognitive
        db = cognitive._get_db()
    except Exception as e:
        return {"error": str(e)}

    table = "stm_memories" if store == "stm" else "ltm_memories"
    where = "promoted_to_ltm = 0" if store == "stm" else "is_dormant = 0"

    rows = db.execute(f"SELECT id, embedding FROM {table} WHERE {where}").fetchall()
    if not rows:
        return {"count": 0, "store": store, "status": "empty"}

    count = len(rows)
    index = hnswlib.Index(space='cosine', dim=EMBEDDING_DIM)
    index.init_index(max_elements=max(count * 2, 1000), ef_construction=EF_CONSTRUCTION, M=M)
    index.set_ef(EF_SEARCH)

    id_map = {}
    vectors = []
    internal_ids = []

    for i, row in enumerate(rows):
        vec = np.frombuffer(row["embedding"], dtype=np.float32)
        if len(vec) != EMBEDDING_DIM:
            continue
        vectors.append(vec)
        internal_ids.append(i)
        id_map[i] = row["id"]

    if not vectors:
        return {"count": 0, "store": store, "status": "no_valid_vectors"}

    data = np.array(vectors, dtype=np.float32)
    ids = np.array(internal_ids, dtype=np.int64)
    index.add_items(data, ids)

    # Save to disk
    Path(_INDEX_DIR).mkdir(exist_ok=True)
    index.save_index(_index_path(store))
    np.save(_id_map_path(store), id_map)

    with _index_lock:
        _indices[store] = index
        _id_maps[store] = id_map

    return {"count": count, "store": store, "status": "built"}


def load_index(store: str) -> bool:
    """Load a previously built index from disk."""
    if not HNSWLIB_AVAILABLE:
        return False

    idx_path = _index_path(store)
    map_path = _id_map_path(store) + ".npy" if not _id_map_path(store).endswith(".npy") else _id_map_path(store)

    if not os.path.exists(idx_path):
        return False

    try:
        index = hnswlib.Index(space='cosine', dim=EMBEDDING_DIM)
        index.load_index(idx_path)
        index.set_ef(EF_SEARCH)

        id_map = np.load(map_path, allow_pickle=True).item()

        with _index_lock:
            _indices[store] = index
            _id_maps[store] = id_map
        return True
    except Exception:
        return False


def search(query_vec: np.ndarray, store: str = "stm", top_k: int = 50) -> Optional[list[tuple[int, float]]]:
    """Search the HNSW index for approximate nearest neighbors.

    Args:
        query_vec: Query embedding (768-dim float32)
        store: "stm" or "ltm"
        top_k: Number of results

    Returns:
        List of (db_memory_id, cosine_distance) or None if index not available.
        Note: hnswlib with cosine space returns 1 - cosine_similarity as distance.
    """
    with _index_lock:
        index = _indices.get(store)
        id_map = _id_maps.get(store)

    if index is None or id_map is None:
        # Try loading from disk
        if load_index(store):
            with _index_lock:
                index = _indices.get(store)
                id_map = _id_maps.get(store)
        if index is None:
            return None

    try:
        query = query_vec.reshape(1, -1).astype(np.float32)
        labels, distances = index.knn_query(query, k=min(top_k, index.get_current_count()))
        results = []
        for label, dist in zip(labels[0], distances[0]):
            db_id = id_map.get(int(label))
            if db_id is not None:
                # Convert cosine distance to similarity: sim = 1 - dist
                results.append((db_id, float(1.0 - dist)))
        return results
    except Exception:
        return None


def add_item(store: str, db_id: int, embedding: np.ndarray) -> bool:
    """Incrementally add a single item to the index (for new ingestions)."""
    with _index_lock:
        index = _indices.get(store)
        id_map = _id_maps.get(store)

    if index is None or id_map is None:
        return False

    try:
        internal_id = max(id_map.keys()) + 1 if id_map else 0
        # Resize if needed
        if index.get_current_count() >= index.get_max_elements() - 1:
            index.resize_index(index.get_max_elements() * 2)

        vec = embedding.reshape(1, -1).astype(np.float32)
        index.add_items(vec, np.array([internal_id], dtype=np.int64))

        with _index_lock:
            id_map[internal_id] = db_id
        return True
    except Exception:
        return False


def invalidate(store: str = "both"):
    """Remove indices from memory (forces rebuild on next use)."""
    with _index_lock:
        if store in ("both", "stm"):
            _indices.pop("stm", None)
            _id_maps.pop("stm", None)
        if store in ("both", "ltm"):
            _indices.pop("ltm", None)
            _id_maps.pop("ltm", None)


def stats() -> dict:
    """Return HNSW index statistics."""
    result = {
        "hnswlib_available": HNSWLIB_AVAILABLE,
        "activation_threshold": ACTIVATION_THRESHOLD,
        "indices": {},
    }
    with _index_lock:
        for store in ("stm", "ltm"):
            idx = _indices.get(store)
            if idx:
                result["indices"][store] = {
                    "count": idx.get_current_count(),
                    "max_elements": idx.get_max_elements(),
                    "ef_search": EF_SEARCH,
                }
            else:
                result["indices"][store] = {"status": "not_loaded"}
    return result
