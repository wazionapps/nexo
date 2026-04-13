"""Artifact Registry plugin — structured index of things NEXO creates/deploys.

Solves 'recent work amnesia': NEXO builds services, dashboards, scripts, APIs
but can't find them hours later because semantic search ('backend') doesn't
match operational terms ('FastAPI localhost:6174').

Architecture (from 3-way AI debate — GPT-5.4 + Gemini 3.1 Pro + Claude Opus 4.6):
1. Structured SQLite table with aliases, ports, paths, run commands
2. Retrieval ladder: exact alias → port/path match → fuzzy token → semantic fallback
3. User-language alias learning: when the user says 'backend' and it resolves
   to dashboard:6174, store that mapping for O(1) next time
4. Temporal filtering: 'last night' → hard SQL constraint before any search
"""

import json
import datetime
from db import get_db


# Valid artifact kinds
VALID_KINDS = {
    'service', 'dashboard', 'script', 'api', 'cron', 'website',
    'database', 'repo', 'config', 'tool', 'plugin', 'other',
}

VALID_STATES = {'active', 'inactive', 'broken', 'archived'}


def _cognitive_ingest_safe(content, source_type, source_id="", source_title="", domain=""):
    """Ingest to cognitive STM. Silently fails if cognitive engine unavailable."""
    try:
        import cognitive
        cognitive.ingest(content, source_type, source_id, source_title, domain)
    except Exception:
        pass


def handle_artifact_create(
    kind: str,
    canonical_name: str,
    aliases: str = '[]',
    description: str = '',
    uri: str = '',
    ports: str = '[]',
    paths: str = '[]',
    run_cmd: str = '',
    repo: str = '',
    domain: str = '',
    session_id: str = '',
    metadata: str = '{}',
) -> str:
    """Register a new artifact (service, dashboard, script, API, etc.).

    Call this whenever NEXO creates, deploys, or discovers a runnable/accessible artifact.

    Args:
        kind: Type — service, dashboard, script, api, cron, website, database, repo, config, tool, plugin, other
        canonical_name: Primary name (e.g., 'NEXO Brain Dashboard')
        aliases: JSON array of alternative names users might use (e.g., '["backend", "dashboard", "nexo web"]')
        description: What it does (1-2 sentences)
        uri: Access URL or address (e.g., 'localhost:6174', 'nexo-brain.com')
        ports: JSON array of ports (e.g., '[6174]')
        paths: JSON array of file paths (e.g., '["/Users/x/nexo/src/dashboard/app.py"]')
        run_cmd: Command to start/open it (e.g., 'python3 -m dashboard.app --port 6174')
        repo: Repository path or URL
        domain: Project domain (nexo, my-project, project-a, project-b, etc.)
        session_id: Current session ID
        metadata: JSON object with extra key-value pairs
    """
    if kind not in VALID_KINDS:
        return f"ERROR: kind must be one of: {', '.join(sorted(VALID_KINDS))}"

    # Parse aliases
    try:
        alias_list = json.loads(aliases) if aliases and aliases != '[]' else []
    except (json.JSONDecodeError, TypeError):
        alias_list = [a.strip() for a in aliases.split(',') if a.strip()]

    conn = get_db()
    cur = conn.execute(
        """INSERT INTO artifact_registry
           (kind, canonical_name, aliases, description, uri, ports, paths,
            run_cmd, repo, domain, state, session_id, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
        (kind, canonical_name, json.dumps(alias_list), description, uri, ports,
         paths, run_cmd, repo, domain, session_id, metadata),
    )
    artifact_id = cur.lastrowid
    conn.commit()

    # Insert aliases into lookup table
    for alias in alias_list + [canonical_name.lower()]:
        alias_clean = alias.strip().lower()
        if alias_clean:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO artifact_aliases (artifact_id, phrase, source) VALUES (?, ?, 'create')",
                    (artifact_id, alias_clean),
                )
            except Exception:
                pass
    conn.commit()

    # Ingest to cognitive memory
    content = f"Artifact: {canonical_name} ({kind}). {description}. URI: {uri}. Aliases: {', '.join(alias_list)}"
    _cognitive_ingest_safe(content, "artifact", f"A{artifact_id}", canonical_name[:80], domain)

    return f"Artifact #{artifact_id} created: {canonical_name} ({kind}) — {uri or 'no URI'}"


def handle_artifact_find(query: str, kind: str = '', state: str = 'active') -> str:
    """Find artifacts using the retrieval ladder: exact alias → port/path → fuzzy token → all recent.

    This is the PRIMARY retrieval tool. Use it when the user references something
    they or NEXO built/deployed/created. Designed for natural language like
    'the backend', 'that script from yesterday', 'localhost something'.

    Args:
        query: What to search for — name, alias, port, path, or description fragment
        kind: Filter by kind (optional)
        state: Filter by state — default 'active'. Use 'all' for everything.
    """
    conn = get_db()
    results = []
    query_lower = query.strip().lower()

    state_filter = "AND state = ?" if state != 'all' else ""
    state_params = (state,) if state != 'all' else ()

    kind_filter = "AND kind = ?" if kind else ""
    kind_params = (kind,) if kind else ()

    extra_filters = state_filter + " " + kind_filter
    extra_params = state_params + kind_params

    # --- STAGE 1: Exact alias match (fastest, O(1)) ---
    rows = conn.execute(
        f"""SELECT DISTINCT r.* FROM artifact_registry r
            JOIN artifact_aliases a ON a.artifact_id = r.id
            WHERE a.phrase = ? {extra_filters}
            ORDER BY r.last_touched_at DESC LIMIT 5""",
        (query_lower,) + extra_params,
    ).fetchall()
    if rows:
        results = [dict(r) for r in rows]
        return _format_results(results, "alias match", query)

    # --- STAGE 2: Port or URI match ---
    rows = conn.execute(
        f"""SELECT * FROM artifact_registry
            WHERE (uri LIKE ? OR ports LIKE ?) {extra_filters}
            ORDER BY last_touched_at DESC LIMIT 5""",
        (f"%{query_lower}%", f"%{query_lower}%") + extra_params,
    ).fetchall()
    if rows:
        results = [dict(r) for r in rows]
        return _format_results(results, "URI/port match", query)

    # --- STAGE 3: Path match ---
    rows = conn.execute(
        f"""SELECT * FROM artifact_registry
            WHERE paths LIKE ? {extra_filters}
            ORDER BY last_touched_at DESC LIMIT 5""",
        (f"%{query_lower}%",) + extra_params,
    ).fetchall()
    if rows:
        results = [dict(r) for r in rows]
        return _format_results(results, "path match", query)

    # --- STAGE 4: Fuzzy token match on name, description, aliases ---
    tokens = query_lower.split()
    if tokens:
        conditions = " AND ".join(
            "(LOWER(canonical_name) LIKE ? OR LOWER(description) LIKE ? OR LOWER(aliases) LIKE ?)"
            for _ in tokens
        )
        params = []
        for t in tokens:
            p = f"%{t}%"
            params.extend([p, p, p])
        rows = conn.execute(
            f"""SELECT * FROM artifact_registry
                WHERE {conditions} {extra_filters}
                ORDER BY last_touched_at DESC LIMIT 10""",
            tuple(params) + extra_params,
        ).fetchall()
        if rows:
            results = [dict(r) for r in rows]
            return _format_results(results, "token match", query)

    # --- STAGE 5: Recent artifacts (last 72h) as fallback ---
    cutoff = (datetime.datetime.now() - datetime.timedelta(hours=72)).isoformat()
    rows = conn.execute(
        f"""SELECT * FROM artifact_registry
            WHERE last_touched_at >= ? {extra_filters}
            ORDER BY last_touched_at DESC LIMIT 10""",
        (cutoff,) + extra_params,
    ).fetchall()
    if rows:
        results = [dict(r) for r in rows]
        return _format_results(results, "recent (72h)", query)

    return f"No artifacts found for '{query}'. Use artifact_list to see all registered artifacts."


def handle_artifact_update(
    id: int,
    canonical_name: str = '',
    aliases: str = '',
    description: str = '',
    uri: str = '',
    ports: str = '',
    paths: str = '',
    run_cmd: str = '',
    state: str = '',
    domain: str = '',
    metadata: str = '',
) -> str:
    """Update an artifact. Only non-empty fields are changed.

    Args:
        id: Artifact ID to update
        canonical_name: New primary name
        aliases: New JSON array of aliases (replaces existing)
        description: New description
        uri: New URI
        ports: New ports JSON array
        paths: New paths JSON array
        run_cmd: New run command
        state: New state (active, inactive, broken, archived)
        domain: New domain
        metadata: New metadata JSON (merged with existing)
    """
    conn = get_db()
    row = conn.execute("SELECT * FROM artifact_registry WHERE id = ?", (id,)).fetchone()
    if not row:
        return f"ERROR: Artifact #{id} not found."

    updates = []
    params = []

    if canonical_name:
        updates.append("canonical_name = ?"); params.append(canonical_name)
    if description:
        updates.append("description = ?"); params.append(description)
    if uri:
        updates.append("uri = ?"); params.append(uri)
    if ports:
        updates.append("ports = ?"); params.append(ports)
    if paths:
        updates.append("paths = ?"); params.append(paths)
    if run_cmd:
        updates.append("run_cmd = ?"); params.append(run_cmd)
    if domain:
        updates.append("domain = ?"); params.append(domain)
    if state:
        if state not in VALID_STATES:
            return f"ERROR: state must be one of: {', '.join(sorted(VALID_STATES))}"
        updates.append("state = ?"); params.append(state)
    if metadata:
        try:
            existing = json.loads(row["metadata"] or '{}')
            new = json.loads(metadata)
            existing.update(new)
            updates.append("metadata = ?"); params.append(json.dumps(existing))
        except (json.JSONDecodeError, TypeError):
            pass

    if aliases:
        try:
            alias_list = json.loads(aliases) if aliases.startswith('[') else [a.strip() for a in aliases.split(',')]
        except (json.JSONDecodeError, TypeError):
            alias_list = [a.strip() for a in aliases.split(',')]
        updates.append("aliases = ?"); params.append(json.dumps(alias_list))
        # Rebuild alias lookup table
        conn.execute("DELETE FROM artifact_aliases WHERE artifact_id = ?", (id,))
        for alias in alias_list:
            alias_clean = alias.strip().lower()
            if alias_clean:
                conn.execute(
                    "INSERT OR IGNORE INTO artifact_aliases (artifact_id, phrase, source) VALUES (?, ?, 'update')",
                    (id, alias_clean),
                )

    if not updates:
        return "Nothing to update."

    updates.append("last_touched_at = datetime('now')")
    params.append(id)
    conn.execute(f"UPDATE artifact_registry SET {', '.join(updates)} WHERE id = ?", tuple(params))
    conn.commit()
    return f"Artifact #{id} updated."


def handle_artifact_learn_alias(id: int, phrase: str) -> str:
    """Learn a new alias from user language. Call this when the user refers to an
    artifact with a term not yet registered (e.g., the user says 'backend' for dashboard:6174).

    Args:
        id: Artifact ID
        phrase: The user's term (e.g., 'backend', 'that api thing')
    """
    conn = get_db()
    row = conn.execute("SELECT * FROM artifact_registry WHERE id = ?", (id,)).fetchone()
    if not row:
        return f"ERROR: Artifact #{id} not found."

    phrase_clean = phrase.strip().lower()
    if not phrase_clean:
        return "ERROR: Empty phrase."

    # Add to alias lookup table
    conn.execute(
        "INSERT OR IGNORE INTO artifact_aliases (artifact_id, phrase, source) VALUES (?, ?, 'user_language')",
        (id, phrase_clean),
    )

    # Also add to the artifact's aliases JSON array
    try:
        existing = json.loads(row["aliases"] or '[]')
    except (json.JSONDecodeError, TypeError):
        existing = []
    if phrase_clean not in [a.lower() for a in existing]:
        existing.append(phrase_clean)
        conn.execute(
            "UPDATE artifact_registry SET aliases = ?, last_touched_at = datetime('now') WHERE id = ?",
            (json.dumps(existing), id),
        )

    conn.commit()
    return f"Alias '{phrase_clean}' learned for artifact #{id} ({row['canonical_name']})."


def handle_artifact_list(kind: str = '', state: str = 'active', recent_hours: int = 0) -> str:
    """List all artifacts, optionally filtered.

    Args:
        kind: Filter by kind (service, dashboard, script, etc.)
        state: Filter by state — 'active' (default), 'all', 'inactive', 'broken', 'archived'
        recent_hours: If >0, only show artifacts touched in the last N hours
    """
    conn = get_db()
    conditions = []
    params = []

    if state != 'all':
        conditions.append("state = ?"); params.append(state)
    if kind:
        conditions.append("kind = ?"); params.append(kind)
    if recent_hours > 0:
        cutoff = (datetime.datetime.now() - datetime.timedelta(hours=recent_hours)).isoformat()
        conditions.append("last_touched_at >= ?"); params.append(cutoff)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    rows = conn.execute(
        f"SELECT * FROM artifact_registry {where} ORDER BY last_touched_at DESC",
        tuple(params),
    ).fetchall()

    if not rows:
        filters = []
        if kind: filters.append(f"kind={kind}")
        if state != 'all': filters.append(f"state={state}")
        if recent_hours: filters.append(f"last {recent_hours}h")
        return f"No artifacts found{' (' + ', '.join(filters) + ')' if filters else ''}."

    lines = [f"ARTIFACT REGISTRY ({len(rows)}):"]
    for r in rows:
        r = dict(r)
        aliases_str = ""
        try:
            aliases = json.loads(r.get("aliases", "[]"))
            if aliases:
                aliases_str = f" aka [{', '.join(aliases[:3])}]"
        except (json.JSONDecodeError, TypeError):
            pass
        uri_str = f" → {r['uri']}" if r.get("uri") else ""
        cmd_str = f" | cmd: {r['run_cmd'][:60]}" if r.get("run_cmd") else ""
        touched = r.get("last_touched_at", "")[:16]
        lines.append(
            f"  #{r['id']} [{r['kind']}] {r['canonical_name']}{aliases_str}{uri_str}{cmd_str} "
            f"({r['state']}, {touched})"
        )
    return "\n".join(lines)


def handle_artifact_delete(id: int) -> str:
    """Delete an artifact from the registry.

    Args:
        id: Artifact ID to delete
    """
    conn = get_db()
    row = conn.execute("SELECT canonical_name FROM artifact_registry WHERE id = ?", (id,)).fetchone()
    if not row:
        return f"ERROR: Artifact #{id} not found."
    name = row["canonical_name"]
    conn.execute("DELETE FROM artifact_aliases WHERE artifact_id = ?", (id,))
    conn.execute("DELETE FROM artifact_registry WHERE id = ?", (id,))
    conn.commit()
    return f"Artifact #{id} ({name}) deleted."


def _format_results(results, method, query):
    """Format search results for display."""
    lines = [f"ARTIFACTS FOUND ({len(results)}, via {method} for '{query}'):"]
    for r in results:
        aliases_str = ""
        try:
            aliases = json.loads(r.get("aliases", "[]"))
            if aliases:
                aliases_str = f" aka [{', '.join(aliases[:4])}]"
        except (json.JSONDecodeError, TypeError):
            pass
        uri_str = f" → {r['uri']}" if r.get("uri") else ""
        cmd_str = f"\n    Run: {r['run_cmd']}" if r.get("run_cmd") else ""
        paths_str = ""
        try:
            paths = json.loads(r.get("paths", "[]"))
            if paths:
                paths_str = f"\n    Paths: {', '.join(paths[:3])}"
        except (json.JSONDecodeError, TypeError):
            pass
        touched = r.get("last_touched_at", "")[:16]
        lines.append(
            f"  #{r['id']} [{r['kind']}] {r['canonical_name']}{aliases_str}{uri_str} "
            f"({r['state']}, {touched}){cmd_str}{paths_str}"
        )
    return "\n".join(lines)


# Plugin registration — TOOLS array consumed by plugin_loader.py
TOOLS = [
    (handle_artifact_create, "nexo_artifact_create",
     "Register a new artifact (service, dashboard, script, API, etc.) in the Artifact Registry. "
     "Call this whenever NEXO creates, deploys, or discovers a runnable/accessible artifact."),
    (handle_artifact_find, "nexo_artifact_find",
     "Find artifacts using the retrieval ladder: exact alias → port/path → fuzzy token → recent. "
     "PRIMARY retrieval tool for when users reference something built/deployed. Handles natural "
     "language like 'the backend', 'that script', 'localhost something'."),
    (handle_artifact_update, "nexo_artifact_update",
     "Update an existing artifact. Only non-empty fields are changed."),
    (handle_artifact_learn_alias, "nexo_artifact_learn_alias",
     "Learn a new alias from user language. Call when the user refers to an artifact with "
     "an unregistered term (e.g., 'backend' for the NEXO Brain Dashboard)."),
    (handle_artifact_list, "nexo_artifact_list",
     "List all registered artifacts, optionally filtered by kind, state, or recency."),
    (handle_artifact_delete, "nexo_artifact_delete",
     "Delete an artifact from the registry."),
]
