from __future__ import annotations

"""Canonical learning candidate resolver.

This module decides what should happen to an incoming learning before any
caller mutates the learnings table. It deliberately returns a decision only;
MCP tools, Deep Sleep, validators and self-audit own the actual write.
"""

import re
import sqlite3
import unicodedata
from typing import Any

from db import extract_keywords, get_db
from db._semantic_similarity import hybrid_similarity_score


AUTHORITY_RANKS: dict[str, int] = {
    "francisco_correction": 100,
    "explicit_instruction": 80,
    "code_test_evidence": 60,
    "deep_sleep": 40,
    "inference": 20,
}

CANONICAL_ACTIONS = ("new", "merge", "supersede", "conflict_review", "reject")

NEGATION_PATTERNS = (
    "do not", "don't", "never", "avoid", "skip", "without", "forbid", "forbidden",
    "disable", "disabled", "remove", "ban", "bypass",
    " no ", " nunca ", " evita ", " evitar ", " sin ", " prohibe ", " prohibido ",
    " desactiva ", " desactivar ", " elimina ", " eliminar ", " bloquea ", " bloquear ",
)
CONTRADICTION_PAIRS = (
    ("enable", "disable"),
    ("use", "avoid"),
    ("add", "remove"),
    ("allow", "forbid"),
    ("always", "never"),
    ("before", "after"),
    ("require", "skip"),
    ("validate", "skip"),
    ("validate", "bypass"),
    ("include", "exclude"),
    ("activar", "desactivar"),
    ("usar", "evitar"),
    ("usar", "no usar"),
    ("editar", "no editar"),
    ("tocar", "no tocar"),
    ("anadir", "eliminar"),
    ("permitir", "prohibir"),
    ("validar", "saltar"),
    ("incluir", "excluir"),
)


def normalize_authority(value: str | None) -> str:
    clean = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "francisco": "francisco_correction",
        "user_correction": "francisco_correction",
        "correction": "francisco_correction",
        "explicit": "explicit_instruction",
        "operator": "explicit_instruction",
        "manual": "explicit_instruction",
        "code": "code_test_evidence",
        "test": "code_test_evidence",
        "evidence": "code_test_evidence",
        "deep": "deep_sleep",
        "deepsleep": "deep_sleep",
        "overnight": "deep_sleep",
        "inferred": "inference",
    }
    clean = aliases.get(clean, clean)
    return clean if clean in AUTHORITY_RANKS else "inference"


def authority_rank(value: str | None) -> int:
    return AUTHORITY_RANKS[normalize_authority(value)]


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_text.strip().lower())


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_-]+", _normalize_text(text))


def _token_sets_related(left: set[str], right: set[str]) -> bool:
    if left & right:
        return True
    for left_token in left:
        for right_token in right:
            if len(left_token) < 5 or len(right_token) < 5:
                continue
            if left_token.startswith(right_token[:5]) or right_token.startswith(left_token[:5]):
                return True
    return False


def _contains_negation(text: str) -> bool:
    lowered = f" {_normalize_text(text)} "
    return any(token in lowered for token in NEGATION_PATTERNS)


def _negated_action_verbs(text: str) -> set[str]:
    lowered = _normalize_text(text)
    matches: set[str] = set()
    for pattern in (
        r"(?:never|avoid|skip|disable|remove|forbid|bypass|nunca|evita|evitar|desactiva|desactivar|elimina|eliminar|prohibe|prohibir|bloquea|bloquear)\s+([a-z0-9_-]+)",
        r"(?:do not|don't|no)\s+([a-z0-9_-]+)",
        r"(?:without|sin)\s+([a-z0-9_-]+)",
    ):
        matches.update(re.findall(pattern, lowered))
    return {match for match in matches if len(match) > 2}


def looks_contradictory(existing_text: str, new_text: str) -> bool:
    existing_norm = _normalize_text(existing_text)
    new_norm = _normalize_text(new_text)
    if not existing_norm or not new_norm:
        return False
    existing_tokens = set(_tokenize(existing_norm))
    new_tokens = set(_tokenize(new_norm))
    if not _token_sets_related(existing_tokens, new_tokens):
        return False
    existing_negated = _negated_action_verbs(existing_norm)
    new_negated = _negated_action_verbs(new_norm)
    if existing_negated & new_tokens and not existing_negated & new_negated:
        return True
    if new_negated & existing_tokens and not existing_negated & new_negated:
        return True
    if _contains_negation(existing_norm) != _contains_negation(new_norm):
        return True
    for positive, negative in CONTRADICTION_PAIRS:
        existing_has_pair = positive in existing_norm or negative in existing_norm
        new_has_pair = positive in new_norm or negative in new_norm
        if existing_has_pair and new_has_pair:
            if (positive in existing_norm and negative in new_norm) or (negative in existing_norm and positive in new_norm):
                return True
    return False


def _split_applies_to(applies_to: str) -> list[str]:
    return [item.strip() for item in str(applies_to or "").split(",") if item.strip()]


def _normalize_applies_token(value: str) -> str:
    return str(value or "").replace("\\", "/").rstrip("/").lower()


def applies_overlap(left: str, right: str) -> bool:
    left_tokens = {_normalize_applies_token(item) for item in _split_applies_to(left)}
    right_tokens = {_normalize_applies_token(item) for item in _split_applies_to(right)}
    left_tokens.discard("")
    right_tokens.discard("")
    if not left_tokens or not right_tokens:
        return False
    if left_tokens & right_tokens:
        return True
    for left_token in left_tokens:
        for right_token in right_tokens:
            if "/" not in left_token and "/" not in right_token:
                continue
            if left_token.startswith(f"{right_token}/") or right_token.startswith(f"{left_token}/"):
                return True
            if left_token.endswith(f"/{right_token}") or right_token.endswith(f"/{left_token}"):
                return True
    return False


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _row_authority_rank(row: dict[str, Any]) -> int:
    text = " ".join(
        str(row.get(key) or "")
        for key in ("title", "content", "reasoning", "prevention")
    ).lower()
    if "francisco" in text or "correction" in text or "correccion" in text:
        return AUTHORITY_RANKS["francisco_correction"]
    priority = str(row.get("priority") or "medium").strip().lower()
    return {
        "critical": 85,
        "high": 70,
        "medium": 50,
        "low": 30,
    }.get(priority, 50)


def _similarity(candidate_text: str, row: dict[str, Any]) -> float:
    existing_text = f"{row.get('title') or ''} {row.get('content') or ''}".strip()
    if not candidate_text or not existing_text:
        return 0.0
    return float(
        hybrid_similarity_score(
            candidate_text,
            existing_text,
            keyword_extractor=extract_keywords,
            strong_semantic_threshold=0.82,
            moderate_semantic_threshold=0.74,
            moderate_keyword_floor=0.08,
        )
    )


def _decision(
    *,
    action: str,
    reason: str,
    target: dict[str, Any] | None,
    similarity: float = 0.0,
    source_authority: str,
    existing_rank: int = 0,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    normalized_authority = normalize_authority(source_authority)
    return {
        "ok": action != "reject",
        "action": action,
        "allowed_actions": list(CANONICAL_ACTIONS),
        "reason": reason,
        "target_id": int(target.get("id") or 0) if target else 0,
        "target_title": str(target.get("title") or "") if target else "",
        "target_status": str(target.get("status") or "") if target else "",
        "similarity": round(float(similarity or 0.0), 4),
        "source_authority": normalized_authority,
        "authority_rank": AUTHORITY_RANKS[normalized_authority],
        "existing_authority_rank": int(existing_rank or 0),
        "candidate": candidate,
    }


def resolve_learning_candidate(
    *,
    category: str,
    title: str,
    content: str,
    reasoning: str = "",
    prevention: str = "",
    applies_to: str = "",
    priority: str = "medium",
    supersedes_id: int = 0,
    source_authority: str = "inference",
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Return the canonical action for an incoming learning candidate."""

    clean_category = str(category or "").strip().lower()
    clean_title = str(title or "").strip()
    clean_content = str(content or "").strip()
    candidate = {
        "category": clean_category,
        "title": clean_title,
        "content_preview": clean_content[:240],
        "applies_to": str(applies_to or "").strip(),
        "priority": str(priority or "medium").strip().lower(),
        "supersedes_id": int(supersedes_id or 0),
    }
    if not clean_category:
        return _decision(
            action="reject",
            reason="category_required",
            target=None,
            source_authority=source_authority,
            candidate=candidate,
        )
    if not clean_title or not clean_content:
        return _decision(
            action="reject",
            reason="title_and_content_required",
            target=None,
            source_authority=source_authority,
            candidate=candidate,
        )

    own_conn = conn is None
    conn = conn or get_db()
    try:
        columns = _table_columns(conn, "learnings")
        if not columns:
            return _decision(
                action="new",
                reason="learnings_table_unavailable",
                target=None,
                source_authority=source_authority,
                candidate=candidate,
            )
        status_filter = " AND COALESCE(status, 'active') = 'active'" if "status" in columns else ""
        order_by = "updated_at DESC, id DESC" if "updated_at" in columns else "id DESC"
        rows = conn.execute(
            f"""
            SELECT *
              FROM learnings
             WHERE category = ?
               {status_filter}
             ORDER BY {order_by}
             LIMIT 500
            """,
            (clean_category,),
        ).fetchall()
        active_rows = [dict(row) for row in rows]
    finally:
        if own_conn:
            pass

    incoming_text = f"{clean_title} {clean_content}".strip()
    incoming_rank = authority_rank(source_authority)
    best_sim: tuple[float, dict[str, Any] | None] = (0.0, None)
    conflict: dict[str, Any] | None = None
    conflict_similarity = 0.0

    for row in active_rows:
        row_title = str(row.get("title") or "").strip()
        row_content = str(row.get("content") or "").strip()
        if row_title.lower() == clean_title.lower():
            return _decision(
                action="merge",
                reason="exact_title_duplicate",
                target=row,
                similarity=1.0,
                source_authority=source_authority,
                existing_rank=_row_authority_rank(row),
                candidate=candidate,
            )

        row_applies = str(row.get("applies_to") or "")
        scoped_overlap = bool(applies_to and row_applies and applies_overlap(row_applies, applies_to))
        if scoped_overlap and looks_contradictory(f"{row_title} {row_content}", incoming_text):
            sim = _similarity(incoming_text, row)
            conflict = row
            conflict_similarity = sim
            break

        sim = _similarity(incoming_text, row)
        if sim > best_sim[0]:
            best_sim = (sim, row)

    if conflict:
        existing_rank = _row_authority_rank(conflict)
        normalized_authority = normalize_authority(source_authority)
        if int(supersedes_id or 0) == int(conflict.get("id") or 0):
            return _decision(
                action="supersede",
                reason="explicit_supersedes_conflict",
                target=conflict,
                similarity=conflict_similarity,
                source_authority=source_authority,
                existing_rank=existing_rank,
                candidate=candidate,
            )
        can_auto_supersede = (
            normalized_authority == "francisco_correction"
            or (
                normalized_authority == "explicit_instruction"
                and incoming_rank >= existing_rank
                and existing_rank < AUTHORITY_RANKS["code_test_evidence"]
            )
        )
        if can_auto_supersede:
            return _decision(
                action="supersede",
                reason="higher_authority_conflict",
                target=conflict,
                similarity=conflict_similarity,
                source_authority=source_authority,
                existing_rank=existing_rank,
                candidate=candidate,
            )
        return _decision(
            action="conflict_review",
            reason="conflicting_active_learning",
            target=conflict,
            similarity=conflict_similarity,
            source_authority=source_authority,
            existing_rank=existing_rank,
            candidate=candidate,
        )

    best_score, best_row = best_sim
    if best_row and best_score >= 0.85:
        return _decision(
            action="merge",
            reason="high_similarity",
            target=best_row,
            similarity=best_score,
            source_authority=source_authority,
            existing_rank=_row_authority_rank(best_row),
            candidate=candidate,
        )

    return _decision(
        action="new",
        reason="no_active_match",
        target=None,
        similarity=best_score,
        source_authority=source_authority,
        existing_rank=0,
        candidate=candidate,
    )


__all__ = [
    "AUTHORITY_RANKS",
    "CANONICAL_ACTIONS",
    "applies_overlap",
    "authority_rank",
    "looks_contradictory",
    "normalize_authority",
    "resolve_learning_candidate",
]
