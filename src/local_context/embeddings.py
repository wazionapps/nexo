from __future__ import annotations

import hashlib
import math

from .util import tokenize

MODEL_ID = "nexo-local-hash-embedding"
MODEL_REVISION = "1"
DIMENSION = 128


def embed_text(text: str) -> list[float]:
    """Deterministic local embedding fallback.

    This is intentionally local and dependency-free. It gives the resolver a
    working semantic-ish retrieval substrate even on machines where the pinned
    FastEmbed model has not warmed yet. The model id/revision make it safe to
    supersede later with pinned model vectors.
    """
    vec = [0.0] * DIMENSION
    for token in tokenize(text):
        digest = hashlib.sha256(token.encode("utf-8", errors="ignore")).digest()
        idx = int.from_bytes(digest[:2], "big") % DIMENSION
        sign = -1.0 if digest[2] % 2 else 1.0
        vec[idx] += sign
    norm = math.sqrt(sum(value * value for value in vec)) or 1.0
    return [round(value / norm, 8) for value in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b)))
