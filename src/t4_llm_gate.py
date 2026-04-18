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


PROMPTS: dict[str, dict] = {
    "R15": {
        "instruction": (
            "Decide whether the user just started work on a project without the "
            "agent having pulled the project context (atlas, git log, project "
            "files). Answer \"yes\" if a context pull is required, \"no\" if the "
            "turn is conversational / off-topic / meta."
        ),
        "positives": [
            "User: \"Vamos a arreglar el bug del checkout\" → yes",
            "User: \"Hazme un refactor del login de CanaRirural\" → yes",
            "User: \"Revisa la PR del orchestrator\" → yes",
        ],
        "negatives": [
            "User: \"qué hora es\" → no",
            "User: \"gracias, ya está\" → no",
            "User: \"dime un chiste\" → no",
        ],
    },
    "R23e": {
        "instruction": (
            "Decide whether the proposed `git push --force` command would actually "
            "rewrite a protected branch (main, master, production, release-*). "
            "Answer \"yes\" if the target is protected, \"no\" if it targets a "
            "personal branch, a temporary backup branch, or is clearly a local-only "
            "operation that the user explicitly authorised."
        ),
        "positives": [
            "`git push --force origin main` → yes",
            "`git push -f origin production` → yes",
            "`git push --force origin release-2026-04` → yes",
        ],
        "negatives": [
            "`git push --force origin my-feature` → no",
            "`git push --force-with-lease origin main` → no",
            "`git push --force origin backup-before-refactor` → no",
        ],
    },
    "R23f": {
        "instruction": (
            "Decide whether the SQL statement performs DELETE/UPDATE without a "
            "WHERE clause against a production table. Answer \"yes\" if it is an "
            "unscoped destructive write, \"no\" if it is a well-scoped delete, a "
            "DDL command, or a scratch table known to be temporary."
        ),
        "positives": [
            "`DELETE FROM orders` → yes",
            "`UPDATE users SET active=0` → yes",
            "`DELETE FROM clients` → yes",
        ],
        "negatives": [
            "`DELETE FROM orders WHERE id = 123` → no",
            "`TRUNCATE TABLE tmp_scratch` → no",
            "`UPDATE users SET last_login = NOW() WHERE id = 42` → no",
        ],
    },
    "R23h": {
        "instruction": (
            "Decide whether the shebang of the script disagrees with the "
            "interpreter that will actually be invoked. Answer \"yes\" if the "
            "mismatch will break execution, \"no\" otherwise."
        ),
        "positives": [
            "\"#!/usr/bin/env python3\" + bash body with `for i in $(seq 1 10); do` → yes",
            "\"#!/bin/sh\" + bashisms like `[[ ${foo} == \"bar\" ]]` → yes",
            "\"#!/usr/bin/env node\" + bash heredoc body → yes",
        ],
        "negatives": [
            "\"#!/usr/bin/env python3\" + real Python body → no",
            "\"#!/bin/bash\" + bash arrays → no",
            "Python script with no shebang at all → no",
        ],
    },
}


def build_prompt(rule_id: str, *, span: str = "", context: str = "") -> Optional[str]:
    p = PROMPTS.get(rule_id)
    if p is None:
        return None
    examples = "\n".join(
        ["+ " + e for e in p["positives"]] + ["- " + e for e in p["negatives"]]
    )
    body = p["instruction"] + "\n\nExamples:\n" + examples
    body += "\n\nNow decide. Input:\n" + (span or "")
    if context:
        body += "\n\nAdditional context:\n" + context
    body += "\n\nAnswer exactly \"yes\" or \"no\"."
    return body


__all__ = [
    "PROMPTS",
    "build_prompt",
    "classify_with_llm",
    "_cache",
]
