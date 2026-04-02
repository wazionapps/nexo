from __future__ import annotations
"""NEXO DB — Learnings module."""
import re, time
from db._core import get_db, now_epoch
from db._fts import fts_upsert, fts_search

# ── Learnings ──────────────────────────────────────────────────────

def create_learning(
    category: str,
    title: str,
    content: str,
    reasoning: str = '',
    prevention: str = '',
    applies_to: str = '',
    status: str = 'active',
    review_due_at: float | None = None,
    last_reviewed_at: float | None = None,
) -> dict:
    """Create a new learning entry with optional reasoning."""
    conn = get_db()
    now = now_epoch()
    cursor = conn.execute(
        "INSERT INTO learnings "
        "(category, title, content, reasoning, prevention, applies_to, status, review_due_at, last_reviewed_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            category, title, content, reasoning, prevention, applies_to,
            status, review_due_at, last_reviewed_at, now, now,
        )
    )
    conn.commit()
    lid = cursor.lastrowid
    fts_upsert("learning", str(lid), title, f"{content} {reasoning or ''}", category, commit=False)
    row = conn.execute("SELECT * FROM learnings WHERE id = ?", (lid,)).fetchone()
    return dict(row)


def update_learning(id: int, **kwargs) -> dict:
    """Update any fields of a learning: category, title, content, reasoning."""
    conn = get_db()
    row = conn.execute("SELECT * FROM learnings WHERE id = ?", (id,)).fetchone()
    if not row:
            return {"error": f"Learning {id} not found"}
    allowed = {
        "category", "title", "content", "reasoning", "prevention",
        "applies_to", "status", "review_due_at", "last_reviewed_at",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
            return dict(row)
    updates["updated_at"] = now_epoch()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [id]
    conn.execute(f"UPDATE learnings SET {set_clause} WHERE id = ?", values)
    conn.commit()
    row = conn.execute("SELECT * FROM learnings WHERE id = ?", (id,)).fetchone()
    r = dict(row)
    fts_upsert("learning", str(id), r.get("title", ""), f"{r.get('content', '')} {r.get('reasoning', '')}", r.get("category", ""), commit=False)
    return r


def delete_learning(id: int) -> bool:
    """Delete a learning entry."""
    conn = get_db()
    result = conn.execute("DELETE FROM learnings WHERE id = ?", (id,))
    conn.execute("DELETE FROM unified_search WHERE source = 'learning' AND source_id = ?", (str(id),))
    conn.commit()
    deleted = result.rowcount > 0
    return deleted


def search_learnings(query: str, category: str = None) -> list[dict]:
    """Search learnings using FTS5 for ranked results. Falls back to LIKE if FTS fails."""
    # Try FTS5 first
    fts_results = fts_search(query, source_filter="learning", limit=30)
    if fts_results:
        conn = get_db()
        ids = [int(r['source_id']) for r in fts_results]
        placeholders = ','.join('?' * len(ids))
        rows = conn.execute(
            f"SELECT * FROM learnings WHERE id IN ({placeholders}) ORDER BY updated_at DESC",
            ids
        ).fetchall()
        filtered = [dict(r) for r in rows]
        if category:
            filtered = [r for r in filtered if r.get('category') == category]
        return filtered

    # Fallback to LIKE
    conn = get_db()
    words = query.strip().split()
    if not words:
        return []
    conditions = []
    params = []
    for word in words:
        pattern = f"%{word}%"
        conditions.append("(title LIKE ? OR content LIKE ? OR reasoning LIKE ? OR prevention LIKE ?)")
        params.extend([pattern, pattern, pattern, pattern])
    where = " AND ".join(conditions)
    if category:
        where = f"category = ? AND ({where})"
        params.insert(0, category)
    rows = conn.execute(
        f"SELECT * FROM learnings WHERE {where} ORDER BY updated_at DESC",
        params
    ).fetchall()
    return [dict(r) for r in rows]


def list_learnings(category: str = None) -> list[dict]:
    """List all learnings, optionally filtered by category."""
    conn = get_db()
    if category:
        rows = conn.execute(
            "SELECT * FROM learnings WHERE category = ? ORDER BY updated_at DESC",
            (category,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM learnings ORDER BY category ASC, updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from text for similarity matching."""
    import re
    stop = {'the', 'a', 'an', 'is', 'was', 'are', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
            'should', 'may', 'might', 'can', 'shall', 'to', 'of', 'in', 'for',
            'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through', 'during',
            'before', 'after', 'above', 'below', 'between', 'out', 'off', 'over',
            'under', 'again', 'further', 'then', 'once', 'and', 'but', 'or', 'nor',
            'not', 'so', 'yet', 'both', 'either', 'neither', 'each', 'every', 'all',
            'any', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'only',
            'own', 'same', 'than', 'too', 'very', 'just', 'que', 'de', 'en', 'la',
            'el', 'los', 'las', 'un', 'una', 'por', 'con', 'para', 'del', 'al',
            'es', 'se', 'no', 'si', 'como', 'pero', 'su', 'ya', 'esto', 'esta'}
    words = re.findall(r'[a-zA-Z0-9_]+', text.lower())
    return [w for w in words if len(w) > 2 and w not in stop]


def find_similar_learnings(new_id: int, title: str, content: str, category: str) -> list[tuple[int, float]]:
    """Find learnings similar to the given one based on keyword overlap.
    Returns list of (learning_id, similarity_score) tuples for matches > 0.3."""
    keywords_new = set(extract_keywords(f"{title} {content}"))
    if not keywords_new:
        return []
    conn = get_db()
    rows = conn.execute(
        "SELECT id, title, content FROM learnings WHERE category = ? AND id != ?",
        (category, new_id)
    ).fetchall()
    results = []
    for row in rows:
        keywords_existing = set(extract_keywords(f"{row['title']} {row['content']}"))
        if not keywords_existing:
            continue
        overlap = keywords_new & keywords_existing
        union = keywords_new | keywords_existing
        similarity = len(overlap) / len(union) if union else 0
        if similarity > 0.3:
            results.append((row['id'], round(similarity, 2)))
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:5]


