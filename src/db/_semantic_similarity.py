from __future__ import annotations

"""Lightweight semantic similarity helpers for DB-side duplicate checks.

These helpers keep the old keyword-overlap path as deterministic fallback,
but allow selected call sites to opportunistically use the existing local
embedding stack when it is available. The goal is not to make everything
"AI-only"; it is to remove brittle exact-keyword coupling where paraphrases
should still count as "the same thing".
"""

from functools import lru_cache
import re


def normalize_similarity_text(text: str) -> str:
    """Return a compact, cache-friendly text representation."""
    clean = re.sub(r"\s+", " ", str(text or "").strip().lower())
    return clean[:1600]


def keyword_jaccard_similarity(
    text_a: str,
    text_b: str,
    *,
    keyword_extractor,
) -> float:
    """Deterministic keyword overlap score in [0, 1]."""
    keywords_a = set(keyword_extractor(text_a or ""))
    keywords_b = set(keyword_extractor(text_b or ""))
    if not keywords_a or not keywords_b:
        return 0.0
    union = keywords_a | keywords_b
    if not union:
        return 0.0
    return len(keywords_a & keywords_b) / len(union)


@lru_cache(maxsize=1024)
def _embed_cached(text: str):
    from cognitive._core import embed

    return embed(text)


def semantic_similarity_score(text_a: str, text_b: str) -> float | None:
    """Best-effort semantic similarity using the local embedding stack.

    Returns ``None`` when embeddings are unavailable or any semantic path fails,
    so callers can cleanly fall back to deterministic heuristics.
    """
    left = normalize_similarity_text(text_a)
    right = normalize_similarity_text(text_b)
    if not left or not right:
        return None
    try:
        from cognitive._core import cosine_similarity

        score = float(cosine_similarity(_embed_cached(left), _embed_cached(right)))
        if score < 0:
            return 0.0
        if score > 1:
            return 1.0
        return score
    except Exception:
        return None


def hybrid_similarity_score(
    text_a: str,
    text_b: str,
    *,
    keyword_extractor,
    strong_semantic_threshold: float,
    moderate_semantic_threshold: float,
    moderate_keyword_floor: float,
) -> float:
    """Blend semantic similarity with keyword overlap conservatively.

    Rules:
    - strong semantic match wins even if wording diverges
    - moderate semantic match only counts when there is at least some lexical
      overlap, which cuts false positives
    - if semantic scoring is unavailable, callers retain pure keyword behaviour
    """
    keyword_score = keyword_jaccard_similarity(
        text_a,
        text_b,
        keyword_extractor=keyword_extractor,
    )
    semantic_score = semantic_similarity_score(text_a, text_b)
    if semantic_score is None:
        return round(keyword_score, 4)
    if semantic_score >= strong_semantic_threshold:
        return round(max(keyword_score, semantic_score), 4)
    if semantic_score >= moderate_semantic_threshold and keyword_score >= moderate_keyword_floor:
        return round(max(keyword_score, semantic_score), 4)
    return round(keyword_score, 4)
