"""T4 LLM gate — Plan Consolidado T4.

Python twin of nexo-desktop/lib/t4-llm-gate.js. Callers (R15, R23e, R23f,
R23h) wrap their regex decision with this gate: the LLM classifier is
given the matched span + surrounding context and answers yes / no. The
caller uses the answer to decide whether to inject.

Return values:
  - "yes"     → caller proceeds (inject).
  - "no"      → caller aborts (no injection); cuts false positives.
  - "unknown" → caller falls back to pre-T4 behaviour (regex wins).

Cache: 5-minute TTL keyed on sha256(rule_id + prompt + context). Cache
instance is module-level; tests pass a fresh cache via the `cache`
argument.

Fail-closed: any classifier error collapses to "unknown" so the rule's
regex layer keeps protecting us.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Callable, Optional

from core_prompts import render_core_prompt

_TTL_SECONDS = 5 * 60
_MAX_ENTRIES = 256

_cache: dict[str, tuple[float, str]] = {}


def _cache_key(rule_id: str, prompt: str, context: str) -> str:
    h = hashlib.sha256()
    h.update((rule_id or "").encode())
    h.update(b"\n")
    h.update((prompt or "").encode())
    h.update(b"\n")
    h.update((context or "").encode())
    return h.hexdigest()


def _evict(store: dict) -> None:
    if len(store) <= _MAX_ENTRIES:
        return
    oldest_key = min(store.items(), key=lambda kv: kv[1][0])[0]
    store.pop(oldest_key, None)


def classify_with_llm(
    rule_id: str,
    *,
    prompt: str,
    context: str = "",
    classifier: Optional[Callable[[str, str], Any]] = None,
    cache: Optional[dict] = None,
) -> str:
    if classifier is None:
        return "unknown"
    store = cache if cache is not None else _cache
    key = _cache_key(rule_id, prompt, context)
    hit = store.get(key)
    if hit and (time.time() - hit[0]) < _TTL_SECONDS:
        return hit[1]
    verdict = "unknown"
    try:
        result = classifier(prompt, context)
        if result is True or result == "yes":
            verdict = "yes"
        elif result is False or result == "no":
            verdict = "no"
    except Exception:
        verdict = "unknown"
    store[key] = (time.time(), verdict)
    _evict(store)
    return verdict


PROMPT_TEMPLATE_NAMES: dict[str, str] = {
    "R15": "t4-r15-project-context-gate",
    "R23e": "t4-r23e-force-push-gate",
    "R23f": "t4-r23f-db-no-where-gate",
    "R23h": "t4-r23h-shebang-mismatch-gate",
}


def build_prompt(rule_id: str, *, span: str = "", context: str = "") -> Optional[str]:
    template_name = PROMPT_TEMPLATE_NAMES.get(rule_id)
    if template_name is None:
        return None
    context_section = ""
    if context:
        context_section = "\n\nAdditional context:\n" + context
    return render_core_prompt(
        template_name,
        span=(span or ""),
        context_section=context_section,
    )


__all__ = [
    "PROMPT_TEMPLATE_NAMES",
    "build_prompt",
    "classify_with_llm",
    "_cache",
]
