"""Plan Consolidado 0.21 — Local zero-shot multilingual classifier.

Skeleton + pinned HuggingFace coordinates. The heavy load
(`transformers`, ~500 MB model download) is lazy so the rest of the
runtime does not pay the cost on every import.

Contract:

    clf = LocalZeroShotClassifier()
    result = clf.classify(
        "lo hemos dejado, ya estaría",
        labels=("done_claim", "status_update", "question", "noise"),
    )
    result == {"label": "done_claim", "confidence": 0.87, "scores": {...}}

When transformers is not installed or the download fails (offline),
`classify` returns `None` and `classify_fail_closed` returns a
conservative fallback label so rules degrade gracefully (item 0.20).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Iterable

_logger = logging.getLogger(__name__)


# Keep in lockstep with docs/classifier-model-notes.md.
# Plan 0.21 wave-2 update: the original pin
# (MoritzLaurer/mDeBERTa-v3-base-mnli-xnli @ a1a5a76) refused to load
# under transformers 5.x with a missing `model_type` error. Switched
# to the multilingual-2mil7 sibling which is the same DeBERTa-v2
# architecture, multilingual, and loads cleanly. Revision pinned to
# the last HF upstream commit verified in smoke.
MODEL_ID = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
MODEL_REVISION = "b5113eb38ab63efdd7f280f8c144ea8b13f978ce"
DEFAULT_CONFIDENCE_FLOOR = 0.6


@dataclass
class ClassificationResult:
    label: str
    confidence: float
    scores: dict[str, float]
    latency_ms: float


class LocalZeroShotClassifier:
    """Lazy wrapper around transformers' zero-shot-classification pipeline.

    Thread-safe lazy load; failures degrade to `classify(...) = None` so
    the Guardian can decide whether to invoke the LLM fallback
    (`call_model_raw`) or a conservative regex path.
    """

    def __init__(
        self,
        *,
        model_id: str = MODEL_ID,
        revision: str = MODEL_REVISION,
        confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
    ) -> None:
        self.model_id = model_id
        self.revision = revision
        self.confidence_floor = confidence_floor
        self._pipe = None
        self._load_failed = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lazy load
    # ------------------------------------------------------------------
    def _ensure_loaded(self) -> bool:
        if self._pipe is not None:
            return True
        if self._load_failed:
            return False
        with self._lock:
            if self._pipe is not None:
                return True
            if self._load_failed:
                return False
            try:
                from transformers import pipeline  # type: ignore
            except Exception as exc:  # pragma: no cover — no HF on CI
                _logger.warning(
                    "classifier_local disabled: transformers unavailable (%s)",
                    exc,
                )
                self._load_failed = True
                return False
            try:
                self._pipe = pipeline(
                    "zero-shot-classification",
                    model=self.model_id,
                    revision=self.revision,
                    device=-1,  # CPU-only
                )
                return True
            except Exception as exc:  # pragma: no cover — network / disk
                _logger.warning(
                    "classifier_local pipeline failed to initialise: %s", exc
                )
                self._load_failed = True
                return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        return self._ensure_loaded()

    def classify(
        self,
        text: str,
        labels: Iterable[str],
        *,
        multi_label: bool = False,
    ) -> ClassificationResult | None:
        """Return best label + confidence or None if the local pipeline
        is unavailable."""
        if not text or not labels:
            return None
        if not self._ensure_loaded():
            return None
        import time
        t0 = time.time()
        try:
            raw = self._pipe(  # type: ignore[operator]
                text,
                candidate_labels=list(labels),
                multi_label=multi_label,
            )
        except Exception as exc:  # pragma: no cover
            _logger.warning("classifier_local inference failed: %s", exc)
            return None
        latency_ms = (time.time() - t0) * 1000.0
        scores = dict(zip(raw["labels"], raw["scores"]))
        top_label = raw["labels"][0]
        return ClassificationResult(
            label=top_label,
            confidence=float(raw["scores"][0]),
            scores=scores,
            latency_ms=latency_ms,
        )

    def classify_fail_closed(
        self,
        text: str,
        labels: Iterable[str],
        fallback_label: str,
    ) -> ClassificationResult:
        """Never returns None — falls back to `fallback_label` with
        confidence 0 so the Guardian can still decide without crashing.
        """
        got = self.classify(text, labels)
        if got is not None and got.confidence >= self.confidence_floor:
            return got
        return ClassificationResult(
            label=fallback_label,
            confidence=0.0,
            scores={label: 0.0 for label in labels},
            latency_ms=0.0,
        )


__all__ = [
    "LocalZeroShotClassifier",
    "ClassificationResult",
    "MODEL_ID",
    "MODEL_REVISION",
    "DEFAULT_CONFIDENCE_FLOOR",
]
