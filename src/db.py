"""SQLite database for NEXO session coordination."""

import sqlite3
import time
import os
import secrets
import string
import datetime
import pathlib
import threading

DB_PATH = os.environ.get(
    "NEXO_TEST_DB",
    os.environ.get(
        "NEXO_DB",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "nexo.db"),
    ),
)

# TTLs in seconds (match session-coord.sh behavior)
SESSION_STALE_SECONDS = 900    # 15 min (documented TTL)
MESSAGE_TTL_SECONDS = 3600     # 1 hour
QUESTION_TTL_SECONDS = 600     # 10 min

# Single shared connection per process with write serialization.
# SQLite allows only one writer at a time. Using a shared connection with
# check_same_thread=False and a write lock ensures:
# - No FTS5 corruption from concurrent write connections
# - Reads can happen freely (WAL allows concurrent readers)
# - Writes are serialized via _write_lock to prevent 'database is locked' errors
_shared_conn: sqlite3.Connection | None = None
_write_lock = threading.RLock()  # RLock allows re-entrant locking (function A calls B, both serialize)


def get_db() -> sqlite3.Connection:
    """Get shared database connection with WAL mode.

    Returns a _SerializedConnection wrapper that serializes all execute
    calls via _write_lock, preventing race conditions and FTS5 corruption
    under concurrent thread access.
    """
    global _shared_conn
    if _shared_conn is None:
        raw = sqlite3.connect(
            DB_PATH, timeout=30, check_same_thread=False,
            isolation_level=None,  # autocommit — no implicit BEGIN holding locks
        )
        raw.execute("PRAGMA journal_mode=WAL")
        raw.execute("PRAGMA busy_timeout=30000")
        raw.execute("PRAGMA foreign_keys=ON")
        raw.execute("PRAGMA wal_autocheckpoint=100")
        raw.row_factory = sqlite3.Row
        _shared_conn = _SerializedConnection(raw)
    return _shared_conn


def close_db():
    """Close the shared database connection. Called on shutdown signals."""
    global _shared_conn
    if _shared_conn is not None:
        try:
            _shared_conn.close()
        except Exception:
            pass
        _shared_conn = None


def _get_raw_conn() -> sqlite3.Connection:
    """Get the raw unwrapped connection (for PRAGMA queries that need direct access)."""
    conn = get_db()
    if isinstance(conn, _SerializedConnection):
        return conn._conn
    return conn


class _SerializedConnection:
    """Wrapper around sqlite3.Connection that serializes all execute calls.

    SQLite with a single shared connection and check_same_thread=False needs
    serialization to prevent:
    - Stale lastrowid when concurrent INSERTs happen
    - FTS5 index corruption from concurrent writes
    - 'NoneType' errors from interleaved INSERT+SELECT sequences

    All execute/executemany/executescript calls go through _write_lock.
    Property access (row_factory etc.) passes through directly.
    """
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def execute(self, *args, **kwargs):
        with _write_lock:
            return self._conn.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        with _write_lock:
            return self._conn.executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        with _write_lock:
            return self._conn.executescript(*args, **kwargs)

    def commit(self):
        with _write_lock:
            return self._conn.commit()

    def close(self):
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name == '_conn':
            super().__setattr__(name, value)
        else:
            setattr(self._conn, name, value)


def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            sid TEXT PRIMARY KEY,
            task TEXT NOT NULL DEFAULT '',
            started_epoch REAL NOT NULL,
            last_update_epoch REAL NOT NULL,
            local_time TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS tracked_files (
            sid TEXT NOT NULL,
            path TEXT NOT NULL,
            tracked_at REAL NOT NULL,
            PRIMARY KEY (sid, path),
            FOREIGN KEY (sid) REFERENCES sessions(sid) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            from_sid TEXT NOT NULL,
            to_sid TEXT NOT NULL,
            text TEXT NOT NULL,
            created_epoch REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS message_reads (
            message_id TEXT NOT NULL,
            sid TEXT NOT NULL,
            PRIMARY KEY (message_id, sid),
            FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS questions (
            qid TEXT PRIMARY KEY,
            from_sid TEXT NOT NULL,
            to_sid TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_epoch REAL NOT NULL,
            answered_epoch REAL
        );


        CREATE TABLE IF NOT EXISTS reminders (
            id TEXT PRIMARY KEY,
            date TEXT,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            category TEXT DEFAULT 'general',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS followups (
            id TEXT PRIMARY KEY,
            date TEXT,
            description TEXT NOT NULL,
            verification TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'PENDING',
            recurrence TEXT DEFAULT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS learnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            notes TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(service, key)
        );

        CREATE TABLE IF NOT EXISTS task_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_num TEXT NOT NULL,
            task_name TEXT NOT NULL,
            executed_at REAL NOT NULL,
            notes TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS task_frequencies (
            task_num TEXT PRIMARY KEY,
            task_name TEXT NOT NULL,
            frequency_days INTEGER NOT NULL,
            description TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS plugins (
            filename TEXT PRIMARY KEY,
            tools_count INTEGER DEFAULT 0,
            tool_names TEXT DEFAULT '',
            loaded_at REAL,
            created_by TEXT DEFAULT 'manual'
        );

        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'general',
            value TEXT NOT NULL,
            notes TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS preferences (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            specialization TEXT NOT NULL,
            model TEXT DEFAULT 'sonnet',
            tools TEXT DEFAULT '',
            context_files TEXT DEFAULT '',
            rules TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS change_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            files TEXT NOT NULL,
            what_changed TEXT NOT NULL,
            why TEXT NOT NULL,
            triggered_by TEXT DEFAULT '',
            affects TEXT DEFAULT '',
            risks TEXT DEFAULT '',
            verify TEXT DEFAULT '',
            commit_ref TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            domain TEXT NOT NULL,
            decision TEXT NOT NULL,
            alternatives TEXT,
            based_on TEXT,
            confidence TEXT DEFAULT 'medium',
            context_ref TEXT,
            outcome TEXT,
            outcome_at TEXT
        );

        CREATE TABLE IF NOT EXISTS session_diary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            decisions TEXT NOT NULL,
            discarded TEXT,
            pending TEXT,
            context_next TEXT,
            mental_state TEXT,
            domain TEXT,
            user_signals TEXT,
            summary TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evolution_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dimension TEXT NOT NULL,
            score INTEGER NOT NULL CHECK(score >= 0 AND score <= 100),
            measured_at TEXT DEFAULT (datetime('now')),
            evidence TEXT NOT NULL,
            delta INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS evolution_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT (datetime('now')),
            cycle_number INTEGER NOT NULL,
            dimension TEXT NOT NULL,
            proposal TEXT NOT NULL,
            classification TEXT NOT NULL DEFAULT 'auto',
            status TEXT DEFAULT 'pending',
            files_changed TEXT,
            snapshot_ref TEXT,
            test_result TEXT,
            impact INTEGER DEFAULT 0,
            reasoning TEXT NOT NULL
        );
    """)
    # foreign_keys=ON is set in get_db() per-connection

    # ── Run formal migrations ────────────────────────────────────
    run_migrations(conn)

    # ── FTS5 unified search index ────────────────────────────────
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS unified_search USING fts5(
            source,
            source_id,
            title,
            body,
            category,
            updated_at UNINDEXED,
            tokenize='unicode61 remove_diacritics 2'
        )
    """)

    # Dynamic directory registry for FTS indexing
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fts_dirs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            dir_type TEXT NOT NULL DEFAULT 'code',
            patterns TEXT NOT NULL DEFAULT '*.php,*.js,*.json,*.py,*.ts,*.tsx',
            added_at REAL NOT NULL,
            notes TEXT DEFAULT ''
        )
    """)
    conn.commit()

    if os.environ.get("NEXO_SKIP_FS_INDEX", "0") != "1":
        # FTS refresh in background thread — never block server startup
        import threading

        def _bg_fts():
            try:
                bg_conn = sqlite3.connect(DB_PATH, timeout=30)
                bg_conn.execute("PRAGMA journal_mode=WAL")
                bg_conn.execute("PRAGMA busy_timeout=30000")
                bg_conn.row_factory = sqlite3.Row
                row = bg_conn.execute("SELECT COUNT(*) FROM unified_search").fetchone()
                if row[0] == 0:
                    rebuild_fts_index(bg_conn)
                else:
                    _refresh_fts_files(bg_conn)
                bg_conn.close()
            except Exception:
                pass

        threading.Thread(target=_bg_fts, daemon=True).start()


# ── FTS5 Unified Search ──────────────────────────────────────────

# Directories to index for unified search
_FTS_MD_DIRS = [
    os.path.expanduser("~/claude/docs"),
    os.path.expanduser("~/claude/projects"),
    os.path.expanduser("~/claude/memory"),
    os.path.expanduser("~/claude/operations"),
    os.path.expanduser("~/claude/learnings"),
    os.path.expanduser("~/claude/brain"),
    os.path.expanduser("~/claude/agents"),
    os.path.expanduser("~/claude/skills"),
]
# Code repos: index source files (skip vendor, node_modules, etc.)
_FTS_CODE_DIRS = [
    (os.path.expanduser("~/Documents/_PhpstormProjects"), ["*.php", "*.js", "*.json", "*.py", "*.ts", "*.tsx"]),
]
_FTS_CODE_SKIP = {
    "vendor", "node_modules", ".git", "cache", "tmp", "logs", "uploads",
    "assets/img", "assets/fonts", ".next", "dist", "build", ".prisma",
    "PROYECTOS ANTIGUOS", "public/build", ".turbo", "__pycache__",
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
    words = query.strip().split()
    if not words:
        return []

    # Expand with synonyms for cross-language matching
    all_words = _expand_synonyms(words)

    # Build FTS5 query: each word as quoted term with OR for broad matching
    fts_terms = []
    for w in all_words:
        # Strip FTS5 special chars to avoid syntax errors
        safe = w.replace('"', '').replace("'", '').replace('*', '').replace('^', '').replace('-', ' ').strip()
        if not safe:
            continue
        # Split on dots (e.g., "capabilities.json" → "capabilities" + "json")
        parts = [p.strip() for p in safe.split('.') if p.strip()]
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
        rows = conn.execute(f"""
            SELECT source, source_id, title,
                   snippet(unified_search, 3, '»', '«', '...', 40) AS snippet,
                   category, updated_at, rank
            FROM unified_search
            WHERE unified_search MATCH ? {where_extra}
            ORDER BY rank
            LIMIT ?
        """, params).fetchall()
        return [dict(r) for r in rows]
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


# ── Formal Migration System ─────────────────────────────────────
#
# Each migration is (version, name, callable). Migrations run once
# and are tracked in schema_migrations. The version number MUST be
# strictly increasing. Add new migrations at the end of the list.
#
# For users upgrading via npm/git, init_db() calls run_migrations()
# automatically — no manual steps needed.

def _m1_learnings_columns(conn):
    _migrate_add_column(conn, "learnings", "reasoning", "TEXT")
    _migrate_add_column(conn, "learnings", "prevention", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "learnings", "applies_to", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "learnings", "status", "TEXT DEFAULT 'active'")
    _migrate_add_column(conn, "learnings", "review_due_at", "REAL")
    _migrate_add_column(conn, "learnings", "last_reviewed_at", "REAL")

def _m2_followups_reasoning(conn):
    _migrate_add_column(conn, "followups", "reasoning", "TEXT")
    _migrate_add_column(conn, "task_history", "reasoning", "TEXT")

def _m3_decisions_review(conn):
    _migrate_add_column(conn, "decisions", "status", "TEXT DEFAULT 'pending_review'")
    _migrate_add_column(conn, "decisions", "review_due_at", "TEXT")
    _migrate_add_column(conn, "decisions", "last_reviewed_at", "TEXT")
    _migrate_add_index(conn, "idx_decisions_domain", "decisions", "domain")
    _migrate_add_index(conn, "idx_decisions_created", "decisions", "created_at")
    _migrate_add_index(conn, "idx_decisions_review_due", "decisions", "review_due_at")

def _m4_session_diary_columns(conn):
    _migrate_add_index(conn, "idx_session_diary_sid", "session_diary", "session_id")
    _migrate_add_column(conn, "session_diary", "mental_state", "TEXT")
    _migrate_add_column(conn, "session_diary", "domain", "TEXT")
    _migrate_add_column(conn, "session_diary", "user_signals", "TEXT")
    _migrate_add_column(conn, "session_diary", "self_critique", "TEXT")

def _m5_change_log_indexes(conn):
    _migrate_add_index(conn, "idx_change_log_created", "change_log", "created_at")
    _migrate_add_index(conn, "idx_change_log_files", "change_log", "files")
    _migrate_add_index(conn, "idx_learnings_status", "learnings", "status")
    _migrate_add_index(conn, "idx_learnings_review_due", "learnings", "review_due_at")

def _m6_error_guard_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS error_repetitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            new_learning_id INTEGER NOT NULL,
            original_learning_id INTEGER NOT NULL,
            similarity REAL NOT NULL,
            area TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS guard_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            files TEXT,
            area TEXT,
            learnings_returned INTEGER DEFAULT 0,
            blocking_rules_returned INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    _migrate_add_index(conn, "idx_error_repetitions_area", "error_repetitions", "area")
    _migrate_add_index(conn, "idx_guard_checks_session", "guard_checks", "session_id")

def _m7_diary_source_and_draft(conn):
    _migrate_add_column(conn, "session_diary", "source", "TEXT DEFAULT 'claude'")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_diary_draft (
            sid TEXT PRIMARY KEY,
            summary_draft TEXT DEFAULT '',
            tasks_seen TEXT DEFAULT '[]',
            change_ids TEXT DEFAULT '[]',
            decision_ids TEXT DEFAULT '[]',
            last_context_hint TEXT DEFAULT '',
            heartbeat_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)


def _m8_adaptive_log_and_somatic(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS adaptive_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            mode TEXT NOT NULL,
            tension_score REAL NOT NULL,
            sig_vibe REAL DEFAULT 0,
            sig_corrections REAL DEFAULT 0,
            sig_brevity REAL DEFAULT 0,
            sig_topic REAL DEFAULT 0,
            sig_tool_errors REAL DEFAULT 0,
            sig_git_diff REAL DEFAULT 0,
            context_hint TEXT DEFAULT '',
            feedback_event TEXT DEFAULT NULL,
            feedback_delta INTEGER DEFAULT NULL,
            feedback_ts TEXT DEFAULT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_adaptive_log_ts ON adaptive_log(timestamp)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS somatic_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            target TEXT NOT NULL,
            target_type TEXT NOT NULL,
            event_type TEXT NOT NULL,
            delta REAL NOT NULL,
            source TEXT DEFAULT '',
            projected INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_somatic_events_target ON somatic_events(target)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_somatic_events_projected ON somatic_events(projected)")


def _m10_diary_archive(conn):
    """Permanent diary archive — diaries are never truly deleted, just moved here."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS diary_archive (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            decisions TEXT NOT NULL,
            discarded TEXT,
            pending TEXT,
            context_next TEXT,
            summary TEXT NOT NULL,
            mental_state TEXT,
            domain TEXT,
            user_signals TEXT,
            self_critique TEXT DEFAULT '',
            source TEXT DEFAULT 'claude',
            archived_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_diary_archive_created
        ON diary_archive (created_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_diary_archive_domain
        ON diary_archive (domain)
    """)


def _m9_maintenance_schedule(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS maintenance_schedule (
            task_name TEXT PRIMARY KEY,
            interval_hours REAL NOT NULL,
            last_run_at TEXT DEFAULT NULL,
            last_duration_ms INTEGER DEFAULT 0,
            run_count INTEGER DEFAULT 0
        )
    """)
    tasks = [
        ('cognitive_decay', 20), ('synthesis', 20), ('self_audit', 144),
        ('weight_learning', 20), ('somatic_projection', 20), ('somatic_decay', 20),
        ('graph_maintenance', 48),
    ]
    for name, hours in tasks:
        conn.execute(
            "INSERT OR IGNORE INTO maintenance_schedule (task_name, interval_hours) VALUES (?, ?)",
            (name, hours)
        )


def _m11_core_rules(conn):
    """Core system rules table — versioned behavioral rules with migration support."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS core_rules (
            id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            rule TEXT NOT NULL,
            why TEXT NOT NULL,
            importance INTEGER NOT NULL DEFAULT 4,
            type TEXT NOT NULL DEFAULT 'advisory' CHECK(type IN ('blocking', 'advisory')),
            added_in TEXT NOT NULL DEFAULT '1.0.0',
            removed_in TEXT DEFAULT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS core_rules_version (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            version TEXT NOT NULL DEFAULT '0.0.0',
            updated_at TEXT DEFAULT (datetime('now'))
        );

        INSERT OR IGNORE INTO core_rules_version (id, version) VALUES (1, '0.0.0');
    """)
    # Seed rules from core-rules.json if available
    _seed_core_rules(conn)


def _seed_core_rules(conn):
    """Load rules from core-rules.json into the database."""
    import json
    rules_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules", "core-rules.json")
    if not os.path.exists(rules_file):
        return

    with open(rules_file) as f:
        data = json.load(f)

    version = data["_meta"]["version"]

    for cat_key, cat in data["categories"].items():
        for rule in cat["rules"]:
            conn.execute(
                """INSERT OR REPLACE INTO core_rules (id, category, rule, why, importance, type, added_in)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (rule["id"], cat_key, rule["rule"], rule["why"],
                 rule["importance"], rule["type"], rule.get("added_in", version))
            )

    conn.execute("UPDATE core_rules_version SET version = ?, updated_at = datetime('now') WHERE id = 1", (version,))
    conn.commit()


def _m12_session_checkpoints(conn):
    """Session checkpoints for intelligent auto-compaction.

    PreCompact saves a checkpoint; PostCompact reads it to re-inject a
    Core Memory Block that preserves continuity after context compression.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_checkpoints (
            sid TEXT PRIMARY KEY,
            task TEXT DEFAULT '',
            task_status TEXT DEFAULT 'active',
            active_files TEXT DEFAULT '[]',
            current_goal TEXT DEFAULT '',
            decisions_summary TEXT DEFAULT '',
            errors_found TEXT DEFAULT '',
            reasoning_thread TEXT DEFAULT '',
            next_step TEXT DEFAULT '',
            compaction_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)


# Migration registry — APPEND ONLY, never reorder or delete
MIGRATIONS = [
    (1, "learnings_columns", _m1_learnings_columns),
    (2, "followups_reasoning", _m2_followups_reasoning),
    (3, "decisions_review", _m3_decisions_review),
    (4, "session_diary_columns", _m4_session_diary_columns),
    (5, "change_log_indexes", _m5_change_log_indexes),
    (6, "error_guard_tables", _m6_error_guard_tables),
    (7, "diary_source_and_draft", _m7_diary_source_and_draft),
    (8, "adaptive_log_and_somatic", _m8_adaptive_log_and_somatic),
    (9, "maintenance_schedule", _m9_maintenance_schedule),
    (10, "diary_archive", _m10_diary_archive),
    (11, "core_rules", _m11_core_rules),
    (12, "session_checkpoints", _m12_session_checkpoints),
]


def run_migrations(conn=None):
    """Run pending migrations. Tracks applied versions in schema_migrations.

    Safe to call multiple times — skips already-applied migrations.
    Called automatically by init_db() on every server start.
    """
    if conn is None:
        conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()

    applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}

    for version, name, fn in MIGRATIONS:
        if version not in applied:
            try:
                fn(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                    (version, name)
                )
                conn.commit()
            except Exception as e:
                # Log but don't crash — partial migration is better than no server
                import sys
                print(f"[MIGRATION] v{version} ({name}) failed: {e}", file=sys.stderr)

    return len(MIGRATIONS) - len(applied)


def get_schema_version() -> int:
    """Return the highest applied migration version, or 0 if none."""
    conn = get_db()
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return row[0] or 0
    except Exception:
        return 0


def _gen_id(prefix: str, length: int = 8) -> str:
    """Generate a random ID like 'msg-a1b2c3' or 'q-x9y8z7w6'."""
    chars = string.ascii_lowercase + string.digits
    suffix = ''.join(secrets.choice(chars) for _ in range(length))
    return f"{prefix}-{suffix}"


# ── Session operations ──────────────────────────────────────────────

def now_epoch() -> float:
    return time.time()


def local_time_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%H:%M")


def register_session(sid: str, task: str) -> dict:
    """Register or re-register a session."""
    conn = get_db()
    now = now_epoch()
    conn.execute(
        "INSERT OR REPLACE INTO sessions (sid, task, started_epoch, last_update_epoch, local_time) "
        "VALUES (?, ?, ?, ?, ?)",
        (sid, task, now, now, local_time_str())
    )
    conn.commit()
    return {"sid": sid, "task": task}


def update_session(sid: str, task: str | None) -> dict:
    """Update session timestamp (and task if provided). Preserves started_epoch.

    Args:
        sid: Session ID.
        task: New task description, or None to keep current task (keepalive touch).
    """
    conn = get_db()
    now = now_epoch()
    row = conn.execute("SELECT started_epoch, task FROM sessions WHERE sid = ?", (sid,)).fetchone()
    if row:
        effective_task = task if task is not None else row["task"]
        conn.execute(
            "UPDATE sessions SET task = ?, last_update_epoch = ?, local_time = ? WHERE sid = ?",
            (effective_task, now, local_time_str(), sid)
        )
    else:
        effective_task = task or "Unknown"
        conn.execute(
            "INSERT INTO sessions (sid, task, started_epoch, last_update_epoch, local_time) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, effective_task, now, now, local_time_str())
        )
    conn.commit()
    return {"sid": sid, "task": effective_task}


def complete_session(sid: str):
    """Remove session and its tracked files."""
    conn = get_db()
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("DELETE FROM tracked_files WHERE sid = ?", (sid,))
    conn.execute("DELETE FROM sessions WHERE sid = ?", (sid,))
    conn.commit()


def get_active_sessions() -> list[dict]:
    """Get all sessions updated within STALE threshold."""
    conn = get_db()
    cutoff = now_epoch() - SESSION_STALE_SECONDS
    rows = conn.execute(
        "SELECT sid, task, started_epoch, last_update_epoch, local_time "
        "FROM sessions WHERE last_update_epoch > ?",
        (cutoff,)
    ).fetchall()
    return [dict(r) for r in rows]


def clean_stale_sessions() -> int:
    """Remove stale sessions. Returns count removed."""
    conn = get_db()
    cutoff = now_epoch() - SESSION_STALE_SECONDS
    stale = conn.execute(
        "SELECT sid FROM sessions WHERE last_update_epoch <= ?", (cutoff,)
    ).fetchall()
    for row in stale:
        conn.execute("DELETE FROM tracked_files WHERE sid = ?", (row["sid"],))
    result = conn.execute(
        "DELETE FROM sessions WHERE last_update_epoch <= ?", (cutoff,)
    )
    count = result.rowcount
    conn.commit()
    return count


def search_sessions(keyword: str) -> list[dict]:
    """Find sessions whose task contains keyword (case-insensitive)."""
    conn = get_db()
    cutoff = now_epoch() - SESSION_STALE_SECONDS
    rows = conn.execute(
        "SELECT sid, task, last_update_epoch, local_time FROM sessions "
        "WHERE last_update_epoch > ? AND LOWER(task) LIKE ?",
        (cutoff, f"%{keyword.lower()}%")
    ).fetchall()
    return [dict(r) for r in rows]


# ── File tracking ───────────────────────────────────────────────────

def track_files(sid: str, paths: list[str]) -> dict:
    """Track files for a session. Returns conflicts if any."""
    conn = get_db()
    now = now_epoch()
    session = conn.execute("SELECT sid FROM sessions WHERE sid = ?", (sid,)).fetchone()
    if not session:
            return {"error": f"Session {sid} not found. Register first."}

    for path in paths:
        conn.execute(
            "INSERT OR IGNORE INTO tracked_files (sid, path, tracked_at) VALUES (?, ?, ?)",
            (sid, path, now)
        )
    conn.commit()
    conflicts = _check_conflicts(conn, sid)
    return {"tracked": paths, "conflicts": conflicts}


def untrack_files(sid: str, paths: list[str] | None = None):
    """Untrack files. If paths is None, untrack all."""
    conn = get_db()
    if paths:
        for path in paths:
            conn.execute(
                "DELETE FROM tracked_files WHERE sid = ? AND path = ?",
                (sid, path)
            )
    else:
        conn.execute("DELETE FROM tracked_files WHERE sid = ?", (sid,))
    conn.commit()


def get_all_tracked_files() -> dict:
    """Get all tracked files grouped by session."""
    conn = get_db()
    cutoff = now_epoch() - SESSION_STALE_SECONDS
    rows = conn.execute(
        "SELECT tf.sid, tf.path, s.task FROM tracked_files tf "
        "JOIN sessions s ON tf.sid = s.sid "
        "WHERE s.last_update_epoch > ?",
        (cutoff,)
    ).fetchall()
    result = {}
    for r in rows:
        sid = r["sid"]
        if sid not in result:
            result[sid] = {"task": r["task"], "files": []}
        result[sid]["files"].append(r["path"])
    return result


def _check_conflicts(conn: sqlite3.Connection, sid: str) -> list[dict]:
    """Check if any of sid's files are tracked by other active sessions."""
    cutoff = now_epoch() - SESSION_STALE_SECONDS
    my_files = conn.execute(
        "SELECT path FROM tracked_files WHERE sid = ?", (sid,)
    ).fetchall()
    my_paths = {r["path"] for r in my_files}
    if not my_paths:
        return []

    conflicts = []
    others = conn.execute(
        "SELECT tf.sid, tf.path, s.task FROM tracked_files tf "
        "JOIN sessions s ON tf.sid = s.sid "
        "WHERE tf.sid != ? AND s.last_update_epoch > ?",
        (sid, cutoff)
    ).fetchall()
    by_sid = {}
    for r in others:
        if r["path"] in my_paths:
            osid = r["sid"]
            if osid not in by_sid:
                by_sid[osid] = {"sid": osid, "task": r["task"], "files": []}
            by_sid[osid]["files"].append(r["path"])
    return list(by_sid.values())


# ── Messages ────────────────────────────────────────────────────────

def send_message(from_sid: str, to_sid: str, text: str) -> str:
    """Send a message. to_sid can be 'all' for broadcast."""
    conn = get_db()
    _clean_old_messages(conn)
    msg_id = _gen_id("msg", 6)
    conn.execute(
        "INSERT INTO messages (id, from_sid, to_sid, text, created_epoch) "
        "VALUES (?, ?, ?, ?, ?)",
        (msg_id, from_sid, to_sid, text, now_epoch())
    )
    conn.commit()
    return msg_id


def get_inbox(sid: str) -> list[dict]:
    """Get unread messages for a session."""
    conn = get_db()
    _clean_old_messages(conn)
    rows = conn.execute(
        "SELECT m.id, m.from_sid, m.to_sid, m.text, m.created_epoch "
        "FROM messages m "
        "WHERE (m.to_sid = 'all' OR m.to_sid = ?) "
        "AND m.from_sid != ? "
        "AND m.id NOT IN (SELECT message_id FROM message_reads WHERE sid = ?)",
        (sid, sid, sid)
    ).fetchall()
    for r in rows:
        conn.execute(
            "INSERT OR IGNORE INTO message_reads (message_id, sid) VALUES (?, ?)",
            (r["id"], sid)
        )
    conn.commit()
    result = [dict(r) for r in rows]
    return result


def _clean_old_messages(conn: sqlite3.Connection):
    """Remove expired messages and commit immediately."""
    cutoff = now_epoch() - MESSAGE_TTL_SECONDS
    conn.execute("DELETE FROM messages WHERE created_epoch < ?", (cutoff,))
    conn.commit()


# ── Questions ───────────────────────────────────────────────────────

def ask_question(from_sid: str, to_sid: str, question: str) -> str:
    """Create a pending question. Returns qid."""
    conn = get_db()
    _expire_old_questions(conn)
    qid = _gen_id("q", 8)
    conn.execute(
        "INSERT INTO questions (qid, from_sid, to_sid, question, status, created_epoch) "
        "VALUES (?, ?, ?, ?, 'pending', ?)",
        (qid, from_sid, to_sid, question, now_epoch())
    )
    conn.commit()
    return qid


def answer_question(qid: str, answer: str) -> dict:
    """Answer a pending question."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM questions WHERE qid = ?", (qid,)
    ).fetchone()
    if not row:
            return {"error": f"Question {qid} not found"}
    if row["status"] != "pending":
            return {"error": f"Question {qid} is {row['status']}, not pending"}
    conn.execute(
        "UPDATE questions SET answer = ?, status = 'answered', answered_epoch = ? "
        "WHERE qid = ?",
        (answer, now_epoch(), qid)
    )
    conn.commit()
    return {"qid": qid, "status": "answered"}


def get_pending_questions(sid: str) -> list[dict]:
    """Get pending questions addressed to this session."""
    conn = get_db()
    _expire_old_questions(conn)
    rows = conn.execute(
        "SELECT qid, from_sid, question, created_epoch FROM questions "
        "WHERE to_sid = ? AND status = 'pending'",
        (sid,)
    ).fetchall()
    conn.commit()
    return [dict(r) for r in rows]


def check_answer(qid: str) -> dict | None:
    """Check if a question has been answered. Returns answer or None."""
    conn = get_db()
    row = conn.execute(
        "SELECT qid, answer, status FROM questions WHERE qid = ?", (qid,)
    ).fetchone()
    if not row:
        return None
    return dict(row)


def _expire_old_questions(conn: sqlite3.Connection):
    """Mark old pending questions as expired."""
    cutoff = now_epoch() - QUESTION_TTL_SECONDS
    conn.execute(
        "UPDATE questions SET status = 'expired' "
        "WHERE status = 'pending' AND created_epoch < ?",
        (cutoff,)
    )


# ── Reminders ──────────────────────────────────────────────────────

def create_reminder(id: str, description: str, date: str = None,
                    status: str = 'PENDING', category: str = 'general') -> dict:
    """Create a new reminder."""
    conn = get_db()
    now = now_epoch()
    try:
        conn.execute(
            "INSERT INTO reminders (id, date, description, status, category, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id, date, description, status, category, now, now)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return {"error": f"Reminder {id} already exists. Use update instead."}
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (id,)).fetchone()
    return dict(row)


def update_reminder(id: str, **kwargs) -> dict:
    """Update any fields of a reminder: description, date, status, category."""
    conn = get_db()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (id,)).fetchone()
    if not row:
            return {"error": f"Reminder {id} not found"}
    allowed = {"description", "date", "status", "category"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
            return {"error": "No valid fields to update"}
    updates["updated_at"] = now_epoch()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [id]
    conn.execute(f"UPDATE reminders SET {set_clause} WHERE id = ?", values)
    conn.commit()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (id,)).fetchone()
    return dict(row)


def complete_reminder(id: str) -> dict:
    """Mark a reminder as completed with today's date."""
    today = datetime.date.today().isoformat()
    return update_reminder(id, status=f"COMPLETED {today}")


def delete_reminder(id: str) -> bool:
    """Delete a reminder."""
    conn = get_db()
    result = conn.execute("DELETE FROM reminders WHERE id = ?", (id,))
    conn.commit()
    deleted = result.rowcount > 0
    return deleted


def get_reminders(filter_type: str = 'all') -> list[dict]:
    """Get reminders by filter: 'all' (active), 'due' (date <= today), 'completed'."""
    conn = get_db()
    today = datetime.date.today().isoformat()
    if filter_type == 'completed':
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status LIKE 'COMPLET%' ORDER BY updated_at DESC"
        ).fetchall()
    elif filter_type == 'due':
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status NOT LIKE 'COMPLET%' "
            "AND status != 'ELIMINADO' AND date IS NOT NULL AND date <= ? "
            "ORDER BY date ASC",
            (today,)
        ).fetchall()
    else:  # 'all' — active only
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status NOT LIKE 'COMPLET%' "
            "AND status != 'ELIMINADO' ORDER BY date ASC NULLS LAST"
        ).fetchall()
    return [dict(r) for r in rows]


def get_reminder(id: str) -> dict | None:
    """Get a single reminder by id."""
    conn = get_db()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (id,)).fetchone()
    return dict(row) if row else None


# ── Followups ──────────────────────────────────────────────────────

def create_followup(id: str, description: str, date: str = None,
                    verification: str = '', status: str = 'PENDING',
                    reasoning: str = '', recurrence: str = None) -> dict:
    """Create a new followup with optional reasoning and recurrence.

    recurrence format: 'weekly:monday', 'monthly:1', 'monthly:10', 'quarterly', etc.
    When a recurring followup is completed, a new one is auto-created with the next date.
    """
    conn = get_db()
    now = now_epoch()
    try:
        conn.execute(
            "INSERT INTO followups (id, date, description, verification, status, reasoning, recurrence, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id, date, description, verification, status, reasoning, recurrence, now, now)
        )
        conn.commit()
        fts_upsert("followup", id, id, f"{description} {verification} {reasoning}", "followup", commit=False)
    except sqlite3.IntegrityError:
        return {"error": f"Followup {id} already exists. Use update instead."}
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    return dict(row)


def update_followup(id: str, **kwargs) -> dict:
    """Update any fields of a followup: description, date, verification, status, reasoning."""
    conn = get_db()
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    if not row:
            return {"error": f"Followup {id} not found"}
    allowed = {"description", "date", "verification", "status", "reasoning", "recurrence"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
            return {"error": "No valid fields to update"}
    updates["updated_at"] = now_epoch()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [id]
    conn.execute(f"UPDATE followups SET {set_clause} WHERE id = ?", values)
    conn.commit()
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    r = dict(row)
    fts_upsert("followup", id, id, f"{r.get('description','')} {r.get('verification','')} {r.get('reasoning','')}", "followup", commit=False)
    return r


def _calc_next_recurrence_date(recurrence: str, current_date: str = None) -> str:
    """Calculate the next date for a recurring followup.

    Formats:
        weekly:monday, weekly:thursday, weekly:friday, weekly:sunday
        monthly:1, monthly:10, monthly:15
        quarterly
    """
    today = datetime.date.today()
    base = datetime.date.fromisoformat(current_date) if current_date else today

    if recurrence.startswith('weekly:'):
        day_name = recurrence.split(':')[1].lower()
        day_map = {'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
                   'friday': 4, 'saturday': 5, 'sunday': 6}
        target_day = day_map.get(day_name, 0)
        days_ahead = (target_day - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7  # next week, not today
        return (today + datetime.timedelta(days=days_ahead)).isoformat()

    elif recurrence.startswith('monthly:'):
        target_day = int(recurrence.split(':')[1])
        # Next month from today
        if today.month == 12:
            next_date = datetime.date(today.year + 1, 1, min(target_day, 28))
        else:
            import calendar
            max_day = calendar.monthrange(today.year, today.month + 1)[1]
            next_date = datetime.date(today.year, today.month + 1, min(target_day, max_day))
        return next_date.isoformat()

    elif recurrence == 'quarterly':
        # 3 months from current date
        month = base.month + 3
        year = base.year
        if month > 12:
            month -= 12
            year += 1
        import calendar
        max_day = calendar.monthrange(year, month)[1]
        return datetime.date(year, month, min(base.day, max_day)).isoformat()

    return None


def complete_followup(id: str, result: str = '') -> dict:
    """Mark a followup as completed with today's date and optional result.
    If the followup has a recurrence pattern, auto-creates the next occurrence."""
    conn = get_db()
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    if not row:
        return {"error": f"Followup {id} not found"}

    today = datetime.date.today().isoformat()
    kwargs = {"status": f"COMPLETED {today}"}
    if result:
        existing = row["verification"] or ''
        kwargs["verification"] = f"{existing}\n{result}".strip() if existing else result

    update_result = update_followup(id, **kwargs)

    # Auto-regenerate if recurring
    recurrence = row["recurrence"]
    if recurrence:
        next_date = _calc_next_recurrence_date(recurrence, row["date"])
        if next_date:
            # Rename completed one to include date suffix, then create fresh one
            archived_id = f"{id}-{today}"
            conn.execute("UPDATE followups SET id = ? WHERE id = ?", (archived_id, id))
            conn.commit()
            create_followup(
                id=id,
                description=row["description"],
                date=next_date,
                verification='',
                reasoning=row["reasoning"] or '',
                recurrence=recurrence,
            )

    return update_result


def delete_followup(id: str) -> bool:
    """Delete a followup."""
    conn = get_db()
    result = conn.execute("DELETE FROM followups WHERE id = ?", (id,))
    conn.execute("DELETE FROM unified_search WHERE source = 'followup' AND source_id = ?", (str(id),))
    conn.commit()
    deleted = result.rowcount > 0
    return deleted


def get_followups(filter_type: str = 'all') -> list[dict]:
    """Get followups by filter: 'all' (active), 'due' (date <= today), 'completed'."""
    conn = get_db()
    today = datetime.date.today().isoformat()
    if filter_type == 'completed':
        rows = conn.execute(
            "SELECT * FROM followups WHERE status LIKE 'COMPLET%' ORDER BY updated_at DESC"
        ).fetchall()
    elif filter_type == 'due':
        rows = conn.execute(
            "SELECT * FROM followups WHERE status NOT LIKE 'COMPLET%' "
            "AND status != 'ELIMINADO' AND date IS NOT NULL AND date <= ? "
            "ORDER BY date ASC",
            (today,)
        ).fetchall()
    else:  # 'all' — active only
        rows = conn.execute(
            "SELECT * FROM followups WHERE status NOT LIKE 'COMPLET%' "
            "AND status != 'ELIMINADO' ORDER BY date ASC NULLS LAST"
        ).fetchall()
    return [dict(r) for r in rows]


def get_followup(id: str) -> dict | None:
    """Get a single followup by id."""
    conn = get_db()
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (id,)).fetchone()
    return dict(row) if row else None


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


# ── Credentials ────────────────────────────────────────────────────

def create_credential(service: str, key: str, value: str, notes: str = '') -> dict:
    """Create a new credential entry."""
    conn = get_db()
    now = now_epoch()
    try:
        conn.execute(
            "INSERT INTO credentials (service, key, value, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (service, key, value, notes, now, now)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return {"error": f"Credential {service}/{key} already exists. Use update instead."}
    row = conn.execute(
        "SELECT * FROM credentials WHERE service = ? AND key = ?", (service, key)
    ).fetchone()
    return dict(row)


def update_credential(service: str, key: str, value: str = None, notes: str = None) -> dict:
    """Update value and/or notes for a credential."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM credentials WHERE service = ? AND key = ?", (service, key)
    ).fetchone()
    if not row:
            return {"error": f"Credential {service}/{key} not found"}
    updates = {"updated_at": now_epoch()}
    if value is not None:
        updates["value"] = value
    if notes is not None:
        updates["notes"] = notes
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [service, key]
    conn.execute(
        f"UPDATE credentials SET {set_clause} WHERE service = ? AND key = ?", values
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM credentials WHERE service = ? AND key = ?", (service, key)
    ).fetchone()
    return dict(row)


def delete_credential(service: str, key: str = None) -> bool:
    """Delete credential(s). If key=None, delete all for the service."""
    conn = get_db()
    if key:
        result = conn.execute(
            "DELETE FROM credentials WHERE service = ? AND key = ?", (service, key)
        )
    else:
        result = conn.execute(
            "DELETE FROM credentials WHERE service = ?", (service,)
        )
    conn.commit()
    deleted = result.rowcount > 0
    return deleted


def get_credential(service: str, key: str = None) -> list[dict]:
    """Get credential(s). If key=None, return all for the service.

    When exact match fails, performs fuzzy search across service, key,
    and notes fields. Returns results tagged with _fuzzy=True so
    the caller can differentiate suggestions from exact hits.
    """
    conn = get_db()
    if key:
        rows = conn.execute(
            "SELECT * FROM credentials WHERE service = ? AND key = ?", (service, key)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM credentials WHERE service = ?", (service,)
        ).fetchall()
    if rows:
        return [dict(r) for r in rows]

    # Fuzzy fallback: search term in service, key and notes (not value — too noisy)
    # Prioritize: service/key matches first, notes-only matches second
    term = f"%{service}%"
    fuzzy_rows = conn.execute(
        "SELECT *, "
        "CASE WHEN service LIKE ? THEN 0 "
        "     WHEN key LIKE ? THEN 1 "
        "     ELSE 2 END AS _rank "
        "FROM credentials WHERE "
        "service LIKE ? OR key LIKE ? OR notes LIKE ? "
        "ORDER BY _rank ASC, service ASC, key ASC",
        (term, term, term, term, term),
    ).fetchall()
    results = []
    for r in fuzzy_rows:
        d = dict(r)
        d["_fuzzy"] = True
        d.pop("_rank", None)
        results.append(d)
    return results


def list_credentials(service: str = None) -> list[dict]:
    """List service+key only (NO values) for security."""
    conn = get_db()
    if service:
        rows = conn.execute(
            "SELECT id, service, key, notes, created_at, updated_at "
            "FROM credentials WHERE service = ? ORDER BY key ASC",
            (service,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, service, key, notes, created_at, updated_at "
            "FROM credentials ORDER BY service ASC, key ASC"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Task History & Frequencies ─────────────────────────────────────

def log_task(task_num: str, task_name: str, notes: str = '', reasoning: str = '') -> dict:
    """Log a task execution with optional reasoning."""
    conn = get_db()
    now = now_epoch()
    cursor = conn.execute(
        "INSERT INTO task_history (task_num, task_name, executed_at, notes, reasoning) "
        "VALUES (?, ?, ?, ?, ?)",
        (task_num, task_name, now, notes, reasoning)
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM task_history WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return dict(row)


def list_task_history(task_num: str = None, days: int = 30) -> list[dict]:
    """List task execution history, optionally filtered by task_num."""
    conn = get_db()
    cutoff = now_epoch() - (days * 86400)
    if task_num:
        rows = conn.execute(
            "SELECT * FROM task_history WHERE task_num = ? AND executed_at >= ? "
            "ORDER BY executed_at DESC",
            (task_num, cutoff)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM task_history WHERE executed_at >= ? "
            "ORDER BY executed_at DESC",
            (cutoff,)
        ).fetchall()
    return [dict(r) for r in rows]


def set_task_frequency(task_num: str, task_name: str,
                       frequency_days: int, description: str = '') -> dict:
    """Set or update the expected frequency for a task."""
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO task_frequencies (task_num, task_name, frequency_days, description) "
        "VALUES (?, ?, ?, ?)",
        (task_num, task_name, frequency_days, description)
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM task_frequencies WHERE task_num = ?", (task_num,)
    ).fetchone()
    return dict(row)


def get_overdue_tasks() -> list[dict]:
    """Get tasks where last execution exceeds the configured frequency."""
    conn = get_db()
    freqs = conn.execute("SELECT * FROM task_frequencies").fetchall()
    now = now_epoch()
    overdue = []
    for f in freqs:
        last = conn.execute(
            "SELECT MAX(executed_at) as last_exec FROM task_history WHERE task_num = ?",
            (f["task_num"],)
        ).fetchone()
        last_exec = last["last_exec"] if last and last["last_exec"] else None
        threshold = f["frequency_days"] * 86400
        if last_exec is None or (now - last_exec) > threshold:
            days_ago = round((now - last_exec) / 86400, 1) if last_exec else None
            overdue.append({
                "task_num": f["task_num"],
                "task_name": f["task_name"],
                "frequency_days": f["frequency_days"],
                "last_executed": last_exec,
                "days_since_last": days_ago,
                "description": f["description"]
            })
    return overdue


def get_task_frequencies() -> list[dict]:
    """Get all configured task frequencies."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM task_frequencies ORDER BY task_num ASC"
    ).fetchall()
    return [dict(r) for r in rows]


# ── Entities ──────────────────────────────────────────────────────

def create_entity(name: str, type: str, value: str, notes: str = "") -> int:
    """Create a new entity. Returns the entity ID."""
    conn = get_db()
    now = time.time()
    cursor = conn.execute(
        "INSERT INTO entities (name, type, value, notes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, type, value, notes, now, now)
    )
    conn.commit()
    eid = cursor.lastrowid
    fts_upsert("entity", str(eid), name, f"{name} {value} {notes}", type or "general", commit=False)
    return eid


def search_entities(query: str, type: str = "") -> list[dict]:
    """Search entities by name or value. Multi-word AND search."""
    conn = get_db()
    frag, params = _multi_word_like(query, ["name", "value"])
    if type:
        where = f"type = ? AND ({frag})"
        params.insert(0, type)
    else:
        where = frag
    rows = conn.execute(
        f"SELECT * FROM entities WHERE {where} ORDER BY updated_at DESC",
        params
    ).fetchall()
    return [dict(r) for r in rows]


def list_entities(type: str = "") -> list[dict]:
    """List all entities, optionally filtered by type."""
    conn = get_db()
    if type:
        rows = conn.execute(
            "SELECT * FROM entities WHERE type = ? ORDER BY name ASC",
            (type,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM entities ORDER BY type ASC, name ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def update_entity(id: int, **kwargs):
    """Update entity fields: name, type, value, notes."""
    conn = get_db()
    allowed = {"name", "type", "value", "notes"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
            return
    updates["updated_at"] = time.time()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [id]
    conn.execute(f"UPDATE entities SET {set_clause} WHERE id = ?", values)
    conn.commit()
    row = conn.execute("SELECT * FROM entities WHERE id = ?", (id,)).fetchone()
    if row:
        r = dict(row)
        fts_upsert("entity", str(id), r.get("name",""), f"{r.get('name','')} {r.get('value','')} {r.get('notes','')}", r.get("type","general"), commit=False)


def delete_entity(id: int) -> bool:
    """Delete an entity. Returns True if deleted, False if not found."""
    conn = get_db()
    result = conn.execute("DELETE FROM entities WHERE id = ?", (id,))
    conn.execute("DELETE FROM unified_search WHERE source = 'entity' AND source_id = ?", (str(id),))
    conn.commit()
    return result.rowcount > 0


# ── Preferences ───────────────────────────────────────────────────

def set_preference(key: str, value: str, category: str = "general"):
    """Set a preference (insert or update)."""
    conn = get_db()
    now = time.time()
    conn.execute(
        "INSERT OR REPLACE INTO preferences (key, value, category, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (key, value, category, now)
    )
    conn.commit()


def get_preference(key: str) -> dict | None:
    """Get a single preference by key."""
    conn = get_db()
    row = conn.execute("SELECT * FROM preferences WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else None


def list_preferences(category: str = "") -> list[dict]:
    """List all preferences, optionally filtered by category."""
    conn = get_db()
    if category:
        rows = conn.execute(
            "SELECT * FROM preferences WHERE category = ? ORDER BY key ASC",
            (category,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM preferences ORDER BY category ASC, key ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_preference(key: str) -> bool:
    """Delete a preference. Returns True if deleted, False if not found."""
    conn = get_db()
    result = conn.execute("DELETE FROM preferences WHERE key = ?", (key,))
    conn.commit()
    return result.rowcount > 0


# ── Agents ────────────────────────────────────────────────────────

def create_agent(id: str, name: str, specialization: str, model: str = "sonnet",
                 tools: str = "", context_files: str = "", rules: str = "") -> dict:
    """Register a new agent. Uses INSERT OR REPLACE to allow re-registration."""
    conn = get_db()
    now = time.time()
    conn.execute(
        "INSERT OR REPLACE INTO agents (id, name, specialization, model, tools, context_files, rules, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (id, name, specialization, model, tools, context_files, rules, now, now)
    )
    conn.commit()
    return {"id": id, "name": name}


def get_agent(id: str) -> dict | None:
    """Get an agent by ID."""
    conn = get_db()
    row = conn.execute("SELECT * FROM agents WHERE id = ?", (id,)).fetchone()
    return dict(row) if row else None


def list_agents() -> list[dict]:
    """List all registered agents."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM agents ORDER BY name ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def update_agent(id: str, **kwargs):
    """Update agent fields: name, specialization, model, tools, context_files, rules."""
    conn = get_db()
    allowed = {"name", "specialization", "model", "tools", "context_files", "rules"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
            return
    updates["updated_at"] = time.time()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [id]
    conn.execute(f"UPDATE agents SET {set_clause} WHERE id = ?", values)
    conn.commit()


def delete_agent(id: str) -> bool:
    """Delete an agent. Returns True if deleted, False if not found."""
    conn = get_db()
    result = conn.execute("DELETE FROM agents WHERE id = ?", (id,))
    conn.commit()
    return result.rowcount > 0


# ── Change Log ───────────────────────────────────────────────────

def cleanup_old_changes(retention_days: int = 90) -> int:
    """Delete change_log entries older than retention_days. Returns count deleted."""
    conn = get_db()
    # Get IDs before deleting so we can clean FTS
    ids = [str(r[0]) for r in conn.execute(
        "SELECT id FROM change_log WHERE created_at < datetime('now', ?)",
        (f"-{retention_days} days",)
    ).fetchall()]
    cursor = conn.execute(
        "DELETE FROM change_log WHERE created_at < datetime('now', ?)",
        (f"-{retention_days} days",)
    )
    for cid in ids:
        conn.execute("DELETE FROM unified_search WHERE source = 'change' AND source_id = ?", (cid,))
    conn.commit()
    return cursor.rowcount


def log_change(session_id: str, files: str, what_changed: str, why: str,
               triggered_by: str = '', affects: str = '', risks: str = '',
               verify: str = '', commit_ref: str = '') -> dict:
    """Log a code/config change with full context."""
    conn = get_db()
    cleanup_old_changes()
    try:
        cursor = conn.execute(
            "INSERT INTO change_log (session_id, files, what_changed, why, triggered_by, affects, risks, verify, commit_ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, files, what_changed, why, triggered_by, affects, risks, verify, commit_ref)
        )
        conn.commit()
        cid = cursor.lastrowid
        body = f"{what_changed} {why} {triggered_by} {affects} {risks}"
        fts_upsert("change", str(cid), files, body, "change_log", commit=False)
        row = conn.execute("SELECT * FROM change_log WHERE id = ?", (cid,)).fetchone()
        return dict(row)
    except Exception as e:
        return {"error": str(e)}


def search_changes(query: str = '', files: str = '', days: int = 30) -> list[dict]:
    """Search change log by text and/or file path."""
    conn = get_db()
    days = max(1, int(days))
    conditions = []
    params = []
    if query:
        frag, qparams = _multi_word_like(query, ["what_changed", "why", "affects", "triggered_by"])
        conditions.append(f"({frag})")
        params.extend(qparams)
    if files:
        frag_f, fparams = _multi_word_like(files, ["files"])
        conditions.append(f"({frag_f})")
        params.extend(fparams)
    conditions.append("created_at >= datetime('now', ?)")
    params.append(f"-{days} days")
    where = " AND ".join(conditions)
    rows = conn.execute(
        f"SELECT * FROM change_log WHERE {where} ORDER BY created_at DESC",
        params
    ).fetchall()
    return [dict(r) for r in rows]


def update_change_commit(id: int, commit_ref: str) -> dict:
    """Link a change log entry to its git commit after commit."""
    conn = get_db()
    row = conn.execute("SELECT * FROM change_log WHERE id = ?", (id,)).fetchone()
    if not row:
        return {"error": f"Change {id} not found"}
    conn.execute("UPDATE change_log SET commit_ref = ? WHERE id = ?", (commit_ref, id))
    conn.commit()
    row = conn.execute("SELECT * FROM change_log WHERE id = ?", (id,)).fetchone()
    r = dict(row)
    body = f"{r.get('what_changed','')} {r.get('why','')} {r.get('triggered_by','')} {r.get('affects','')} {r.get('risks','')}"
    fts_upsert("change", str(id), r.get("files",""), body, "change_log", commit=False)
    return r


# ── Decisions (episodic memory) ──────────────────────────────────

def cleanup_old_decisions(retention_days: int = 90) -> int:
    """Delete decisions entries older than retention_days. Returns count deleted."""
    conn = get_db()
    ids = [str(r[0]) for r in conn.execute(
        "SELECT id FROM decisions WHERE created_at < datetime('now', ?)",
        (f"-{retention_days} days",)
    ).fetchall()]
    cursor = conn.execute(
        "DELETE FROM decisions WHERE created_at < datetime('now', ?)",
        (f"-{retention_days} days",)
    )
    for did in ids:
        conn.execute("DELETE FROM unified_search WHERE source = 'decision' AND source_id = ?", (did,))
    conn.commit()
    return cursor.rowcount


def log_decision(session_id: str, domain: str, decision: str,
                 alternatives: str = '', based_on: str = '',
                 confidence: str = 'medium', context_ref: str = '',
                 status: str = 'pending_review',
                 review_due_at: str | None = None) -> dict:
    """Log a decision with reasoning context."""
    conn = get_db()
    cleanup_old_decisions()
    try:
        cursor = conn.execute(
            "INSERT INTO decisions "
            "(session_id, domain, decision, alternatives, based_on, confidence, context_ref, status, review_due_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id, domain, decision, alternatives, based_on,
                confidence, context_ref, status, review_due_at,
            )
        )
        conn.commit()
        did = cursor.lastrowid
        body = f"{decision} {alternatives} {based_on}"
        fts_upsert("decision", str(did), decision[:200], body, domain or '', commit=False)
        row = conn.execute("SELECT * FROM decisions WHERE id = ?", (did,)).fetchone()
        return dict(row)
    except Exception as e:
        return {"error": str(e)}


def update_decision_outcome(id: int, outcome: str) -> dict:
    """Record the outcome of a past decision."""
    conn = get_db()
    row = conn.execute("SELECT * FROM decisions WHERE id = ?", (id,)).fetchone()
    if not row:
        return {"error": f"Decision {id} not found"}
    conn.execute(
        "UPDATE decisions "
        "SET outcome = ?, outcome_at = datetime('now'), status = 'reviewed', "
        "review_due_at = NULL, last_reviewed_at = datetime('now') "
        "WHERE id = ?",
        (outcome, id)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM decisions WHERE id = ?", (id,)).fetchone()
    r = dict(row)
    body = f"{r.get('decision','')} {r.get('alternatives','')} {r.get('based_on','')} {r.get('outcome','')}"
    fts_upsert("decision", str(id), r.get("decision","")[:200], body, r.get("domain",""), commit=False)
    return r


def get_memory_review_queue(days: int = 7) -> dict:
    """Return learnings and decisions whose review date falls within N days."""
    conn = get_db()
    learning_cutoff = now_epoch() + (days * 86400)
    learnings = conn.execute(
        "SELECT * FROM learnings "
        "WHERE review_due_at IS NOT NULL AND review_due_at <= ? "
        "ORDER BY review_due_at ASC, updated_at DESC",
        (learning_cutoff,)
    ).fetchall()
    decisions = conn.execute(
        "SELECT * FROM decisions "
        "WHERE review_due_at IS NOT NULL AND review_due_at <= datetime('now', ?) "
        "ORDER BY review_due_at ASC, created_at DESC",
        (f"+{days} days",)
    ).fetchall()
    return {
        "learnings": [dict(r) for r in learnings],
        "decisions": [dict(r) for r in decisions],
    }


def find_decisions_by_context_ref(ref: str) -> list[dict]:
    """Find decisions linked to a specific context_ref (e.g., followup ID)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM decisions WHERE context_ref = ? AND (outcome IS NULL OR outcome = '')",
        (ref,)
    ).fetchall()
    return [dict(r) for r in rows]


def search_decisions(query: str = '', domain: str = '', days: int = 30) -> list[dict]:
    """Search decisions by text and/or domain within a time window."""
    conn = get_db()
    days = max(1, int(days))
    conditions = []
    params = []
    if query:
        frag, qparams = _multi_word_like(query, ["decision", "alternatives", "based_on", "outcome"])
        conditions.append(f"({frag})")
        params.extend(qparams)
    if domain:
        conditions.append("domain = ?")
        params.append(domain)
    conditions.append("created_at >= datetime('now', ?)")
    params.append(f"-{days} days")

    where = " AND ".join(conditions)
    rows = conn.execute(
        f"SELECT * FROM decisions WHERE {where} ORDER BY created_at DESC",
        params
    ).fetchall()
    return [dict(r) for r in rows]


# ── Session Diary ────────────────────────────────────────────────

def cleanup_old_diaries(retention_days: int = 180) -> int:
    """Archive then delete session_diary entries older than retention_days.

    Diaries are moved to diary_archive (permanent) before being removed from
    the active session_diary table. Nothing is ever truly lost.
    """
    conn = get_db()
    cutoff = f"-{retention_days} days"

    # Archive before deleting — permanent subconscious memory
    try:
        conn.execute("""
            INSERT OR IGNORE INTO diary_archive
                (id, session_id, created_at, decisions, discarded, pending,
                 context_next, summary, mental_state, domain, user_signals,
                 self_critique, source)
            SELECT id, session_id, created_at, decisions, discarded, pending,
                   context_next, summary, mental_state, domain, user_signals,
                   self_critique, source
            FROM session_diary
            WHERE created_at < datetime('now', ?)
        """, (cutoff,))
    except Exception:
        pass  # Table may not exist yet (pre-migration)

    ids = [str(r[0]) for r in conn.execute(
        "SELECT id FROM session_diary WHERE created_at < datetime('now', ?)",
        (cutoff,)
    ).fetchall()]
    cursor = conn.execute(
        "DELETE FROM session_diary WHERE created_at < datetime('now', ?)",
        (cutoff,)
    )
    for did in ids:
        conn.execute("DELETE FROM unified_search WHERE source = 'diary' AND source_id = ?", (did,))
    conn.commit()
    return cursor.rowcount


def write_session_diary(session_id: str, decisions: str, summary: str,
                        discarded: str = '', pending: str = '',
                        context_next: str = '', mental_state: str = '',
                        domain: str = '', user_signals: str = '',
                        self_critique: str = '', source: str = 'claude') -> dict:
    """Write a session diary entry with mental state and self-critique for continuity."""
    conn = get_db()
    cleanup_old_diaries()
    cursor = conn.execute(
        "INSERT INTO session_diary (session_id, decisions, discarded, pending, context_next, mental_state, summary, domain, user_signals, self_critique, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, decisions, discarded, pending, context_next, mental_state, summary, domain, user_signals, self_critique, source)
    )
    conn.commit()
    did = cursor.lastrowid
    body = f"{summary} {decisions} {pending} {context_next} {mental_state} {self_critique}"
    fts_upsert("diary", str(did), (summary or '')[:200], body, domain or "general", commit=False)
    row = conn.execute("SELECT * FROM session_diary WHERE id = ?", (did,)).fetchone()
    return dict(row)


def check_session_has_diary(session_id: str) -> bool:
    """Return True if this session already has a diary entry."""
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM session_diary WHERE session_id = ? LIMIT 1",
        (session_id,)
    ).fetchone()
    return row is not None


# ── Diary Archive (permanent subconscious) ──────────────────────


def diary_archive_search(query: str = '', domain: str = '',
                         year: int = 0, month: int = 0,
                         limit: int = 20) -> list[dict]:
    """Search the permanent diary archive. Supports text search, domain filter, and date filter.

    Args:
        query: Text to search in summary, decisions, mental_state, pending
        domain: Filter by domain (e.g. 'my-project', 'my-store')
        year: Filter by year (e.g. 2026)
        month: Filter by month (1-12), requires year
        limit: Max results (default 20)
    """
    conn = get_db()
    try:
        conn.execute("SELECT 1 FROM diary_archive LIMIT 1")
    except Exception:
        return []  # Table doesn't exist yet

    conditions = []
    params = []

    if query:
        words = query.strip().split()
        for word in words:
            conditions.append(
                "(summary LIKE ? OR decisions LIKE ? OR mental_state LIKE ? "
                "OR pending LIKE ? OR self_critique LIKE ?)"
            )
            w = f"%{word}%"
            params.extend([w, w, w, w, w])

    if domain:
        conditions.append("domain = ?")
        params.append(domain)

    if year:
        if month:
            date_start = f"{year:04d}-{month:02d}-01"
            if month == 12:
                date_end = f"{year + 1:04d}-01-01"
            else:
                date_end = f"{year:04d}-{month + 1:02d}-01"
            conditions.append("created_at >= ? AND created_at < ?")
            params.extend([date_start, date_end])
        else:
            conditions.append("created_at >= ? AND created_at < ?")
            params.extend([f"{year:04d}-01-01", f"{year + 1:04d}-01-01"])

    where = " AND ".join(conditions) if conditions else "1=1"

    rows = conn.execute(f"""
        SELECT id, session_id, created_at, summary, decisions, domain,
               mental_state, pending, self_critique, source
        FROM diary_archive
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ?
    """, params + [limit]).fetchall()
    return [dict(r) for r in rows]


def diary_archive_read(diary_id: int) -> dict | None:
    """Read a single archived diary entry by ID — full content."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM diary_archive WHERE id = ?", (diary_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def diary_archive_stats() -> dict:
    """Get archive statistics: count, date range, domains."""
    conn = get_db()
    try:
        count = conn.execute("SELECT COUNT(*) FROM diary_archive").fetchone()[0]
        if count == 0:
            return {"count": 0, "oldest": None, "newest": None, "domains": []}
        oldest = conn.execute("SELECT MIN(created_at) FROM diary_archive").fetchone()[0]
        newest = conn.execute("SELECT MAX(created_at) FROM diary_archive").fetchone()[0]
        domains = [r[0] for r in conn.execute(
            "SELECT DISTINCT domain FROM diary_archive WHERE domain IS NOT NULL AND domain != '' ORDER BY domain"
        ).fetchall()]
        return {"count": count, "oldest": oldest, "newest": newest, "domains": domains}
    except Exception:
        return {"count": 0, "oldest": None, "newest": None, "domains": []}


# ── Session Diary Drafts ─────────────────────────────────────────


def upsert_diary_draft(sid: str, tasks_seen: str, change_ids: str,
                       decision_ids: str, last_context_hint: str,
                       heartbeat_count: int, summary_draft: str = '') -> dict:
    """UPSERT diary draft for a session. Called by heartbeat to accumulate context."""
    conn = get_db()
    conn.execute(
        """INSERT INTO session_diary_draft
           (sid, summary_draft, tasks_seen, change_ids, decision_ids,
            last_context_hint, heartbeat_count, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(sid) DO UPDATE SET
             summary_draft = excluded.summary_draft,
             tasks_seen = excluded.tasks_seen,
             change_ids = excluded.change_ids,
             decision_ids = excluded.decision_ids,
             last_context_hint = excluded.last_context_hint,
             heartbeat_count = excluded.heartbeat_count,
             updated_at = datetime('now')""",
        (sid, summary_draft, tasks_seen, change_ids, decision_ids,
         last_context_hint, heartbeat_count)
    )
    conn.commit()
    return {"sid": sid, "heartbeat_count": heartbeat_count}


def get_diary_draft(sid: str) -> dict | None:
    """Get diary draft for a session, or None."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM session_diary_draft WHERE sid = ?", (sid,)
    ).fetchone()
    return dict(row) if row else None


def delete_diary_draft(sid: str):
    """Delete diary draft after real diary is written."""
    conn = get_db()
    conn.execute("DELETE FROM session_diary_draft WHERE sid = ?", (sid,))
    conn.commit()


def get_orphan_sessions(ttl_seconds: int = 900) -> list[dict]:
    """Get sessions that exceeded TTL and have no diary."""
    conn = get_db()
    cutoff = now_epoch() - ttl_seconds
    rows = conn.execute(
        """SELECT s.sid, s.task, s.started_epoch, s.last_update_epoch
           FROM sessions s
           LEFT JOIN session_diary sd ON sd.session_id = s.sid
           WHERE s.last_update_epoch <= ? AND sd.id IS NULL""",
        (cutoff,)
    ).fetchall()
    return [dict(r) for r in rows]


def read_session_diary(session_id: str = '', last_n: int = 3, last_day: bool = False,
                       domain: str = '') -> list[dict]:
    """Read session diary entries.

    - session_id: returns entries for that specific session
    - last_day: returns ALL entries from the most recent day (multi-terminal aware)
    - last_n: returns last N entries (default)
    - domain: filter by project context (nexo, other)
    """
    conn = get_db()
    domain_clause = " AND domain = ?" if domain else ""
    domain_params = (domain,) if domain else ()

    if session_id:
        rows = conn.execute(
            f"SELECT * FROM session_diary WHERE session_id = ?{domain_clause} ORDER BY created_at DESC",
            (session_id,) + domain_params
        ).fetchall()
    elif last_day:
        # Get all entries from the most recent calendar day
        if domain:
            latest = conn.execute(
                "SELECT date(created_at) as day FROM session_diary WHERE domain = ? ORDER BY created_at DESC LIMIT 1",
                (domain,)
            ).fetchone()
        else:
            latest = conn.execute(
                "SELECT date(created_at) as day FROM session_diary ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if not latest:
            return []
        rows = conn.execute(
            f"SELECT * FROM session_diary WHERE date(created_at) = ?{domain_clause} ORDER BY created_at DESC",
            (latest['day'],) + domain_params
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM session_diary WHERE 1=1{domain_clause} ORDER BY created_at DESC LIMIT ?",
            domain_params + (last_n,)
        ).fetchall()
    return [dict(r) for r in rows]


def _multi_word_like(query: str, columns: list[str]) -> tuple[str, list]:
    """Build AND-ed LIKE conditions: every word must appear in at least one of the columns.

    Returns (sql_fragment, params) ready for WHERE clause.
    Example: query="cron learn", columns=["title","content"]
    → "(title LIKE ? OR content LIKE ?) AND (title LIKE ? OR content LIKE ?)"
    with params ["%cron%","%cron%","%learn%","%learn%"]
    """
    words = query.strip().split()
    if not words:
        return "1=1", []
    word_conditions = []
    params = []
    for word in words:
        pattern = f"%{word}%"
        col_or = " OR ".join(f"{c} LIKE ?" for c in columns)
        word_conditions.append(f"({col_or})")
        params.extend([pattern] * len(columns))
    return " AND ".join(word_conditions), params


def recall(query: str, days: int = 30) -> list[dict]:
    """Cross-search ALL memory using FTS5: learnings, decisions, changes, diary, followups, entities, .md files.

    Returns up to 20 results ranked by relevance (FTS5 bm25).
    Falls back to LIKE-based search if FTS fails.
    """
    # Try FTS5 first (fast, ranked), then filter by days
    results = fts_search(query, limit=40)  # fetch extra to allow filtering
    if results:
        cutoff_epoch = now_epoch() - (days * 86400)
        filtered = []
        for r in results:
            ua = str(r.get('updated_at', ''))
            if not ua:
                filtered.append(r)
                continue
            # Normalize to epoch for comparison
            try:
                if ua[0].isdigit() and ('.' in ua or len(ua) > 12):
                    # Could be epoch float or ISO date
                    if '-' in ua[:5]:
                        # ISO datetime like "2026-03-13 16:17:40"
                        dt = datetime.datetime.fromisoformat(ua.replace(' ', 'T'))
                        ts = dt.timestamp()
                    else:
                        ts = float(ua)
                else:
                    ts = float(ua)
                if ts >= cutoff_epoch:
                    filtered.append(r)
            except (ValueError, TypeError):
                filtered.append(r)  # keep if can't parse
        if filtered:
            return filtered[:20]

    # Fallback to old LIKE-based search
    days = max(1, int(days))
    conn = get_db()
    cutoff_dt = datetime.datetime.now() - datetime.timedelta(days=days)
    cutoff_str = cutoff_dt.strftime("%Y-%m-%d")
    cutoff_epoch = now_epoch() - (days * 86400)

    results = []

    frag, params = _multi_word_like(query, ["files", "what_changed", "why", "triggered_by", "affects", "risks"])
    rows = conn.execute(f"""
        SELECT id, created_at, 'change' AS source,
               files AS title,
               (what_changed || ' | ' || why) AS snippet, 'change_log' AS category, 0 AS rank
        FROM change_log
        WHERE created_at >= ? AND ({frag})
        ORDER BY created_at DESC LIMIT 20
    """, [cutoff_str] + params).fetchall()
    results.extend([dict(r) for r in rows])

    frag, params = _multi_word_like(query, ["decision", "alternatives", "based_on", "outcome"])
    rows = conn.execute(f"""
        SELECT id, created_at, 'decision' AS source,
               decision AS title,
               (COALESCE(based_on,'') || ' | ' || COALESCE(alternatives,'')) AS snippet, domain AS category, 0 AS rank
        FROM decisions
        WHERE created_at >= ? AND ({frag})
        ORDER BY created_at DESC LIMIT 20
    """, [cutoff_str] + params).fetchall()
    results.extend([dict(r) for r in rows])

    frag, params = _multi_word_like(query, ["title", "content", "reasoning"])
    rows = conn.execute(f"""
        SELECT id, datetime(created_at, 'unixepoch') AS created_at, 'learning' AS source,
               title,
               (COALESCE(content,'') || ' | ' || COALESCE(reasoning,'')) AS snippet, category, 0 AS rank
        FROM learnings
        WHERE created_at >= ? AND ({frag})
        ORDER BY created_at DESC LIMIT 20
    """, [cutoff_epoch] + params).fetchall()
    results.extend([dict(r) for r in rows])

    frag, params = _multi_word_like(query, ["id", "description", "verification", "reasoning"])
    rows = conn.execute(f"""
        SELECT id, datetime(created_at, 'unixepoch') AS created_at, 'followup' AS source,
               id AS title,
               (COALESCE(description,'') || ' | ' || COALESCE(verification,'') || ' | ' || COALESCE(reasoning,'')) AS snippet,
               'followup' AS category, 0 AS rank
        FROM followups
        WHERE created_at >= ? AND ({frag})
        ORDER BY created_at DESC LIMIT 20
    """, [cutoff_epoch] + params).fetchall()
    results.extend([dict(r) for r in rows])

    frag, params = _multi_word_like(query, ["decisions", "discarded", "pending", "context_next", "mental_state", "summary"])
    rows = conn.execute(f"""
        SELECT id, created_at, 'diary' AS source,
               summary AS title,
               (COALESCE(decisions,'') || ' | ' || COALESCE(pending,'') || ' | ' || COALESCE(context_next,'')) AS snippet,
               COALESCE(domain, 'general') AS category, 0 AS rank
        FROM session_diary
        WHERE created_at >= ? AND ({frag})
        ORDER BY created_at DESC LIMIT 20
    """, [cutoff_str] + params).fetchall()
    results.extend([dict(r) for r in rows])

    results.sort(key=lambda r: r.get('created_at', ''), reverse=True)
    return results[:20]


# ── Evolution helpers ─────────────────────────────────────────────────────

def insert_evolution_metric(dimension: str, score: int, evidence: str, delta: int = 0):
    conn = get_db()
    conn.execute(
        "INSERT INTO evolution_metrics (dimension, score, evidence, delta) VALUES (?, ?, ?, ?)",
        (dimension, score, evidence, delta)
    )


def get_latest_metrics() -> dict:
    conn = get_db()
    rows = conn.execute(
        "SELECT dimension, score, delta, measured_at FROM evolution_metrics "
        "WHERE id IN (SELECT MAX(id) FROM evolution_metrics GROUP BY dimension)"
    ).fetchall()
    return {r["dimension"]: dict(r) for r in rows}


def insert_evolution_log(cycle_number: int, dimension: str, proposal: str,
                         classification: str, reasoning: str, **kwargs) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO evolution_log (cycle_number, dimension, proposal, classification, reasoning, "
        "files_changed, snapshot_ref, test_result, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cycle_number, dimension, proposal, classification, reasoning,
         kwargs.get("files_changed"), kwargs.get("snapshot_ref"),
         kwargs.get("test_result"), kwargs.get("status", "pending"))
    )
    return cur.lastrowid


def get_evolution_history(limit: int = 20) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM evolution_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def update_evolution_log_status(log_id: int, status: str, **kwargs):
    conn = get_db()
    sets = ["status = ?"]
    vals = [status]
    for k in ("test_result", "impact", "files_changed", "snapshot_ref"):
        if k in kwargs:
            sets.append(f"{k} = ?")
            vals.append(kwargs[k])
    vals.append(log_id)
    conn.execute(f"UPDATE evolution_log SET {', '.join(sets)} WHERE id = ?", vals)


# ── Session Checkpoint operations ──────────────────────────────────

def save_checkpoint(sid: str, task: str = '', task_status: str = 'active',
                    active_files: str = '[]', current_goal: str = '',
                    decisions_summary: str = '', errors_found: str = '',
                    reasoning_thread: str = '', next_step: str = '') -> dict:
    """Save or update a session checkpoint. Called by PreCompact hook."""
    conn = get_db()
    # Get current compaction count
    existing = conn.execute(
        "SELECT compaction_count FROM session_checkpoints WHERE sid = ?", (sid,)
    ).fetchone()
    count = (existing["compaction_count"] + 1) if existing else 0

    conn.execute(
        """INSERT INTO session_checkpoints
           (sid, task, task_status, active_files, current_goal,
            decisions_summary, errors_found, reasoning_thread, next_step,
            compaction_count, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(sid) DO UPDATE SET
             task = excluded.task,
             task_status = excluded.task_status,
             active_files = excluded.active_files,
             current_goal = excluded.current_goal,
             decisions_summary = excluded.decisions_summary,
             errors_found = excluded.errors_found,
             reasoning_thread = excluded.reasoning_thread,
             next_step = excluded.next_step,
             compaction_count = excluded.compaction_count,
             updated_at = datetime('now')""",
        (sid, task, task_status, active_files, current_goal,
         decisions_summary, errors_found, reasoning_thread, next_step, count)
    )
    conn.commit()
    return {"sid": sid, "compaction_count": count}


def read_checkpoint(sid: str = '') -> dict | None:
    """Read the most recent session checkpoint. If no sid, returns the latest."""
    conn = get_db()
    if sid:
        row = conn.execute(
            "SELECT * FROM session_checkpoints WHERE sid = ?", (sid,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM session_checkpoints ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def increment_compaction_count(sid: str) -> int:
    """Increment and return the compaction count for a session."""
    conn = get_db()
    conn.execute(
        """UPDATE session_checkpoints
           SET compaction_count = compaction_count + 1, updated_at = datetime('now')
           WHERE sid = ?""",
        (sid,)
    )
    conn.commit()
    row = conn.execute(
        "SELECT compaction_count FROM session_checkpoints WHERE sid = ?", (sid,)
    ).fetchone()
    return row["compaction_count"] if row else 0
