#!/usr/bin/env python3
"""JSON-in JSON-out CLI wrapper around ``src/semantic_router.route``.

Plan ONEPASS LLM Coverage — Desktop bridge.

Used by ``nexo-desktop/lib/brain-semantic-router.js`` to perform semantic
decisions without embedding a second policy tree in Desktop. Keeps Brain
as the single authority for semantic decisions; Desktop is a client.

Contract:

    stdin   — JSON object with at least {decision_kind, question}.
              Optional fields: context, labels, allow_remote_fallback.

    stdout  — JSON object mirroring RouterResult (ok, decision_kind,
              verdict, label, confidence, route_used, degraded, error,
              meta).

    exit    — 0 on well-formed result (ok or not_ok are both exit 0).
              1 only when the CLI itself cannot parse input or resolve
              the router module.

This deliberately does NOT rehydrate the NEXO MCP stack, does NOT touch
the database, does NOT invoke nexo_startup. It is a pure function
wrapper so Desktop can call it hot-path without the automation cost.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _setup_imports() -> None:
    """Insert ``src/`` at the head of sys.path so ``import semantic_router``
    resolves inside the repo install (same pattern tests/conftest.py uses).
    Falls back to the packaged runtime location if the repo layout is not
    present (client installs).
    """
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "src",  # repo layout
        Path.home() / ".nexo" / "core" / "src",  # installed runtime
    ]
    for candidate in candidates:
        if candidate.is_dir():
            sys.path.insert(0, str(candidate))
            return


def _error(msg: str, *, code: int = 1) -> int:
    print(json.dumps({"ok": False, "error": msg, "route_used": "no_route"}))
    return code


def _coerce_labels(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return tuple(str(x) for x in value)
    return None


def main() -> int:
    _setup_imports()

    raw = sys.stdin.read()
    if not raw.strip():
        return _error("empty stdin; expected JSON payload")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _error(f"invalid JSON on stdin: {exc}")

    if not isinstance(payload, dict):
        return _error("payload must be a JSON object")

    decision_kind = str(payload.get("decision_kind", "") or "").strip()
    question = str(payload.get("question", "") or "")
    context = str(payload.get("context", "") or "")
    labels = _coerce_labels(payload.get("labels"))
    allow_remote_fallback = bool(payload.get("allow_remote_fallback", True))

    if not decision_kind:
        return _error("missing required field: decision_kind")
    if not question:
        return _error("missing required field: question")

    try:
        import semantic_router as sr
    except ImportError as exc:
        return _error(f"semantic_router unavailable: {exc}", code=1)

    result = sr.route(
        decision_kind=decision_kind,
        question=question,
        context=context,
        labels=labels,
        allow_remote_fallback=allow_remote_fallback,
    )
    print(
        json.dumps(
            {
                "ok": bool(result.ok),
                "decision_kind": result.decision_kind,
                "verdict": result.verdict,
                "label": result.label,
                "confidence": float(result.confidence),
                "route_used": result.route_used,
                "degraded": bool(result.degraded),
                "error": result.error,
                "meta": result.meta or {},
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
