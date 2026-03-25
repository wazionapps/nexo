"""Learnings CRUD tools: add, search, update, delete, list."""

from db import (create_learning, update_learning, delete_learning, search_learnings,
                list_learnings, find_similar_learnings, get_db, now_epoch)

VALID_CATEGORIES = {
    "nexo-ops", "google-ads", "meta-ads", "google-analytics",
    "shopify", "wazion", "cloud-sql", "infrastructure", "security", "brain-engine"
}


def handle_learning_add(category: str, title: str, content: str, reasoning: str = '',
                        prevention: str = '', applies_to: str = '', review_days: int = 30) -> str:
    """Add a new learning entry to the specified category.

    Args:
        category: One of the valid categories
        title: Short title for the learning
        content: Full description of what was learned
        reasoning: WHY this matters — what led to discovering this, what was the context
        prevention: Concrete rule/check that prevents repeating this mistake
        applies_to: Files, systems, or areas this learning applies to
        review_days: Days until this learning should be reviewed again
    """
    if category not in VALID_CATEGORIES:
        valid = ", ".join(sorted(VALID_CATEGORIES))
        return f"ERROR: Category '{category}' invalid. Valid: {valid}"
    result = create_learning(
        category, title, content, reasoning=reasoning
    )
    if "error" in result:
        return f"ERROR: {result['error']}"
    if prevention or applies_to or review_days > 0:
        conn = get_db()
        conn.execute(
            "UPDATE learnings SET prevention = ?, applies_to = ?, status = COALESCE(status, 'active'), "
            "review_due_at = ?, updated_at = ? WHERE id = ?",
            (prevention, applies_to, now_epoch() + (max(1, int(review_days)) * 86400), now_epoch(), result["id"])
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

    meta = []
    if prevention:
        meta.append("with prevention")
    if applies_to:
        meta.append(f"applies_to={applies_to}")
    meta_str = f" ({', '.join(meta)})" if meta else ""
    return f"Learning #{result['id']} añadido en {category}: {title}{meta_str}{repetition_msg}"


def handle_learning_search(query: str, category: str = '') -> str:
    """Search learnings by query string, optionally filtered by category."""
    results = search_learnings(query, category if category else None)
    if not results:
        return f"Sin resultados para '{query}'."
    lines = [f"RESULTADOS ({len(results)}):"]
    for r in results:
        snippet = r["content"][:100] + "..." if len(r["content"]) > 100 else r["content"]
        status = r.get("status", "active")
        review_due = r.get("review_due_at")
        review_note = f" | review_due={review_due:.0f}" if isinstance(review_due, (int, float)) and review_due else ""
        lines.append(f"  #{r['id']} [{r['category']}] [{status}] {r['title']}{review_note}")
        lines.append(f"    {snippet}")
        if r.get("prevention"):
            lines.append(f"    Prevención: {r['prevention'][:100]}")

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
                           status: str = '', review_days: int = 0) -> str:
    """Update an existing learning, including review metadata."""
    kwargs = {}
    if title:
        kwargs["title"] = title
    if content:
        kwargs["content"] = content
    if category:
        if category not in VALID_CATEGORIES:
            valid = ", ".join(sorted(VALID_CATEGORIES))
            return f"ERROR: Category '{category}' invalid. Valid: {valid}"
        kwargs["category"] = category
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
        return "ERROR: Nada que actualizar. Proporciona campos nuevos."
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
    if review_days > 0:
        extra_updates["review_due_at"] = now_epoch() + (max(1, int(review_days)) * 86400)
    if extra_updates:
        extra_updates["updated_at"] = now_epoch()
        set_clause = ", ".join(f"{k} = ?" for k in extra_updates)
        values = list(extra_updates.values()) + [id]
        conn = get_db()
        conn.execute(f"UPDATE learnings SET {set_clause} WHERE id = ?", values)
        conn.commit()
    return f"Learning #{id} actualizado."


def handle_learning_delete(id: int) -> str:
    """Delete a learning entry by ID."""
    deleted = delete_learning(id)
    if not deleted:
        return f"ERROR: Learning #{id} not found."
    return f"Learning #{id} eliminado."


def handle_learning_list(category: str = '') -> str:
    """List all learnings, grouped by category if no filter given."""
    results = list_learnings(category if category else None)
    if not results:
        label = category if category else "TODOS"
        return f"LEARNINGS {label} (0): Sin entradas."

    if category:
        label = category.upper()
        lines = [f"LEARNINGS {label} ({len(results)}):"]
        for r in results:
            lines.append(f"  #{r['id']} [{r.get('status','active')}] {r['title']}")
    else:
        lines = [f"LEARNINGS TODOS ({len(results)}):"]
        current_cat = None
        for r in results:
            if r["category"] != current_cat:
                current_cat = r["category"]
                lines.append(f"\n  [{current_cat.upper()}]")
            lines.append(f"    #{r['id']} [{r.get('status','active')}] {r['title']}")

    return "\n".join(lines)
