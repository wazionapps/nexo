from __future__ import annotations

"""Read-only consolidation brief builder for the nightly postmortem.

Why this module exists
----------------------
The nightly postmortem consolidator hands the LLM a tiny diary slice, but the
prompt's "do not duplicate / detect contradiction" steps used to make the
headless model pull the ENTIRE learnings corpus into its own context (via
nexo_learning_list / nexo_learning_search / reading MEMORY.md). At hundreds of
learnings the working context blows up and the timeout wrapper SIGKILLs the
session (exit 124).

The fix: precompute ALL corpus-wide MECHANICAL work here, in the consolidator
SCRIPT process, and feed the LLM only a small, hard-capped JSON brief. The LLM
keeps the SEMANTIC judgment it is uniquely good at (is this self-critique worth
a permanent rule? which precomputed contradiction is real and how to phrase the
canonical rule?) and loses every task that requires scanning the whole corpus.

This module is READ-ONLY by construction: it performs SELECT-only queries on its
own short-lived sqlite connection (mirrors apply_findings connection style) and
NEVER commits, inserts, updates, or deletes. The only single source of truth for
similarity / contradiction / dedup math remains learning_resolver — this module
depends only on its PUBLIC surface.
"""

import json
import os
import sqlite3
from typing import Any

import learning_resolver

try:  # paths is available in the runtime; keep import defensive for odd installs
    import paths as _paths
except Exception:  # pragma: no cover - defensive
    _paths = None


# Read learnings in bounded batches so even a 5k-row corpus stays O(n) and the
# helper itself never holds the whole textual corpus in a single prompt — it only
# emits the capped brief below.
_CHUNK = 200

# A learning is "weak" (stale candidate) when its weight is low, OR it lacks both
# reasoning and prevention (no rationale to act on), OR it claims a file scope but
# was never reinforced by a guard hit. Mirrors apply_findings weak-learning logic;
# copied here as small local predicates rather than importing apply_findings (to
# avoid that module's _DynamicPath side effects).
_WEAK_WEIGHT = 1.0


def _resolve_db_path() -> str:
    for env_key in ("NEXO_TEST_DB", "NEXO_DB"):
        value = str(os.environ.get(env_key, "") or "").strip()
        if value:
            return value
    if _paths is not None:
        try:
            return str(_paths.resolve_db_path())
        except Exception:
            pass
    return ""


def _open_conn() -> sqlite3.Connection | None:
    db_path = _resolve_db_path()
    if not db_path or not os.path.isfile(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _preview(text: str, limit: int = 160) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) > limit:
        return clean[: limit - 1].rstrip() + "…"
    return clean


def _slugify(text: str) -> str:
    normalized = learning_resolver._normalize_text(text)
    tokens = [tok for tok in normalized.replace("/", " ").split() if tok]
    return "-".join(tokens[:8])[:80] or "topic"


def _critique_text(diary: dict[str, Any]) -> str:
    parts = [
        str(diary.get("self_critique") or ""),
        str(diary.get("summary") or ""),
    ]
    return " ".join(part for part in parts if part).strip()


def _is_weak(row: dict[str, Any], columns: set[str]) -> str:
    """Return a non-empty weakness reason if the learning looks stale/weak."""
    if "weight" in columns:
        try:
            weight = float(row.get("weight") if row.get("weight") is not None else 0.5)
        except Exception:
            weight = 0.5
        if weight < _WEAK_WEIGHT:
            return f"low_weight ({round(weight, 2)})"
    reasoning = str(row.get("reasoning") or "").strip()
    prevention = str(row.get("prevention") or "").strip()
    if not reasoning and not prevention:
        return "no_reasoning_or_prevention"
    if "applies_to" in columns and "guard_hits" in columns:
        applies = str(row.get("applies_to") or "").strip()
        try:
            guard_hits = int(row.get("guard_hits") or 0)
        except Exception:
            guard_hits = 0
        if applies and guard_hits == 0:
            return "scoped_never_guard_hit"
    return ""


def _iter_active_learnings(conn: sqlite3.Connection, columns: set[str]):
    """Yield active learnings dicts in bounded LIMIT/OFFSET batches."""
    status_filter = " WHERE COALESCE(status, 'active') = 'active'" if "status" in columns else ""
    order_by = "updated_at DESC, id DESC" if "updated_at" in columns else "id DESC"
    offset = 0
    while True:
        try:
            rows = conn.execute(
                f"SELECT * FROM learnings{status_filter} ORDER BY {order_by} LIMIT ? OFFSET ?",
                (_CHUNK, offset),
            ).fetchall()
        except Exception:
            return
        if not rows:
            return
        for row in rows:
            yield dict(row)
        if len(rows) < _CHUNK:
            return
        offset += _CHUNK


def build_consolidation_brief(
    diaries_with_critique: list[dict],
    *,
    conn: sqlite3.Connection | None = None,
    max_chars: int = 6000,
    max_shortlist: int = 25,
    max_contradictions: int = 15,
    max_stale: int = 15,
) -> dict:
    """Build a small, hard-capped JSON brief from today's critiques + the corpus.

    READ-ONLY: opens its own short-lived connection (unless one is supplied),
    performs only SELECT queries, and never commits. The brief is the ONLY thing
    handed to the LLM, so the model never lists the whole corpus.
    """

    own_conn = conn is None
    if own_conn:
        conn = _open_conn()

    brief: dict[str, Any] = {
        "corpus_size": 0,
        "today_topics": [],
        "shortlist": [],
        "contradiction_pairs": [],
        "supersession_stubs": [],
        "stale_candidates": [],
        "preference_key_dupes": [],
        "truncated": False,
    }

    # Build today's topics regardless of corpus availability.
    today_topics: list[dict[str, Any]] = []
    for diary in diaries_with_critique or []:
        text = _critique_text(diary)
        if not text:
            continue
        title = _preview(diary.get("summary") or diary.get("self_critique") or "", 120)
        today_topics.append(
            {
                "slug": _slugify(diary.get("summary") or diary.get("self_critique") or ""),
                "title": title,
                "_text": text,
                "_tokens": set(learning_resolver._tokenize(text)),
                "_applies": str(diary.get("domain") or ""),
                "has_existing_coverage": False,
                "covering_ids": [],
            }
        )

    if conn is None:
        # No corpus available (fresh install / missing DB). Emit topics only.
        brief["today_topics"] = [
            {
                "slug": t["slug"],
                "title": t["title"],
                "has_existing_coverage": False,
                "covering_ids": [],
            }
            for t in today_topics
        ]
        return brief

    try:
        columns = _table_columns(conn, "learnings")
        if not columns:
            brief["today_topics"] = [
                {
                    "slug": t["slug"],
                    "title": t["title"],
                    "has_existing_coverage": False,
                    "covering_ids": [],
                }
                for t in today_topics
            ]
            return brief

        corpus_size = 0
        shortlist: list[dict[str, Any]] = []
        contradiction_pairs: list[dict[str, Any]] = []
        stale_candidates: list[dict[str, Any]] = []
        key_buckets: dict[str, list[int]] = {}
        seen_shortlist_ids: set[int] = set()

        for row in _iter_active_learnings(conn, columns):
            corpus_size += 1
            row_id = int(row.get("id") or 0)
            row_title = str(row.get("title") or "")
            row_content = str(row.get("content") or "")
            row_applies = str(row.get("applies_to") or "")
            row_text = f"{row_title} {row_content}".strip()

            # (5) preference-key dedup — collapse colliding normalized keys.
            key = learning_resolver.normalized_key(row_title, row_applies)
            if key:
                key_buckets.setdefault(key, []).append(row_id)

            # (4) stale shortlist — weak/low-weight/never-guard-hit actives.
            if len(stale_candidates) < max_stale:
                weakness = _is_weak(row, columns)
                if weakness:
                    stale_candidates.append(
                        {"id": row_id, "title": _preview(row_title, 120), "weakness": weakness}
                    )

            # Relevance vs today's topics drives shortlist + coverage + contradiction.
            relevant_to: list[dict[str, Any]] = []
            for topic in today_topics:
                related = bool(topic["_tokens"] & set(learning_resolver._tokenize(row_text)))
                scoped = bool(
                    topic["_applies"]
                    and row_applies
                    and learning_resolver.applies_overlap(row_applies, topic["_applies"])
                )
                if not (related or scoped):
                    continue
                sim = learning_resolver.candidate_similarity(topic["_text"], row_text)
                if sim >= 0.55 or scoped:
                    relevant_to.append(topic)
                    if row_id:
                        topic["has_existing_coverage"] = True
                        # Cap example covering ids so a topic covered by hundreds of
                        # rules cannot balloon the brief; the boolean flag is what
                        # the LLM acts on.
                        if row_id not in topic["covering_ids"] and len(topic["covering_ids"]) < 10:
                            topic["covering_ids"].append(row_id)

                # (6) contradiction pairs vs today-topics.
                if len(contradiction_pairs) < max_contradictions and learning_resolver.looks_contradictory(
                    row_text, topic["_text"]
                ):
                    contradiction_pairs.append(
                        {
                            "existing_id": row_id,
                            "existing_title": _preview(row_title, 120),
                            "with": "today_topic",
                            "snippet_a": _preview(row_text, 160),
                            "snippet_b": _preview(topic["_text"], 160),
                            "similarity": round(float(sim), 4),
                        }
                    )

            if relevant_to and len(shortlist) < max_shortlist and row_id not in seen_shortlist_ids:
                seen_shortlist_ids.add(row_id)
                shortlist.append(
                    {
                        "id": row_id,
                        "title": _preview(row_title, 120),
                        "category": str(row.get("category") or ""),
                        "applies_to": row_applies,
                        "content_preview": _preview(row_content, 160),
                    }
                )

        # (5) preference-key dupes — only keys with 2+ colliding ids. Cap both the
        # number of dupe groups and the ids listed per group so a pathological
        # corpus (hundreds of identical-title rules) cannot balloon the brief.
        preference_key_dupes = []
        for key, ids in key_buckets.items():
            if len(ids) <= 1:
                continue
            preference_key_dupes.append({"key": key, "ids": ids[:10], "total": len(ids)})
            if len(preference_key_dupes) >= max_stale:
                break

        # (3) supersession stubs — today-topics that already have higher-authority
        # coverage are candidates to be replaced by a canonical rule.
        supersession_stubs: list[dict[str, Any]] = []
        for topic in today_topics:
            for old_id in topic["covering_ids"][:1]:
                supersession_stubs.append(
                    {
                        "old_id": old_id,
                        "old_title": next(
                            (s["title"] for s in shortlist if s["id"] == old_id),
                            "",
                        ),
                        "reason": f"today topic '{topic['slug']}' may replace existing rule #{old_id}",
                    }
                )

        brief["corpus_size"] = corpus_size
        brief["today_topics"] = [
            {
                "slug": t["slug"],
                "title": t["title"],
                "has_existing_coverage": bool(t["has_existing_coverage"]),
                "covering_ids": list(t["covering_ids"]),
            }
            for t in today_topics
        ]
        brief["shortlist"] = shortlist
        brief["contradiction_pairs"] = contradiction_pairs
        brief["supersession_stubs"] = supersession_stubs
        brief["stale_candidates"] = stale_candidates
        brief["preference_key_dupes"] = preference_key_dupes
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass

    # Enforce max_chars: drop lowest-priority items until the serialized brief is
    # under budget. Stale candidates and supersession stubs are the first to go,
    # then contradiction pairs (least relevant first), then shortlist tail.
    def _size() -> int:
        return len(json.dumps(brief, ensure_ascii=False))

    if _size() > max_chars:
        brief["truncated"] = True
        trim_order = ("preference_key_dupes", "supersession_stubs", "stale_candidates")
        for field in trim_order:
            while brief[field] and _size() > max_chars:
                brief[field].pop()
        while len(brief["contradiction_pairs"]) > 1 and _size() > max_chars:
            brief["contradiction_pairs"].pop()
        while len(brief["shortlist"]) > 1 and _size() > max_chars:
            brief["shortlist"].pop()
        # Last resort: trim contradiction/shortlist to empty-ish.
        while brief["contradiction_pairs"] and _size() > max_chars:
            brief["contradiction_pairs"].pop()
        while brief["shortlist"] and _size() > max_chars:
            brief["shortlist"].pop()

    return brief


__all__ = ["build_consolidation_brief"]
