"""enforcement_classifier — Triple-reinforced yes/no classifier for the Guardian.

Fase 2 spec item 0.7. Wraps call_model_raw with:

  1. Strict system prompt ("respond with exactly one word: yes OR no").
  2. Tiny max_tokens (3, enough for "yes"/"no" + a safety token).
  3. Regex parser (^(yes|no)$ case-insensitive) over trimmed text.
  4. ONE retry with an even stricter reformulation if the first answer
     does not match /^(yes|no)$/i.
  5. Fallback "no" conservative when the second answer also does not
     match. Fase 2 doc 1 spec: "Con las 3 combinadas, probabilidad de
     output no yes/no <0.1%".
  6. LRU cache 60s keyed on sha256(question + "\n\n" + context).

Fail-closed behaviour (spec 0.20): any ClassifierUnavailableError raised
by call_model_raw propagates up as ClassifierUnavailableError. The
guardian engine (headless enforcer_engine.py + Desktop enforcement-
engine.js) is expected to catch and either (a) degrade the rule to
shadow for the session, or (b) inject a generic reminder. NEVER fall
through to "classifier said yes/no" silently.

Thread-safety: the cache uses functools.lru_cache under a manual TTL
wrapper. Access is NOT thread-safe across calls; callers that run the
classifier from multiple threads should serialise or move to a
per-thread cache. The headless enforcer is single-threaded (one Claude
Code subprocess per run_with_enforcement invocation), so the current
shape is enough.

Parity note: the Desktop equivalent (nexo-desktop/lib/enforcement-
classifier.js) MUST match the three reinforcements byte-for-byte. If
you add a new safety layer here, update the JS twin in the same commit
(see docs/client-parity-checklist.md).
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Callable

from call_model_raw import ClassifierUnavailableError, call_model_raw


_logger = logging.getLogger("nexo.enforcement_classifier")


_STRICT_SYSTEM_PROMPT = (
    "You are a binary classifier for the NEXO Protocol Enforcer. "
    "Respond with EXACTLY ONE WORD: yes OR no. "
    "No explanation. No preface. No punctuation. No quotes. "
    "Only 'yes' or 'no', lowercase, no surrounding text."
)

_RETRY_SYSTEM_PROMPT = (
    "Your previous response was not valid. "
    "Answer with only the single word 'yes' or the single word 'no'. "
    "Any other output is rejected. Do not explain. Do not apologise. "
    "Do not repeat the question. Emit 'yes' or 'no' and stop."
)

_PARSER_REGEX = re.compile(r"^\s*(yes|no)\b", flags=re.IGNORECASE)


def _cache_key(question: str, context: str) -> str:
    blob = f"{question}\n\n{context}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class _TTLCache:
    """Tiny TTL cache — Fase 2 spec calls for 60s dedup on (question, context).

    functools.lru_cache is not suitable because it has no TTL and the
    classifier's answers are tied to session state that ages. We use a
    dict + explicit eviction.
    """

    def __init__(self, ttl_seconds: float = 60.0, max_entries: int = 512):
        self._ttl = float(ttl_seconds)
        self._max = int(max_entries)
        self._store: dict[str, tuple[float, bool]] = {}

    def get(self, key: str) -> bool | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if (time.time() - ts) > self._ttl:
            self._store.pop(key, None)
            return None
        return value

    def put(self, key: str, value: bool) -> None:
        # Trivial LRU approximation: if full, drop the oldest entry.
        if len(self._store) >= self._max:
            oldest = min(self._store.items(), key=lambda kv: kv[1][0])[0]
            self._store.pop(oldest, None)
        self._store[key] = (time.time(), bool(value))

    def clear(self) -> None:
        self._store.clear()


_cache = _TTLCache()


def _parse_yes_no(raw: str) -> bool | None:
    """Return True for yes, False for no, None if unparseable."""
    if not raw:
        return None
    match = _PARSER_REGEX.match(raw.strip().lower())
    if not match:
        return None
    return match.group(1) == "yes"


def classify(
    question: str,
    context: str = "",
    *,
    call_raw: Callable[..., str] = call_model_raw,
    cache: _TTLCache = _cache,
    tier: str = "muy_bajo",
) -> bool:
    """Run a triple-reinforced yes/no classification.

    Args:
        question: The yes/no question for the classifier.
        context: Optional extra context appended to the user message.
        call_raw: Injection point for tests — defaults to call_model_raw.
        cache: TTL cache instance. Tests can pass a fresh cache.
        tier: Resonance tier. Default "muy_bajo" (Haiku / gpt-5.4-mini).

    Returns:
        True iff the classifier confidently answers "yes". False otherwise
        (including when the second retry fails — conservative fallback per
        plan doc 1 "triple refuerzo").

    Raises:
        ClassifierUnavailableError: Propagated from call_model_raw when the
        backend is unavailable. The caller (enforcement engine) MUST catch
        and degrade the rule to shadow or inject a generic reminder.
    """
    key = _cache_key(question, context)
    cached = cache.get(key)
    if cached is not None:
        _logger.debug("CACHE_HIT key=%s → %s", key[:12], cached)
        return cached

    user_text = question if not context else f"{question}\n\nContext:\n{context}"

    first = call_raw(
        user_text,
        tier=tier,
        max_tokens=3,
        temperature=0.0,
        stop_sequences=["\n", ".", " "],
        system=_STRICT_SYSTEM_PROMPT,
    )
    parsed = _parse_yes_no(first)
    if parsed is not None:
        cache.put(key, parsed)
        _logger.debug("FIRST_OK raw=%r → %s", first, parsed)
        return parsed

    # Retry with stricter reformulation — one time, then give up conservative.
    second = call_raw(
        user_text,
        tier=tier,
        max_tokens=3,
        temperature=0.0,
        stop_sequences=["\n", ".", " "],
        system=_RETRY_SYSTEM_PROMPT,
    )
    parsed = _parse_yes_no(second)
    if parsed is not None:
        cache.put(key, parsed)
        _logger.debug("RETRY_OK raw=%r → %s", second, parsed)
        return parsed

    # Both attempts unparseable. Conservative default: NO.
    _logger.warning(
        "PARSER_FAIL (fallback no) first=%r second=%r q=%r",
        first, second, question[:120],
    )
    cache.put(key, False)
    return False


__all__ = [
    "classify",
    "ClassifierUnavailableError",
    "_cache",
    "_parse_yes_no",
    "_STRICT_SYSTEM_PROMPT",
    "_RETRY_SYSTEM_PROMPT",
    "_TTLCache",
]
