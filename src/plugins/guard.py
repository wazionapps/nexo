"""Guard plugin — Error prevention closed-loop system.

Surfaces relevant learnings at the moment of action, tracks repetitions,
and provides stats on error prevention effectiveness.
"""
import json
import os
from datetime import datetime, timedelta
from db import get_db, find_similar_learnings, extract_keywords, search_learnings, search_changes



def _load_schema_cache() -> dict:
    """Load cached DB schemas from schema_cache.json."""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "schema_cache.json")
        if os.path.exists(path):
            with open(path) as f:
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
    # Match FROM/JOIN/INTO/UPDATE/TABLE patterns
    patterns = [
        r'(?:FROM|JOIN|INTO|UPDATE)\s+`?(\w+)`?',
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?',
        r'DESCRIBE\s+`?(\w+)`?',
        r'table_info\([\'\"]?(\w+)[\'\"]?\)',
    ]
    for pat in patterns:
        for m in re.finditer(pat, content, re.IGNORECASE):
            tables.add(m.group(1))
    # Filter out SQL keywords that might match
    sql_keywords = {'SELECT', 'WHERE', 'AND', 'OR', 'NOT', 'NULL', 'SET', 'VALUES', 'INTO', 'AS'}
    return {t for t in tables if t.upper() not in sql_keywords}


def handle_guard_check(files: str = "", area: str = "", include_schemas: str = "true") -> str:
    """Check learnings relevant to files/area before editing. Call BEFORE any code change.

    Args:
        files: Comma-separated file paths about to be edited
        area: System area (my-project, shopify, infrastructure, nexo-ops, etc.)
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
            seen_ids.add(r["id"])
            result["universal_rules"].append({"id": r["id"], "rule": r["title"], "category": r["category"]})

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
            # Try nexo.db first
            schema = _get_nexo_table_schema(table)
            if schema:
                result["schemas"][table] = schema
            elif "cloud_sql" in cache and table in cache["cloud_sql"]:
                result["schemas"][table] = cache["cloud_sql"][table]

    # 5. Check for blocking rules — two paths:
    #    (a) 5+ repetitions (existing behavior)
    #    (b) Learning contains NUNCA/NEVER/PROHIBIDO and matches semantically (aggressive mode)
    import re
    BLOCKING_KEYWORDS = re.compile(
        r'\bNUNCA\b|\bNEVER\b|\bPROHIBIDO\b|\bNO\s+\w+\b|\bFORBIDDEN\b|\bBLOCKING\b|\bSIEMPRE\b|\bALWAYS\b',
        re.IGNORECASE
    )
    # Check both learnings and universal_rules for blocking
    all_candidates = [(l, "learning") for l in result["learnings"]] + \
                     [(u, "universal") for u in result["universal_rules"]]
    blocking_seen = set()
    for learning, source in all_candidates:
        lid = learning["id"]
        if lid in blocking_seen:
            continue
        rep_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM error_repetitions WHERE original_learning_id = ?",
            (lid,)
        ).fetchone()["cnt"]

        # Path (a): 5+ repetitions
        if rep_count >= 5:
            blocking_seen.add(lid)
            result["blocking_rules"].append({
                "id": lid, "rule": learning["rule"], "repetitions": rep_count,
                "reason": "repeated_error"
            })
            continue

        # Path (b): Aggressive — learning TITLE contains prohibition keywords
        if BLOCKING_KEYWORDS.search(learning["rule"]):
            blocking_seen.add(lid)
            result["blocking_rules"].append({
                "id": lid, "rule": learning["rule"], "repetitions": rep_count,
                "reason": "prohibition_keyword"
            })

    # 5b. Behavioral rules — when called without files (session-level check)
    if not file_list:
        behavioral = conn.execute(
            """SELECT l.id, l.title, l.category, COUNT(e.id) as violations
               FROM learnings l
               LEFT JOIN error_repetitions e ON e.original_learning_id = l.id
               WHERE l.category = 'nexo-ops' AND l.status = 'active'
               GROUP BY l.id
               ORDER BY violations DESC, l.created_at DESC
               LIMIT 5"""
        ).fetchall()
        if behavioral:
            result["behavioral_rules"] = [
                {"id": r["id"], "rule": r["title"], "violations": r["violations"]}
                for r in behavioral
            ]

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
    #    Trust score modulates rigor: <40 = paranoid mode (more results, lower threshold)
    cognitive_warnings = []
    trust_note = ""
    try:
        import cognitive
        trust = cognitive.get_trust_score()

        # Rigor modulation based on trust
        if trust < 40:
            cog_top_k = 6       # More results
            cog_min_score = 0.55  # Lower threshold = catch more
            trust_note = f" [RIGOR: PARANOID — trust={trust:.0f}]"
        elif trust > 80:
            cog_top_k = 2       # Fewer results
            cog_min_score = 0.75  # Higher threshold = only strong matches
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

    # 8. Somatic markers — risk score per file/area
    somatic_risk = 0.0
    somatic_details = {}
    try:
        import cognitive
        risk_result = cognitive.somatic_get_risk(file_list, area)
        somatic_risk = risk_result["max_risk"]
        somatic_details = risk_result["scores"]
        # Validated recovery: if no learnings found, guard check is "clean"
        if not result["learnings"]:
            for fp in file_list:
                cognitive.somatic_guard_decay(fp, "file")
    except Exception:
        pass

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
            reason = r.get("reason", "repeated_error")
            if reason == "prohibition_keyword":
                lines.append(f"  #{r['id']} [PROHIBIT]: {r['rule']}")
            else:
                lines.append(f"  #{r['id']} ({r['repetitions']}x repeated): {r['rule']}")
        lines.append("")

    if result["learnings"]:
        lines.append(f"RELEVANT LEARNINGS ({len(result['learnings'])}):")
        for l in result["learnings"][:15]:
            lines.append(f"  #{l['id']} [{l['category']}] {l['rule']}")
        lines.append("")

    if result.get("behavioral_rules"):
        lines.append("SESSION BEHAVIORAL RULES (top 5 most-violated):")
        for r in result["behavioral_rules"]:
            v = f" ({r['violations']}x violated)" if r["violations"] > 0 else ""
            lines.append(f"  #{r['id']} {r['rule']}{v}")
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

    if somatic_risk > 0:
        if somatic_risk > 0.8:
            lines.insert(0, "CRITICAL RISK (score {:.2f}) — suggest code review before editing".format(somatic_risk))
        elif somatic_risk > 0.5:
            lines.insert(0, "HIGH RISK (score {:.2f}) — extra caution recommended".format(somatic_risk))
        else:
            lines.append("\nSomatic risk: {:.2f} (low)".format(somatic_risk))
        if somatic_details:
            lines.append("Risk scores:")
            for target, data in somatic_details.items():
                lines.append("  {}: {:.2f} ({} incidents, last: {})".format(
                    target, data["risk"], data["incidents"], data["last"][:10] if data["last"] else "unknown"))

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

    # Repetition rate
    new_learnings_period = conn.execute(
        "SELECT COUNT(*) as cnt FROM learnings WHERE created_at > ?",
        ((datetime.now() - timedelta(days=period_days)).timestamp(),)
    ).fetchone()["cnt"]
    rep_rate = round(total_reps / new_learnings_period, 2) if new_learnings_period > 0 else 0.0

    # Previous period for trend
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

    # Top areas
    area_rows = conn.execute(
        "SELECT area, COUNT(*) as cnt FROM error_repetitions WHERE created_at > ? GROUP BY area ORDER BY cnt DESC LIMIT 5",
        (cutoff,)
    ).fetchall()

    # Most ignored learnings (most repetitions)
    ignored_rows = conn.execute(
        "SELECT original_learning_id, COUNT(*) as cnt FROM error_repetitions "
        "GROUP BY original_learning_id ORDER BY cnt DESC LIMIT 5"
    ).fetchall()
    most_ignored = []
    for r in ignored_rows:
        lr = conn.execute("SELECT title FROM learnings WHERE id = ?", (r["original_learning_id"],)).fetchone()
        if lr:
            most_ignored.append({"id": r["original_learning_id"], "title": lr["title"], "times_repeated": r["cnt"]})

    # Guard checks performed
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

    # Get the area from the new learning
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


def handle_somatic_check(files: str = "", area: str = "") -> str:
    """View somatic risk scores for specific files and/or area.
    Args:
        files: Comma-separated file paths to check
        area: System area to check
    """
    try:
        import cognitive
        file_list = [f.strip() for f in files.split(",") if f.strip()] if files else []
        result = cognitive.somatic_get_risk(file_list, area)
        if not result["scores"]:
            return "No somatic markers found for these targets."
        lines = ["Max risk: {:.2f}".format(result["max_risk"]), ""]
        for target, data in result["scores"].items():
            level = "CRITICAL" if data["risk"] > 0.8 else "HIGH" if data["risk"] > 0.5 else "Low"
            lines.append("  {} {}: {:.2f} ({} incidents, last: {})".format(
                level, target, data["risk"], data["incidents"], data["last"][:10] if data["last"] else "unknown"))
        return "\n".join(lines)
    except Exception as e:
        return "Error: {}".format(e)


def handle_somatic_stats() -> str:
    """View top 10 riskiest files/areas and system-wide risk distribution."""
    try:
        import cognitive
        top = cognitive.somatic_top_risks(limit=10)
        if not top:
            return "No somatic markers recorded yet."
        lines = ["TOP RISK TARGETS:", ""]
        for r in top:
            level = "CRIT" if r["risk_score"] > 0.8 else "HIGH" if r["risk_score"] > 0.5 else "low"
            lines.append("  [{}] [{}] {}: {:.2f} ({} incidents)".format(
                level, r["target_type"], r["target"], r["risk_score"], r["incident_count"]))
        db = cognitive._get_db()
        total = db.execute("SELECT COUNT(*) FROM somatic_markers WHERE risk_score > 0").fetchone()[0]
        high = db.execute("SELECT COUNT(*) FROM somatic_markers WHERE risk_score > 0.5").fetchone()[0]
        critical = db.execute("SELECT COUNT(*) FROM somatic_markers WHERE risk_score > 0.8").fetchone()[0]
        lines.extend(["", "Distribution: {} tracked | {} high risk | {} critical".format(total, high, critical)])
        return "\n".join(lines)
    except Exception as e:
        return "Error: {}".format(e)


def handle_guard_cross_check(findings: list, area: str = "") -> str:
    """Cross-check audit findings against known learnings to filter false positives.

    Args:
        findings: List of audit finding strings to cross-check
        area: System area to narrow the learning search (my-project, shopify, etc.)
    """
    # Common English/Spanish stopwords to skip during keyword extraction
    STOPWORDS = {
        "the", "a", "an", "is", "in", "on", "at", "to", "of", "and", "or", "but",
        "for", "with", "that", "this", "it", "as", "are", "was", "be", "by", "not",
        "has", "have", "from", "which", "when", "if", "then", "do", "does", "can",
        "el", "la", "los", "las", "un", "una", "en", "de", "del", "al", "y", "o",
        "que", "se", "no", "es", "por", "con", "su", "pero", "como", "para",
        "este", "esta", "esto", "son", "hay", "más", "ya",
    }

    new_issues = []
    known_issues = []

    for finding in findings:
        if not finding or not finding.strip():
            continue

        # Extract significant keywords from the finding text
        words = finding.lower().split()
        keywords = [
            w.strip(".,;:!?\"'()[]{}") for w in words
            if len(w) >= 4 and w.lower() not in STOPWORDS
        ]
        # Use up to 5 most distinctive keywords to build the search query
        query_keywords = keywords[:5]

        matched_learnings = []
        if query_keywords:
            query = " ".join(query_keywords)
            try:
                results = search_learnings(query, category=area if area else None)
                if not results and area:
                    # Retry without category filter if area-filtered search returns nothing
                    results = search_learnings(query)
                matched_learnings = results[:3]  # Top 3 matches per finding
            except Exception:
                pass

        if matched_learnings:
            refs = [
                {"id": r["id"], "title": r["title"], "category": r.get("category", "")}
                for r in matched_learnings
            ]
            known_issues.append({
                "finding": finding,
                "status": "known",
                "learning_refs": refs,
            })
        else:
            new_issues.append({
                "finding": finding,
                "status": "new",
            })

    # Build output
    lines = [
        f"CROSS-CHECK RESULTS: {len(findings)} findings — "
        f"{len(new_issues)} new, {len(known_issues)} already documented",
        "",
    ]

    if new_issues:
        lines.append(f"NEW ISSUES ({len(new_issues)}) — not in learnings, investigate:")
        for i, item in enumerate(new_issues, 1):
            lines.append(f"  {i}. {item['finding']}")
        lines.append("")

    if known_issues:
        lines.append(f"KNOWN ISSUES ({len(known_issues)}) — covered by existing learnings:")
        for i, item in enumerate(known_issues, 1):
            refs_str = ", ".join(
                f"#{r['id']} [{r['category']}] {r['title'][:60]}"
                for r in item["learning_refs"]
            )
            lines.append(f"  {i}. {item['finding']}")
            lines.append(f"     -> {refs_str}")
        lines.append("")

    summary = {
        "total": len(findings),
        "new_count": len(new_issues),
        "known_count": len(known_issues),
        "new_issues": [i["finding"] for i in new_issues],
        "known_issues": [
            {"finding": i["finding"], "refs": i["learning_refs"]}
            for i in known_issues
        ],
    }
    lines.append(f"SUMMARY JSON: {json.dumps(summary)}")

    return "\n".join(lines)


def handle_guard_file_check(files: list) -> str:
    """Pre-edit check: surfaces learnings and recent changes for files about to be modified.

    Args:
        files: List of file paths about to be edited
    """
    from pathlib import Path
    import re

    BLOCKING_KEYWORDS = re.compile(
        r'\bNUNCA\b|\bNEVER\b|\bPROHIBIDO\b|\bFORBIDDEN\b|\bBLOCKING\b',
        re.IGNORECASE
    )

    if not files:
        return "ERROR: No files provided."

    file_learnings: dict = {}
    recent_changes: dict = {}
    warnings: list = []
    seen_learning_ids: set = set()

    for filepath in files:
        p = Path(filepath)
        filename = p.name
        parent_dir = p.parent.name
        stem = p.stem  # filename without extension

        # Build search keywords: filename, stem, parent directory (deduplicated)
        keywords = [kw for kw in [filename, stem, parent_dir] if kw and kw not in (".", "")]
        seen_kw: set = set()
        unique_keywords = []
        for kw in keywords:
            if kw not in seen_kw:
                seen_kw.add(kw)
                unique_keywords.append(kw)

        file_results = []
        file_seen_ids: set = set()

        for keyword in unique_keywords:
            try:
                rows = search_learnings(keyword)
                for r in rows:
                    lid = r.get("id")
                    if lid and lid not in seen_learning_ids and lid not in file_seen_ids:
                        file_seen_ids.add(lid)
                        seen_learning_ids.add(lid)
                        entry = {
                            "id": lid,
                            "category": r.get("category", ""),
                            "title": r.get("title", ""),
                            "content": (r.get("content") or "")[:300],
                        }
                        file_results.append(entry)
                        # Flag blocking learnings
                        if BLOCKING_KEYWORDS.search(r.get("title", "")) or \
                                BLOCKING_KEYWORDS.search(r.get("content") or ""):
                            warnings.append(
                                f"[BLOCKING] #{lid} ({filepath}): {r.get('title', '')}"
                            )
            except Exception:
                pass

        file_learnings[filepath] = file_results

        # Search recent changes (last 7 days) for this file by filename/stem
        file_changes = []
        for keyword in unique_keywords[:2]:  # filename + stem are most specific
            try:
                changes = search_changes(files=keyword, days=7)
                for c in changes:
                    cid = c.get("id")
                    if cid and not any(fc.get("id") == cid for fc in file_changes):
                        file_changes.append({
                            "id": cid,
                            "files": c.get("files", ""),
                            "what_changed": (c.get("what_changed") or "")[:200],
                            "why": (c.get("why") or "")[:150],
                            "created_at": (c.get("created_at") or "")[:16],
                        })
            except Exception:
                pass

        recent_changes[filepath] = file_changes

    # Build summary line
    total_learnings = sum(len(v) for v in file_learnings.values())
    total_changes = sum(len(v) for v in recent_changes.values())
    summary_parts = []
    if total_learnings:
        summary_parts.append(f"{total_learnings} learning(s) found")
    if total_changes:
        summary_parts.append(f"{total_changes} recent change(s) in last 7 days")
    if warnings:
        summary_parts.append(f"{len(warnings)} BLOCKING warning(s)")
    summary = ", ".join(summary_parts) if summary_parts else "No relevant learnings or recent changes found."

    # Format output
    lines = []

    if warnings:
        lines.append("WARNINGS — resolve before editing:")
        for w in warnings:
            lines.append(f"  {w}")
        lines.append("")

    for filepath in files:
        learnings = file_learnings.get(filepath, [])
        changes = recent_changes.get(filepath, [])
        if not learnings and not changes:
            continue
        lines.append(f"FILE: {filepath}")
        if learnings:
            lines.append(f"  Learnings ({len(learnings)}):")
            for entry in learnings[:10]:
                lines.append(f"    #{entry['id']} [{entry['category']}] {entry['title']}")
                if entry["content"]:
                    lines.append(f"      {entry['content'][:120]}")
        if changes:
            lines.append(f"  Recent changes ({len(changes)}, last 7d):")
            for c in changes[:5]:
                lines.append(f"    [{c['created_at']}] {c['what_changed'][:100]}")
                if c["why"]:
                    lines.append(f"      Why: {c['why'][:80]}")
        lines.append("")

    lines.append(f"SUMMARY: {summary}")

    return "\n".join(lines) if lines else summary


TOOLS = [
    (handle_guard_check, "nexo_guard_check", "Check learnings relevant to files/area BEFORE editing code. Call this before any code change."),
    (handle_guard_stats, "nexo_guard_stats", "Get guard system statistics: repetition rate, trends, top problem areas"),
    (handle_guard_log_repetition, "nexo_guard_log_repetition", "Log a learning repetition (new learning matches existing one)"),
    (handle_somatic_check, "nexo_somatic_check", "View somatic risk scores for files/areas — pain memory"),
    (handle_somatic_stats, "nexo_somatic_stats", "Top 10 riskiest targets + risk distribution"),
    (handle_guard_cross_check, "nexo_guard_cross_check", "Cross-check audit findings against known learnings to filter false positives"),
    (handle_guard_file_check, "nexo_guard_file_check", "Pre-edit check: surfaces learnings and recent changes for files about to be modified"),
]
