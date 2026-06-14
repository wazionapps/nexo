"""Release A / A7 — defensive cosine, performance PRAGMAs, pinned deps.

Three independent guards:

1. ``embeddings.cosine`` must be a TRUE cosine similarity, normalized at
   comparison time WITHOUT re-embedding. Today it returns a raw dot product;
   that only happens to be correct because fastembed L2-normalizes its output,
   so it is silently fragile against a model swap (B2 → e5-small, which needs
   custom ONNX registration to normalize). The only caller is
   ``api.py`` retrieval where ``vector_score`` competes inside a ``max()`` with
   lexical scores in [0, 1] — an un-normalized dot product would blow that out.

2. The writer connection must set the performance PRAGMAs the index DB needs
   under bursty cron indexing + concurrent read-only retrieval
   (``db.py:_connect``).

3. ``src/requirements.txt`` must hard-pin fastembed AND onnxruntime so the
   offline wheel bundle is reproducible (the native onnxruntime wheel is the
   fragile, platform-specific one fastembed pulls transitively).
"""

import math
from pathlib import Path

from local_context import db as lcdb
from local_context import embeddings


# --- 1. cosine -------------------------------------------------------------

def test_cosine_normalizes_non_unit_vectors():
    # Identical direction, non-unit magnitude → cosine is 1.0, not the dot product.
    assert math.isclose(embeddings.cosine([3.0, 0.0], [3.0, 0.0]), 1.0, abs_tol=1e-9)
    assert math.isclose(embeddings.cosine([1.0, 0.0], [2.0, 0.0]), 1.0, abs_tol=1e-9)


def test_cosine_known_value_is_normalized():
    # [1,1] vs [1,0] → cos = 1/sqrt(2) ≈ 0.7071 (dot product would be 1.0).
    assert math.isclose(
        embeddings.cosine([1.0, 1.0], [1.0, 0.0]), 1.0 / math.sqrt(2.0), abs_tol=1e-9
    )


def test_cosine_orthogonal_is_zero():
    assert math.isclose(embeddings.cosine([1.0, 0.0], [0.0, 5.0]), 0.0, abs_tol=1e-9)


def test_cosine_opposite_is_minus_one():
    assert math.isclose(embeddings.cosine([2.0, 0.0], [-2.0, 0.0]), -1.0, abs_tol=1e-9)


def test_cosine_zero_vector_never_divides_by_zero():
    assert embeddings.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert embeddings.cosine([1.0, 1.0], [0.0, 0.0]) == 0.0
    assert embeddings.cosine([0.0, 0.0], [0.0, 0.0]) == 0.0


def test_cosine_guards_on_shape_and_empty():
    assert embeddings.cosine([], [1.0]) == 0.0
    assert embeddings.cosine([1.0, 2.0], [1.0]) == 0.0


def test_cosine_on_normalized_fallback_vectors_is_unchanged():
    # Already-unit fallback vectors must keep scoring self-similarity at 1.0.
    vec = embeddings.embed_text("alpha beta gamma delta")
    assert math.isclose(embeddings.cosine(vec, vec), 1.0, abs_tol=1e-6)


# --- 2. PRAGMAs ------------------------------------------------------------

def test_connect_sets_performance_pragmas(tmp_path):
    conn = lcdb._connect(Path(tmp_path) / "perf.db")
    try:
        wal_autocheckpoint = int(conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0])
        mmap_size = int(conn.execute("PRAGMA mmap_size").fetchone()[0])
        cache_size = int(conn.execute("PRAGMA cache_size").fetchone()[0])
    finally:
        conn.close()

    # wal_autocheckpoint must be explicitly tuned above the 1000-page default so
    # bursty indexing checkpoints less often while keeping the WAL bounded.
    assert wal_autocheckpoint == 2000
    # Memory-mapped I/O must be enabled (256 MB) for the read-heavy index DB.
    assert mmap_size >= 256 * 1024 * 1024
    # Negative cache_size = KiB; must be at least a 16 MB page cache.
    assert cache_size <= -16000


# --- 3. pinned deps --------------------------------------------------------

def test_requirements_hard_pin_fastembed_and_onnxruntime():
    req = Path(__file__).resolve().parents[1] / "src" / "requirements.txt"
    text = req.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]

    def _pinned(pkg: str) -> bool:
        for ln in lines:
            head = ln.split(";")[0].strip()  # drop environment markers
            if head.lower().startswith(pkg) and "==" in head:
                return True
        return False

    assert _pinned("fastembed=="), "fastembed must be hard-pinned (==) for the offline wheel bundle"
    assert _pinned("onnxruntime=="), "onnxruntime must be hard-pinned (==): it is the fragile native wheel"
