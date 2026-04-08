"""Learnings CRUD tools: add, search, update, delete, list."""

import re
from datetime import datetime

from db import (create_learning, update_learning, delete_learning, search_learnings,
                list_learnings, find_similar_learnings, get_db, now_epoch, supersede_learning)

NEGATION_PATTERNS = (
    "do not", "don't", "never", "avoid", "skip", "without", "forbid", "forbidden",
    "disable", "disabled", "remove", "ban", "bypass",
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
)


def _split_applies_to(applies_to: str) -> list[str]:
    return [item.strip() for item in str(applies_to or "").split(",") if item.strip()]


def _normalize_applies_token(value: str) -> str:
    return str(value or "").replace("\\", "/").rstrip("/").lower()


def _applies_overlap(left: str, right: str) -> bool:
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
            if "/" in left_token or "/" in right_token:
                if left_token.startswith(f"{right_token}/") or right_token.startswith(f"{left_token}/"):
                    return True
                if left_token.endswith(f"/{right_token}") or right_token.endswith(f"/{left_token}"):
                    return True
    return False


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_-]+", _normalize_text(text))


def _contains_negation(text: str) -> bool:
    lowered = _normalize_text(text)
    return any(token in lowered for token in NEGATION_PATTERNS)


def _negated_action_verbs(text: str) -> set[str]:
    lowered = _normalize_text(text)
    matches = set()
    for pattern in (
        r"(?:never|avoid|skip|disable|remove|forbid|bypass)\s+([a-z0-9_-]+)",
        r"(?:do not|don't)\s+([a-z0-9_-]+)",
    ):
        matches.update(re.findall(pattern, lowered))
    return {match for match in matches if len(match) > 2}


def _looks_contradictory(existing_text: str, new_text: str) -> bool:
    existing_norm = _normalize_text(existing_text)
    new_norm = _normalize_text(new_text)
    if not existing_norm or not new_norm:
        return False
    existing_tokens = set(_tokenize(existing_norm))
    new_tokens = set(_tokenize(new_norm))
    if not (existing_tokens & new_tokens):
        return False
    existing_negated_verbs = _negated_action_verbs(existing_norm)
    new_negated_verbs = _negated_action_verbs(new_norm)
    if existing_negated_verbs & new_tokens and not existing_negated_verbs & new_negated_verbs:
        return True
    if new_negated_verbs & existing_tokens and not existing_negated_verbs & new_negated_verbs:
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


def _find_conflicting_active_learning(conn, *, category: str, title: str, content: str,
                                      applies_to: str, exclude_id: int | None = None) -> dict | None:
    if not applies_to:
        return None
    params = [category]
    sql = (
        "SELECT id, title, content, applies_to FROM learnings "
        "WHERE category = ? AND status = 'active' AND COALESCE(applies_to, '') != ''"
    )
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    rows = conn.execute(sql, tuple(params)).fetchall()
    incoming_text = f"{title} {content}"
    for row in rows:
        if not _applies_overlap(row["applies_to"], applies_to):
            continue
        if _looks_contradictory(f"{row['title']} {row['content']}", incoming_text):
            return dict(row)
    return None


def find_conflicting_active_learning(*, category: str, title: str, content: str,
                                     applies_to: str, exclude_id: int | None = None) -> dict | None:
    """Public wrapper for canonical-rule enforcement on file-scoped learnings."""
    conn = get_db()
    return _find_conflicting_active_learning(
        conn,
        category=category.lower().strip(),
        title=title,
        content=content,
        applies_to=applies_to,
        exclude_id=exclude_id,
    )


def _priority_score(priority: str) -> float:
    return {
        "critical": 1.0,
        "high": 0.85,
        "medium": 0.65,
        "low": 0.45,
    }.get(str(priority or "medium").strip().lower(), 0.65)


def _parse_timestamp(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except Exception:
        pass
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            if fmt is None:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
            return datetime.strptime(text, fmt).timestamp()
        except Exception:
            continue
    return 0.0


def _recency_score(row: dict) -> float:
    reference = _parse_timestamp(row.get("last_guard_hit_at")) or _parse_timestamp(row.get("updated_at")) or _parse_timestamp(row.get("created_at"))
    if not reference:
        return 0.35
    age_days = max(0.0, (now_epoch() - reference) / 86400.0)
    if age_days <= 7:
        return 1.0
    if age_days <= 30:
        return 0.8
    if age_days <= 90:
        return 0.6
    if age_days <= 180:
        return 0.4
    return 0.25


def _usefulness_score(row: dict) -> float:
    hits = int(row.get("guard_hits") or 0)
    if hits >= 5:
        return 1.0
    if hits >= 3:
        return 0.85
    if hits >= 1:
        return 0.65
    return 0.35 if str(row.get("status") or "active") == "active" else 0.15


def _source_richness_score(row: dict) -> float:
    parts = 0.0
    if str(row.get("reasoning") or "").strip():
        parts += 0.25
    if str(row.get("prevention") or "").strip():
        parts += 0.25
    if str(row.get("applies_to") or "").strip():
        parts += 0.2
    if row.get("review_due_at"):
        parts += 0.15
    if len(str(row.get("content") or "").strip()) >= 80:
        parts += 0.15
    return min(1.0, parts)


def _contradiction_pressure_score(conn, row: dict) -> float:
    if str(row.get("status") or "active") != "active":
        return 0.7
    conflicting = _find_conflicting_active_learning(
        conn,
        category=str(row.get("category") or ""),
        title=str(row.get("title") or ""),
        content=str(row.get("content") or ""),
        applies_to=str(row.get("applies_to") or ""),
        exclude_id=int(row.get("id") or 0),
    )
    return 0.0 if conflicting else 1.0


def score_learning_quality(row: dict, conn=None) -> dict:
    """Compute a 0-100 quality score for a learning using usefulness and conflict pressure."""
    conn = conn or get_db()
    confidence = min(1.0, (_priority_score(row.get("priority")) * 0.45) + (float(row.get("weight") or 0.5) * 0.55))
    usefulness = _usefulness_score(row)
    recency = _recency_score(row)
    contradiction = _contradiction_pressure_score(conn, row)
    source_richness = _source_richness_score(row)
    status = str(row.get("status") or "active")
    status_multiplier = 1.0 if status == "active" else 0.65 if status in {"pending_review", "review"} else 0.45
    overall = (
        confidence * 0.28
        + usefulness * 0.24
        + recency * 0.18
        + contradiction * 0.18
        + source_richness * 0.12
    ) * status_multiplier
    score = max(0, min(100, round(overall * 100)))
    if score >= 80:
        label = "strong"
    elif score >= 60:
        label = "usable"
    elif score >= 40:
        label = "weak"
    else:
        label = "fragile"
    return {
        "score": score,
        "label": label,
        "confidence": round(confidence * 100),
        "usefulness": round(usefulness * 100),
        "recency_relevance": round(recency * 100),
        "contradiction_pressure": round((1.0 - contradiction) * 100),
        "source_richness": round(source_richness * 100),
    }


def handle_learning_add(category: str, title: str, content: str, reasoning: str = '',
                        prevention: str = '', applies_to: str = '', review_days: int = 30,
                        priority: str = 'medium', supersedes_id: int = 0) -> str:
    """Add a new learning entry to the specified category.

    Args:
        category: Free-form category name (e.g., 'backend', 'frontend', 'devops', 'infrastructure', 'security', 'nexo-ops'). Use consistent names — learnings are grouped and searched by category.
        title: Short title for the learning
        content: Full description of what was learned
        reasoning: WHY this matters — what led to discovering this, what was the context
        prevention: Concrete rule/check that prevents repeating this mistake
        applies_to: Files, systems, or areas this learning applies to
        review_days: Days until this learning should be reviewed again
        priority: critical, high, medium, low (default: medium)
    """
    if priority not in ('critical', 'high', 'medium', 'low'):
        priority = 'medium'
    category = category.lower().strip()
    if not category:
        return "ERROR: Category cannot be empty."
    # Dedup guard: block exact title duplicates in same category
    conn = get_db()
    existing = conn.execute(
        "SELECT id, title FROM learnings WHERE LOWER(title) = LOWER(?) AND category = ? AND status = 'active'",
        (title.strip(), category)
    ).fetchone()
    if existing:
        return f"Learning #{existing['id']} already exists with same title in {category}: {existing['title']}. Use nexo_learning_update to modify it."
    conflicting = _find_conflicting_active_learning(
        conn,
        category=category,
        title=title,
        content=content,
        applies_to=applies_to,
    )
    if conflicting and int(supersedes_id or 0) != int(conflicting["id"]):
        return (
            f"ERROR: Contradictory active learning #{conflicting['id']} already exists for applies_to="
            f"{conflicting.get('applies_to', '')}: {conflicting['title']}. "
            f"Supersede or update the existing canonical rule instead of creating two active file rules."
        )
    result = create_learning(
        category, title, content, reasoning=reasoning, supersedes_id=(int(supersedes_id) if supersedes_id else None)
    )
    if "error" in result:
        return f"ERROR: {result['error']}"
    if prevention or applies_to or review_days > 0 or priority != 'medium':
        initial_weight = {'critical': 0.9, 'high': 0.7, 'medium': 0.5, 'low': 0.3}[priority]
        conn = get_db()
        conn.execute(
            "UPDATE learnings SET prevention = ?, applies_to = ?, status = COALESCE(status, 'active'), "
            "review_due_at = ?, updated_at = ?, priority = ?, weight = ? WHERE id = ?",
            (prevention, applies_to, now_epoch() + (max(1, int(review_days)) * 86400), now_epoch(),
             priority, initial_weight, result["id"])
        )
        conn.commit()
        result = conn.execute("SELECT * FROM learnings WHERE id = ?", (result["id"],)).fetchone()
        result = dict(result)

    # Cognitive ingest — embed learning for semantic search
    new_id = result["id"]
    try:
        import cognitive
        cognitive.ingest(f"{title}: {content}", "learning", f"L{new_id}", title, category)
    except Exception:
        pass

    # Similarity check — detect repeated errors
    matches = find_similar_learnings(new_id, title, content, category)
    repetition_msg = ""
    if matches:
        conn = get_db()
        for original_id, similarity in matches:
            conn.execute(
                "INSERT INTO error_repetitions (new_learning_id, original_learning_id, similarity, area) VALUES (?,?,?,?)",
                (new_id, original_id, similarity, category)
            )
        conn.commit()
        repetition_msg = f"\n⚠️ REPETITION WARNING: Similar to {len(matches)} existing learning(s): " + \
            ", ".join(f"#{m[0]} ({m[1]:.0%})" for m in matches[:3])

    # Somatic event logging (append-only in nexo.db, projected to cognitive.db nightly)
    try:
        if applies_to:
            for file_path in [f.strip() for f in applies_to.split(",") if f.strip()]:
                get_db().execute(
                    "INSERT INTO somatic_events (target, target_type, event_type, delta, source) VALUES (?, ?, ?, ?, ?)",
                    (file_path, "file", "learning_add", 0.15, f"learning:{new_id}")
                )
        # Area + extra file pain ONLY for repeated errors
        if matches:
            get_db().execute(
                "INSERT INTO somatic_events (target, target_type, event_type, delta, source) VALUES (?, ?, ?, ?, ?)",
                (category, "area", "error_repetition", 0.15, f"learning:{new_id}")
            )
            if applies_to:
                for file_path in [f.strip() for f in applies_to.split(",") if f.strip()]:
                    get_db().execute(
                        "INSERT INTO somatic_events (target, target_type, event_type, delta, source) VALUES (?, ?, ?, ?, ?)",
                        (file_path, "file", "error_repetition", 0.25, f"learning:{new_id}")
                    )
        get_db().commit()
    except Exception:
        pass  # Somatic event logging is best-effort

    # Knowledge graph incremental population
    try:
        from kg_populate import on_learning_add
        on_learning_add(new_id, category, title, applies_to)
    except Exception:
        pass

    if supersedes_id:
        superseded = supersede_learning(int(supersedes_id), new_id, f"Superseded by learning #{new_id}.")
        if "error" in superseded:
            return f"ERROR: Learning #{new_id} created but supersede failed: {superseded['error']}"

    # Post-insert verification: confirm the learning actually persisted
    verify_conn = get_db()
    verified = verify_conn.execute(
        "SELECT id, title, category FROM learnings WHERE id = ? AND status = 'active'",
        (result["id"],)
    ).fetchone()
    if not verified:
        return (
            f"⚠ PERSISTENCE FAILURE: Learning #{result['id']} was inserted but NOT found on verification read. "
            f"Retry nexo_learning_add or investigate DB integrity."
        )

    meta = []
    if prevention:
        meta.append("with prevention")
    if applies_to:
        meta.append(f"applies_to={applies_to}")
    if supersedes_id:
        meta.append(f"supersedes={int(supersedes_id)}")
    meta_str = f" ({', '.join(meta)})" if meta else ""
    return f"Learning #{result['id']} added in {category}: {title}{meta_str} ✓verified{repetition_msg}"


def handle_learning_search(query: str, category: str = '') -> str:
    """Search learnings by query string, optionally filtered by category."""
    results = search_learnings(query, category if category else None)
    if not results:
        return f"No results for '{query}'."
    lines = [f"RESULTS ({len(results)}):"]
    conn = get_db()
    for r in results:
        snippet = r["content"][:100] + "..." if len(r["content"]) > 100 else r["content"]
        status = r.get("status", "active")
        review_due = r.get("review_due_at")
        review_note = f" | review_due={review_due:.0f}" if isinstance(review_due, (int, float)) and review_due else ""
        pri = r.get("priority", "medium") or "medium"
        w = r.get("weight", 0.5) or 0.5
        quality = score_learning_quality(r, conn)
        pri_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}.get(pri, "🟡")
        lines.append(f"  #{r['id']} [{r['category']}] [{status}] {pri_icon}{pri} w={w:.2f} q={quality['score']} {r['title']}{review_note}")
        lines.append(f"    {snippet}")
        if r.get("prevention"):
            lines.append(f"    Prevention: {r['prevention'][:100]}")

    # v1.2: Passive rehearsal — strengthen matching cognitive memories
    try:
        import cognitive
        for r in results[:5]:
            cognitive.rehearse_by_content(f"{r.get('title', '')} {r.get('content', '')[:200]}")
    except Exception:
        pass

    return "\n".join(lines)


def handle_learning_update(id: int, title: str = '', content: str = '', category: str = '',
                           reasoning: str = '', prevention: str = '', applies_to: str = '',
                           status: str = '', review_days: int = 0, priority: str = '',
                           supersedes_id: int = 0) -> str:
    """Update an existing learning, including review metadata and priority."""
    conn = get_db()
    current = conn.execute("SELECT * FROM learnings WHERE id = ?", (id,)).fetchone()
    if not current:
        return f"ERROR: Learning #{id} not found."
    kwargs = {}
    if title:
        kwargs["title"] = title
    if content:
        kwargs["content"] = content
    if category:
        kwargs["category"] = category.lower().strip()
    if reasoning:
        kwargs["reasoning"] = reasoning
    if prevention:
        kwargs["prevention"] = prevention
    if applies_to:
        kwargs["applies_to"] = applies_to
    if status:
        kwargs["status"] = status
    if review_days > 0:
        kwargs["review_days"] = review_days
    if not kwargs:
        return "ERROR: Nothing to update. Provide new fields."
    effective_category = kwargs.get("category", current["category"])
    effective_title = kwargs.get("title", current["title"])
    effective_content = kwargs.get("content", current["content"])
    effective_applies_to = kwargs.get("applies_to", current["applies_to"])
    effective_status = kwargs.get("status", current["status"])
    if effective_status != "superseded":
        conflicting = _find_conflicting_active_learning(
            conn,
            category=effective_category,
            title=effective_title,
            content=effective_content,
            applies_to=effective_applies_to,
            exclude_id=id,
        )
        if conflicting and int(supersedes_id or 0) != int(conflicting["id"]):
            return (
                f"ERROR: Update would conflict with active learning #{conflicting['id']} "
                f"for applies_to={conflicting.get('applies_to', '')}. "
                f"Supersede the old rule or merge into one canonical learning."
            )
    basic_kwargs = {k: v for k, v in kwargs.items() if k in {"title", "content", "category", "reasoning"}}
    result = update_learning(id, **basic_kwargs)
    if "error" in result:
        return f"ERROR: {result['error']}"
    extra_updates = {}
    if prevention:
        extra_updates["prevention"] = prevention
    if applies_to:
        extra_updates["applies_to"] = applies_to
    if status:
        extra_updates["status"] = status
    if priority and priority in ('critical', 'high', 'medium', 'low'):
        extra_updates["priority"] = priority
        extra_updates["weight"] = {'critical': 0.9, 'high': 0.7, 'medium': 0.5, 'low': 0.3}[priority]
    if review_days > 0:
        extra_updates["review_due_at"] = now_epoch() + (max(1, int(review_days)) * 86400)
    if extra_updates:
        extra_updates["updated_at"] = now_epoch()
        set_clause = ", ".join(f"{k} = ?" for k in extra_updates)
        values = list(extra_updates.values()) + [id]
        conn = get_db()
        conn.execute(f"UPDATE learnings SET {set_clause} WHERE id = ?", values)
        conn.commit()
    if supersedes_id:
        superseded = supersede_learning(int(supersedes_id), id, f"Superseded by learning #{id}.")
        if "error" in superseded:
            return f"ERROR: Learning #{id} updated but supersede failed: {superseded['error']}"
    return f"Learning #{id} updated."


def handle_learning_delete(id: int) -> str:
    """Delete a learning entry by ID."""
    deleted = delete_learning(id)
    if not deleted:
        return f"ERROR: Learning #{id} not found."
    return f"Learning #{id} deleted."


def handle_learning_list(category: str = '') -> str:
    """List all learnings, grouped by category if no filter given."""
    results = list_learnings(category if category else None)
    if not results:
        label = category if category else "ALL"
        return f"LEARNINGS {label} (0): No entries."

    conn = get_db()
    if category:
        label = category.upper()
        lines = [f"LEARNINGS {label} ({len(results)}):"]
        for r in results:
            pri = r.get("priority", "medium") or "medium"
            w = r.get("weight", 0.5) or 0.5
            quality = score_learning_quality(r, conn)
            pri_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}.get(pri, "🟡")
            lines.append(f"  #{r['id']} [{r.get('status','active')}] {pri_icon}{pri} w={w:.2f} q={quality['score']} {r['title']}")
    else:
        lines = [f"LEARNINGS ALL ({len(results)}):"]
        current_cat = None
        for r in results:
            if r["category"] != current_cat:
                current_cat = r["category"]
                lines.append(f"\n  [{current_cat.upper()}]")
            pri = r.get("priority", "medium") or "medium"
            w = r.get("weight", 0.5) or 0.5
            quality = score_learning_quality(r, conn)
            pri_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}.get(pri, "🟡")
            lines.append(f"    #{r['id']} [{r.get('status','active')}] {pri_icon}{pri} w={w:.2f} q={quality['score']} {r['title']}")

    return "\n".join(lines)


def handle_learning_quality(id: int = 0, category: str = "", status: str = "active", limit: int = 20) -> str:
    """Inspect memory quality so fragile learnings can be tightened before they mislead guard/retrieval."""
    results = list_learnings(category if category else None)
    if id:
        results = [row for row in results if int(row.get("id") or 0) == int(id)]
    if status:
        results = [row for row in results if str(row.get("status") or "").lower() == str(status).lower()]
    results = results[: max(1, int(limit or 20))]
    if not results:
        return "LEARNING QUALITY (0): No matching learnings."

    conn = get_db()
    scored = []
    for row in results:
        quality = score_learning_quality(row, conn)
        scored.append((row, quality))
    avg_score = round(sum(item[1]["score"] for item in scored) / len(scored))
    weak = [item for item in scored if item[1]["score"] < 60]
    lines = [f"LEARNING QUALITY ({len(scored)}) avg={avg_score} weak={len(weak)}:"]
    for row, quality in scored:
        lines.append(
            f"  #{row['id']} q={quality['score']} [{quality['label']}] {row['title']} "
            f"(conf={quality['confidence']} useful={quality['usefulness']} recency={quality['recency_relevance']} "
            f"pressure={quality['contradiction_pressure']} richness={quality['source_richness']})"
        )
    return "\n".join(lines)
