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


def _m21_external_session_fields(conn):
    """Generalize session linkage beyond Claude-specific naming."""
    _migrate_add_column(conn, "sessions", "external_session_id", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "sessions", "session_client", "TEXT DEFAULT ''")
    _migrate_add_index(conn, "idx_sessions_external_sid", "sessions", "external_session_id")
    _migrate_add_index(conn, "idx_sessions_client", "sessions", "session_client")
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


def _m20_personal_scripts_registry(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS personal_scripts (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            runtime TEXT DEFAULT 'unknown',
            metadata_json TEXT DEFAULT '{}',
            created_by TEXT DEFAULT 'manual',
            source TEXT DEFAULT 'filesystem',
            enabled INTEGER NOT NULL DEFAULT 1,
            has_inline_metadata INTEGER NOT NULL DEFAULT 0,
            last_run_at TEXT DEFAULT NULL,
            last_exit_code INTEGER DEFAULT NULL,
            last_synced_at TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS personal_script_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            script_id TEXT NOT NULL REFERENCES personal_scripts(id) ON DELETE CASCADE,
            cron_id TEXT NOT NULL UNIQUE,
            schedule_type TEXT DEFAULT '',
            schedule_value TEXT DEFAULT '',
            schedule_label TEXT DEFAULT '',
            launchd_label TEXT DEFAULT '',
            plist_path TEXT DEFAULT '',
            description TEXT DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            last_synced_at TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    _migrate_add_index(conn, "idx_personal_scripts_name", "personal_scripts", "name")
    _migrate_add_index(conn, "idx_personal_scripts_enabled", "personal_scripts", "enabled")
    _migrate_add_index(conn, "idx_personal_script_schedules_script", "personal_script_schedules", "script_id")
    _migrate_add_index(conn, "idx_personal_script_schedules_enabled", "personal_script_schedules", "enabled")


def _m22_protocol_discipline_tables(conn):
    """Protocol discipline runtime: persistent task contracts + debt tracking."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS protocol_tasks (
            task_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            goal TEXT NOT NULL,
            task_type TEXT NOT NULL DEFAULT 'answer',
            area TEXT DEFAULT '',
            project_hint TEXT DEFAULT '',
            context_hint TEXT DEFAULT '',
            files TEXT DEFAULT '[]',
            plan TEXT DEFAULT '[]',
            known_facts TEXT DEFAULT '[]',
            unknowns TEXT DEFAULT '[]',
            constraints TEXT DEFAULT '[]',
            evidence_refs TEXT DEFAULT '[]',
            verification_step TEXT DEFAULT '',
            cortex_mode TEXT DEFAULT '',
            cortex_check_id TEXT DEFAULT '',
            cortex_blocked_reason TEXT DEFAULT '',
            cortex_warnings TEXT DEFAULT '[]',
            cortex_rules TEXT DEFAULT '[]',
            opened_with_guard INTEGER NOT NULL DEFAULT 0,
            opened_with_rules INTEGER NOT NULL DEFAULT 0,
            guard_has_blocking INTEGER NOT NULL DEFAULT 0,
            guard_summary TEXT DEFAULT '',
            must_verify INTEGER NOT NULL DEFAULT 0,
            must_change_log INTEGER NOT NULL DEFAULT 0,
            must_learning_if_corrected INTEGER NOT NULL DEFAULT 1,
            must_write_diary_on_close INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open',
            close_evidence TEXT DEFAULT '',
            files_changed TEXT DEFAULT '[]',
            correction_happened INTEGER NOT NULL DEFAULT 0,
            change_log_id INTEGER,
            learning_id INTEGER,
            followup_id TEXT DEFAULT '',
            outcome_notes TEXT DEFAULT '',
            opened_at TEXT DEFAULT (datetime('now')),
            closed_at TEXT DEFAULT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS protocol_debt (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT DEFAULT '',
            task_id TEXT DEFAULT '',
            debt_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'warn',
            status TEXT NOT NULL DEFAULT 'open',
            evidence TEXT DEFAULT '',
            resolution TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            resolved_at TEXT DEFAULT NULL
        )
    """)
    _migrate_add_index(conn, "idx_protocol_tasks_session", "protocol_tasks", "session_id")
    _migrate_add_index(conn, "idx_protocol_tasks_status", "protocol_tasks", "status")
    _migrate_add_index(conn, "idx_protocol_tasks_opened", "protocol_tasks", "opened_at")
    _migrate_add_index(conn, "idx_protocol_debt_session", "protocol_debt", "session_id")
    _migrate_add_index(conn, "idx_protocol_debt_task", "protocol_debt", "task_id")
    _migrate_add_index(conn, "idx_protocol_debt_status", "protocol_debt", "status")
    _migrate_add_index(conn, "idx_protocol_debt_created", "protocol_debt", "created_at")


def _m23_learning_superseded_lifecycle(conn):
    """Track canonical learning replacement instead of leaving rule drift implicit."""
    _migrate_add_column(conn, "learnings", "supersedes_id", "INTEGER")
    _migrate_add_index(conn, "idx_learnings_supersedes", "learnings", "supersedes_id")


def _m24_durable_workflow_runtime(conn):
    """Durable workflow execution runtime for long multi-step tasks."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workflow_runs (
            run_id TEXT PRIMARY KEY,
            session_id TEXT DEFAULT '',
            protocol_task_id TEXT DEFAULT '',
            goal TEXT NOT NULL,
            workflow_kind TEXT DEFAULT 'general',
            status TEXT NOT NULL DEFAULT 'open',
            priority TEXT NOT NULL DEFAULT 'normal',
            idempotency_key TEXT DEFAULT '',
            shared_state TEXT DEFAULT '{}',
            next_action TEXT DEFAULT '',
            current_step_key TEXT DEFAULT '',
            last_checkpoint_label TEXT DEFAULT '',
            owner TEXT DEFAULT '',
            opened_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            closed_at TEXT DEFAULT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workflow_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            step_key TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            step_index INTEGER NOT NULL DEFAULT 999,
            status TEXT NOT NULL DEFAULT 'pending',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 0,
            retry_policy TEXT DEFAULT '',
            retry_after TEXT DEFAULT '',
            human_gate INTEGER NOT NULL DEFAULT 0,
            requires_approval INTEGER NOT NULL DEFAULT 0,
            compensation TEXT DEFAULT '',
            last_summary TEXT DEFAULT '',
            last_evidence TEXT DEFAULT '',
            last_state_patch TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            started_at TEXT DEFAULT NULL,
            completed_at TEXT DEFAULT NULL,
            UNIQUE(run_id, step_key)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workflow_checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            step_key TEXT DEFAULT '',
            checkpoint_label TEXT NOT NULL DEFAULT 'checkpoint',
            run_status TEXT DEFAULT '',
            step_status TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            shared_state TEXT DEFAULT '{}',
            state_patch TEXT DEFAULT '{}',
            evidence TEXT DEFAULT '',
            next_action TEXT DEFAULT '',
            retry_after TEXT DEFAULT '',
            requires_approval INTEGER NOT NULL DEFAULT 0,
            compensation_note TEXT DEFAULT '',
            attempt INTEGER NOT NULL DEFAULT 0,
            actor TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    _migrate_add_index(conn, "idx_workflow_runs_session", "workflow_runs", "session_id")
    _migrate_add_index(conn, "idx_workflow_runs_status", "workflow_runs", "status")
    _migrate_add_index(conn, "idx_workflow_runs_updated", "workflow_runs", "updated_at")
    _migrate_add_index(conn, "idx_workflow_runs_protocol_task", "workflow_runs", "protocol_task_id")
    _migrate_add_index(conn, "idx_workflow_runs_idempotency", "workflow_runs", "idempotency_key")
    _migrate_add_index(conn, "idx_workflow_steps_run", "workflow_steps", "run_id")
    _migrate_add_index(conn, "idx_workflow_steps_status", "workflow_steps", "status")
    _migrate_add_index(conn, "idx_workflow_checkpoints_run", "workflow_checkpoints", "run_id")
    _migrate_add_index(conn, "idx_workflow_checkpoints_created", "workflow_checkpoints", "created_at")


def _m25_workflow_goal_stack(conn):
    """Durable goal stack linked to workflows."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workflow_goals (
            goal_id TEXT PRIMARY KEY,
            session_id TEXT DEFAULT '',
            title TEXT NOT NULL,
            objective TEXT DEFAULT '',
            parent_goal_id TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            priority TEXT NOT NULL DEFAULT 'normal',
            owner TEXT DEFAULT '',
            next_action TEXT DEFAULT '',
            success_signal TEXT DEFAULT '',
            blocker_reason TEXT DEFAULT '',
            shared_state TEXT DEFAULT '{}',
            opened_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            closed_at TEXT DEFAULT NULL
        )
    """)
    _migrate_add_column(conn, "workflow_runs", "goal_id", "TEXT DEFAULT ''")
    _migrate_add_index(conn, "idx_workflow_goals_status", "workflow_goals", "status")
    _migrate_add_index(conn, "idx_workflow_goals_parent", "workflow_goals", "parent_goal_id")
    _migrate_add_index(conn, "idx_workflow_goals_updated", "workflow_goals", "updated_at")
    _migrate_add_index(conn, "idx_workflow_goals_session", "workflow_goals", "session_id")
    _migrate_add_index(conn, "idx_workflow_runs_goal", "workflow_runs", "goal_id")


def _m26_protocol_answer_confidence(conn):
    """Persist answer/analyze response mode so discipline survives the prompt."""
    _migrate_add_column(conn, "protocol_tasks", "response_mode", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "protocol_tasks", "response_confidence", "INTEGER DEFAULT 0")
    _migrate_add_column(conn, "protocol_tasks", "response_reasons", "TEXT DEFAULT '[]'")
    _migrate_add_column(conn, "protocol_tasks", "response_high_stakes", "INTEGER DEFAULT 0")


def _m27_state_watchers(conn):
    """Persistent state watchers for drift, health, and expiry tracking."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS state_watchers (
            watcher_id TEXT PRIMARY KEY,
            watcher_type TEXT NOT NULL,
            title TEXT NOT NULL,
            target TEXT DEFAULT '',
            severity TEXT NOT NULL DEFAULT 'warn',
            status TEXT NOT NULL DEFAULT 'active',
            config TEXT DEFAULT '{}',
            last_health TEXT NOT NULL DEFAULT 'unknown',
            last_result TEXT DEFAULT '{}',
            last_checked_at TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    _migrate_add_index(conn, "idx_state_watchers_type", "state_watchers", "watcher_type")
    _migrate_add_index(conn, "idx_state_watchers_status", "state_watchers", "status")
    _migrate_add_index(conn, "idx_state_watchers_health", "state_watchers", "last_health")


def _m28_automation_runs(conn):
    """Persist automation-backend telemetry for parity, degraded-mode audits, and cost metrics."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS automation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            backend TEXT NOT NULL,
            task_profile TEXT DEFAULT 'default',
            model TEXT DEFAULT '',
            reasoning_effort TEXT DEFAULT '',
            cwd TEXT DEFAULT '',
            output_format TEXT DEFAULT 'text',
            prompt_chars INTEGER DEFAULT 0,
            returncode INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            cached_input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            total_cost_usd REAL,
            telemetry_source TEXT DEFAULT '',
            cost_source TEXT DEFAULT '',
            status TEXT DEFAULT 'ok',
            metadata TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    _migrate_add_index(conn, "idx_automation_runs_backend", "automation_runs", "backend")
    _migrate_add_index(conn, "idx_automation_runs_created", "automation_runs", "created_at")
    _migrate_add_index(conn, "idx_automation_runs_status", "automation_runs", "status")


def _m29_item_history_and_soft_delete(conn):
    """Persist reminder/followup history and read-before-mutate tokens."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS item_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type TEXT NOT NULL,
            item_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            note TEXT DEFAULT '',
            actor TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}',
            created_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS item_read_tokens (
            token TEXT PRIMARY KEY,
            item_type TEXT NOT NULL,
            item_id TEXT NOT NULL,
            history_seq INTEGER DEFAULT 0,
            issued_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
    """)
    _migrate_add_index(conn, "idx_item_history_lookup", "item_history", "item_type, item_id, created_at")
    _migrate_add_index(conn, "idx_item_history_item", "item_history", "item_id")
    _migrate_add_index(conn, "idx_item_read_tokens_lookup", "item_read_tokens", "item_type, item_id, expires_at")


def _m30_hot_context_memory(conn):
    """Persist recent events + hot context for 24h operational continuity."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hot_context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            context_key TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            summary TEXT DEFAULT '',
            context_type TEXT DEFAULT 'topic',
            state TEXT DEFAULT 'active',
            owner TEXT DEFAULT '',
            source_type TEXT DEFAULT '',
            source_id TEXT DEFAULT '',
            session_id TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}',
            first_seen_at REAL NOT NULL,
            last_event_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            context_key TEXT DEFAULT '',
            event_type TEXT NOT NULL,
            title TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            body TEXT DEFAULT '',
            actor TEXT DEFAULT '',
            source_type TEXT DEFAULT '',
            source_id TEXT DEFAULT '',
            session_id TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}',
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
    """)
    _migrate_add_index(conn, "idx_hot_context_last_event", "hot_context", "last_event_at")
    _migrate_add_index(conn, "idx_hot_context_state", "hot_context", "state")
    _migrate_add_index(conn, "idx_hot_context_source", "hot_context", "source_type, source_id")
    _migrate_add_index(conn, "idx_hot_context_session", "hot_context", "session_id, last_event_at")
    _migrate_add_index(conn, "idx_recent_events_created", "recent_events", "created_at")
    _migrate_add_index(conn, "idx_recent_events_context", "recent_events", "context_key, created_at")
    _migrate_add_index(conn, "idx_recent_events_source", "recent_events", "source_type, source_id, created_at")
    _migrate_add_index(conn, "idx_recent_events_session", "recent_events", "session_id, created_at")


def _m31_drive_signals(conn):
    """Drive/Curiosity layer — autonomous tension-based investigation signals."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS drive_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_type TEXT NOT NULL,
            source TEXT NOT NULL,
            source_id TEXT DEFAULT '',
            area TEXT DEFAULT '',
            summary TEXT NOT NULL,
            tension REAL DEFAULT 0.3,
            evidence TEXT DEFAULT '[]',
            status TEXT DEFAULT 'latent',
            first_seen TEXT DEFAULT (datetime('now')),
            last_reinforced TEXT,
            acted_at TEXT,
            outcome TEXT,
            decay_rate REAL DEFAULT 0.05
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_drive_status ON drive_signals(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_drive_area ON drive_signals(area)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_drive_tension ON drive_signals(tension)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_drive_first_seen ON drive_signals(first_seen)")
    # Register drive_decay in maintenance_schedule
    conn.execute(
        "INSERT OR IGNORE INTO maintenance_schedule (task_name, interval_hours) VALUES (?, ?)",
        ('drive_decay', 24),
    )


def _m32_outcomes(conn):
    """Outcome tracker v1 — close action -> expected result -> actual result loops."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL,
            action_id TEXT DEFAULT '',
            session_id TEXT DEFAULT '',
            description TEXT NOT NULL,
            expected_result TEXT NOT NULL,
            metric_source TEXT NOT NULL DEFAULT 'manual',
            metric_query TEXT DEFAULT '',
            baseline_value REAL,
            target_value REAL,
            target_operator TEXT NOT NULL DEFAULT 'gte',
            actual_value REAL,
            actual_value_text TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            deadline TEXT NOT NULL,
            checked_at TEXT DEFAULT NULL,
            notes TEXT DEFAULT '',
            learning_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_status ON outcomes(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_deadline ON outcomes(deadline)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_action ON outcomes(action_type, action_id)")


def _m33_followup_impact_scoring(conn):
    """Followup impact scoring v1 — persistent prioritization over real queues."""
    _migrate_add_column(conn, "followups", "impact_score", "REAL DEFAULT 0")
    _migrate_add_column(conn, "followups", "impact_factors", "TEXT DEFAULT '{}'")
    _migrate_add_column(conn, "followups", "last_scored_at", "TEXT DEFAULT NULL")
    _migrate_add_index(conn, "idx_followups_impact_score", "followups", "impact_score")


def _m34_cortex_evaluations(conn):
    """Persist high-impact alternative evaluations on top of the existing Cortex."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cortex_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT DEFAULT '',
            task_id TEXT DEFAULT '',
            goal TEXT NOT NULL,
            task_type TEXT DEFAULT '',
            area TEXT DEFAULT '',
            impact_level TEXT NOT NULL DEFAULT 'high',
            context_hint TEXT DEFAULT '',
            alternatives TEXT NOT NULL DEFAULT '[]',
            scores TEXT NOT NULL DEFAULT '[]',
            recommended_choice TEXT DEFAULT '',
            recommended_reasoning TEXT DEFAULT '',
            linked_outcome_id INTEGER DEFAULT NULL,
            selected_choice TEXT DEFAULT '',
            selection_reason TEXT DEFAULT '',
            selection_source TEXT NOT NULL DEFAULT 'recommended',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cortex_evaluations_task ON cortex_evaluations(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cortex_evaluations_session ON cortex_evaluations(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cortex_evaluations_created ON cortex_evaluations(created_at)")


def _m35_cortex_evaluation_outcome_link(conn):
    """Link Cortex evaluations to tracked outcomes when the task has a measurable result."""
    _migrate_add_column(conn, "cortex_evaluations", "linked_outcome_id", "INTEGER DEFAULT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cortex_evaluations_outcome ON cortex_evaluations(linked_outcome_id)")


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
    (20, "personal_scripts_registry", _m20_personal_scripts_registry),
    (21, "external_session_fields", _m21_external_session_fields),
    (22, "protocol_discipline_tables", _m22_protocol_discipline_tables),
    (23, "learning_superseded_lifecycle", _m23_learning_superseded_lifecycle),
    (24, "durable_workflow_runtime", _m24_durable_workflow_runtime),
    (25, "workflow_goal_stack", _m25_workflow_goal_stack),
    (26, "protocol_answer_confidence", _m26_protocol_answer_confidence),
    (27, "state_watchers", _m27_state_watchers),
    (28, "automation_runs", _m28_automation_runs),
    (29, "item_history_and_soft_delete", _m29_item_history_and_soft_delete),
    (30, "hot_context_memory", _m30_hot_context_memory),
    (31, "drive_signals", _m31_drive_signals),
    (32, "outcomes", _m32_outcomes),
    (33, "followup_impact_scoring", _m33_followup_impact_scoring),
    (34, "cortex_evaluations", _m34_cortex_evaluations),
    (35, "cortex_evaluation_outcome_link", _m35_cortex_evaluation_outcome_link),
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
