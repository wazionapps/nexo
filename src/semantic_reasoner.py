"""semantic_reasoner — second-layer semantic decision maker.

Plan ONEPASS LLM Coverage. Called through ``src/semantic_router.py``.
Exposes a single ``reason()`` entrypoint with two modes:

    Mode A  — ``multipass_local``  (textual decision kinds)

        Reuses the already-pinned ``LocalZeroShotClassifier`` (see
        ``docs/classifier-model-notes.md``) but with stricter behaviour:
        three inference passes with mild prompt perturbations, then
        majority vote across passes. A decision is only accepted if at
        least two of three passes agree AND the agreed confidence is
        above the stricter threshold. This kills single-pass false
        positives without adding a new model dependency.

    Mode B  — ``cached_llm``  (code-aware decision kinds)

        Thin wrapper around ``call_model_raw`` with a disk cache scoped
        by (decision_kind, sha256(normalized_prompt)). TTL = 24h. The
        cache lives under ``~/.nexo/runtime/operations/semantic-reasoner-cache.json``
        alongside the existing classifier install state. Cache hits
        return instantly and are flagged in ``meta.cache_hit``. Misses
        call the LLM; the response and its normalized verdict are
        written back to the cache atomically.

Pin notes: this module does not introduce a new downloaded model.
Mode A reuses ``MODEL_ID``/``MODEL_REVISION`` from ``classifier_local``.
Mode B resolves the LLM through the standard resonance map with
``caller='semantic_reasoner'`` and ``tier='muy_bajo'``; the pin lives
in ``resonance_map`` like every other LLM caller.

See ``docs/semantic-reasoner-model-notes.md`` for the rationale behind
this "upgrade-in-place, pin-by-reuse" strategy, and why a dedicated
stronger local LLM (Llama 3.1 8B, etc.) is explicitly deferred to a
future release.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared dataclass imported from the router
# ---------------------------------------------------------------------------


def _import_router_result():
    """Lazy import to avoid circular dependency on semantic_router."""
    from semantic_router import RouterResult

    return RouterResult


# ---------------------------------------------------------------------------
# Mode A — multi-pass local
# ---------------------------------------------------------------------------


_PROMPT_PERTURBATIONS: tuple[str, ...] = (
    "{q}",
    "Decide: {q}",
    "Classify this utterance: {q}",
)


def _collect_local_votes(
    question: str, labels: tuple[str, ...]
) -> list[tuple[str, float, dict[str, float]]]:
    """Run the local classifier three times with mild prompt variations.

    Returns a list of ``(label, confidence, scores)`` triples. Any
    pass that fails silently returns a zero-confidence entry so the
    vote aggregator can still detect quorum problems.
    """
    try:
        from classifier_local import get_shared_zero_shot_classifier
    except Exception as exc:  # pragma: no cover
        _logger.debug("semantic_reasoner: classifier_local unavailable (%s)", exc)
        return []

    clf = get_shared_zero_shot_classifier(confidence_floor=0.0)
    votes: list[tuple[str, float, dict[str, float]]] = []
    for template in _PROMPT_PERTURBATIONS:
        prompt = template.format(q=question)
        result = clf.classify(prompt, labels)
        if result is None:
            votes.append(("", 0.0, {}))
            continue
        votes.append((result.label, float(result.confidence), dict(result.scores)))
    return votes


def _aggregate_votes(
    votes: list[tuple[str, float, dict[str, float]]],
    confidence_floor: float,
) -> tuple[str | None, float, dict[str, Any]]:
    """Majority vote across passes. Returns (label_or_none, confidence, meta)."""
    if not votes:
        return None, 0.0, {"reason": "no_votes"}

    counts: dict[str, int] = {}
    confidences: dict[str, list[float]] = {}
    for label, confidence, _scores in votes:
        if not label:
            continue
        counts[label] = counts.get(label, 0) + 1
        confidences.setdefault(label, []).append(confidence)

    if not counts:
        return None, 0.0, {"reason": "all_passes_failed", "votes": len(votes)}

    best_label = max(counts, key=lambda lbl: (counts[lbl], max(confidences[lbl])))
    vote_count = counts[best_label]
    avg_confidence = sum(confidences[best_label]) / len(confidences[best_label])

    meta: dict[str, Any] = {
        "votes_total": len(votes),
        "votes_for_best": vote_count,
        "avg_confidence": round(avg_confidence, 4),
        "per_label_counts": dict(counts),
    }

    if vote_count < 2:
        meta["reason"] = "no_majority"
        return None, avg_confidence, meta
    if avg_confidence < confidence_floor:
        meta["reason"] = "below_threshold"
        return None, avg_confidence, meta
    return best_label, avg_confidence, meta


def _reason_multipass_local(
    *,
    decision_kind: str,
    question: str,
    context: str = "",
    labels: tuple[str, ...] | None,
    confidence_floor: float,
):
    RouterResult = _import_router_result()
    if not labels:
        return RouterResult(
            ok=False,
            decision_kind=decision_kind,
            route_used="semantic_reasoner",
            degraded=True,
            error="multipass_local requires labels",
        )

    semantic_input = (context or "").strip() or question
    votes = _collect_local_votes(semantic_input, labels)
    label, confidence, meta = _aggregate_votes(votes, confidence_floor)
    if label is None:
        return RouterResult(
            ok=False,
            decision_kind=decision_kind,
            route_used="semantic_reasoner",
            degraded=True,
            error=meta.get("reason", "aggregation_failed"),
            meta={"mode": "multipass_local", "aggregate": meta},
        )
    return RouterResult(
        ok=True,
        decision_kind=decision_kind,
        verdict=label,
        label=label,
        confidence=round(float(confidence), 4),
        route_used="semantic_reasoner",
        degraded=False,
        meta={"mode": "multipass_local", "aggregate": meta},
    )


# ---------------------------------------------------------------------------
# Mode B — cached LLM
# ---------------------------------------------------------------------------


_DEFAULT_CACHE_TTL_SECONDS = 24 * 3600


def _cache_path() -> Path:
    """Resolve the on-disk cache location.

    Reuses ``paths.operations_dir()`` so the reasoner state lives next to
    the existing ``classifier-install-state.json``. If ``paths`` is not
    importable (heavy module; test context), fall back to a deterministic
    location under ``NEXO_HOME``.
    """
    override = os.environ.get("NEXO_SEMANTIC_REASONER_CACHE_PATH", "").strip()
    if override:
        return Path(override)
    try:
        import paths

        return paths.operations_dir() / "semantic-reasoner-cache.json"
    except Exception:
        home = os.environ.get("NEXO_HOME", "").strip()
        root = Path(home) if home else Path.home() / ".nexo"
        return root / "runtime" / "operations" / "semantic-reasoner-cache.json"


def _normalize_for_hash(text: str) -> str:
    """Normalise whitespace/case so equivalent prompts hit the same cache
    entry. Does not touch content semantics beyond whitespace collapse."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _cache_key(
    *,
    decision_kind: str,
    question: str,
    labels: tuple[str, ...] | None,
    context: str,
) -> str:
    payload = json.dumps(
        {
            "kind": decision_kind,
            "q": _normalize_for_hash(question),
            "ctx": _normalize_for_hash(context)[:400],
            "labels": list(labels) if labels else [],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_cache() -> dict[str, Any]:
    try:
        path = _cache_path()
        if not path.is_file():
            return {}
        data = json.loads(path.read_text() or "{}")
        if isinstance(data, dict):
            return data
    except Exception as exc:  # pragma: no cover — corrupt cache
        _logger.warning("semantic_reasoner: cache read failed (%s); starting fresh", exc)
    return {}


def _write_cache(cache: dict[str, Any]) -> None:
    """Atomic write with pid+uuid suffix so concurrent Brain / Desktop CLI
    writers do not stomp each other's temp file."""
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        import os as _os
        import uuid as _uuid

        tmp = path.with_name(
            f"{path.name}.tmp.{_os.getpid()}.{_uuid.uuid4().hex[:8]}"
        )
        payload = json.dumps(cache, ensure_ascii=False, sort_keys=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            try:
                _os.fsync(handle.fileno())
            except OSError:
                pass
        _os.replace(tmp, path)
    except Exception as exc:  # pragma: no cover
        _logger.warning("semantic_reasoner: cache write failed (%s)", exc)


def _cache_get(key: str, ttl_seconds: int) -> dict[str, Any] | None:
    cache = _read_cache()
    entry = cache.get(key)
    if not isinstance(entry, dict):
        return None
    ts = float(entry.get("ts", 0.0) or 0.0)
    if ts <= 0.0:
        return None
    if (time.time() - ts) > ttl_seconds:
        return None
    return entry


def _cache_put(key: str, entry: dict[str, Any]) -> None:
    cache = _read_cache()
    cache[key] = {**entry, "ts": time.time()}
    if len(cache) > 2000:
        # Keep the 1800 most-recent entries to avoid unbounded growth. The
        # bound is advisory; callers should keep reasoner prompts small.
        items = sorted(cache.items(), key=lambda kv: float(kv[1].get("ts", 0.0) or 0.0))
        cache = dict(items[-1800:])
    _write_cache(cache)


def _parse_ttl_env() -> int:
    """Read ``NEXO_SEMANTIC_REASONER_TTL`` defensively.

    Malformed values (non-integer, negative) fall back to the default so
    operator typos never crash the reasoner on first call.
    """
    raw = os.environ.get("NEXO_SEMANTIC_REASONER_TTL", "")
    if not raw:
        return _DEFAULT_CACHE_TTL_SECONDS
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        _logger.warning(
            "semantic_reasoner: invalid NEXO_SEMANTIC_REASONER_TTL=%r; "
            "using default %d",
            raw,
            _DEFAULT_CACHE_TTL_SECONDS,
        )
        return _DEFAULT_CACHE_TTL_SECONDS
    if parsed <= 0:
        return _DEFAULT_CACHE_TTL_SECONDS
    return parsed


def _reason_cached_llm(
    *,
    decision_kind: str,
    question: str,
    labels: tuple[str, ...] | None,
    context: str,
    confidence_floor: float,
):
    RouterResult = _import_router_result()
    ttl = _parse_ttl_env()
    key = _cache_key(
        decision_kind=decision_kind,
        question=question,
        labels=labels,
        context=context,
    )

    cached = _cache_get(key, ttl)
    if cached is not None:
        cached_verdict = cached.get("verdict")
        if isinstance(cached_verdict, str) and cached_verdict.strip():
            return RouterResult(
                ok=True,
                decision_kind=decision_kind,
                verdict=cached_verdict,
                label=cached_verdict,
                confidence=float(cached.get("confidence", 0.6)),
                route_used="semantic_reasoner",
                degraded=False,
                meta={
                    "mode": "cached_llm",
                    "cache_hit": True,
                    "cache_key": key[:12],
                },
            )
        # Corrupt entry (verdict missing or non-string). Drop it and fall
        # through to a live call so the caller is never handed a cached
        # "ok=True, verdict=None" sentinel.
        _logger.warning(
            "semantic_reasoner: dropping corrupt cache entry for key=%s",
            key[:12],
        )

    try:
        import call_model_raw as _cmr
    except Exception as exc:  # pragma: no cover
        return RouterResult(
            ok=False,
            decision_kind=decision_kind,
            route_used="semantic_reasoner",
            degraded=True,
            error=f"call_model_raw unavailable: {exc}",
            meta={"mode": "cached_llm", "cache_hit": False},
        )

    call_model_raw_fn = getattr(_cmr, "call_model_raw", None)
    classifier_unavailable_cls = getattr(
        _cmr, "ClassifierUnavailableError", Exception
    )
    if call_model_raw_fn is None:
        return RouterResult(
            ok=False,
            decision_kind=decision_kind,
            route_used="semantic_reasoner",
            degraded=True,
            error="call_model_raw callable missing",
            meta={"mode": "cached_llm", "cache_hit": False},
        )

    prompt = _build_reasoner_prompt(
        decision_kind=decision_kind,
        question=question,
        labels=labels,
        context=context,
    )
    system = (
        "You are NEXO's code-aware semantic reasoner. Answer with the "
        "single best label from the provided list (no prose). If no "
        "label fits, answer 'unknown'."
    )
    try:
        raw = call_model_raw_fn(
            prompt,
            system=system,
            caller="semantic_reasoner",
            tier="muy_bajo",
            max_tokens=32,
            temperature=0.0,
        )
    except classifier_unavailable_cls as exc:
        return RouterResult(
            ok=False,
            decision_kind=decision_kind,
            route_used="semantic_reasoner",
            degraded=True,
            error=f"remote_unavailable: {exc}",
            meta={"mode": "cached_llm", "cache_hit": False},
        )
    except Exception as exc:  # noqa: BLE001 — fail-closed
        return RouterResult(
            ok=False,
            decision_kind=decision_kind,
            route_used="semantic_reasoner",
            degraded=True,
            error=f"remote_error: {exc}",
            meta={"mode": "cached_llm", "cache_hit": False},
        )

    verdict = _normalize_verdict(raw, labels)
    if verdict is None:
        return RouterResult(
            ok=False,
            decision_kind=decision_kind,
            route_used="semantic_reasoner",
            degraded=True,
            error="llm_returned_unknown_or_unparseable",
            meta={
                "mode": "cached_llm",
                "cache_hit": False,
                "raw": (raw or "")[:80],
            },
        )

    _cache_put(
        key,
        {
            "verdict": verdict,
            "confidence": max(confidence_floor, 0.6),
            "decision_kind": decision_kind,
        },
    )

    return RouterResult(
        ok=True,
        decision_kind=decision_kind,
        verdict=verdict,
        label=verdict,
        confidence=max(confidence_floor, 0.6),
        route_used="semantic_reasoner",
        degraded=False,
        meta={"mode": "cached_llm", "cache_hit": False, "cache_key": key[:12]},
    )


def _build_reasoner_prompt(
    *,
    decision_kind: str,
    question: str,
    labels: tuple[str, ...] | None,
    context: str,
) -> str:
    parts = [
        f"decision_kind: {decision_kind}",
        f"question: {question}",
    ]
    if context:
        parts.append(f"context: {context[:600]}")
    if labels:
        parts.append("candidate_labels: " + ", ".join(labels))
        parts.append("Reply with exactly one of the labels above.")
    else:
        parts.append("Reply with the shortest phrase answering the question.")
    return "\n".join(parts)


def _normalize_verdict(
    raw: str, labels: tuple[str, ...] | None
) -> str | None:
    text = (raw or "").strip().lower()
    if not text:
        return None
    if text == "unknown":
        return None
    if labels:
        for label in labels:
            if label.lower() == text:
                return label
        for label in labels:
            if label.lower() in text:
                return label
        return None
    return text


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


_REASONER_OFF_VALUES = {"0", "off", "false", "no", "disable", "disabled"}


def _is_reasoner_disabled() -> bool:
    """Honour the ``NEXO_SEMANTIC_REASONER`` runtime kill switch.

    The plan (ONEPASS LLM Coverage) explicitly required an env opt-out
    dedicated to the reasoner, separate from ``NEXO_LOCAL_CLASSIFIER``
    (which only gates install-time provisioning). Operators who hit a
    reasoner regression in production can set ``NEXO_SEMANTIC_REASONER=0``
    to force every ``reason()`` call to refuse; the router then falls
    through to ``remote_fallback`` on its own.
    """
    raw = os.environ.get("NEXO_SEMANTIC_REASONER", "").strip().lower()
    return raw in _REASONER_OFF_VALUES


def reason(
    *,
    decision_kind: str,
    question: str,
    labels: tuple[str, ...] | list[str] | None,
    context: str = "",
    mode: str = "multipass_local",
    confidence_floor: float = 0.75,
):
    """Dispatch to the configured mode. Called by ``semantic_router.route``.

    Returns a ``RouterResult``. The router knows how to keep going to
    ``remote_fallback`` if this layer refuses.
    """
    RouterResult = _import_router_result()

    if _is_reasoner_disabled():
        return RouterResult(
            ok=False,
            decision_kind=decision_kind,
            route_used="semantic_reasoner",
            degraded=True,
            error="reasoner_disabled_by_env",
            meta={"env": "NEXO_SEMANTIC_REASONER"},
        )

    labels_tuple: tuple[str, ...] | None = tuple(labels) if labels else None
    if mode == "multipass_local":
        return _reason_multipass_local(
            decision_kind=decision_kind,
            question=question,
            context=context,
            labels=labels_tuple,
            confidence_floor=confidence_floor,
        )
    if mode == "cached_llm":
        return _reason_cached_llm(
            decision_kind=decision_kind,
            question=question,
            labels=labels_tuple,
            context=context,
            confidence_floor=confidence_floor,
        )

    return RouterResult(
        ok=False,
        decision_kind=decision_kind,
        route_used="semantic_reasoner",
        degraded=True,
        error=f"unknown reasoner mode: {mode}",
    )


__all__ = ["reason"]
