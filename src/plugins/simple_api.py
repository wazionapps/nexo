"""Minimal public API wrappers for the core NEXO mental model."""

from __future__ import annotations

import hashlib
import json

import cognitive

from plugins.episodic_memory import handle_recall
from plugins.workflow import handle_workflow_open


def handle_remember(
    content: str,
    title: str = "",
    domain: str = "",
    source_type: str = "note",
    tags: str = "",
    bypass_gate: bool = True,
) -> str:
    """Store one durable memory item with a single high-level call."""
    clean_content = (content or "").strip()
    if not clean_content:
        return json.dumps({"ok": False, "error": "content is required"}, ensure_ascii=False, indent=2)

    clean_title = (title or "").strip()[:120]
    source_id = hashlib.sha1(f"{clean_title}|{clean_content}".encode("utf-8")).hexdigest()[:12]
    memory_id = cognitive.ingest_to_ltm(
        clean_content,
        source_type=(source_type or "note").strip()[:40],
        source_id=source_id,
        source_title=clean_title or clean_content[:80],
        domain=(domain or "").strip()[:120],
        tags=(tags or "").strip()[:200],
        bypass_gate=bool(bypass_gate),
    )
    return json.dumps(
        {
            "ok": bool(memory_id),
            "memory_id": int(memory_id or 0),
            "source_type": (source_type or "note").strip()[:40],
            "title": clean_title or clean_content[:80],
            "domain": (domain or "").strip()[:120],
        },
        ensure_ascii=False,
        indent=2,
    )


def handle_memory_recall(query: str, days: int = 30) -> str:
    """High-level memory lookup wrapper around nexo_recall."""
    return handle_recall((query or "").strip(), days=max(1, int(days or 30)))


def handle_consolidate(
    max_insights: int = 12,
    threshold: float = 0.9,
    dry_run: bool = False,
) -> str:
    """Run the core memory consolidation cycle explicitly."""
    promoted = cognitive.promote_stm_to_ltm()
    quarantine = cognitive.process_quarantine()
    dreamed = cognitive.dream_cycle(max_insights=max(1, int(max_insights or 12)))
    semantic = cognitive.consolidate_semantic(threshold=float(threshold or 0.9), dry_run=bool(dry_run))
    payload = {
        "ok": True,
        "promoted_to_ltm": int(promoted or 0),
        "quarantine": quarantine,
        "dream_cycle": dreamed,
        "semantic_consolidation": semantic,
        "dry_run": bool(dry_run),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def handle_run_workflow(
    sid: str,
    goal: str,
    steps: str = "[]",
    goal_id: str = "",
    shared_state: str = "{}",
    owner: str = "",
    idempotency_key: str = "",
) -> str:
    """Open a durable workflow with the public mental-model naming."""
    return handle_workflow_open(
        sid=sid,
        goal=goal,
        steps=steps,
        goal_id=goal_id,
        shared_state=shared_state,
        owner=owner,
        idempotency_key=idempotency_key,
    )


TOOLS = [
    (handle_remember, "nexo_remember", "High-level memory write: store one durable memory item."),
    (handle_memory_recall, "nexo_memory_recall", "High-level memory lookup wrapper around nexo_recall."),
    (handle_consolidate, "nexo_consolidate", "Run NEXO memory consolidation explicitly: promote, process quarantine, dream, consolidate."),
    (handle_run_workflow, "nexo_run_workflow", "High-level durable workflow entry point for the public API surface."),
]
