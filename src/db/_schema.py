"""NEXO DB — Schema module."""
from db._core import get_db
from db._fts import _migrate_add_column, _migrate_add_index

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


def _m13_claude_session_id(conn):
    """Add claude_session_id to sessions for inter-terminal coordination (D+)."""
    _migrate_add_column(conn, "sessions", "claude_session_id", "TEXT DEFAULT ''")
    _migrate_add_index(conn, "idx_sessions_claude_sid", "sessions", "claude_session_id")
    conn.commit()


def _m14_learnings_priority_weight(conn):
    """Add priority, weight, and guard usage tracking to learnings + followup priority."""
    _migrate_add_column(conn, "learnings", "priority", "TEXT DEFAULT 'medium'")
    _migrate_add_column(conn, "learnings", "weight", "REAL DEFAULT 0.5")
    _migrate_add_column(conn, "learnings", "guard_hits", "INTEGER DEFAULT 0")
    _migrate_add_column(conn, "learnings", "last_guard_hit_at", "REAL")
    _migrate_add_column(conn, "followups", "priority", "TEXT DEFAULT 'medium'")


def _m15_core_rules_tables(conn):
    """Core rules and version tracking tables for the core_rules plugin."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS core_rules (
            id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            rule TEXT NOT NULL,
            why TEXT NOT NULL,
            importance INTEGER NOT NULL DEFAULT 3,
            type TEXT NOT NULL DEFAULT 'advisory',
            added_in TEXT DEFAULT '',
            removed_in TEXT DEFAULT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS core_rules_version (
            id INTEGER PRIMARY KEY,
            version TEXT NOT NULL DEFAULT '0.0.0',
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Seed the version row so UPDATE statements in the plugin always find it
    conn.execute(
        "INSERT OR IGNORE INTO core_rules_version (id, version) VALUES (1, '0.0.0')"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_core_rules_category ON core_rules(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_core_rules_active ON core_rules(is_active)")


def _m16_skills_tables(conn):
    """Skill Auto-Creation system — reusable procedures extracted from complex tasks.

    Skills are procedural knowledge (step-by-step how-tos) vs learnings which are
    declarative (don't do X). Pipeline: trace → draft → published, fully autonomous.
    Trust score with decay controls quality without human approval gates.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            level TEXT NOT NULL DEFAULT 'trace',
            trust_score INTEGER NOT NULL DEFAULT 50,
            file_path TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            trigger_patterns TEXT DEFAULT '[]',
            source_sessions TEXT DEFAULT '[]',
            linked_learnings TEXT DEFAULT '[]',
            use_count INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            fail_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            last_used_at TEXT DEFAULT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skill_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
            session_id TEXT DEFAULT '',
            success INTEGER NOT NULL DEFAULT 1,
            context TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    _migrate_add_index(conn, "idx_skills_level", "skills", "level")
    _migrate_add_index(conn, "idx_skills_trust", "skills", "trust_score")
    _migrate_add_index(conn, "idx_skills_last_used", "skills", "last_used_at")
    _migrate_add_index(conn, "idx_skill_usage_skill_id", "skill_usage", "skill_id")
    _migrate_add_index(conn, "idx_skill_usage_created", "skill_usage", "created_at")


# Migration registry — APPEND ONLY, never reorder or delete
def _m17_cron_runs(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cron_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cron_id TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            ended_at TEXT,
            exit_code INTEGER,
            summary TEXT DEFAULT '',
            error TEXT DEFAULT '',
            duration_secs REAL
        )
    """)
    _migrate_add_index(conn, "idx_cron_runs_cron_id", "cron_runs", "cron_id")
    _migrate_add_index(conn, "idx_cron_runs_started", "cron_runs", "started_at")


def _m18_skills_steps(conn):
    # content: the full procedure — markdown with steps, gotchas, notes.
    # Can also reference a script file via file_path column.
    _migrate_add_column(conn, "skills", "content", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "skills", "steps", "TEXT DEFAULT '[]'")
    _migrate_add_column(conn, "skills", "gotchas", "TEXT DEFAULT '[]'")


def _m19_skills_v2(conn):
    _migrate_add_column(conn, "skills", "mode", "TEXT DEFAULT 'guide'")
    _migrate_add_column(conn, "skills", "source_kind", "TEXT DEFAULT 'personal'")
    _migrate_add_column(conn, "skills", "execution_level", "TEXT DEFAULT 'none'")
    _migrate_add_column(conn, "skills", "approval_required", "INTEGER DEFAULT 0")
    _migrate_add_column(conn, "skills", "approved_at", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "skills", "approved_by", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "skills", "params_schema", "TEXT DEFAULT '{}'")
    _migrate_add_column(conn, "skills", "command_template", "TEXT DEFAULT '{}'")
    _migrate_add_column(conn, "skills", "executable_entry", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "skills", "stable_after_uses", "INTEGER DEFAULT 10")
    _migrate_add_column(conn, "skills", "definition_path", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "skills", "last_reviewed_at", "TEXT DEFAULT NULL")


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
    (13, "claude_session_id", _m13_claude_session_id),
    (14, "learnings_priority_weight", _m14_learnings_priority_weight),
    (15, "core_rules_tables", _m15_core_rules_tables),
    (16, "skills_tables", _m16_skills_tables),
    (17, "cron_runs", _m17_cron_runs),
    (18, "skills_steps_column", _m18_skills_steps),
    (19, "skills_v2", _m19_skills_v2),
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

    failed = []
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
                conn.rollback()
                import sys
                print(f"[MIGRATION] v{version} ({name}) failed: {e}", file=sys.stderr)
                failed.append((version, name, str(e)))
                # Stop on first failure — don't run subsequent migrations
                # against a potentially inconsistent schema
                break

    if failed:
        raise RuntimeError(
            f"Migration failed: v{failed[0][0]} ({failed[0][1]}): {failed[0][2]}"
        )

    return len(MIGRATIONS) - len(applied)


def get_schema_version() -> int:
    """Return the highest applied migration version, or 0 if none."""
    conn = get_db()
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return row[0] or 0
    except Exception:
        return 0

