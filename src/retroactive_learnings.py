"""Retroactive application of new learnings to past decisions.

Closes Fase 2 item 3 of NEXO-AUDIT-2026-04-11. The other six items in the
phase wired existing infrastructure together, but this one was a true
green-field gap: grep for "retroactive" in src/ returned zero matches
before this module existed.

The idea is simple. Whenever a new learning lands with a `prevention`
rule, scan recent decisions and find the ones that would have been
decided differently under the new rule. Surface each match as a
deterministic `NF-RETRO-L<learning_id>-D<decision_id>` followup so the
operator can revisit the call without the system silently mutating any
historical record.

Why no new schema:
    Followups already have idempotent INSERT OR REPLACE semantics on the
    primary id. Using a deterministic id per (learning, decision) pair
    means re-running the helper is a no-op. There is no need for a
    `retroactive_learning_matches` table; the followups table is the
    single source of truth and the existing dashboards already render it.

Matching strategy:
    Two cheap signals combined into a single score in [0.0, 1.0]:
      1. applies_to overlap: if the learning lists files / areas / domains
         in `applies_to`, and the decision's `domain` (or words from it)
         matches any of those tokens, applies_to_score = 1.0 else 0.0.
      2. keyword overlap: significant tokens (>= 4 chars, not stopwords)
         from the learning's title + content + prevention, intersected
         with significant tokens from the decision's
         decision + based_on + alternatives + context_ref. Score is
         intersection_size / max(1, learning_token_count) clipped to 1.0.
    Combined score: 0.5 * applies_to_score + 0.5 * keyword_score.
    Default match threshold: 0.4. Default cap: 5 matches per learning.

Anti-spam guards:
    - Skip if the learning has no `prevention` (just narrative learnings
      do not generate retroactive followups — they are not enforceable).
    - Skip if the learning's status is not 'active'.
    - Hard cap of `max_matches` followups per call (default 5).
    - Per (learning, decision) idempotency via deterministic id.
    - Lookback window default 14 days; configurable.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# A small stopword list keeps the keyword matcher from picking up filler.
_STOPWORDS = frozenset({
    "para", "este", "esta", "esto", "como", "cuando", "donde", "porque",
    "pero", "tambien", "siempre", "nunca", "antes", "despues", "sobre",
    "entre", "hacia", "desde", "hasta", "with", "from", "this", "that",
    "have", "been", "will", "would", "should", "could", "after", "before",
    "while", "when", "where", "what", "which", "their", "there", "these",
    "those", "into", "than", "then", "very", "more", "most", "less",
    "make", "made", "take", "took", "give", "given", "para", "como",
    "cosa", "todo", "toda", "todos", "todas", "muy",
})

_TOKEN_RE = re.compile(r"[a-zA-ZáéíóúñÁÉÍÓÚÑ_][a-zA-Z0-9áéíóúñÁÉÍÓÚÑ_]{3,}")


def _significant_tokens(text: str) -> set[str]:
    """Extract significant tokens (>=4 chars, not stopwords, lowercased)."""
    if not text:
        return set()
    tokens = _TOKEN_RE.findall(text)
    return {t.lower() for t in tokens if t.lower() not in _STOPWORDS and len(t) >= 4}


def _split_applies_to(value: str) -> set[str]:
    """Split a learning's applies_to into normalized tokens."""
    if not value:
        return set()
    pieces: set[str] = set()
    for chunk in re.split(r"[,;\s]+", value):
        chunk = chunk.strip().lower()
        if chunk:
            pieces.add(chunk)
            # Also keep the basename so 'src/db/_core.py' matches a domain like '_core'.
            base = chunk.rsplit("/", 1)[-1]
            if base and base != chunk:
                pieces.add(base)
    return pieces


def _decision_text_blob(row: dict) -> str:
    """Concatenate the searchable text fields of a decision row."""
    parts = [
        row.get("decision", "") or "",
        row.get("based_on", "") or "",
        row.get("alternatives", "") or "",
        row.get("context_ref", "") or "",
        row.get("domain", "") or "",
    ]
    return " ".join(parts)


def _score_match(
    *,
    learning_keywords: set[str],
    learning_applies_to: set[str],
    decision_row: dict,
) -> tuple[float, dict]:
    """Score how strongly a learning applies retroactively to a decision.

    Returns (score in [0.0, 1.0], breakdown dict for transparency).
    """
    decision_blob = _decision_text_blob(decision_row)
    decision_tokens = _significant_tokens(decision_blob)

    if learning_applies_to:
        domain_tokens = _significant_tokens(decision_row.get("domain", "") or "")
        applies_to_hits = learning_applies_to & (domain_tokens | decision_tokens)
        applies_to_score = 1.0 if applies_to_hits else 0.0
    else:
        applies_to_hits = set()
        applies_to_score = 0.0

    if learning_keywords:
        keyword_hits = learning_keywords & decision_tokens
        # Three significant overlapping tokens is a strong signal on its
        # own — that is the threshold a human reviewer needs to suspect a
        # rule violation. We score linearly up to that and clip.
        keyword_score = min(1.0, len(keyword_hits) / 3.0)
    else:
        keyword_hits = set()
        keyword_score = 0.0

    # max() rather than weighted average so a strong signal alone qualifies.
    # The two signals (applies_to overlap, keyword overlap) are independent
    # paths to the same conclusion: this past decision is in the new rule's
    # blast radius. If either one fires strongly, surface the match.
    score = max(applies_to_score, keyword_score)
    breakdown = {
        "applies_to_score": round(applies_to_score, 3),
        "applies_to_hits": sorted(applies_to_hits),
        "keyword_score": round(keyword_score, 3),
        "keyword_hits": sorted(keyword_hits),
    }
    return round(score, 3), breakdown


def _format_followup_description(
    *,
    learning: dict,
    decision: dict,
    score: float,
    breakdown: dict,
) -> str:
    learning_id = learning.get("id")
    decision_id = decision.get("id")
    title = (learning.get("title") or "").strip()
    prevention = (learning.get("prevention") or "").strip()
    domain = (decision.get("domain") or "").strip()
    decision_text = (decision.get("decision") or "").strip()
    created = (decision.get("created_at") or "").strip()

    lines = [
        f"Retroactive review: learning #{learning_id} may apply to decision #{decision_id}.",
        f"Score: {score:.2f} (applies_to={breakdown.get('applies_to_score', 0)}, "
        f"keyword={breakdown.get('keyword_score', 0)})",
        "",
        f"New learning: {title}",
        f"Prevention rule: {prevention}",
        "",
        f"Past decision (#{decision_id}, {domain}, {created}):",
        f"  {decision_text[:280]}",
        "",
        "Action: revisit this decision under the new rule. Update the "
        "decision row, capture a corrective learning, or close this "
        "followup as 'still valid' if the rule does not actually conflict.",
    ]
    return "\n".join(lines)


def apply_learning_retroactively(
    learning_id: int,
    *,
    lookback_days: int = 14,
    max_matches: int = 5,
    min_score: float = 0.4,
    dry_run: bool = False,
) -> dict:
    """Scan recent decisions for ones a new learning would re-evaluate.

    Args:
        learning_id: The learning row id to apply.
        lookback_days: How many days back to scan decisions (default 14).
        max_matches: Hard cap of followups created per call (default 5).
        min_score: Score threshold in [0.0, 1.0] for a match (default 0.4).
        dry_run: If True, scores matches but does not create followups.

    Returns:
        {
          "ok": bool,
          "learning_id": int,
          "scanned": int,            # decisions inspected
          "matched": int,            # decisions scored at or above threshold
          "followups_created": int,  # actual followup INSERT OR REPLACE rows
          "skipped_reason": str|None,
          "matches": [
              {"decision_id", "score", "breakdown", "followup_id"|None}, ...
          ],
        }

    Best-effort: never raises. A failing decision row is logged via the
    breakdown but does not abort the loop. The full payload is returned so
    callers (handle_learning_add, MCP tool, tests) can react.
    """
    from db import get_db

    base_result: dict[str, Any] = {
        "ok": True,
        "learning_id": int(learning_id),
        "scanned": 0,
        "matched": 0,
        "followups_created": 0,
        "skipped_reason": None,
        "matches": [],
    }

    try:
        conn = get_db()
    except Exception as e:
        base_result["ok"] = False
        base_result["skipped_reason"] = f"cannot open db: {e}"
        return base_result

    try:
        learning_row = conn.execute(
            "SELECT id, category, title, content, prevention, applies_to, status, priority "
            "FROM learnings WHERE id = ?",
            (int(learning_id),),
        ).fetchone()
    except Exception as e:
        base_result["ok"] = False
        base_result["skipped_reason"] = f"learnings query failed: {e}"
        return base_result

    if not learning_row:
        base_result["skipped_reason"] = "learning not found"
        return base_result

    learning = dict(learning_row)
    if learning.get("status") and learning["status"] != "active":
        base_result["skipped_reason"] = f"learning status is {learning['status']}, not active"
        return base_result
    prevention = (learning.get("prevention") or "").strip()
    if not prevention:
        base_result["skipped_reason"] = "learning has no prevention rule — nothing enforceable to apply"
        return base_result

    learning_keywords = _significant_tokens(
        " ".join([
            learning.get("title") or "",
            learning.get("content") or "",
            prevention,
        ])
    )
    learning_applies_to = _split_applies_to(learning.get("applies_to") or "")

    if not learning_keywords and not learning_applies_to:
        base_result["skipped_reason"] = "learning has no usable keywords or applies_to anchors"
        return base_result

    try:
        rows = conn.execute(
            "SELECT id, session_id, created_at, domain, decision, alternatives, "
            "based_on, confidence, context_ref, outcome, status "
            "FROM decisions "
            "WHERE created_at >= datetime('now', ?) "
            "ORDER BY created_at DESC LIMIT 200",
            (f"-{max(1, int(lookback_days))} days",),
        ).fetchall()
    except Exception as e:
        base_result["ok"] = False
        base_result["skipped_reason"] = f"decisions query failed: {e}"
        return base_result

    matches: list[dict] = []
    for row in rows:
        try:
            decision = dict(row)
        except Exception:
            continue
        base_result["scanned"] += 1
        score, breakdown = _score_match(
            learning_keywords=learning_keywords,
            learning_applies_to=learning_applies_to,
            decision_row=decision,
        )
        if score >= float(min_score):
            matches.append({
                "decision": decision,
                "score": score,
                "breakdown": breakdown,
            })

    matches.sort(key=lambda m: m["score"], reverse=True)
    capped = matches[: max(0, int(max_matches))]
    base_result["matched"] = len(matches)

    if dry_run:
        base_result["matches"] = [
            {
                "decision_id": int(m["decision"]["id"]),
                "score": m["score"],
                "breakdown": m["breakdown"],
                "followup_id": None,
            }
            for m in capped
        ]
        return base_result

    created = 0
    now_epoch = datetime.now().timestamp()
    summary_matches = []
    for m in capped:
        decision = m["decision"]
        followup_id = f"NF-RETRO-L{learning['id']}-D{decision['id']}"
        description = _format_followup_description(
            learning=learning,
            decision=decision,
            score=m["score"],
            breakdown=m["breakdown"],
        )
        verification = (
            f"SELECT id, domain, decision, based_on, status FROM decisions WHERE id = {int(decision['id'])}"
        )
        try:
            conn.execute(
                "INSERT OR REPLACE INTO followups (id, description, date, status, "
                "verification, created_at, updated_at, priority) "
                "VALUES (?, ?, NULL, 'PENDING', ?, ?, ?, ?)",
                (
                    followup_id,
                    description,
                    verification,
                    now_epoch,
                    now_epoch,
                    learning.get("priority") or "medium",
                ),
            )
            created += 1
            summary_matches.append({
                "decision_id": int(decision["id"]),
                "score": m["score"],
                "breakdown": m["breakdown"],
                "followup_id": followup_id,
            })
        except Exception as e:
            summary_matches.append({
                "decision_id": int(decision.get("id", 0)),
                "score": m["score"],
                "breakdown": m["breakdown"],
                "followup_id": None,
                "error": str(e),
            })

    try:
        conn.commit()
    except Exception:
        pass

    base_result["followups_created"] = created
    base_result["matches"] = summary_matches
    return base_result
