from __future__ import annotations
"""NEXO DB — Skills module.

Skill Auto-Creation system: reusable procedures extracted from complex tasks.
Skills are procedural (step-by-step how-tos) vs learnings which are declarative.

Pipeline: trace → draft → published → archived, fully autonomous.
Trust score with decay controls quality — no human approval gates.

Promotion: draft + 2 successful uses in distinct contexts → published.
Degradation: trust < 20 → archived. Archived + 60 days unused → purge.
"""
import json
import datetime
from db._core import get_db
from db._fts import fts_upsert, fts_search


# ── Constants ──────────────────────────────────────────────────────

VALID_LEVELS = {'trace', 'draft', 'published', 'archived'}
TRUST_ON_SUCCESS = 5
TRUST_ON_FAILURE = -10
TRUST_INITIAL = 50
TRUST_ARCHIVE_THRESHOLD = 20
PROMOTION_USES_REQUIRED = 2


# ── CRUD ───────────────────────────────────────────────────────────

def create_skill(
    skill_id: str,
    name: str,
    description: str = '',
    level: str = 'trace',
    tags: list | str = '[]',
    trigger_patterns: list | str = '[]',
    source_sessions: list | str = '[]',
    linked_learnings: list | str = '[]',
    file_path: str = '',
    trust_score: int = TRUST_INITIAL,
    steps: list | str = '[]',
    gotchas: list | str = '[]',
    content: str = '',
) -> dict:
    """Create a new skill entry.

    Content can be:
    - Markdown with numbered steps (auto-generated from steps/gotchas if empty)
    - A reference to a script file (set file_path)
    - Free-form procedure description
    """
    if level not in VALID_LEVELS:
        return {"error": f"level must be one of: {', '.join(sorted(VALID_LEVELS))}"}

    tags_json = json.dumps(tags) if isinstance(tags, list) else tags
    trigger_json = json.dumps(trigger_patterns) if isinstance(trigger_patterns, list) else trigger_patterns
    sessions_json = json.dumps(source_sessions) if isinstance(source_sessions, list) else source_sessions
    learnings_json = json.dumps(linked_learnings) if isinstance(linked_learnings, list) else linked_learnings
    steps_json = json.dumps(steps) if isinstance(steps, list) else steps
    gotchas_json = json.dumps(gotchas) if isinstance(gotchas, list) else gotchas

    # Auto-generate content from steps/gotchas if not provided
    if not content and steps:
        steps_list = steps if isinstance(steps, list) else json.loads(steps_json)
        gotchas_list = gotchas if isinstance(gotchas, list) else json.loads(gotchas_json)
        lines = [f"# {name}", "", description, "", "## Steps"]
        for i, s in enumerate(steps_list, 1):
            lines.append(f"{i}. {s}")
        if gotchas_list:
            lines.extend(["", "## Gotchas"])
            for g in gotchas_list:
                lines.append(f"- {g}")
        content = "\n".join(lines)

    conn = get_db()
    conn.execute(
        """INSERT INTO skills
           (id, name, description, level, trust_score, file_path, tags,
            trigger_patterns, source_sessions, linked_learnings, content, steps, gotchas)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (skill_id, name, description, level, trust_score, file_path,
         tags_json, trigger_json, sessions_json, learnings_json,
         content, steps_json, gotchas_json),
    )
    conn.commit()

    # FTS index
    body = f"{description} {tags_json} {trigger_json}"
    fts_upsert("skill", skill_id, name, body, "skill", commit=False)

    row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
    return dict(row) if row else {"id": skill_id, "status": "created"}


def get_skill(skill_id: str) -> dict | None:
    """Get a skill by ID."""
    conn = get_db()
    row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
    return dict(row) if row else None


def list_skills(level: str = '', tag: str = '') -> list[dict]:
    """List skills, optionally filtered by level or tag."""
    conn = get_db()
    conditions = []
    params = []

    if level:
        conditions.append("level = ?")
        params.append(level)
    if tag:
        conditions.append("tags LIKE ?")
        params.append(f'%"{tag}"%')

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    rows = conn.execute(
        f"SELECT * FROM skills {where} ORDER BY trust_score DESC, last_used_at DESC",
        tuple(params),
    ).fetchall()
    return [dict(r) for r in rows]


def search_skills(query: str, level: str = '') -> list[dict]:
    """Search skills using FTS5 for ranked results. Falls back to LIKE."""
    fts_results = fts_search(query, source_filter="skill", limit=20)
    if fts_results:
        conn = get_db()
        ids = [r['source_id'] for r in fts_results]
        placeholders = ','.join('?' * len(ids))
        sql = f"SELECT * FROM skills WHERE id IN ({placeholders})"
        params = list(ids)
        if level:
            sql += " AND level = ?"
            params.append(level)
        sql += " ORDER BY trust_score DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # Fallback to LIKE
    conn = get_db()
    words = query.strip().split()
    if not words:
        return []
    conditions = []
    params = []
    for word in words:
        p = f"%{word}%"
        conditions.append("(name LIKE ? OR description LIKE ? OR tags LIKE ? OR trigger_patterns LIKE ?)")
        params.extend([p, p, p, p])
    where = " AND ".join(conditions)
    if level:
        where = f"level = ? AND ({where})"
        params.insert(0, level)
    rows = conn.execute(
        f"SELECT * FROM skills WHERE {where} ORDER BY trust_score DESC",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def update_skill(skill_id: str, **kwargs) -> dict:
    """Update any fields of a skill."""
    conn = get_db()
    row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
    if not row:
        return {"error": f"Skill {skill_id} not found"}

    allowed = {
        "name", "description", "level", "trust_score", "file_path",
        "tags", "trigger_patterns", "source_sessions", "linked_learnings",
    }
    updates = {}
    for k, v in kwargs.items():
        if k in allowed:
            if isinstance(v, (list, dict)):
                updates[k] = json.dumps(v)
            else:
                updates[k] = v

    if not updates:
        return dict(row)

    updates["updated_at"] = datetime.datetime.now().isoformat(timespec='seconds')
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [skill_id]
    conn.execute(f"UPDATE skills SET {set_clause} WHERE id = ?", values)
    conn.commit()

    # Update FTS
    row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
    r = dict(row)
    body = f"{r.get('description', '')} {r.get('tags', '[]')} {r.get('trigger_patterns', '[]')}"
    fts_upsert("skill", skill_id, r.get("name", ""), body, "skill", commit=False)
    return r


def delete_skill(skill_id: str) -> bool:
    """Delete a skill and its usage history."""
    conn = get_db()
    conn.execute("DELETE FROM skill_usage WHERE skill_id = ?", (skill_id,))
    result = conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
    conn.execute("DELETE FROM unified_search WHERE source = 'skill' AND source_id = ?", (skill_id,))
    conn.commit()
    return result.rowcount > 0


# ── Usage tracking & auto-promotion ────────────────────────────────

def record_usage(skill_id: str, session_id: str = '', success: bool = True,
                 context: str = '', notes: str = '') -> dict:
    """Record a skill usage and auto-promote/degrade based on trust rules.

    Returns the updated skill dict with promotion info.
    """
    conn = get_db()
    row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
    if not row:
        return {"error": f"Skill {skill_id} not found"}

    skill = dict(row)

    # Record usage
    conn.execute(
        "INSERT INTO skill_usage (skill_id, session_id, success, context, notes) VALUES (?, ?, ?, ?, ?)",
        (skill_id, session_id, 1 if success else 0, context, notes),
    )

    # Update counters
    delta = TRUST_ON_SUCCESS if success else TRUST_ON_FAILURE
    new_trust = max(0, min(100, skill['trust_score'] + delta))
    count_field = "success_count" if success else "fail_count"

    conn.execute(
        f"""UPDATE skills SET
            use_count = use_count + 1,
            {count_field} = {count_field} + 1,
            trust_score = ?,
            last_used_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?""",
        (new_trust, skill_id),
    )
    conn.commit()

    # Auto-promotion: draft → published if 2+ successful uses in distinct contexts
    promotion = None
    if skill['level'] == 'draft' and success:
        distinct_contexts = conn.execute(
            """SELECT COUNT(DISTINCT context) FROM skill_usage
               WHERE skill_id = ? AND success = 1 AND context != ''""",
            (skill_id,),
        ).fetchone()[0]
        if distinct_contexts >= PROMOTION_USES_REQUIRED:
            conn.execute(
                "UPDATE skills SET level = 'published', updated_at = datetime('now') WHERE id = ?",
                (skill_id,),
            )
            conn.commit()
            promotion = "draft → published"

    # Auto-archive: trust < 20 → archived
    if new_trust < TRUST_ARCHIVE_THRESHOLD and skill['level'] in ('draft', 'published'):
        conn.execute(
            "UPDATE skills SET level = 'archived', updated_at = datetime('now') WHERE id = ?",
            (skill_id,),
        )
        conn.commit()
        promotion = f"{skill['level']} → archived (trust={new_trust})"

    result = dict(conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone())
    if promotion:
        result['_promotion'] = promotion
    return result


def match_skills(task: str, level: str = '', top_n: int = 3) -> list[dict]:
    """Find skills matching a task description.

    Search strategy:
    1. FTS5 on skill name/description/tags
    2. Trigger pattern matching
    3. Keyword overlap

    Returns top-N matches sorted by relevance × trust.
    """
    if not task or not task.strip():
        return []

    conn = get_db()
    seen = set()
    results = []

    # Level filter
    level_filter = "AND level = ?" if level else "AND level IN ('draft', 'published')"
    level_params = (level,) if level else ()

    # Strategy 1: FTS5 search
    fts_results = fts_search(task, source_filter="skill", limit=10)
    if fts_results:
        ids = [r['source_id'] for r in fts_results]
        placeholders = ','.join('?' * len(ids))
        rows = conn.execute(
            f"SELECT * FROM skills WHERE id IN ({placeholders}) {level_filter} ORDER BY trust_score DESC",
            tuple(ids) + level_params,
        ).fetchall()
        for r in rows:
            d = dict(r)
            d['_match'] = 'fts'
            if d['id'] not in seen:
                seen.add(d['id'])
                results.append(d)

    # Strategy 2: Trigger pattern matching
    task_lower = task.lower()
    rows = conn.execute(
        f"SELECT * FROM skills WHERE trigger_patterns != '[]' {level_filter}",
        level_params,
    ).fetchall()
    for r in rows:
        if r['id'] in seen:
            continue
        try:
            patterns = json.loads(r['trigger_patterns'])
            for pattern in patterns:
                if pattern.lower() in task_lower or task_lower in pattern.lower():
                    d = dict(r)
                    d['_match'] = f'trigger:{pattern}'
                    seen.add(d['id'])
                    results.append(d)
                    break
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy 3: Tag keyword overlap
    task_words = set(task_lower.split())
    rows = conn.execute(
        f"SELECT * FROM skills WHERE tags != '[]' {level_filter}",
        level_params,
    ).fetchall()
    for r in rows:
        if r['id'] in seen:
            continue
        try:
            tags = json.loads(r['tags'])
            tag_words = set(t.lower() for t in tags)
            overlap = task_words & tag_words
            if overlap:
                d = dict(r)
                d['_match'] = f'tags:{",".join(overlap)}'
                seen.add(d['id'])
                results.append(d)
        except (json.JSONDecodeError, TypeError):
            pass

    # Sort by trust_score descending, then return top N
    results.sort(key=lambda x: x.get('trust_score', 0), reverse=True)
    return results[:top_n]


def merge_skills(id1: str, id2: str, keep_id: str = '') -> dict:
    """Merge two similar skills into one. The survivor gets combined metadata.

    Args:
        id1: First skill ID
        id2: Second skill ID
        keep_id: Which one to keep (default: higher trust). The other is deleted.
    """
    conn = get_db()
    s1 = conn.execute("SELECT * FROM skills WHERE id = ?", (id1,)).fetchone()
    s2 = conn.execute("SELECT * FROM skills WHERE id = ?", (id2,)).fetchone()
    if not s1:
        return {"error": f"Skill {id1} not found"}
    if not s2:
        return {"error": f"Skill {id2} not found"}

    s1, s2 = dict(s1), dict(s2)

    # Decide which to keep
    if not keep_id:
        keep_id = id1 if s1['trust_score'] >= s2['trust_score'] else id2
    survivor = s1 if keep_id == id1 else s2
    donor = s2 if keep_id == id1 else s1
    donor_id = donor['id']

    # Merge tags
    try:
        tags1 = set(json.loads(survivor.get('tags', '[]')))
        tags2 = set(json.loads(donor.get('tags', '[]')))
        merged_tags = json.dumps(sorted(tags1 | tags2))
    except (json.JSONDecodeError, TypeError):
        merged_tags = survivor.get('tags', '[]')

    # Merge trigger patterns
    try:
        tp1 = set(json.loads(survivor.get('trigger_patterns', '[]')))
        tp2 = set(json.loads(donor.get('trigger_patterns', '[]')))
        merged_tp = json.dumps(sorted(tp1 | tp2))
    except (json.JSONDecodeError, TypeError):
        merged_tp = survivor.get('trigger_patterns', '[]')

    # Merge source sessions
    try:
        ss1 = set(json.loads(survivor.get('source_sessions', '[]')))
        ss2 = set(json.loads(donor.get('source_sessions', '[]')))
        merged_ss = json.dumps(sorted(ss1 | ss2, key=str))
    except (json.JSONDecodeError, TypeError):
        merged_ss = survivor.get('source_sessions', '[]')

    # Merge linked learnings
    try:
        ll1 = set(json.loads(survivor.get('linked_learnings', '[]')))
        ll2 = set(json.loads(donor.get('linked_learnings', '[]')))
        merged_ll = json.dumps(sorted(ll1 | ll2, key=str))
    except (json.JSONDecodeError, TypeError):
        merged_ll = survivor.get('linked_learnings', '[]')

    # Merge counters
    merged_use = survivor['use_count'] + donor['use_count']
    merged_success = survivor['success_count'] + donor['success_count']
    merged_fail = survivor['fail_count'] + donor['fail_count']
    merged_trust = max(survivor['trust_score'], donor['trust_score'])

    # Update survivor
    conn.execute(
        """UPDATE skills SET
            tags = ?, trigger_patterns = ?, source_sessions = ?, linked_learnings = ?,
            use_count = ?, success_count = ?, fail_count = ?, trust_score = ?,
            updated_at = datetime('now')
        WHERE id = ?""",
        (merged_tags, merged_tp, merged_ss, merged_ll,
         merged_use, merged_success, merged_fail, merged_trust, keep_id),
    )

    # Move usage records from donor to survivor
    conn.execute("UPDATE skill_usage SET skill_id = ? WHERE skill_id = ?", (keep_id, donor_id))

    # Delete donor
    conn.execute("DELETE FROM skills WHERE id = ?", (donor_id,))
    conn.execute("DELETE FROM unified_search WHERE source = 'skill' AND source_id = ?", (donor_id,))
    conn.commit()

    result = dict(conn.execute("SELECT * FROM skills WHERE id = ?", (keep_id,)).fetchone())
    result['_merged_from'] = donor_id
    return result


def get_skill_stats() -> dict:
    """Get aggregate skill statistics."""
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
    by_level = {}
    for row in conn.execute("SELECT level, COUNT(*) as cnt FROM skills GROUP BY level").fetchall():
        by_level[row['level']] = row['cnt']

    avg_trust = conn.execute(
        "SELECT AVG(trust_score) FROM skills WHERE level != 'archived'"
    ).fetchone()[0] or 0

    total_uses = conn.execute("SELECT COUNT(*) FROM skill_usage").fetchone()[0]
    success_rate = 0
    if total_uses > 0:
        successes = conn.execute("SELECT COUNT(*) FROM skill_usage WHERE success = 1").fetchone()[0]
        success_rate = round(successes / total_uses * 100, 1)

    recent_uses = conn.execute(
        "SELECT COUNT(*) FROM skill_usage WHERE created_at >= datetime('now', '-7 days')"
    ).fetchone()[0]

    return {
        "total": total,
        "by_level": by_level,
        "avg_trust": round(avg_trust, 1),
        "total_uses": total_uses,
        "success_rate": success_rate,
        "uses_last_7d": recent_uses,
    }


def decay_unused_skills(dry_run: bool = False) -> dict:
    """Decay and purge unused skills. Called by immune.py or maintenance cron.

    Rules:
    - draft: no use in 30 days → trust = 0 → archived
    - published: no use in 90 days → trust -= 5
    - archived: no use in 60 days → purge (delete)
    """
    conn = get_db()
    actions = {"decayed": [], "archived": [], "purged": []}

    # Draft: 30 days no use → archive
    rows = conn.execute("""
        SELECT * FROM skills WHERE level = 'draft'
        AND (last_used_at IS NULL OR last_used_at < datetime('now', '-30 days'))
        AND created_at < datetime('now', '-30 days')
    """).fetchall()
    for r in rows:
        if not dry_run:
            conn.execute(
                "UPDATE skills SET level = 'archived', trust_score = 0, updated_at = datetime('now') WHERE id = ?",
                (r['id'],),
            )
        actions["archived"].append(r['id'])

    # Published: 90 days no use → trust -= 5
    rows = conn.execute("""
        SELECT * FROM skills WHERE level = 'published'
        AND (last_used_at IS NULL OR last_used_at < datetime('now', '-90 days'))
    """).fetchall()
    for r in rows:
        new_trust = max(0, r['trust_score'] - 5)
        if not dry_run:
            conn.execute(
                "UPDATE skills SET trust_score = ?, updated_at = datetime('now') WHERE id = ?",
                (new_trust, r['id']),
            )
            if new_trust < TRUST_ARCHIVE_THRESHOLD:
                conn.execute(
                    "UPDATE skills SET level = 'archived', updated_at = datetime('now') WHERE id = ?",
                    (r['id'],),
                )
                actions["archived"].append(r['id'])
        actions["decayed"].append({"id": r['id'], "trust": f"{r['trust_score']} → {new_trust}"})

    # Archived: 60 days → purge
    rows = conn.execute("""
        SELECT * FROM skills WHERE level = 'archived'
        AND (last_used_at IS NULL OR last_used_at < datetime('now', '-60 days'))
        AND updated_at < datetime('now', '-60 days')
    """).fetchall()
    for r in rows:
        if not dry_run:
            conn.execute("DELETE FROM skill_usage WHERE skill_id = ?", (r['id'],))
            conn.execute("DELETE FROM skills WHERE id = ?", (r['id'],))
            conn.execute("DELETE FROM unified_search WHERE source = 'skill' AND source_id = ?", (r['id'],))
        actions["purged"].append(r['id'])

    if not dry_run:
        conn.commit()
    return actions
