from __future__ import annotations

import hashlib
import math
import os
import warnings
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .util import tokenize

FALLBACK_MODEL_ID = "nexo-local-hash-embedding"
FALLBACK_MODEL_REVISION = "1"
FALLBACK_DIMENSION = 128
PRIMARY_MODEL_SPEC = "bge-base-embeddings"

# Backward-compatible constants. Callers that persist vectors should use
# embed_record(), because the active profile can switch from fallback to BGE.
MODEL_ID = FALLBACK_MODEL_ID
MODEL_REVISION = FALLBACK_MODEL_REVISION
DIMENSION = FALLBACK_DIMENSION


@dataclass(frozen=True)
class EmbeddingProfile:
    model_id: str
    model_revision: str
    dimension: int
    kind: str
    state: str
    profile: str
    problems: tuple[str, ...] = ()


def _hash_embed_text(text: str) -> list[float]:
    vec = [0.0] * FALLBACK_DIMENSION
    for token in tokenize(text):
        digest = hashlib.sha256(token.encode("utf-8", errors="ignore")).digest()
        idx = int.from_bytes(digest[:2], "big") % FALLBACK_DIMENSION
        sign = -1.0 if digest[2] % 2 else 1.0
        vec[idx] += sign
    norm = math.sqrt(sum(value * value for value in vec)) or 1.0
    return [round(value / norm, 8) for value in vec]


def _fallback_profile(*problems: str) -> EmbeddingProfile:
    return EmbeddingProfile(
        model_id=FALLBACK_MODEL_ID,
        model_revision=FALLBACK_MODEL_REVISION,
        dimension=FALLBACK_DIMENSION,
        kind="deterministic_embedding",
        state="available",
        profile="local_context_embedding_fallback",
        problems=tuple(item for item in problems if item),
    )


def _fastembed_disabled() -> bool:
    value = os.environ.get("NEXO_LOCAL_CONTEXT_DISABLE_FASTEMBED", "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    # The unit suite uses temporary NEXO homes that intentionally do not carry
    # model weights. Keep those tests dependency-free unless explicitly opted in.
    if os.environ.get("NEXO_TEST_DB") and os.environ.get("NEXO_LOCAL_CONTEXT_FASTEMBED_IN_TESTS") != "1":
        return True
    return False


@lru_cache(maxsize=1)
def _fastembed_state() -> tuple[Any, EmbeddingProfile] | tuple[None, EmbeddingProfile]:
    if _fastembed_disabled():
        return None, _fallback_profile("fastembed disabled for this process")
    try:
        import local_models
        from fastembed import TextEmbedding

        spec = local_models.get_local_model_spec(PRIMARY_MODEL_SPEC)
        target_dir = local_models.ensure_local_model(spec.name, local_files_only=True)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r"The model .* now uses mean pooling.*", category=UserWarning)
            model = TextEmbedding(spec.model_id, specific_model_path=str(target_dir))
        return model, EmbeddingProfile(
            model_id=spec.model_id,
            model_revision=spec.revision,
            dimension=spec.dimension or 384,
            kind=spec.kind,
            state="available",
            profile=spec.name,
        )
    except Exception as exc:  # pragma: no cover - host/cache dependent
        return None, _fallback_profile(str(exc))


def active_profile() -> EmbeddingProfile:
    _model, profile = _fastembed_state()
    return profile


def reset_cache() -> None:
    _fastembed_state.cache_clear()


def embed_record(text: str) -> dict[str, Any]:
    model, profile = _fastembed_state()
    if model is not None and profile.kind == "fastembed_embedding":
        try:
            vector = list(next(iter(model.embed([text or ""]))))
            return {
                "vector": [float(value) for value in vector],
                "model_id": profile.model_id,
                "model_revision": profile.model_revision,
                "dimension": profile.dimension,
                "profile": profile.profile,
                "kind": profile.kind,
            }
        except Exception:  # pragma: no cover - runtime fallback only
            pass
    fallback = _fallback_profile()
    return {
        "vector": _hash_embed_text(text),
        "model_id": fallback.model_id,
        "model_revision": fallback.model_revision,
        "dimension": fallback.dimension,
        "profile": fallback.profile,
        "kind": fallback.kind,
    }


def embed_text(text: str) -> list[float]:
    return embed_record(text)["vector"]


def cosine(a: list[float], b: list[float]) -> float:
    # Defensive cosine: normalize at comparison time WITHOUT re-embedding.
    # The fallback hash embedding is already L2-normalized and fastembed
    # L2-normalizes its output too, so a bare dot product happens to be correct
    # today — but it silently breaks the moment a model that does not normalize
    # is swapped in (e.g. e5-small needs custom ONNX MEAN-pool + normalize).
    # Dividing by the product of norms keeps the score bounded to [-1, 1] for
    # any vectors, which is what the api.py max() fusion against lexical scores
    # in [0, 1] relies on. For already-unit vectors this is a no-op.
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return float(dot / math.sqrt(norm_a * norm_b))
