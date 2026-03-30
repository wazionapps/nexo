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
    from db._schema import run_migrations
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
                from db._fts import rebuild_fts_index, _refresh_fts_files
                if row[0] == 0:
                    rebuild_fts_index(bg_conn)
                else:
                    _refresh_fts_files(bg_conn)
                bg_conn.close()
            except Exception:
                pass

        threading.Thread(target=_bg_fts, daemon=True).start()



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


def _multi_word_like(query: str, columns: list[str]) -> tuple[str, list]:
    """Build AND-ed LIKE conditions: every word must appear in at least one column."""
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

