"""NEXO DB Schema Migrations."""
import sqlite3


def _get_db():
    from db import get_db
    return get_db()


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


def _m11_artifact_registry(conn):
    """Artifact Registry — structured index of things NEXO creates/deploys.

    Solves 'recent work amnesia': services, dashboards, scripts, APIs that
    NEXO builds but can't find hours later because semantic search fails on
    operational vocabulary mismatches (e.g., 'backend' vs 'FastAPI localhost:6174').

    Design informed by 3-way AI debate (GPT-5.4 + Gemini 3.1 Pro + Claude Opus 4.6).
    Key insight: operational facts need first-class structured storage, not just
    vector embeddings buried in prose diaries.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS artifact_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            aliases TEXT DEFAULT '[]',
            description TEXT DEFAULT '',
            uri TEXT DEFAULT '',
            ports TEXT DEFAULT '[]',
            paths TEXT DEFAULT '[]',
            run_cmd TEXT DEFAULT '',
            repo TEXT DEFAULT '',
            domain TEXT DEFAULT '',
            state TEXT DEFAULT 'active',
            session_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            last_touched_at TEXT DEFAULT (datetime('now')),
            last_verified_at TEXT DEFAULT NULL,
            metadata TEXT DEFAULT '{}'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS artifact_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artifact_id INTEGER NOT NULL REFERENCES artifact_registry(id) ON DELETE CASCADE,
            phrase TEXT NOT NULL,
            source TEXT DEFAULT 'manual',
            confidence REAL DEFAULT 1.0,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(artifact_id, phrase)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_artifact_state ON artifact_registry(state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_artifact_kind ON artifact_registry(kind)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_artifact_domain ON artifact_registry(domain)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_artifact_last_touched ON artifact_registry(last_touched_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_artifact_aliases_phrase ON artifact_aliases(phrase)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_artifact_aliases_aid ON artifact_aliases(artifact_id)")


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
    (11, "artifact_registry", _m11_artifact_registry),
    (12, "session_checkpoints", _m12_session_checkpoints),
]


def run_migrations(conn=None):
    """Run pending migrations. Tracks applied versions in schema_migrations.

    Safe to call multiple times — skips already-applied migrations.
    Called automatically by init_db() on every server start.
    """
    if conn is None:
        conn = _get_db()

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
    conn = _get_db()
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return row[0] or 0
    except Exception:
        return 0
