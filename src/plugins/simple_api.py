"""Minimal public API wrappers for the core NEXO mental model."""

from __future__ import annotations

import hashlib
import json

import cognitive

from plugins.episodic_memory import handle_recall
from plugins.workflow import handle_workflow_open


# ── R12 (Fase 2 Protocol Enforcer) cognitive write dedup threshold ────
# Plan doc 1 R12: remember / claim_add with Jaccard similarity >=0.80
# vs an existing active memory should surface a "similar exists"
# warning rather than silently duplicating. Exact sha1(title|content)
# was already enforced at ingest time; R12 catches paraphrases and
# near-duplicates the exact hash misses.
R12_SIMILARITY_THRESHOLD = 0.80


def _jaccard_tokens(a: str, b: str) -> float:
    """Cheap word-level Jaccard similarity.

    Deliberately not pulled from db.extract_keywords to keep the simple
    API surface dependency-light; the stoplist below is a subset of the
    one in db._learnings so behaviour stays predictable.
    """
    import re as _re
    stop = {
        "the", "a", "an", "is", "of", "to", "and", "or", "but", "on", "in",
        "for", "with", "by", "this", "that", "it", "as", "el", "los", "las",
        "un", "una", "por", "con", "para", "del", "al", "es", "se", "no",
        "si", "como", "pero", "su", "ya", "esto", "esta",
    }
    def toks(text: str) -> set[str]:
        return {w for w in _re.findall(r"[a-zA-Z0-9_]+", (text or "").lower()) if len(w) > 2 and w not in stop}
    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return 0.0
    overlap = ta & tb
    union = ta | tb
    return len(overlap) / len(union) if union else 0.0


def _find_similar_ltm(content: str, title: str, domain: str) -> dict | None:
    """R12 helper — check for near-duplicate LTM memory.

    Returns the best-matching LTM row when Jaccard >= R12_SIMILARITY_THRESHOLD
    within the same domain, else None. Scope is domain-local so different
    projects with similar phrasing do not merge accidentally.
    """
    try:
        from cognitive._core import _get_db as _cog_get_db  # type: ignore
        db = _cog_get_db()
    except Exception:
        return None
    clean_domain = (domain or "").strip()
    needle = f"{(title or '').strip()} {(content or '').strip()}"
    try:
        rows = db.execute(
            "SELECT id, source_title, content, domain FROM ltm_memories "
            "WHERE COALESCE(domain, '') = ? AND (is_dormant = 0 OR is_dormant IS NULL)",
            (clean_domain,),
        ).fetchall()
    except Exception:
        return None
    best = None  # (id, similarity, title)
    for row in rows:
        haystack = f"{row['source_title'] or ''} {row['content'] or ''}"
        sim = _jaccard_tokens(needle, haystack)
        if sim >= R12_SIMILARITY_THRESHOLD and (best is None or sim > best[1]):
            best = {"id": row["id"], "similarity": sim, "title": row["source_title"] or ""}
    return best


def handle_remember(
    content: str,
    title: str = "",
    domain: str = "",
    source_type: str = "note",
    tags: str = "",
    bypass_gate: bool = True,
    force: bool = False,
) -> str:
    """Store one durable memory item with a single high-level call.

    Fase 2 R12: when content+title matches an existing active LTM memory
    in the same domain at Jaccard >= 0.80, no new row is created. The
    response reports which existing memory absorbed the write so the
    caller can decide whether to nexo_cognitive_pin / archive / edit
    the existing one instead. Pass force=True to bypass (e.g. when the
    collision is a distinct artefact that just happens to overlap).
    """
    clean_content = (content or "").strip()
    if not clean_content:
        return json.dumps({"ok": False, "error": "content is required"}, ensure_ascii=False, indent=2)

    clean_title = (title or "").strip()[:120]
    clean_domain = (domain or "").strip()[:120]

    if not bool(force):
        existing = _find_similar_ltm(clean_content, clean_title, clean_domain)
        if existing:
            return json.dumps(
                {
                    "ok": True,
                    "merged_into": int(existing["id"]),
                    "similarity": round(float(existing["similarity"]), 3),
                    "existing_title": existing["title"],
                    "note": (
                        f"R12: near-duplicate (Jaccard {existing['similarity']:.2f}) already "
                        f"in LTM as #{existing['id']}. No duplicate row created. "
                        "Pass force=true to create a distinct entry anyway."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )

    # Content fingerprint for deterministic dedup id — not security-sensitive.
    source_id = hashlib.sha1(
        f"{clean_title}|{clean_content}".encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:12]
    memory_id = cognitive.ingest_to_ltm(
        clean_content,
        source_type=(source_type or "note").strip()[:40],
        source_id=source_id,
        source_title=clean_title or clean_content[:80],
        domain=clean_domain,
        tags=(tags or "").strip()[:200],
        bypass_gate=bool(bypass_gate),
    )
    return json.dumps(
        {
            "ok": bool(memory_id),
            "memory_id": int(memory_id or 0),
            "source_type": (source_type or "note").strip()[:40],
            "title": clean_title or clean_content[:80],
            "domain": clean_domain,
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
