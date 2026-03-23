"""Guard plugin — Error prevention closed-loop system.

Surfaces relevant learnings at the moment of action, tracks repetitions,
and provides stats on error prevention effectiveness.
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from db import get_db, find_similar_learnings, extract_keywords

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
SCHEMA_CACHE_PATH = str(NEXO_HOME / "schema_cache.json")


def _load_schema_cache() -> dict:
    """Load cached DB schemas from schema_cache.json."""
    try:
        if os.path.exists(SCHEMA_CACHE_PATH):
            with open(SCHEMA_CACHE_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _get_nexo_table_schema(table_name: str) -> str:
    """Get schema for a nexo.db table via PRAGMA."""
    conn = get_db()
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        if rows:
            cols = [f"{r['name']}({r['type']})" for r in rows]
            return ", ".join(cols)
    except Exception:
        pass
    return ""


def _extract_table_names(content: str) -> set:
    """Extract SQL table names from source code."""
    import re
    tables = set()
    patterns = [
        r'(?:FROM|JOIN|INTO|UPDATE)\s+`?(\w+)`?',
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?',
        r'DESCRIBE\s+`?(\w+)`?',
        r'table_info\([\'"]?(\w+)[\'"]?\)',
    ]
    for pat in patterns:
        for m in re.finditer(pat, content, re.IGNORECASE):
            tables.add(m.group(1))
    sql_keywords = {'SELECT', 'WHERE', 'AND', 'OR', 'NOT', 'NULL', 'SET', 'VALUES', 'INTO', 'AS'}
    return {t for t in tables if t.upper() not in sql_keywords}


def handle_guard_check(files: str = "", area: str = "", include_schemas: str = "true") -> str:
    """Check learnings relevant to files/area before editing. Call BEFORE any code change.

    Args:
        files: Comma-separated file paths about to be edited
        area: System area (infrastructure, api, database, backend, etc.)
        include_schemas: Include DB table schemas if files touch database code (true/false)
    """
    conn = get_db()
    include_schemas_bool = include_schemas.lower() in ("true", "1", "yes")
    file_list = [f.strip() for f in files.split(",") if f.strip()] if files else []

    result = {
        "learnings": [],
        "universal_rules": [],
        "schemas": {},
        "area_repetition_rate": 0.0,
        "blocking_rules": [],
    }

    seen_ids = set()

    # 1. By file path — learnings mentioning the file name or parent directory
    for filepath in file_list:
        from pathlib import Path
        p = Path(filepath)
        filename = p.name
        parent_dir = p.parent.name

        rows = conn.execute(
            "SELECT id, category, title, content FROM learnings WHERE INSTR(content, ?) > 0 OR INSTR(content, ?) > 0",
            (filename, parent_dir)
        ).fetchall()
        for r in rows:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                result["learnings"].append({"id": r["id"], "category": r["category"], "rule": r["title"]})

    # 2. By area/category
    if area:
        rows = conn.execute(
            "SELECT id, category, title, content FROM learnings WHERE category = ?",
            (area,)
        ).fetchall()
        for r in rows:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                result["learnings"].append({"id": r["id"], "category": r["category"], "rule": r["title"]})

    # 3. Universal rules (SIEMPRE, NUNCA, ANTES, always, never)
    rows = conn.execute(
        "SELECT id, category, title, content FROM learnings WHERE "
        "content LIKE '%SIEMPRE%' OR content LIKE '%NUNCA%' OR content LIKE '%ANTES%' "
        "OR content LIKE '%always%' OR content LIKE '%never%'"
    ).fetchall()
    for r in rows:
        if r["id"] not in seen_ids:
            result["universal_rules"].append({"id": r["id"], "rule": r["title"]})

    # 4. DB schemas if files contain SQL keywords
    if include_schemas_bool and file_list:
        all_tables = set()
        for filepath in file_list:
            try:
                with open(filepath, 'r', errors='ignore') as f:
                    content = f.read()
                sql_keywords = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE TABLE']
                if any(kw in content.upper() for kw in sql_keywords):
                    all_tables.update(_extract_table_names(content))
            except (FileNotFoundError, PermissionError):
                continue

        cache = _load_schema_cache()
        for table in all_tables:
            schema = _get_nexo_table_schema(table)
            if schema:
                result["schemas"][table] = schema
            elif "cloud_sql" in cache and table in cache["cloud_sql"]:
                result["schemas"][table] = cache["cloud_sql"][table]

    # 5. Check for blocking rules (5+ repetitions)
    for learning in result["learnings"]:
        lid = learning["id"]
        rep_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM error_repetitions WHERE original_learning_id = ?",
            (lid,)
        ).fetchone()["cnt"]
        if rep_count >= 5:
            result["blocking_rules"].append(
                {"id": lid, "rule": learning["rule"], "repetitions": rep_count}
            )

    # 6. Area repetition rate
    if area:
        total_area = conn.execute(
            "SELECT COUNT(*) as cnt FROM learnings WHERE category = ?", (area,)
        ).fetchone()["cnt"]
        reps_area = conn.execute(
            "SELECT COUNT(*) as cnt FROM error_repetitions WHERE area = ?", (area,)
        ).fetchone()["cnt"]
        if total_area > 0:
            result["area_repetition_rate"] = round(reps_area / total_area, 2)

    # 7. Cognitive metacognition — semantic search for related warnings
    cognitive_warnings = []
    trust_note = ""
    try:
        import cognitive
        trust = cognitive.get_trust_score()

        if trust < 40:
            cog_top_k = 6
            cog_min_score = 0.55
            trust_note = f" [RIGOR: PARANOID — trust={trust:.0f}]"
        elif trust > 80:
            cog_top_k = 2
            cog_min_score = 0.75
            trust_note = f" [RIGOR: FLUENT — trust={trust:.0f}]"
        else:
            cog_top_k = 3
            cog_min_score = 0.65

        query_parts = []
        if file_list:
            query_parts.append(f"editing files: {', '.join(file_list[:5])}")
        if area:
            query_parts.append(f"area: {area}")
        if query_parts:
            query_text = ". ".join(query_parts)
            cog_results = cognitive.search(
                query_text, top_k=cog_top_k, min_score=cog_min_score,
                stores="ltm", source_type_filter="learning", rehearse=False
            )
            for r in cog_results:
                cognitive_warnings.append(
                    f"[{r['score']:.2f}]: {r['source_title']} — {r['content'][:200]}"
                )
    except Exception:
        pass  # Cognitive is optional

    # Log the guard check
    conn.execute(
        "INSERT INTO guard_checks (session_id, files, area, learnings_returned, blocking_rules_returned) "
        "VALUES (?, ?, ?, ?, ?)",
        ("", files, area, len(result["learnings"]) + len(result["universal_rules"]),
         len(result["blocking_rules"]))
    )
    conn.commit()

    # Format output
    lines = []
    if result["blocking_rules"]:
        lines.append("BLOCKING RULES (resolve BEFORE writing):")
        for r in result["blocking_rules"]:
            lines.append(f"  #{r['id']} ({r['repetitions']}x repeated): {r['rule']}")
        lines.append("")

    if result["learnings"]:
        lines.append(f"RELEVANT LEARNINGS ({len(result['learnings'])}):")
        for l in result["learnings"][:15]:
            lines.append(f"  #{l['id']} [{l['category']}] {l['rule']}")
        lines.append("")

    if result["universal_rules"]:
        lines.append(f"UNIVERSAL RULES ({len(result['universal_rules'])}):")
        for r in result["universal_rules"][:10]:
            lines.append(f"  #{r['id']} {r['rule']}")
        lines.append("")

    if result["schemas"]:
        lines.append("DB SCHEMAS:")
        for table, schema in result["schemas"].items():
            lines.append(f"  {table}: {schema}")
        lines.append("")

    if result["area_repetition_rate"] > 0:
        lines.append(f"Area repetition rate: {result['area_repetition_rate']:.0%}")

    if cognitive_warnings:
        lines.append(f"\nCOGNITIVE SEMANTIC MATCHES{trust_note}:")
        for w in cognitive_warnings:
            lines.append(f"  COGNITIVE MATCH {w}")

    if not lines:
        return "No relevant learnings found for these files/area."

    return "\n".join(lines)


def handle_guard_stats(period_days: int = 7) -> str:
    """Get guard system statistics for the specified period.

    Args:
        period_days: Number of days to look back (default 7)
    """
    conn = get_db()
    cutoff = (datetime.now() - timedelta(days=period_days)).strftime("%Y-%m-%d %H:%M:%S")

    total_learnings = conn.execute("SELECT COUNT(*) as cnt FROM learnings").fetchone()["cnt"]

    total_reps = conn.execute(
        "SELECT COUNT(*) as cnt FROM error_repetitions WHERE created_at > ?", (cutoff,)
    ).fetchone()["cnt"]

    new_learnings_period = conn.execute(
        "SELECT COUNT(*) as cnt FROM learnings WHERE created_at > ?",
        ((datetime.now() - timedelta(days=period_days)).timestamp(),)
    ).fetchone()["cnt"]
    rep_rate = round(total_reps / new_learnings_period, 2) if new_learnings_period > 0 else 0.0

    prev_cutoff = (datetime.now() - timedelta(days=period_days * 2)).strftime("%Y-%m-%d %H:%M:%S")
    prev_reps = conn.execute(
        "SELECT COUNT(*) as cnt FROM error_repetitions WHERE created_at > ? AND created_at <= ?",
        (prev_cutoff, cutoff)
    ).fetchone()["cnt"]
    trend = "stable"
    if total_reps < prev_reps:
        trend = "improving"
    elif total_reps > prev_reps:
        trend = "worsening"

    area_rows = conn.execute(
        "SELECT area, COUNT(*) as cnt FROM error_repetitions WHERE created_at > ? GROUP BY area ORDER BY cnt DESC LIMIT 5",
        (cutoff,)
    ).fetchall()

    ignored_rows = conn.execute(
        "SELECT original_learning_id, COUNT(*) as cnt FROM error_repetitions "
        "GROUP BY original_learning_id ORDER BY cnt DESC LIMIT 5"
    ).fetchall()
    most_ignored = []
    for r in ignored_rows:
        lr = conn.execute("SELECT title FROM learnings WHERE id = ?", (r["original_learning_id"],)).fetchone()
        if lr:
            most_ignored.append({"id": r["original_learning_id"], "title": lr["title"], "times_repeated": r["cnt"]})

    checks_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM guard_checks WHERE created_at > ?", (cutoff,)
    ).fetchone()["cnt"]

    lines = [
        f"GUARD STATS (last {period_days} days):",
        f"  Repetition rate: {rep_rate:.0%} ({trend})",
        f"  Total learnings: {total_learnings}",
        f"  Repetitions in period: {total_reps}",
        f"  Guard checks performed: {checks_count}",
    ]

    if area_rows:
        lines.append("  Top areas:")
        for r in area_rows:
            lines.append(f"    {r['area']}: {r['cnt']} repetitions")

    if most_ignored:
        lines.append("  Most repeated learnings:")
        for m in most_ignored:
            lines.append(f"    #{m['id']} ({m['times_repeated']}x): {m['title'][:60]}")

    return "\n".join(lines)


def handle_guard_log_repetition(new_learning_id: int, original_learning_id: int, similarity: float = 0.75) -> str:
    """Log that a new learning is similar to an existing one (repetition detected).

    Args:
        new_learning_id: ID of the new learning
        original_learning_id: ID of the original learning it matches
        similarity: Similarity score (0-1)
    """
    conn = get_db()

    row = conn.execute("SELECT category FROM learnings WHERE id = ?", (new_learning_id,)).fetchone()
    if not row:
        return f"ERROR: Learning #{new_learning_id} not found."
    area = row["category"]

    conn.execute(
        "INSERT INTO error_repetitions (new_learning_id, original_learning_id, similarity, area) VALUES (?,?,?,?)",
        (new_learning_id, original_learning_id, similarity, area)
    )
    conn.commit()

    return f"Repetition logged: #{new_learning_id} similar to #{original_learning_id} ({similarity:.0%})"


TOOLS = [
    (handle_guard_check, "nexo_guard_check", "Check learnings relevant to files/area BEFORE editing code. Call this before any code change."),
    (handle_guard_stats, "nexo_guard_stats", "Get guard system statistics: repetition rate, trends, top problem areas"),
    (handle_guard_log_repetition, "nexo_guard_log_repetition", "Log a learning repetition (new learning matches existing one)"),
]
