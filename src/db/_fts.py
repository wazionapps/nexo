"""NEXO DB — Fts module."""
import os, pathlib, re, sqlite3, threading, datetime
import paths
from db._core import get_db, now_epoch, DB_PATH

NEXO_HOME = os.environ.get("NEXO_HOME", os.path.expanduser("~/.nexo"))

# ── FTS5 Unified Search ──────────────────────────────────────────

# Directories to index for unified search
_FTS_MD_DIRS = [
    os.path.join(NEXO_HOME, "docs"),
    os.path.join(NEXO_HOME, "projects"),
    str(paths.memory_dir()),
    str(paths.operations_dir()),
    os.path.join(NEXO_HOME, "learnings"),
    str(paths.brain_dir()),
    os.path.join(NEXO_HOME, "agents"),
    str(paths.personal_skills_dir()),
]
# Code repos: index source files (skip vendor, node_modules, etc.)
_FTS_CODE_DIRS = []  # Users can add project dirs via nexo_index_add_dir
_FTS_CODE_SKIP = {
    "vendor", "node_modules", ".git", "cache", "tmp", "logs", "uploads",
    "assets/img", "assets/fonts", ".next", "dist", "build", ".prisma",
    "public/build", ".turbo", "__pycache__",
    "coverage", ".nyc_output", "storage/framework", "bootstrap/cache",
}
_FTS_MAX_FILE_SIZE = 50_000  # skip .md files >50KB
_FTS_MAX_CODE_FILE_SIZE = 30_000  # skip code files >30KB

# Synonym map for cross-language search (ES <-> EN)
_SYNONYMS = {
    "carrito": ["cart", "checkout"],
    "cart": ["carrito", "checkout"],
    "abandoned": ["abandonado"],
    "abandonado": ["abandoned"],
    "busqueda": ["search", "buscar"],
    "search": ["busqueda", "buscar"],
    "envio": ["shipping", "envío"],
    "shipping": ["envio", "envío"],
    "pedido": ["order", "orden"],
    "order": ["pedido", "orden"],
    "cliente": ["customer", "client"],
    "customer": ["cliente", "client"],
    "producto": ["product"],
    "product": ["producto"],
    "precio": ["price"],
    "price": ["precio"],
    "descuento": ["discount"],
    "discount": ["descuento"],
    "pago": ["payment"],
    "payment": ["pago"],
    "factura": ["invoice"],
    "invoice": ["factura"],
    "tienda": ["store", "shop"],
    "store": ["tienda", "shop"],
    "configuracion": ["config", "settings", "configuration"],
    "config": ["configuracion", "settings"],
    "permisos": ["permissions"],
    "permissions": ["permisos"],
    "mensaje": ["message"],
    "message": ["mensaje"],
    "plantilla": ["template"],
    "template": ["plantilla"],
    "webhook": ["gancho"],
    "cron": ["tarea programada", "scheduled"],
    "extension": ["extensión", "plugin", "addon"],
    "plugin": ["extension", "extensión"],
}


def _get_all_code_dirs(conn=None):
    """Return combined list of hardcoded + dynamic code dirs as [(path, [patterns])]."""
    if conn is None:
        conn = get_db()
    dirs = list(_FTS_CODE_DIRS)
    try:
        for r in conn.execute("SELECT path, patterns FROM fts_dirs WHERE dir_type = 'code'").fetchall():
            patterns = [p.strip() for p in r["patterns"].split(",") if p.strip()]
            dirs.append((r["path"], patterns))
    except Exception:
        pass
    return dirs


def _get_all_md_dirs(conn=None):
    """Return combined list of hardcoded + dynamic md dirs."""
    if conn is None:
        conn = get_db()
    dirs = list(_FTS_MD_DIRS)
    try:
        for r in conn.execute("SELECT path FROM fts_dirs WHERE dir_type = 'md'").fetchall():
            dirs.append(r["path"])
    except Exception:
        pass
    return dirs


def fts_add_dir(path: str, dir_type: str = 'code',
                patterns: str = '*.php,*.js,*.json,*.py,*.ts,*.tsx',
                notes: str = '') -> dict:
    """Register a directory for FTS indexing."""
    conn = get_db()
    path = os.path.expanduser(path)
    if not os.path.isdir(path):
        return {"error": f"Directory not found: {path}"}
    try:
        conn.execute(
            "INSERT OR REPLACE INTO fts_dirs (path, dir_type, patterns, added_at, notes) VALUES (?,?,?,?,?)",
            (path, dir_type, patterns, now_epoch(), notes)
        )
        conn.commit()
        return {"path": path, "dir_type": dir_type, "patterns": patterns}
    except Exception as e:
        return {"error": str(e)}


def fts_remove_dir(path: str) -> dict:
    """Remove a directory from FTS indexing and clean up its entries."""
    conn = get_db()
    path = os.path.expanduser(path)
    deleted = conn.execute("DELETE FROM fts_dirs WHERE path = ?", (path,)).rowcount
    if deleted == 0:
        return {"error": f"Directory not registered: {path}"}
    # Remove indexed files from that directory
    conn.execute("DELETE FROM unified_search WHERE source IN ('file', 'code') AND source_id LIKE ?",
                 (path + "%",))
    conn.commit()
    return {"removed": path}


def fts_list_dirs() -> list[dict]:
    """List all registered FTS directories (hardcoded + dynamic)."""
    conn = get_db()
    result = []
    for d in _FTS_MD_DIRS:
        result.append({"path": d, "type": "md", "patterns": "*.md", "source": "builtin"})
    for d, pats in _FTS_CODE_DIRS:
        result.append({"path": d, "type": "code", "patterns": ",".join(pats), "source": "builtin"})
    try:
        for r in conn.execute("SELECT path, dir_type, patterns, notes FROM fts_dirs ORDER BY path").fetchall():
            result.append({"path": r["path"], "type": r["dir_type"], "patterns": r["patterns"],
                           "source": "dynamic", "notes": r["notes"] or ""})
    except Exception:
        pass
    return result


def _fs_indexing_enabled() -> bool:
    """Allow tests and smoke checks to disable expensive filesystem indexing."""
    return os.environ.get("NEXO_SKIP_FS_INDEX", "0") != "1"


def rebuild_fts_index(conn=None):
    """Rebuild FTS5 index from all sources: SQLite tables + .md files."""
    if conn is None:
        conn = get_db()
    conn.execute("DELETE FROM unified_search")

    def _ins(source, source_id, title, body, category, updated_at):
        conn.execute(
            "INSERT INTO unified_search(source, source_id, title, body, category, updated_at) VALUES (?,?,?,?,?,?)",
            (source, str(source_id), str(title)[:200], body or '', category or '', str(updated_at or ''))
        )

    # 1. Learnings
    for r in conn.execute("SELECT id, category, title, content, reasoning, updated_at FROM learnings").fetchall():
        _ins("learning", r["id"], r["title"], f"{r['content']} {r['reasoning'] or ''}", r["category"], r["updated_at"])

    # 2. Decisions
    for r in conn.execute("SELECT id, domain, decision, alternatives, based_on, outcome, created_at FROM decisions").fetchall():
        body = f"{r['decision']} {r['alternatives'] or ''} {r['based_on'] or ''} {r['outcome'] or ''}"
        _ins("decision", r["id"], r["decision"][:200], body, r["domain"] or '', r["created_at"])

    # 3. Change log
    for r in conn.execute("SELECT id, files, what_changed, why, triggered_by, affects, risks, created_at FROM change_log").fetchall():
        body = f"{r['what_changed']} {r['why']} {r['triggered_by'] or ''} {r['affects'] or ''} {r['risks'] or ''}"
        _ins("change", r["id"], r["files"], body, "change_log", r["created_at"])

    # 4. Session diary
    for r in conn.execute("SELECT id, summary, decisions, discarded, pending, context_next, mental_state, domain, created_at FROM session_diary").fetchall():
        body = f"{r['summary']} {r['decisions'] or ''} {r['pending'] or ''} {r['context_next'] or ''} {r['mental_state'] or ''}"
        _ins("diary", r["id"], (r["summary"] or '')[:200], body, r["domain"] or "general", r["created_at"])

    # 5. Followups
    for r in conn.execute("SELECT id, description, verification, reasoning, updated_at FROM followups").fetchall():
        body = f"{r['description']} {r['verification'] or ''} {r['reasoning'] or ''}"
        _ins("followup", r["id"], r["id"], body, "followup", r["updated_at"])

    # 6. Entities
    for r in conn.execute("SELECT id, name, type, value, notes, updated_at FROM entities").fetchall():
        _ins("entity", r["id"], r["name"], f"{r['name']} {r['value']} {r['notes'] or ''}", r["type"] or "general", r["updated_at"])

    if _fs_indexing_enabled():
        # 7. .md files from key directories (hardcoded + dynamic)
        for dir_path in _get_all_md_dirs(conn):
            p = pathlib.Path(dir_path)
            if not p.exists():
                continue
            for md_file in p.rglob("*.md"):
                try:
                    if md_file.stat().st_size > _FTS_MAX_FILE_SIZE:
                        continue
                    content = md_file.read_text(encoding="utf-8", errors="ignore")
                    category = md_file.parent.name or "docs"
                    _ins("file", str(md_file), md_file.stem, content, category, md_file.stat().st_mtime)
                except Exception:
                    continue

        # 8. Code files from project repos (hardcoded + dynamic)
        for dir_path, patterns in _get_all_code_dirs(conn):
            p = pathlib.Path(dir_path)
            if not p.exists():
                continue
            for pattern in patterns:
                for code_file in p.rglob(pattern):
                    # Skip excluded directories
                    if any(skip in code_file.parts for skip in _FTS_CODE_SKIP):
                        continue
                    try:
                        if code_file.stat().st_size > _FTS_MAX_CODE_FILE_SIZE:
                            continue
                        content = code_file.read_text(encoding="utf-8", errors="ignore")
                        # Use relative path from repo root as category
                        rel_parts = code_file.relative_to(p).parts
                        category = rel_parts[0] if rel_parts else "code"
                        _ins("code", str(code_file), code_file.name, content, category, code_file.stat().st_mtime)
                    except Exception:
                        continue

    conn.commit()


def _refresh_fts_files(conn=None):
    """Refresh file + code entries in FTS index — add new, update modified, remove deleted."""
    if conn is None:
        conn = get_db()

    if not _fs_indexing_enabled():
        conn.execute("DELETE FROM unified_search WHERE source IN ('file', 'code')")
        conn.commit()
        return

    # Get currently indexed files with their mtime (both 'file' and 'code' sources)
    indexed = {}
    for r in conn.execute("SELECT source, source_id, updated_at FROM unified_search WHERE source IN ('file', 'code')").fetchall():
        indexed[r[1]] = (r[0], r[2])

    current_files = set()

    # Scan .md files (hardcoded + dynamic)
    for dir_path in _get_all_md_dirs(conn):
        p = pathlib.Path(dir_path)
        if not p.exists():
            continue
        for md_file in p.rglob("*.md"):
            try:
                if md_file.stat().st_size > _FTS_MAX_FILE_SIZE:
                    continue
                fpath = str(md_file)
                current_files.add(fpath)
                mtime = md_file.stat().st_mtime
                old = indexed.get(fpath)
                if old is None or str(mtime) != str(old[1]):
                    content = md_file.read_text(encoding="utf-8", errors="ignore")
                    category = md_file.parent.name or "docs"
                    conn.execute("DELETE FROM unified_search WHERE source_id = ?", (fpath,))
                    conn.execute(
                        "INSERT INTO unified_search(source, source_id, title, body, category, updated_at) VALUES (?,?,?,?,?,?)",
                        ("file", fpath, md_file.stem, content, category, str(mtime))
                    )
            except Exception:
                continue

    # Scan code files (hardcoded + dynamic)
    for dir_path, patterns in _get_all_code_dirs(conn):
        p = pathlib.Path(dir_path)
        if not p.exists():
            continue
        for pattern in patterns:
            for code_file in p.rglob(pattern):
                if any(skip in code_file.parts for skip in _FTS_CODE_SKIP):
                    continue
                try:
                    if code_file.stat().st_size > _FTS_MAX_CODE_FILE_SIZE:
                        continue
                    fpath = str(code_file)
                    current_files.add(fpath)
                    mtime = code_file.stat().st_mtime
                    old = indexed.get(fpath)
                    if old is None or str(mtime) != str(old[1]):
                        content = code_file.read_text(encoding="utf-8", errors="ignore")
                        rel_parts = code_file.relative_to(p).parts
                        category = rel_parts[0] if rel_parts else "code"
                        conn.execute("DELETE FROM unified_search WHERE source_id = ?", (fpath,))
                        conn.execute(
                            "INSERT INTO unified_search(source, source_id, title, body, category, updated_at) VALUES (?,?,?,?,?,?)",
                            ("code", fpath, code_file.name, content, category, str(mtime))
                        )
                except Exception:
                    continue

    # Remove deleted files
    for fpath, (source, _) in indexed.items():
        if fpath not in current_files:
            conn.execute("DELETE FROM unified_search WHERE source_id = ?", (fpath,))

    conn.commit()


def _expand_synonyms(words: list[str]) -> list[str]:
    """Expand search words with synonyms for cross-language matching."""
    expanded = set(words)
    for w in words:
        w_lower = w.lower()
        if w_lower in _SYNONYMS:
            expanded.update(_SYNONYMS[w_lower])
    return list(expanded)


def fts_search(query: str, source_filter: str = None, limit: int = 20) -> list[dict]:
    """Search unified FTS5 index. Returns ranked results.

    Args:
        query: Search text (supports FTS5 syntax: "exact phrase", word*)
        source_filter: Optional filter by source (learning, decision, change, diary, followup, entity, file, code)
        limit: Max results (default 20)
    """
    conn = get_db()
    raw_query = query.strip()
    words = raw_query.split()
    if not words:
        return []

    # Expand with synonyms for cross-language matching
    all_words = _expand_synonyms(words)

    # Build FTS5 query: each word as quoted term with OR for broad matching.
    # Symbol-heavy identifiers (emails, paths, refs) need deterministic token
    # boundaries so FTS5 never treats punctuation as query syntax.
    fts_terms = []
    for w in all_words:
        # Strip FTS5 special chars to avoid syntax errors
        safe = w.replace('"', '').replace("'", '').replace('*', '').replace('^', '').strip()
        safe = re.sub(r"[-@/\\:]+", " ", safe)
        if not safe:
            continue
        # Split on dots and punctuation boundaries (e.g., emails, paths, files).
        parts = [p.strip() for p in re.split(r"[.\s]+", safe) if p.strip()]
        for part in parts:
            fts_terms.append(f'"{part}"')
            # Add prefix search for camelCase/code identifiers (contains uppercase mid-word)
            if any(c.isupper() for c in part[1:]) or '_' in part:
                fts_terms.append(f'{part}*')
    if not fts_terms:
        return []
    fts_query = " OR ".join(fts_terms)

    where_extra = ""
    params = [fts_query]
    if source_filter:
        where_extra = "AND source = ?"
        params.append(source_filter)
    params.append(limit)

    try:
        exact_rows = []
        if re.search(r"[@/\\:.-]", raw_query):
            exact_where = ""
            exact_params = [f"%{raw_query}%", f"%{raw_query}%", f"%{raw_query}%"]
            if source_filter:
                exact_where = "AND source = ?"
                exact_params.append(source_filter)
            exact_params.append(limit)
            exact_rows = conn.execute(f"""
                SELECT source, source_id, title,
                       substr(body, 1, 240) AS snippet,
                       category, updated_at, -100.0 AS rank
                FROM unified_search
                WHERE (title LIKE ? OR body LIKE ? OR source_id LIKE ?) {exact_where}
                ORDER BY updated_at DESC
                LIMIT ?
            """, exact_params).fetchall()

        rows = conn.execute(f"""
            SELECT source, source_id, title,
                   snippet(unified_search, 3, '»', '«', '...', 40) AS snippet,
                   category, updated_at, rank
            FROM unified_search
            WHERE unified_search MATCH ? {where_extra}
            ORDER BY rank
            LIMIT ?
        """, params).fetchall()
        merged = []
        seen = set()
        for row in list(exact_rows) + list(rows):
            item = dict(row)
            key = (item.get("source"), item.get("source_id"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged[:limit]
    except Exception:
        return []


def fts_upsert(source: str, source_id: str, title: str, body: str, category: str = '', commit: bool = True):
    """Add or update a single entry in the FTS index."""
    conn = get_db()
    conn.execute("DELETE FROM unified_search WHERE source = ? AND source_id = ?", (source, str(source_id)))
    conn.execute(
        "INSERT INTO unified_search(source, source_id, title, body, category, updated_at) VALUES (?,?,?,?,?,?)",
        (source, str(source_id), str(title)[:200], body or '', category or '', datetime.datetime.now().isoformat())
    )
    if commit:
        conn.commit()


def _migrate_add_column(conn, table: str, column: str, col_type: str):
    """Add column if it doesn't exist (idempotent)."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            pass
        else:
            raise


def _migrate_add_index(conn, index_name: str, table: str, column: str):
    """Create index if it doesn't exist (idempotent)."""
    conn.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table}({column})")
    conn.commit()
