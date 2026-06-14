"""NEXO DB — Schema module."""
import time

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
            is_active INTEGER NOT NULL DEFAULT 1,
            source_artifact TEXT DEFAULT '',
            source_anchor TEXT DEFAULT '',
            content_hash TEXT DEFAULT '',
            protected INTEGER NOT NULL DEFAULT 1,
            severity TEXT DEFAULT 'critical',
            replacement_rule_id TEXT DEFAULT NULL
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
            origin TEXT NOT NULL DEFAULT 'user',
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
            guard_acknowledged INTEGER NOT NULL DEFAULT 0,
            guard_acknowledged_at TEXT DEFAULT NULL,
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
    _migrate_add_column(conn, "protocol_tasks", "guard_acknowledged", "INTEGER NOT NULL DEFAULT 0")
    _migrate_add_column(conn, "protocol_tasks", "guard_acknowledged_at", "TEXT DEFAULT NULL")
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


def _m36_goal_profiles(conn):
    """Goal Engine v1 — explicit optimization profiles resolved by context."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS goal_profiles (
            profile_id TEXT PRIMARY KEY,
            profile_name TEXT NOT NULL,
            description TEXT DEFAULT '',
            scope_type TEXT NOT NULL DEFAULT 'default',
            scope_value TEXT DEFAULT '',
            goal_labels TEXT DEFAULT '[]',
            weights TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'active',
            source TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_goal_profiles_scope ON goal_profiles(scope_type, scope_value)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_goal_profiles_status ON goal_profiles(status)")


def _m37_cortex_goal_profile_trace(conn):
    """Persist which goal profile influenced each important Cortex decision."""
    _migrate_add_column(conn, "cortex_evaluations", "goal_profile_id", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "cortex_evaluations", "goal_profile_labels", "TEXT DEFAULT '[]'")
    _migrate_add_column(conn, "cortex_evaluations", "goal_profile_weights", "TEXT DEFAULT '{}'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cortex_evaluations_goal_profile ON cortex_evaluations(goal_profile_id)")


def _m38_evolution_log_proposal_payload(conn):
    """Persist the full proposal dict (with `changes` array) so user-approved
    proposals can be applied by a later cycle.

    Before m38, evolution_log only stored the proposal `action` string. When a
    user marked a proposal as `accepted` via nexo_evolution_approve, the next
    cycle had no way to re-execute it because the `changes` operations were
    discarded after the original cycle. Adding `proposal_payload` (TEXT/JSON)
    closes that loop and lets _apply_accepted_proposals() in the runner pick
    up accepted rows and run them through execute_auto_proposal().

    Idempotent and append-only: ALTER TABLE ADD COLUMN is non-destructive in
    SQLite. Pre-m38 rows keep proposal_payload NULL and are skipped by the
    apply step (which requires a non-null payload).
    """
    _migrate_add_column(conn, "evolution_log", "proposal_payload", "TEXT DEFAULT NULL")


def _m55_cortex_critique_trace(conn):
    """Persist heuristic-vs-LLM critique traces for Cortex decisions."""
    # Some legacy/minimal runtimes have schema_migrations backfilled through
    # v48 without the optional Cortex table present. Repair the dependency
    # before adding v55 columns so update never bricks those installs.
    _m34_cortex_evaluations(conn)
    _m35_cortex_evaluation_outcome_link(conn)
    _m37_cortex_goal_profile_trace(conn)
    _migrate_add_column(conn, "cortex_evaluations", "heuristic_choice", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "cortex_evaluations", "heuristic_reasoning", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "cortex_evaluations", "critique_payload", "TEXT DEFAULT '{}'")
    _migrate_add_column(conn, "cortex_evaluations", "decision_mode", "TEXT DEFAULT 'heuristic'")


def _m56_session_correction_requirements(conn):
    """Track user corrections that must be turned into durable learnings."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS session_correction_requirements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            context_hash TEXT NOT NULL,
            correction_text TEXT DEFAULT '',
            source TEXT DEFAULT 'heartbeat',
            status TEXT NOT NULL DEFAULT 'open',
            detected_at TEXT DEFAULT (datetime('now')),
            resolved_at TEXT DEFAULT NULL,
            resolved_learning_id INTEGER DEFAULT NULL,
            followup_id TEXT DEFAULT '',
            UNIQUE(session_id, context_hash)
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_session_correction_requirements_session ON session_correction_requirements(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_session_correction_requirements_status ON session_correction_requirements(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_session_correction_requirements_detected ON session_correction_requirements(detected_at)")


def _m57_hook_runs_retention(conn):
    """Bound hook_runs so existing installs stop growing without manual cleanup."""
    try:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hook_runs'"
        ).fetchone()
    except Exception:
        table = None
    if not table:
        _m39_hook_runs(conn)

    retention_days = 7
    max_rows = 19000
    cutoff = time.time() - (retention_days * 86400)
    conn.execute("DELETE FROM hook_runs WHERE started_at < ?", (cutoff,))
    row = conn.execute("SELECT COUNT(*) FROM hook_runs").fetchone()
    total = int((row[0] if row else 0) or 0)
    if total > max_rows:
        conn.execute(
            """
            DELETE FROM hook_runs
            WHERE id NOT IN (
                SELECT id
                FROM hook_runs
                ORDER BY started_at DESC, id DESC
                LIMIT ?
            )
            """,
            (max_rows,),
        )
    try:
        conn.commit()
        conn.execute("VACUUM")
    except Exception:
        pass


def _m58_morning_briefing_runs(conn):
    """Atomic dedupe lock for daily morning briefings."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS morning_briefing_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            local_date TEXT NOT NULL,
            recipient TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'in_progress',
            subject TEXT DEFAULT '',
            send_output TEXT DEFAULT '',
            error TEXT DEFAULT '',
            started_at TEXT DEFAULT (datetime('now')),
            finished_at TEXT DEFAULT NULL,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(local_date, recipient)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_morning_briefing_runs_date "
        "ON morning_briefing_runs(local_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_morning_briefing_runs_status "
        "ON morning_briefing_runs(status)"
    )


def _m39_hook_runs(conn):
    """Persist hook lifecycle observability — closes Fase 3 item 7.

    Before m39, NEXO had 12 hook scripts (session-start.sh, post-compact.sh,
    pre-compact.sh, inbox-hook.sh, etc.) but no central record of when they
    ran, how long they took, or whether they succeeded. The audit lifecycle
    was a black box. This table is the storage layer that
    src/hook_observability.py records into and that the new
    nexo_hook_runs MCP tool reads from.

    Idempotent: CREATE TABLE IF NOT EXISTS plus indexes by hook_name and
    started_at so the daily query patterns are cheap.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hook_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hook_name TEXT NOT NULL,
            started_at REAL NOT NULL,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            exit_code INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'ok',
            session_id TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}',
            created_at REAL NOT NULL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hook_runs_hook_name ON hook_runs(hook_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hook_runs_started_at ON hook_runs(started_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hook_runs_status ON hook_runs(status)")


def _m40_classification_columns(conn):
    """Add internal (INTEGER 0/1) and owner (TEXT) to followups and reminders.

    Agents creating tasks via nexo_followup_create / nexo_reminder_create
    can set both fields explicitly. The Brain core does not classify tasks
    on behalf of agents — clients that want automatic classification
    compute it themselves (NEXO Desktop does, via its legacy client-side
    helpers) and pass the result.

    Values:
        internal: 0 (external, visible) or 1 (agent bookkeeping, hidden).
        owner: 'user' | 'waiting' | 'agent' | 'shared' | NULL.
            'agent' is deliberately generic — Desktop renders the
            label using the configured assistant name, not a hardcoded
            'NEXO'.

    Idempotent: _migrate_add_column is a no-op when the column exists,
    _migrate_add_index likewise. Pre-v5.8.2 versions of this migration
    also ran a one-shot backfill using a Spanish-first regex heuristic;
    v5.8.2 removed that heuristic so the core stays neutral across
    deployments. Rows that were already backfilled keep their values.
    """
    _migrate_add_column(conn, "followups", "internal", "INTEGER DEFAULT 0")
    _migrate_add_column(conn, "followups", "owner", "TEXT DEFAULT NULL")
    _migrate_add_column(conn, "reminders", "internal", "INTEGER DEFAULT 0")
    _migrate_add_column(conn, "reminders", "owner", "TEXT DEFAULT NULL")
    _migrate_add_index(conn, "idx_followups_internal", "followups", "internal")
    _migrate_add_index(conn, "idx_followups_owner", "followups", "owner")
    _migrate_add_index(conn, "idx_reminders_internal", "reminders", "internal")
    _migrate_add_index(conn, "idx_reminders_owner", "reminders", "owner")


def _m41_automation_sessions_columns(conn):
    """Extend automation_runs with session-level tracking.

    v5.9.0 introduces two changes to how we record Claude/Codex invocations:

    1. Every caller is now required to pass a ``caller=`` string registered in
       ``src/resonance_map.py``. Stored in a new ``caller`` column so every
       row is traceable to the subsystem that started it (deep-sleep/extract,
       evolution/run, nexo_chat, desktop_new_session, …).

    2. Interactive sessions (``nexo chat`` and Desktop new conversation) no
       longer bypass the logging path. They record a row at spawn time with
       ``ended_at IS NULL`` and update it on close. The ``session_type``
       column distinguishes ``headless`` from ``interactive_chat`` and
       ``interactive_desktop`` so dashboards can slice the data by invocation
       shape.

    Migration is idempotent: ``_migrate_add_column`` is a no-op when the
    column already exists; existing rows get empty / NULL values which is
    compatible with callers that have not been updated yet.
    """
    _migrate_add_column(conn, "automation_runs", "caller", "TEXT DEFAULT ''")
    _migrate_add_column(
        conn, "automation_runs", "session_type", "TEXT DEFAULT 'headless'"
    )
    _migrate_add_column(conn, "automation_runs", "started_at", "TEXT")
    _migrate_add_column(conn, "automation_runs", "ended_at", "TEXT")
    _migrate_add_column(conn, "automation_runs", "pid", "INTEGER")
    _migrate_add_column(
        conn, "automation_runs", "resonance_tier", "TEXT DEFAULT ''"
    )
    _migrate_add_index(
        conn, "idx_automation_runs_caller", "automation_runs", "caller"
    )
    _migrate_add_index(
        conn, "idx_automation_runs_session_type", "automation_runs", "session_type"
    )
    _migrate_add_index(
        conn, "idx_automation_runs_started_at", "automation_runs", "started_at"
    )


def _m69_provider_runtime_metadata(conn):
    """Add provider/runtime metadata required for Anthropic/OpenAI parity."""
    if not _table_exists(conn, "automation_runs"):
        _m28_automation_runs(conn)
    if not _table_exists(conn, "cron_runs"):
        _m17_cron_runs(conn)

    if _table_exists(conn, "automation_runs"):
        _migrate_add_column(conn, "automation_runs", "provider", "TEXT DEFAULT ''")
        _migrate_add_column(conn, "automation_runs", "runtime_version", "TEXT DEFAULT ''")
        _migrate_add_column(conn, "automation_runs", "runtime_session_id", "TEXT DEFAULT ''")
        _migrate_add_index(conn, "idx_automation_runs_provider", "automation_runs", "provider")
    if _table_exists(conn, "cron_runs"):
        _migrate_add_column(conn, "cron_runs", "provider", "TEXT DEFAULT ''")
        _migrate_add_column(conn, "cron_runs", "backend", "TEXT DEFAULT ''")
        _migrate_add_column(conn, "cron_runs", "runtime_snapshot", "TEXT DEFAULT '{}'")
        _migrate_add_index(conn, "idx_cron_runs_provider", "cron_runs", "provider")
    if _table_exists(conn, "sessions"):
        _migrate_add_column(conn, "sessions", "session_provider", "TEXT DEFAULT ''")
        _migrate_add_index(conn, "idx_sessions_provider", "sessions", "session_provider")


def _m70_commitments(conn):
    """Durable promise/commitment index linked to existing action artifacts."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS commitments (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            closed_at REAL DEFAULT NULL,
            source_type TEXT NOT NULL DEFAULT '',
            source_id TEXT DEFAULT '',
            memory_event_uid TEXT DEFAULT '',
            session_id TEXT DEFAULT '',
            conversation_id TEXT DEFAULT '',
            project_key TEXT DEFAULT '',
            statement TEXT NOT NULL,
            owner TEXT DEFAULT 'agent',
            deadline TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            confidence REAL DEFAULT 0.5,
            action_ref_type TEXT DEFAULT '',
            action_ref_id TEXT DEFAULT '',
            outcome_id INTEGER DEFAULT NULL,
            evidence_ref TEXT DEFAULT '',
            dedupe_key TEXT DEFAULT '',
            metadata_json TEXT DEFAULT '{}'
        )
        """
    )
    _migrate_add_index(conn, "idx_commitments_status", "commitments", "status, deadline, updated_at")
    _migrate_add_index(conn, "idx_commitments_session", "commitments", "session_id, status, updated_at")
    _migrate_add_index(conn, "idx_commitments_source", "commitments", "source_type, source_id")
    _migrate_add_index(conn, "idx_commitments_action", "commitments", "action_ref_type, action_ref_id")
    _migrate_add_index(conn, "idx_commitments_dedupe", "commitments", "dedupe_key")


def _m42_v6_0_1_hotfix(conn):
    """v6.0.1 hotfix — last_heartbeat_ts on sessions + hook_inbox_reminders.

    Two surfaces:

    1. ``sessions.last_heartbeat_ts`` is a REAL column holding the epoch
       seconds of the most recent ``nexo_heartbeat`` call for that SID.
       The PostToolUse hook uses it to decide whether to emit an
       inbox-reminder systemMessage on autopilot sessions that have not
       checked their inbox in a while.

    2. ``hook_inbox_reminders`` is a tiny table storing the last time we
       surfaced an inbox reminder per SID. The hook reads/writes it to
       enforce a rate limit of at most one reminder per minute per
       session, so long streams of tool calls do not spam the user.

    Idempotent by construction: ``_migrate_add_column`` is a no-op when
    the column exists, ``CREATE TABLE IF NOT EXISTS`` likewise.
    """
    _migrate_add_column(conn, "sessions", "last_heartbeat_ts", "REAL")
    _migrate_add_index(
        conn, "idx_sessions_last_heartbeat_ts", "sessions", "last_heartbeat_ts"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hook_inbox_reminders (
            sid TEXT PRIMARY KEY,
            last_reminder_ts REAL NOT NULL
        )"""
    )


def _m43_session_claude_aliases(conn):
    """Multi-Claude-sid-per-sid aliasing (hotfix for NEXO Desktop
    multi-conversation workflows).

    NEXO Desktop spawns one `claude` CLI subprocess per conversation.
    Each spawn fires its own SessionStart hook with a fresh UUID. The
    legacy schema (``sessions.claude_session_id`` as a single TEXT
    column) can only remember ONE UUID per sid, so when a second
    conversation is opened, the hook's PreToolUse lookup
    (``_resolve_nexo_sid``) receives the wrong UUID and blocks the
    edit with "unknown target".

    Fix: a 1-to-N alias table. ``nexo_startup`` now also writes
    ``(sid, claude_session_id, first_seen, last_seen)`` here, and
    ``_resolve_nexo_sid`` consults this table FIRST (falling back to
    the legacy ``sessions.claude_session_id`` column for backward
    compatibility with rows created before this migration).

    Idempotent.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS session_claude_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sid TEXT NOT NULL,
            claude_session_id TEXT NOT NULL,
            first_seen REAL NOT NULL,
            last_seen REAL NOT NULL,
            UNIQUE(sid, claude_session_id)
        )"""
    )
    _migrate_add_index(
        conn, "idx_claude_aliases_sid", "session_claude_aliases", "sid"
    )
    _migrate_add_index(
        conn,
        "idx_claude_aliases_claude_sid",
        "session_claude_aliases",
        "claude_session_id",
    )


def _m46_email_accounts(conn):
    """Plan Consolidado F1 — first-class multi-account email config.

    Replaces the legacy ~/.nexo/nexo-email/config.json (single tenant,
    password in cleartext, Francisco-hardcoded) with a structured table.

    Columns:
      - id: internal primary key.
      - label: operator-friendly name ('primary', 'wazion', 'canari').
      - email: address the account sends from.
      - imap_host, imap_port: inbound server.
      - smtp_host, smtp_port: outbound server.
      - credential_service, credential_key: reference into the
        `credentials` table (never store the password in this row).
      - operator_email: where the briefing / digest is sent when this
        account runs the morning agent.
      - trusted_domains: JSON array of domains the inbox treats as
        priority (not hard filter).
      - role: 'inbox' (monitor only), 'outbox' (send only), 'both'.
      - enabled: on/off without having to delete the row.
      - created_at / updated_at.

    Idempotent.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS email_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL,
            imap_host TEXT NOT NULL DEFAULT '',
            imap_port INTEGER NOT NULL DEFAULT 993,
            smtp_host TEXT NOT NULL DEFAULT '',
            smtp_port INTEGER NOT NULL DEFAULT 465,
            credential_service TEXT NOT NULL DEFAULT '',
            credential_key TEXT NOT NULL DEFAULT '',
            operator_email TEXT NOT NULL DEFAULT '',
            trusted_domains TEXT NOT NULL DEFAULT '[]',
            role TEXT NOT NULL DEFAULT 'both',
            enabled INTEGER NOT NULL DEFAULT 1,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    _migrate_add_index(conn, "idx_email_accounts_enabled", "email_accounts", "enabled")


def _m47_email_operator_accounts(conn):
    """F2 — enrich email_accounts with agent/operator split + permissions.

    Keeps the single-table model while making the product contract explicit:

    - account_type='agent': the single mailbox NEXO monitors automatically.
    - account_type='operator': human-owned inboxes NEXO may read/send on demand.
    - description: semantic label shown in Desktop and used for user-facing matching.
    - can_read / can_send: operator permissions for on-demand access.
    - is_default: fallback operator destination for alerts / ambiguous sends.

    Idempotent.
    """
    _migrate_add_column(
        conn, "email_accounts", "account_type", "TEXT NOT NULL DEFAULT 'agent'"
    )
    _migrate_add_column(
        conn, "email_accounts", "description", "TEXT NOT NULL DEFAULT ''"
    )
    _migrate_add_column(
        conn, "email_accounts", "can_read", "INTEGER NOT NULL DEFAULT 0"
    )
    _migrate_add_column(
        conn, "email_accounts", "can_send", "INTEGER NOT NULL DEFAULT 0"
    )
    _migrate_add_column(
        conn, "email_accounts", "is_default", "INTEGER NOT NULL DEFAULT 0"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_email_accounts_account_type "
        "ON email_accounts(account_type)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_email_accounts_default "
        "ON email_accounts(is_default) WHERE is_default = 1"
    )


def _m48_email_agent_contract_backfill(conn):
    """F2 follow-up — normalize legacy agent rows after m47.

    Existing installs upgraded from m46 -> m47 inherit the new columns with the
    raw SQL defaults (`0` / empty string), which would incorrectly leave the
    primary agent mailbox without read/send permissions even when role='both'.

    Normalize every agent row so the stored contract matches the runtime rules:
    - account_type defaults to 'agent'
    - can_read / can_send derive from role for agent rows
    - description gets a neutral default if it was still blank
    - is_default is always off for agent rows
    """
    conn.execute(
        "UPDATE email_accounts "
        "SET account_type = 'agent' "
        "WHERE COALESCE(account_type, '') = ''"
    )
    conn.execute(
        "UPDATE email_accounts "
        "SET can_read = CASE WHEN role IN ('inbox', 'both') THEN 1 ELSE 0 END, "
        "    can_send = CASE WHEN role IN ('outbox', 'both') THEN 1 ELSE 0 END, "
        "    is_default = 0 "
        "WHERE COALESCE(account_type, 'agent') = 'agent'"
    )
    conn.execute(
        "UPDATE email_accounts "
        "SET description = 'Agent mailbox' "
        "WHERE COALESCE(account_type, 'agent') = 'agent' "
        "AND COALESCE(description, '') = ''"
    )


def _m45_personal_scripts_origin(conn):
    """Plan Consolidado F0.1 — mark whether a personal_scripts row is
    installed by NEXO Core (origin='core'), contributed by the operator
    (origin='user'), or a dev-only core-dev script (origin='core-dev').

    Used by `nexo update` to know which rows it can replace without
    overwriting operator-authored automations, and by the Desktop
    Automations panel (F0.2) to segment the list.

    Idempotent.
    """
    _migrate_add_column(conn, "personal_scripts", "origin", "TEXT NOT NULL DEFAULT 'user'")
    _migrate_add_index(conn, "idx_personal_scripts_origin", "personal_scripts", "origin")


def _m49_protocol_guard_ack_backfill(conn):
    """Backfill protocol guard-ack columns for installs that already marked
    migration v22 as applied before those columns were added to the migration
    body.

    This must remain a standalone migration instead of reusing v22 because
    real runtimes can legitimately sit at schema version 48 with an older
    ``protocol_tasks`` shape. Re-running ``init_db()`` skips v22 once it is
    recorded in ``schema_migrations``, so the missing columns never land
    without a new version.
    """
    _migrate_add_column(conn, "protocol_tasks", "guard_acknowledged", "INTEGER NOT NULL DEFAULT 0")
    _migrate_add_column(conn, "protocol_tasks", "guard_acknowledged_at", "TEXT DEFAULT NULL")


def _m50_dedupe_nexo_product_learning_pair(conn):
    """Block D.2 / G7-adjacent: dedupe the two learnings that encode the
    "NEXO Brain public product vs Francisco's personal instance"
    invariant as a physically separate pair.

    Francisco's runtime has this concept stored twice (historical IDs 212
    and 224). Guard dedup already collapses them at display time, but
    the underlying rows stayed split, so list/search/update flows still
    saw two rows. Physically supersede the older one by pointing its
    ``supersedes_id`` at the newer duplicate and flipping its status to
    ``superseded``. Anything newer than both is left untouched.

    Idempotent. Fresh installs that never created either row silently
    do nothing; installs where an operator has already set the relation
    manually do nothing. The migration matches on a text-normalised form
    of the title so synonymous wording on both rows is enough — we don't
    need identical strings, and we don't need the IDs to literally be
    212 and 224.
    """
    try:
        rows = conn.execute(
            "SELECT id, title, content, status, supersedes_id FROM learnings "
            "WHERE status = 'active'"
        ).fetchall()
    except Exception:
        return

    def _norm(text: str) -> str:
        # Collapse whitespace and strip punctuation/case so "NEXO Brain
        # public product vs personal instance" matches its twin no
        # matter how the operator rephrased it.
        import re as _re
        stripped = _re.sub(r"[\W_]+", " ", str(text or "")).strip().lower()
        return _re.sub(r"\s+", " ", stripped)

    marker = "nexo brain producto"
    candidates = [r for r in rows if marker in _norm(r[1] or "")]
    # Need at least two rows for this migration to do anything.
    if len(candidates) < 2:
        return
    # Sort by id ascending; the highest id is the canonical survivor.
    candidates.sort(key=lambda r: int(r[0] or 0))
    survivor = candidates[-1]
    for older in candidates[:-1]:
        older_id = int(older[0] or 0)
        if int(older[4] or 0) == int(survivor[0] or 0):
            continue  # already linked
        conn.execute(
            "UPDATE learnings SET supersedes_id = ?, status = 'superseded', "
            "updated_at = strftime('%s','now') WHERE id = ? AND status = 'active'",
            (int(survivor[0] or 0), older_id),
        )


def _m44_entities_extended_schema(conn):
    """Plan Consolidado 0.3 — extend entities with aliases/metadata/source/confidence/access_mode.

    - aliases:     TEXT DEFAULT '[]'   (JSON array of alternative names)
    - metadata:    TEXT DEFAULT '{}'   (JSON object of arbitrary key/value)
    - source:      TEXT DEFAULT 'manual'
                   (enum: preset | manual | quarantine_approved | auto_detected)
    - confidence:  REAL DEFAULT 1.0     (0..1 — preset=1.0, quarantine≈0.6)
    - access_mode: TEXT DEFAULT 'unknown'
                   (enum: read_only | read_write | write_only | unknown)

    Idempotent.
    """
    _migrate_add_column(conn, "entities", "aliases", "TEXT DEFAULT '[]'")
    _migrate_add_column(conn, "entities", "metadata", "TEXT DEFAULT '{}'")
    _migrate_add_column(conn, "entities", "source", "TEXT NOT NULL DEFAULT 'manual'")
    _migrate_add_column(conn, "entities", "confidence", "REAL NOT NULL DEFAULT 1.0")
    _migrate_add_column(conn, "entities", "access_mode", "TEXT DEFAULT 'unknown'")


def _m51_lifecycle_events(conn):
    """v7.4.0 — durable lifecycle event store for the Desktop pipeline.

    Matches the Desktop-side NDJSON queue contract:
    event_id (PRIMARY KEY, uuid from Desktop), source (desktop|cron|...),
    action (close|delete|archive|switch|app-exit|window-close),
    conversation_id, session_id (claude session), reason,
    payload_snapshot (JSON), delivery_status
    (pending|accepted|processed|already_processed|rejected|retryable_error),
    retry_count, created_at, processed_at, last_error.

    Idempotency key is event_id. Re-delivery of the same event_id returns
    status=already_processed without re-running any canonical side effect
    (diary, stop, archive bookkeeping). This is the backbone of
    guardian-claude-desktop-plan.md → "5. Idempotencia real".
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lifecycle_events (
            event_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT 'desktop',
            action TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            session_id TEXT DEFAULT NULL,
            reason TEXT DEFAULT 'user_action',
            payload_snapshot TEXT DEFAULT '{}',
            delivery_status TEXT NOT NULL DEFAULT 'accepted',
            retry_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            processed_at TEXT DEFAULT NULL,
            last_error TEXT DEFAULT NULL
        )
        """
    )
    _migrate_add_index(conn, "idx_lifecycle_events_status", "lifecycle_events", "delivery_status")
    _migrate_add_index(conn, "idx_lifecycle_events_conv", "lifecycle_events", "conversation_id")
    _migrate_add_index(conn, "idx_lifecycle_events_action", "lifecycle_events", "action")


def _m52_lifecycle_canonical_plan(conn):
    """v7.5.0 — Brain promoted to canonical authority for session-end.

    When the Desktop pipeline posts a close / delete / archive / app-exit
    lifecycle event with a live session_id, Brain now decides the exact
    prompt + sequence to run against the live Claude process and hands
    that plan back to Desktop in the same MCP call. Desktop executes
    the plan inline and confirms via a second call
    (``nexo_lifecycle_complete_canonical``).

    New columns on ``lifecycle_events``:

    - ``canonical_plan_id`` — deterministic id hash(event_id+plan_version).
      Used for idempotent retries: Desktop can ask "did you already
      finish this plan_id" and Brain can dedupe without re-running any
      diary write.
    - ``canonical_plan_version`` — schema version of the plan payload
      (INTEGER, default 1). Lets us evolve the action shape without
      breaking older Desktop builds.
    - ``canonical_actions_json`` — the actions array Brain returned,
      verbatim. Persisted so boot reconciliation can re-send the exact
      same plan on a crash between dispatch and confirm.
    - ``canonical_dispatched_at`` — first time Brain returned the plan.
      Used as the "since" cursor for the session_diary dedup query.
    - ``canonical_done_at`` — set only when Desktop calls
      ``nexo_lifecycle_complete_canonical``. Absence + presence of
      ``canonical_dispatched_at`` == "dispatched but not confirmed".
    - ``canonical_done_results`` — JSON array of per-action results
      reported by Desktop, used for telemetry and retry classification.

    Idempotent. Fresh installs already created the table in m51; this
    migration only ADDs the new columns.
    """
    _migrate_add_column(conn, "lifecycle_events", "canonical_plan_id", "TEXT DEFAULT NULL")
    _migrate_add_column(conn, "lifecycle_events", "canonical_plan_version", "INTEGER DEFAULT NULL")
    _migrate_add_column(conn, "lifecycle_events", "canonical_actions_json", "TEXT DEFAULT NULL")
    _migrate_add_column(conn, "lifecycle_events", "canonical_dispatched_at", "TEXT DEFAULT NULL")
    _migrate_add_column(conn, "lifecycle_events", "canonical_done_at", "TEXT DEFAULT NULL")
    _migrate_add_column(conn, "lifecycle_events", "canonical_done_results", "TEXT DEFAULT NULL")
    _migrate_add_index(conn, "idx_lifecycle_events_plan_id", "lifecycle_events", "canonical_plan_id")


def _m53_session_conversation_identity(conn):
    """Stable Desktop conversation identity independent from the runtime SID."""
    _migrate_add_column(conn, "sessions", "conversation_id", "TEXT DEFAULT ''")
    _migrate_add_index(conn, "idx_sessions_conversation_id", "sessions", "conversation_id")


def _m54_continuity_snapshots(conn):
    """Durable continuity snapshots for Desktop/Brain handoff and audit."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS continuity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            session_id TEXT DEFAULT '',
            external_session_id TEXT DEFAULT '',
            client TEXT DEFAULT '',
            event_type TEXT NOT NULL DEFAULT 'turn_end',
            payload_json TEXT NOT NULL DEFAULT '{}',
            trace_id TEXT DEFAULT '',
            idempotency_key TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(conversation_id, idempotency_key)
        )
        """
    )
    _migrate_add_index(conn, "idx_continuity_snapshots_conv", "continuity_snapshots", "conversation_id")
    _migrate_add_index(conn, "idx_continuity_snapshots_sid", "continuity_snapshots", "session_id")
    _migrate_add_index(conn, "idx_continuity_snapshots_created", "continuity_snapshots", "created_at")


def _m59_memory_events(conn):
    """Memory Observations v2 phase 1: append-only event log.

    The table is deliberately independent from the later observation,
    retrieval, viewer, and promotion layers. Fresh installs get it through
    ``init_db() -> run_migrations()``; existing installs receive the same
    idempotent migration on the next startup/update.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_uid TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL,
            session_id TEXT DEFAULT '',
            external_session_id TEXT DEFAULT '',
            client TEXT DEFAULT '',
            conversation_id TEXT DEFAULT '',
            project_key TEXT DEFAULT '',
            source_type TEXT NOT NULL,
            source_id TEXT DEFAULT '',
            event_type TEXT NOT NULL,
            actor TEXT DEFAULT '',
            tool_name TEXT DEFAULT '',
            file_paths_json TEXT DEFAULT '[]',
            command_digest TEXT DEFAULT '',
            input_hash TEXT DEFAULT '',
            output_digest TEXT DEFAULT '',
            raw_ref TEXT DEFAULT '',
            privacy_level TEXT DEFAULT 'normal',
            redaction_applied INTEGER DEFAULT 0,
            confidence REAL DEFAULT 1.0,
            metadata_json TEXT DEFAULT '{}'
        )
        """
    )
    _migrate_add_index(conn, "idx_memory_events_created", "memory_events", "created_at")
    _migrate_add_index(conn, "idx_memory_events_session", "memory_events", "session_id, created_at")
    _migrate_add_index(conn, "idx_memory_events_source", "memory_events", "source_type, source_id")
    _migrate_add_index(conn, "idx_memory_events_type", "memory_events", "event_type, created_at")
    _migrate_add_index(conn, "idx_memory_events_project", "memory_events", "project_key, created_at")


def _m60_memory_observations(conn):
    """Memory Observations v2 phase 2: passive derived observations."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observation_uid TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            project_key TEXT DEFAULT '',
            session_id TEXT DEFAULT '',
            observation_type TEXT NOT NULL,
            subject TEXT DEFAULT '',
            summary TEXT NOT NULL,
            facts_json TEXT DEFAULT '{}',
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            entities_json TEXT DEFAULT '[]',
            salience REAL DEFAULT 0.5,
            confidence REAL DEFAULT 0.5,
            stability REAL DEFAULT 1.0,
            status TEXT DEFAULT 'active',
            promotion_state TEXT DEFAULT 'observation',
            decay_policy TEXT DEFAULT 'normal',
            source_hash TEXT DEFAULT '',
            metadata_json TEXT DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_observation_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_uid TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER DEFAULT 0,
            last_error TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            processed_at REAL DEFAULT NULL
        )
        """
    )
    _migrate_add_index(conn, "idx_memory_obs_type", "memory_observations", "observation_type, created_at")
    _migrate_add_index(conn, "idx_memory_obs_project", "memory_observations", "project_key, created_at")
    _migrate_add_index(conn, "idx_memory_obs_session", "memory_observations", "session_id, created_at")
    _migrate_add_index(conn, "idx_memory_obs_status", "memory_observations", "status, promotion_state")
    _migrate_add_index(conn, "idx_memory_obs_queue_status", "memory_observation_queue", "status, updated_at")


def _m61_memory_observations_fts(conn):
    """FTS5 index for Memory Observations v2."""
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_observations_fts USING fts5(
                observation_uid UNINDEXED,
                summary,
                subject,
                observation_type,
                project_key,
                entities,
                tokenize='unicode61 remove_diacritics 2'
            )
            """
        )
    except Exception:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_observations_fts (
                observation_uid TEXT PRIMARY KEY,
                summary TEXT DEFAULT '',
                subject TEXT DEFAULT '',
                observation_type TEXT DEFAULT '',
                project_key TEXT DEFAULT '',
                entities TEXT DEFAULT ''
            )
            """
        )
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS memory_observations_fts_insert
        AFTER INSERT ON memory_observations BEGIN
            INSERT INTO memory_observations_fts(
                rowid, observation_uid, summary, subject, observation_type, project_key, entities
            )
            VALUES (
                new.id, new.observation_uid, new.summary, new.subject,
                new.observation_type, new.project_key, new.entities_json
            );
        END;

        CREATE TRIGGER IF NOT EXISTS memory_observations_fts_delete
        AFTER DELETE ON memory_observations BEGIN
            DELETE FROM memory_observations_fts WHERE rowid = old.id;
        END;

        CREATE TRIGGER IF NOT EXISTS memory_observations_fts_update
        AFTER UPDATE OF summary, subject, observation_type, project_key, entities_json ON memory_observations BEGIN
            DELETE FROM memory_observations_fts WHERE rowid = old.id;
            INSERT INTO memory_observations_fts(
                rowid, observation_uid, summary, subject, observation_type, project_key, entities
            )
            VALUES (
                new.id, new.observation_uid, new.summary, new.subject,
                new.observation_type, new.project_key, new.entities_json
            );
        END;
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO memory_observations_fts(
            rowid, observation_uid, summary, subject, observation_type, project_key, entities
        )
        SELECT id, observation_uid, summary, subject, observation_type, project_key, entities_json
          FROM memory_observations
        """
    )


def _m62_memory_observations_fts_trigger_fix(conn):
    """Make Memory Observations FTS triggers safe for repeated upserts."""
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_observations_fts USING fts5(
                observation_uid UNINDEXED,
                summary,
                subject,
                observation_type,
                project_key,
                entities,
                tokenize='unicode61 remove_diacritics 2'
            )
            """
        )
    except Exception:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_observations_fts (
                observation_uid TEXT PRIMARY KEY,
                summary TEXT DEFAULT '',
                subject TEXT DEFAULT '',
                observation_type TEXT DEFAULT '',
                project_key TEXT DEFAULT '',
                entities TEXT DEFAULT ''
            )
            """
        )
    conn.executescript(
        """
        DROP TRIGGER IF EXISTS memory_observations_fts_insert;
        DROP TRIGGER IF EXISTS memory_observations_fts_delete;
        DROP TRIGGER IF EXISTS memory_observations_fts_update;

        CREATE TRIGGER IF NOT EXISTS memory_observations_fts_insert
        AFTER INSERT ON memory_observations BEGIN
            INSERT INTO memory_observations_fts(
                rowid, observation_uid, summary, subject, observation_type, project_key, entities
            )
            VALUES (
                new.id, new.observation_uid, new.summary, new.subject,
                new.observation_type, new.project_key, new.entities_json
            );
        END;

        CREATE TRIGGER IF NOT EXISTS memory_observations_fts_delete
        AFTER DELETE ON memory_observations BEGIN
            DELETE FROM memory_observations_fts WHERE rowid = old.id;
        END;

        CREATE TRIGGER IF NOT EXISTS memory_observations_fts_update
        AFTER UPDATE OF summary, subject, observation_type, project_key, entities_json ON memory_observations BEGIN
            DELETE FROM memory_observations_fts WHERE rowid = old.id;
            INSERT INTO memory_observations_fts(
                rowid, observation_uid, summary, subject, observation_type, project_key, entities
            )
            VALUES (
                new.id, new.observation_uid, new.summary, new.subject,
                new.observation_type, new.project_key, new.entities_json
            );
        END;
        """
    )
    conn.execute("DELETE FROM memory_observations_fts")
    conn.execute(
        """
        INSERT INTO memory_observations_fts(
            rowid, observation_uid, summary, subject, observation_type, project_key, entities
        )
        SELECT id, observation_uid, summary, subject, observation_type, project_key, entities_json
          FROM memory_observations
        """
    )


def _m63_local_context_layer(conn):
    """Local Context Layer storage for on-device memory indexing."""
    _m63_repair_legacy_local_context_columns(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS local_index_roots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            root_path TEXT NOT NULL UNIQUE,
            display_path TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'normal',
            depth INTEGER NOT NULL DEFAULT 2,
            source TEXT NOT NULL DEFAULT 'user',
            remote INTEGER NOT NULL DEFAULT 0,
            seed_version INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'active',
            last_scan_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS local_index_exclusions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            display_path TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'user',
            kind TEXT NOT NULL DEFAULT 'folder',
            reason TEXT NOT NULL DEFAULT 'user',
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS local_index_file_type_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            extension TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT 'ignore',
            source TEXT NOT NULL DEFAULT 'user',
            priority INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(extension, source)
        );

        CREATE TABLE IF NOT EXISTS local_index_jobs (
            job_id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 50,
            claimed_by TEXT NOT NULL DEFAULT '',
            lease_expires_at REAL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL,
            last_error_code TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS local_index_checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            root_id INTEGER,
            phase TEXT NOT NULL DEFAULT 'quick_index',
            current_path TEXT NOT NULL DEFAULT '',
            total_seen INTEGER NOT NULL DEFAULT 0,
            total_changed INTEGER NOT NULL DEFAULT 0,
            total_errors INTEGER NOT NULL DEFAULT 0,
            eta_seconds REAL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS local_index_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS local_index_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id TEXT NOT NULL DEFAULT '',
            path TEXT NOT NULL DEFAULT '',
            phase TEXT NOT NULL,
            error_code TEXT NOT NULL,
            user_message TEXT NOT NULL DEFAULT '',
            technical_detail TEXT NOT NULL DEFAULT '',
            retryable INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS local_index_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL,
            level TEXT NOT NULL,
            event TEXT NOT NULL,
            message TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS local_assets (
            asset_id TEXT PRIMARY KEY,
            root_id INTEGER,
            path TEXT NOT NULL UNIQUE,
            display_path TEXT NOT NULL,
            parent_path TEXT NOT NULL DEFAULT '',
            volume_id TEXT NOT NULL DEFAULT '',
            file_type TEXT NOT NULL DEFAULT 'file',
            extension TEXT NOT NULL DEFAULT '',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            created_at_fs REAL,
            modified_at_fs REAL,
            quick_fingerprint TEXT NOT NULL DEFAULT '',
            depth INTEGER NOT NULL DEFAULT 2,
            depth_reason TEXT NOT NULL DEFAULT 'default',
            phase TEXT NOT NULL DEFAULT 'quick_index',
            status TEXT NOT NULL DEFAULT 'active',
            privacy_class TEXT NOT NULL DEFAULT 'normal',
            permission_state TEXT NOT NULL DEFAULT 'unknown',
            first_seen_at REAL NOT NULL,
            last_seen_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            deleted_at REAL
        );

        CREATE TABLE IF NOT EXISTS local_asset_versions (
            version_id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL,
            quick_fingerprint TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL DEFAULT '',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            modified_at_fs REAL,
            summary TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS local_chunks (
            chunk_id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL,
            version_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL DEFAULT 0,
            text TEXT NOT NULL DEFAULT '',
            token_count INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS local_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            version_id TEXT NOT NULL,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL DEFAULT 'entity',
            confidence REAL NOT NULL DEFAULT 0.5,
            evidence TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            UNIQUE(entity_id, asset_id, version_id)
        );

        CREATE TABLE IF NOT EXISTS local_relations (
            relation_id TEXT PRIMARY KEY,
            source_asset_id TEXT NOT NULL,
            target_asset_id TEXT NOT NULL DEFAULT '',
            target_ref TEXT NOT NULL DEFAULT '',
            relation_type TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.5,
            evidence TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS local_embeddings (
            embedding_id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            model_revision TEXT NOT NULL DEFAULT '',
            dimension INTEGER NOT NULL,
            vector_json TEXT NOT NULL,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS local_context_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_hash TEXT NOT NULL,
            intent TEXT NOT NULL DEFAULT 'answer',
            result_count INTEGER NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0.0,
            warnings_json TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_local_index_roots_status
            ON local_index_roots(status);
        CREATE INDEX IF NOT EXISTS idx_local_index_roots_source
            ON local_index_roots(source, status);
        CREATE INDEX IF NOT EXISTS idx_local_index_exclusions_source
            ON local_index_exclusions(source);
        CREATE INDEX IF NOT EXISTS idx_local_index_file_type_rules_ext
            ON local_index_file_type_rules(extension, source);
        CREATE INDEX IF NOT EXISTS idx_local_index_jobs_status_priority
            ON local_index_jobs(status, priority, created_at);
        CREATE INDEX IF NOT EXISTS idx_local_index_jobs_asset
            ON local_index_jobs(asset_id);
        CREATE INDEX IF NOT EXISTS idx_local_index_errors_created
            ON local_index_errors(created_at);
        CREATE INDEX IF NOT EXISTS idx_local_index_logs_created
            ON local_index_logs(created_at);
        CREATE INDEX IF NOT EXISTS idx_local_assets_root_status
            ON local_assets(root_id, status);
        CREATE INDEX IF NOT EXISTS idx_local_assets_path
            ON local_assets(path);
        CREATE INDEX IF NOT EXISTS idx_local_assets_parent
            ON local_assets(parent_path);
        CREATE INDEX IF NOT EXISTS idx_local_assets_volume
            ON local_assets(volume_id);
        CREATE INDEX IF NOT EXISTS idx_local_versions_asset
            ON local_asset_versions(asset_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_local_chunks_asset
            ON local_chunks(asset_id);
        CREATE INDEX IF NOT EXISTS idx_local_entities_name
            ON local_entities(name);
        CREATE INDEX IF NOT EXISTS idx_local_entities_asset
            ON local_entities(asset_id);
        CREATE INDEX IF NOT EXISTS idx_local_relations_source
            ON local_relations(source_asset_id, relation_type);
        CREATE INDEX IF NOT EXISTS idx_local_embeddings_chunk
            ON local_embeddings(chunk_id);
        """
    )


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return bool(row)


def _m63_repair_legacy_local_context_columns(conn):
    """Add v2 columns before m63 creates indexes that reference them.

    Existing sidecar DBs can already have m63-era tables without the v2
    columns. CREATE TABLE IF NOT EXISTS will not alter those tables, so index
    creation must be preceded by additive repairs.
    """
    if _table_exists(conn, "local_index_roots"):
        _migrate_add_column(conn, "local_index_roots", "source", "TEXT NOT NULL DEFAULT 'legacy'")
        _migrate_add_column(conn, "local_index_roots", "remote", "INTEGER NOT NULL DEFAULT 0")
        _migrate_add_column(conn, "local_index_roots", "seed_version", "INTEGER NOT NULL DEFAULT 1")
    if _table_exists(conn, "local_index_exclusions"):
        _migrate_add_column(conn, "local_index_exclusions", "source", "TEXT NOT NULL DEFAULT 'legacy'")
        _migrate_add_column(conn, "local_index_exclusions", "kind", "TEXT NOT NULL DEFAULT 'folder'")
    if _table_exists(conn, "local_index_file_type_rules"):
        _migrate_add_column(conn, "local_index_file_type_rules", "source", "TEXT NOT NULL DEFAULT 'legacy'")
        _migrate_add_column(conn, "local_index_file_type_rules", "priority", "INTEGER NOT NULL DEFAULT 0")
        _migrate_add_column(conn, "local_index_file_type_rules", "reason", "TEXT NOT NULL DEFAULT ''")
        _migrate_add_column(conn, "local_index_file_type_rules", "updated_at", "REAL NOT NULL DEFAULT 0")


def _m64_local_context_live_dirs(conn):
    """Track known folders so local context can detect new/deleted/changed files quickly."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS local_index_dirs (
            dir_id TEXT PRIMARY KEY,
            root_id INTEGER,
            path TEXT NOT NULL UNIQUE,
            display_path TEXT NOT NULL,
            parent_path TEXT NOT NULL DEFAULT '',
            quick_fingerprint TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            first_seen_at REAL NOT NULL,
            last_seen_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            deleted_at REAL
        );

        CREATE INDEX IF NOT EXISTS idx_local_index_dirs_root_status
            ON local_index_dirs(root_id, status);
        CREATE INDEX IF NOT EXISTS idx_local_index_dirs_path
            ON local_index_dirs(path);
        CREATE INDEX IF NOT EXISTS idx_local_index_dirs_parent
            ON local_index_dirs(parent_path);
        CREATE INDEX IF NOT EXISTS idx_local_assets_updated
            ON local_assets(updated_at);
        """
    )


def _backfill_diary_quality(conn):
    for table in ("session_diary", "diary_archive"):
        conn.execute(f"""
            UPDATE {table}
            SET quality_tier = CASE
                WHEN source = 'auto-close' THEN 'auto_close_minimal'
                WHEN source IN ('cron', 'automation') OR COALESCE(summary, '') LIKE '[AUTO-%' THEN 'fallback_minimal'
                ELSE 'agent_authored'
            END
            WHERE quality_tier IS NULL
               OR quality_tier = ''
               OR quality_tier = 'agent_authored'
        """)
        conn.execute(f"""
            UPDATE {table}
            SET quality_score =
                CASE WHEN COALESCE(summary, '') != '' THEN 25 ELSE 0 END +
                CASE WHEN COALESCE(decisions, '') != '' THEN 20 ELSE 0 END +
                CASE WHEN COALESCE(pending, '') != '' THEN 15 ELSE 0 END +
                CASE WHEN COALESCE(context_next, '') != '' THEN 20 ELSE 0 END +
                CASE WHEN COALESCE(self_critique, '') != '' THEN 20 ELSE 0 END
            WHERE quality_score IS NULL OR quality_score = 0
        """)


def _m65_diary_quality(conn):
    _m4_session_diary_columns(conn)
    _m7_diary_source_and_draft(conn)
    _m10_diary_archive(conn)
    _migrate_add_column(conn, "session_diary", "quality_tier", "TEXT DEFAULT 'agent_authored'")
    _migrate_add_column(conn, "session_diary", "quality_score", "INTEGER DEFAULT 0")
    _migrate_add_column(conn, "diary_archive", "quality_tier", "TEXT DEFAULT 'agent_authored'")
    _migrate_add_column(conn, "diary_archive", "quality_score", "INTEGER DEFAULT 0")
    _backfill_diary_quality(conn)
    _migrate_add_index(conn, "idx_session_diary_quality", "session_diary", "quality_tier, quality_score, created_at")
    _migrate_add_index(conn, "idx_diary_archive_quality", "diary_archive", "quality_tier, quality_score, created_at")


def _m66_transcript_index(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transcript_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_client TEXT NOT NULL,
            conversation_id TEXT DEFAULT '',
            session_id TEXT DEFAULT '',
            message_count INTEGER DEFAULT 0,
            user_message_count INTEGER DEFAULT 0,
            first_user_at TEXT DEFAULT '',
            last_user_at TEXT DEFAULT '',
            path_ref TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            indexed_at TEXT DEFAULT (datetime('now')),
            modified_at TEXT DEFAULT '',
            content_hash TEXT NOT NULL,
            sanitized_summary TEXT DEFAULT '',
            metadata_json TEXT DEFAULT '{}',
            UNIQUE(source_client, path_ref)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_transcript_index_client_modified ON transcript_index(source_client, modified_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_transcript_index_session ON transcript_index(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_transcript_index_conversation ON transcript_index(conversation_id)")


def _m67_diary_quality_backfill_repair(conn):
    """Repair DBs that already ran the original m65 default-only backfill."""
    _m4_session_diary_columns(conn)
    _m7_diary_source_and_draft(conn)
    _m10_diary_archive(conn)
    _migrate_add_column(conn, "session_diary", "quality_tier", "TEXT DEFAULT 'agent_authored'")
    _migrate_add_column(conn, "session_diary", "quality_score", "INTEGER DEFAULT 0")
    _migrate_add_column(conn, "diary_archive", "quality_tier", "TEXT DEFAULT 'agent_authored'")
    _migrate_add_column(conn, "diary_archive", "quality_score", "INTEGER DEFAULT 0")
    _backfill_diary_quality(conn)
    _migrate_add_index(conn, "idx_session_diary_quality", "session_diary", "quality_tier, quality_score, created_at")
    _migrate_add_index(conn, "idx_diary_archive_quality", "diary_archive", "quality_tier, quality_score, created_at")


def _m68_memory_fabric_index(conn):
    """Memory Fabric v1 index tables for historical backup memory."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS memory_fabric_sources (
            source_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            item_count INTEGER NOT NULL DEFAULT 0,
            last_indexed_at TEXT DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS historical_diary_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_backup_path TEXT NOT NULL,
            source_table TEXT NOT NULL DEFAULT 'session_diary',
            source_row_id INTEGER NOT NULL,
            session_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            decisions TEXT NOT NULL DEFAULT '',
            pending TEXT NOT NULL DEFAULT '',
            context_next TEXT NOT NULL DEFAULT '',
            mental_state TEXT NOT NULL DEFAULT '',
            self_critique TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL UNIQUE,
            indexed_at TEXT DEFAULT (datetime('now')),
            metadata_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(source_backup_path, source_table, source_row_id)
        );

        CREATE INDEX IF NOT EXISTS idx_historical_diary_session
            ON historical_diary_index(session_id);
        CREATE INDEX IF NOT EXISTS idx_historical_diary_created
            ON historical_diary_index(created_at);
        CREATE INDEX IF NOT EXISTS idx_historical_diary_domain
            ON historical_diary_index(domain);
        """
    )


def _m71_causal_edge_candidates(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS causal_edge_candidates (
            candidate_uid TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            source_type TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            relation TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_ref TEXT NOT NULL,
            reason_public TEXT DEFAULT '',
            evidence_refs_json TEXT DEFAULT '[]',
            source_event_uid TEXT DEFAULT '',
            producer TEXT NOT NULL,
            project_key TEXT DEFAULT '',
            privacy_level TEXT DEFAULT 'normal',
            confidence REAL DEFAULT 0.5,
            status TEXT DEFAULT 'proposed',
            review_reason TEXT DEFAULT '',
            promoted_edge_uid TEXT DEFAULT '',
            metadata_json TEXT DEFAULT '{}'
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_causal_candidates_status_updated "
        "ON causal_edge_candidates(status, updated_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_causal_candidates_source "
        "ON causal_edge_candidates(source_type, source_ref, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_causal_candidates_target "
        "ON causal_edge_candidates(target_type, target_ref, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_causal_candidates_project "
        "ON causal_edge_candidates(project_key, status, updated_at)"
    )


def _m72_memory_utility(conn):
    """Append-only memory usefulness ledger and idempotent delta applications."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_use_events (
            event_uid TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            retrieval_trace_id TEXT DEFAULT '',
            route_event_id TEXT DEFAULT '',
            session_id TEXT DEFAULT '',
            conversation_id TEXT DEFAULT '',
            project_key TEXT DEFAULT '',
            client TEXT DEFAULT '',
            consumer_ref TEXT DEFAULT '',
            memory_ref TEXT NOT NULL,
            memory_kind TEXT NOT NULL,
            source_ref TEXT DEFAULT '',
            query_hash TEXT DEFAULT '',
            query_preview_redacted TEXT DEFAULT '',
            context_kind TEXT DEFAULT '',
            use_stage TEXT NOT NULL DEFAULT 'retrieved',
            outcome TEXT NOT NULL DEFAULT 'unknown',
            used_in_answer INTEGER NOT NULL DEFAULT 0,
            cited_in_answer INTEGER NOT NULL DEFAULT 0,
            acted_on INTEGER NOT NULL DEFAULT 0,
            validated_by_ref TEXT DEFAULT '',
            evidence_refs_json TEXT DEFAULT '[]',
            reason_code TEXT DEFAULT '',
            delta_json TEXT DEFAULT '{}',
            policy_version TEXT DEFAULT 'memory_utility_v1',
            confidence REAL DEFAULT 0.5,
            privacy_level TEXT DEFAULT 'normal',
            redaction_applied INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT DEFAULT '{}'
        )
        """
    )
    _migrate_add_index(conn, "idx_memory_use_events_memory_created", "memory_use_events", "memory_ref, created_at")
    _migrate_add_index(conn, "idx_memory_use_events_memory_reason", "memory_use_events", "memory_ref, reason_code, created_at")
    _migrate_add_index(conn, "idx_memory_use_events_trace_stage", "memory_use_events", "retrieval_trace_id, use_stage")
    _migrate_add_index(conn, "idx_memory_use_events_query_created", "memory_use_events", "query_hash, created_at")
    _migrate_add_index(conn, "idx_memory_use_events_stage_outcome", "memory_use_events", "use_stage, outcome, created_at")
    _migrate_add_index(conn, "idx_memory_use_events_policy_created", "memory_use_events", "policy_version, created_at")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_utility_applications (
            application_uid TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            memory_ref TEXT NOT NULL,
            memory_kind TEXT NOT NULL,
            target_field TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            event_uids_hash TEXT NOT NULL,
            event_uids_json TEXT NOT NULL DEFAULT '[]',
            old_value REAL,
            new_value REAL,
            delta REAL NOT NULL DEFAULT 0,
            applied INTEGER NOT NULL DEFAULT 0,
            rolled_back INTEGER NOT NULL DEFAULT 0,
            rollback_ref TEXT DEFAULT '',
            metadata_json TEXT DEFAULT '{}'
        )
        """
    )
    _migrate_add_index(conn, "idx_memory_utility_app_memory", "memory_utility_applications", "memory_ref, created_at")
    _migrate_add_index(conn, "idx_memory_utility_app_policy", "memory_utility_applications", "policy_version, created_at")
    _migrate_add_index(conn, "idx_memory_utility_app_rollback", "memory_utility_applications", "rolled_back, created_at")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_utility_application_events (
            application_uid TEXT NOT NULL,
            event_uid TEXT NOT NULL,
            memory_ref TEXT NOT NULL,
            target_field TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            UNIQUE(event_uid, memory_ref, target_field, policy_version)
        )
        """
    )
    _migrate_add_index(conn, "idx_memory_utility_app_events_event", "memory_utility_application_events", "event_uid")


def _m73_operational_state_snapshots(conn):
    """Operational state policy snapshots by area/scope."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS operational_state_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_uid TEXT NOT NULL UNIQUE,
            policy_version TEXT NOT NULL,
            created_at REAL NOT NULL,
            area_key TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            task_type TEXT DEFAULT '',
            caution_level TEXT NOT NULL,
            communication_mode TEXT NOT NULL,
            detail_mode TEXT NOT NULL,
            verification_requirement TEXT NOT NULL,
            autonomy_limit TEXT NOT NULL,
            area_risk TEXT NOT NULL,
            reason_codes_json TEXT NOT NULL DEFAULT '[]',
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            privacy_level TEXT NOT NULL DEFAULT 'normal',
            input_hash TEXT NOT NULL,
            expires_at REAL NOT NULL,
            decay_policy_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    _migrate_add_index(conn, "idx_operational_state_area_created", "operational_state_snapshots", "area_key, created_at")
    _migrate_add_index(conn, "idx_operational_state_scope", "operational_state_snapshots", "scope_key, created_at")
    _migrate_add_index(conn, "idx_operational_state_expires", "operational_state_snapshots", "expires_at")


def _m74_entity_live_profiles(conn):
    """EntityLiveProfile cache plus managed asset/context update bridges.

    The tables below are deliberately non-authoritative. Entity identity,
    artifacts, local evidence, and history remain owned by their existing
    stores; this migration only adds cache/bridge/event surfaces.
    """
    _m11_artifact_registry(conn)
    _m59_memory_events(conn)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entity_profile_cache (
            profile_uid TEXT PRIMARY KEY,
            profile_version TEXT NOT NULL DEFAULT 'entity_live_profile.v1',
            entity_key TEXT NOT NULL,
            canonical_kind TEXT DEFAULT '',
            canonical_name TEXT DEFAULT '',
            source_refs_hash TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            profile_redacted_json TEXT NOT NULL DEFAULT '{}',
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            stale_status TEXT NOT NULL DEFAULT 'unknown',
            privacy_level TEXT NOT NULL DEFAULT 'normal',
            allowed_surfaces_json TEXT NOT NULL DEFAULT '[]',
            last_verified_at REAL,
            expires_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(entity_key, profile_version, source_refs_hash, input_hash)
        )
        """
    )
    _migrate_add_index(conn, "idx_entity_profile_cache_entity", "entity_profile_cache", "entity_key, profile_version")
    _migrate_add_index(conn, "idx_entity_profile_cache_expires", "entity_profile_cache", "expires_at")
    _migrate_add_index(conn, "idx_entity_profile_cache_stale", "entity_profile_cache", "stale_status, expires_at")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nexo_managed_assets (
            asset_uid TEXT PRIMARY KEY,
            artifact_id INTEGER REFERENCES artifact_registry(id) ON DELETE SET NULL,
            entity_key TEXT NOT NULL,
            project_key TEXT DEFAULT '',
            asset_kind TEXT NOT NULL DEFAULT 'other',
            provider_ref TEXT DEFAULT '',
            provider_redacted TEXT DEFAULT '',
            external_ref_hash TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'planned',
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            privacy_level TEXT NOT NULL DEFAULT 'normal',
            last_verified_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_nexo_managed_assets_provider_external
        ON nexo_managed_assets(provider_ref, external_ref_hash)
        WHERE provider_ref != '' AND external_ref_hash != ''
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_nexo_managed_assets_artifact
        ON nexo_managed_assets(artifact_id)
        WHERE artifact_id IS NOT NULL
        """
    )
    _migrate_add_index(conn, "idx_nexo_managed_assets_entity", "nexo_managed_assets", "entity_key")
    _migrate_add_index(conn, "idx_nexo_managed_assets_project", "nexo_managed_assets", "project_key")
    _migrate_add_index(conn, "idx_nexo_managed_assets_status", "nexo_managed_assets", "status")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_context_updated (
            event_uid TEXT PRIMARY KEY,
            entity_key TEXT NOT NULL,
            asset_uid TEXT NOT NULL,
            artifact_id INTEGER,
            project_key TEXT DEFAULT '',
            change_type TEXT NOT NULL,
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            privacy_level TEXT NOT NULL DEFAULT 'normal',
            redaction_applied INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            memory_event_uid TEXT DEFAULT ''
        )
        """
    )
    _migrate_add_index(conn, "idx_asset_context_updated_entity", "asset_context_updated", "entity_key, created_at")
    _migrate_add_index(conn, "idx_asset_context_updated_asset", "asset_context_updated", "asset_uid, created_at")
    _migrate_add_index(conn, "idx_asset_context_updated_artifact", "asset_context_updated", "artifact_id, created_at")


def _m75_failure_prevention_ledger(conn):
    """FailurePrevention ledger for autopsy candidates and antibody proposals.

    This is deliberately a non-authoritative coordination layer. Canonical
    rules, outcomes, guard checks, protocol debt, hook runs, corrections, and
    memory events remain owned by their existing tables.
    """
    _m6_error_guard_tables(conn)
    _m8_adaptive_log_and_somatic(conn)
    _m22_protocol_discipline_tables(conn)
    _m32_outcomes(conn)
    _m39_hook_runs(conn)
    _m56_session_correction_requirements(conn)
    _m59_memory_events(conn)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS failure_prevention_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            failure_uid TEXT NOT NULL UNIQUE,
            policy_version TEXT NOT NULL DEFAULT 'failure_prevention.v1',
            failure_type TEXT NOT NULL DEFAULT 'other',
            area TEXT DEFAULT '',
            entity_refs_json TEXT NOT NULL DEFAULT '[]',
            primary_source_type TEXT NOT NULL,
            primary_source_ref TEXT NOT NULL,
            source_event_refs_json TEXT NOT NULL DEFAULT '[]',
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            symptom_json TEXT NOT NULL DEFAULT '{}',
            trigger_json TEXT NOT NULL DEFAULT '{}',
            missed_signal_json TEXT NOT NULL DEFAULT '{}',
            wrong_assumption_json TEXT NOT NULL DEFAULT '{}',
            root_cause_json TEXT NOT NULL DEFAULT '{}',
            corrective_action_json TEXT NOT NULL DEFAULT '{}',
            severity TEXT NOT NULL DEFAULT 'p3',
            frequency_count INTEGER NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'candidate',
            learning_resolution_json TEXT NOT NULL DEFAULT '{}',
            antibody_refs_json TEXT NOT NULL DEFAULT '[]',
            privacy_level TEXT NOT NULL DEFAULT 'normal',
            allowed_surfaces_json TEXT NOT NULL DEFAULT '["debug_local","audit"]',
            opened_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            review_due_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            false_positive_count INTEGER NOT NULL DEFAULT 0,
            last_triggered_at REAL NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            CHECK(severity IN ('p0','p1','p2','p3','p4')),
            CHECK(status IN (
                'candidate','analyzing','action_required','antibody_pending',
                'verifying','verified','resolved','rejected','false_positive',
                'expired','rolled_back','conflict_review'
            )),
            CHECK(privacy_level IN ('public','normal','private','sensitive','secret')),
            CHECK(frequency_count >= 0),
            CHECK(false_positive_count >= 0),
            CHECK(confidence >= 0.0 AND confidence <= 1.0)
        )
        """
    )
    _migrate_add_index(conn, "idx_failure_cases_area_status", "failure_prevention_cases", "area, status")
    _migrate_add_index(conn, "idx_failure_cases_severity_status", "failure_prevention_cases", "severity, status")
    _migrate_add_index(conn, "idx_failure_cases_status_review", "failure_prevention_cases", "status, review_due_at")
    _migrate_add_index(conn, "idx_failure_cases_updated", "failure_prevention_cases", "updated_at")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS failure_source_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_event_uid TEXT NOT NULL UNIQUE,
            failure_uid TEXT NOT NULL,
            policy_version TEXT NOT NULL DEFAULT 'failure_prevention.v1',
            source_type TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            observed_at REAL NOT NULL,
            validated INTEGER NOT NULL DEFAULT 0,
            validator TEXT DEFAULT '',
            validation_error TEXT DEFAULT '',
            privacy_level TEXT NOT NULL DEFAULT 'normal',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(failure_uid) REFERENCES failure_prevention_cases(failure_uid) ON DELETE CASCADE,
            CHECK(validated IN (0, 1)),
            CHECK(privacy_level IN ('public','normal','private','sensitive','secret'))
        )
        """
    )
    _migrate_add_index(conn, "idx_failure_source_events_failure", "failure_source_events", "failure_uid")
    _migrate_add_index(conn, "idx_failure_source_events_type", "failure_source_events", "source_type")
    _migrate_add_index(conn, "idx_failure_source_events_observed", "failure_source_events", "observed_at")
    _migrate_add_index(conn, "idx_failure_source_events_ref", "failure_source_events", "source_type, source_ref")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS antibody_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            antibody_uid TEXT NOT NULL UNIQUE,
            failure_uid TEXT NOT NULL,
            policy_version TEXT NOT NULL DEFAULT 'failure_prevention.v1',
            action_type TEXT NOT NULL,
            target_system TEXT NOT NULL,
            target_ref TEXT NOT NULL,
            action_payload_ref TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'proposed',
            activation_policy TEXT NOT NULL DEFAULT 'candidate_only',
            required_verification TEXT DEFAULT '',
            verification_ref TEXT DEFAULT '',
            verification_status TEXT NOT NULL DEFAULT 'missing',
            approved_by TEXT DEFAULT '',
            approved_ref TEXT DEFAULT '',
            rollback_ref TEXT DEFAULT '',
            review_due_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            privacy_level TEXT NOT NULL DEFAULT 'normal',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            applied_at REAL,
            verified_at REAL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(failure_uid) REFERENCES failure_prevention_cases(failure_uid) ON DELETE CASCADE,
            CHECK(target_ref != ''),
            CHECK(status IN ('proposed','approved','applied','verifying','verified','rejected','expired','rolled_back','false_positive')),
            CHECK(activation_policy IN ('candidate_only','shadow','warn','block_after_verification','manual_approval_required')),
            CHECK(verification_status IN ('missing','pending','passed','failed','not_applicable')),
            CHECK(privacy_level IN ('public','normal','private','sensitive','secret'))
        )
        """
    )
    _migrate_add_index(conn, "idx_antibody_actions_failure", "antibody_actions", "failure_uid")
    _migrate_add_index(conn, "idx_antibody_actions_status", "antibody_actions", "status")
    _migrate_add_index(conn, "idx_antibody_actions_target", "antibody_actions", "action_type, target_system, target_ref")
    _migrate_add_index(conn, "idx_antibody_actions_verification", "antibody_actions", "verification_status, review_due_at")


def _m76_semantic_layers(conn):
    """SemanticLayers cache for compact, source-backed continuity.

    The tables are deliberately non-authoritative. Original facts remain owned
    by diary, workflow, task, evidence, memory, transcript-index and continuity
    stores; these rows only cache redacted views with source fingerprints.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_diary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            decisions TEXT NOT NULL DEFAULT '',
            discarded TEXT,
            pending TEXT,
            context_next TEXT,
            mental_state TEXT,
            domain TEXT,
            user_signals TEXT,
            summary TEXT NOT NULL DEFAULT ''
        )
        """
    )
    _m4_session_diary_columns(conn)
    _m7_diary_source_and_draft(conn)
    _m22_protocol_discipline_tables(conn)
    _m24_durable_workflow_runtime(conn)
    _m25_workflow_goal_stack(conn)
    _m30_hot_context_memory(conn)
    _m54_continuity_snapshots(conn)
    _m59_memory_events(conn)
    _m60_memory_observations(conn)
    _m66_transcript_index(conn)
    _m70_commitments(conn)
    _m74_entity_live_profiles(conn)
    _m75_failure_prevention_ledger(conn)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS semantic_layers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            layer_uid TEXT NOT NULL UNIQUE,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            layer_kind TEXT NOT NULL,
            policy_version TEXT NOT NULL DEFAULT 'semantic_layers_v1',
            status TEXT NOT NULL DEFAULT 'fresh',
            quality_state TEXT NOT NULL DEFAULT 'complete',
            value_redacted TEXT NOT NULL DEFAULT '',
            value_ref TEXT NOT NULL DEFAULT '',
            token_size INTEGER NOT NULL DEFAULT 0,
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            source_fingerprint TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            privacy_level TEXT NOT NULL DEFAULT 'normal',
            allowed_surfaces_json TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 0.0,
            coverage REAL NOT NULL DEFAULT 0.0,
            generated_by TEXT NOT NULL DEFAULT '',
            generator_version TEXT NOT NULL DEFAULT 'continuity_layer_builder_v1',
            generated_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            source_max_updated_at TEXT NOT NULL DEFAULT '',
            expires_at REAL NOT NULL DEFAULT 0,
            stale_at REAL NOT NULL DEFAULT 0,
            stale_reason TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            CHECK(scope_type IN (
                'session','conversation','workflow','workflow_goal',
                'protocol_task','release','project_entity'
            )),
            CHECK(layer_kind IN (
                'headline','brief','timeline','decisions','commitments',
                'files','evidence','risks','next_action','semantic_tags',
                'source_map'
            )),
            CHECK(status IN ('fresh','stale','expired','invalid')),
            CHECK(quality_state IN (
                'complete','partial','degraded','conflicted',
                'source_missing','invalid'
            )),
            CHECK(privacy_level IN ('public','normal','private','sensitive','secret')),
            CHECK(confidence >= 0.0 AND confidence <= 1.0),
            CHECK(coverage >= 0.0 AND coverage <= 1.0),
            CHECK(token_size >= 0),
            UNIQUE(scope_type, scope_id, layer_kind, source_fingerprint, policy_version)
        )
        """
    )
    _migrate_add_index(conn, "idx_semantic_layers_scope_kind_status", "semantic_layers", "scope_type, scope_id, layer_kind, status, updated_at")
    _migrate_add_index(conn, "idx_semantic_layers_scope_status", "semantic_layers", "scope_type, scope_id, status, updated_at")
    _migrate_add_index(conn, "idx_semantic_layers_fingerprint", "semantic_layers", "source_fingerprint")
    _migrate_add_index(conn, "idx_semantic_layers_stale", "semantic_layers", "status, stale_at")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS semantic_layer_source_refs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            layer_uid TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_version TEXT NOT NULL,
            source_updated_at TEXT NOT NULL DEFAULT '',
            privacy_level TEXT NOT NULL DEFAULT 'normal',
            required_for_layer INTEGER NOT NULL DEFAULT 1,
            validation_status TEXT NOT NULL DEFAULT 'ok',
            validation_error TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(layer_uid) REFERENCES semantic_layers(layer_uid) ON DELETE CASCADE,
            CHECK(required_for_layer IN (0, 1)),
            CHECK(validation_status IN ('ok','missing','changed','invalid','unsupported')),
            CHECK(privacy_level IN ('public','normal','private','sensitive','secret')),
            UNIQUE(layer_uid, source_ref, source_version)
        )
        """
    )
    _migrate_add_index(conn, "idx_semantic_layer_sources_layer", "semantic_layer_source_refs", "layer_uid")
    _migrate_add_index(conn, "idx_semantic_layer_sources_ref", "semantic_layer_source_refs", "source_ref")
    _migrate_add_index(conn, "idx_semantic_layer_sources_kind", "semantic_layer_source_refs", "source_kind, validation_status")


def _m77_morning_briefing_presentation(conn):
    """Persist sanitized briefing bodies and Desktop read-state."""
    _m58_morning_briefing_runs(conn)
    _migrate_add_column(conn, "morning_briefing_runs", "body_text", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "morning_briefing_runs", "body_html", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "morning_briefing_runs", "artifact_json", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "morning_briefing_runs", "desktop_shown_at", "TEXT DEFAULT NULL")
    _migrate_add_column(conn, "morning_briefing_runs", "desktop_opened_at", "TEXT DEFAULT NULL")
    _migrate_add_column(conn, "morning_briefing_runs", "desktop_dismissed_at", "TEXT DEFAULT NULL")
    _migrate_add_index(
        conn,
        "idx_morning_briefing_runs_desktop",
        "morning_briefing_runs",
        "status, desktop_shown_at, finished_at",
    )


def _m78_operational_closure_plane(conn):
    """Operational Closure Plane MVP: canonical read-only closure items."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS closure_items (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'open',
            source_primary TEXT NOT NULL,
            source_key TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            impact_score REAL NOT NULL DEFAULT 0,
            urgency_score REAL NOT NULL DEFAULT 0,
            risk_score REAL NOT NULL DEFAULT 0,
            confidence_score REAL NOT NULL DEFAULT 0,
            priority_score REAL NOT NULL DEFAULT 0,
            safety_class TEXT NOT NULL DEFAULT 'normal',
            capability_required TEXT NOT NULL DEFAULT '',
            capability_status TEXT NOT NULL DEFAULT 'unknown',
            owner TEXT NOT NULL DEFAULT 'nero',
            next_action TEXT NOT NULL DEFAULT '',
            blocker_reason TEXT NOT NULL DEFAULT '',
            evidence_required TEXT NOT NULL DEFAULT '',
            evidence_observed TEXT NOT NULL DEFAULT '',
            deadline_at TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_progress_at TEXT NOT NULL DEFAULT '',
            closed_at TEXT NOT NULL DEFAULT '',
            close_reason TEXT NOT NULL DEFAULT '',
            source_payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(dedupe_key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS closure_item_sources (
            id TEXT PRIMARY KEY,
            closure_item_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_status TEXT NOT NULL DEFAULT '',
            source_payload_json TEXT NOT NULL DEFAULT '{}',
            observed_at TEXT NOT NULL,
            FOREIGN KEY(closure_item_id) REFERENCES closure_items(id) ON DELETE CASCADE,
            UNIQUE(closure_item_id, source_type, source_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS closure_item_events (
            id TEXT PRIMARY KEY,
            closure_item_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            from_state TEXT NOT NULL DEFAULT '',
            to_state TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '',
            actor TEXT NOT NULL DEFAULT 'nexo',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(closure_item_id) REFERENCES closure_items(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS closure_daily_snapshots (
            snapshot_date TEXT PRIMARY KEY,
            total_open INTEGER NOT NULL DEFAULT 0,
            total_verified INTEGER NOT NULL DEFAULT 0,
            total_waiting INTEGER NOT NULL DEFAULT 0,
            total_closed INTEGER NOT NULL DEFAULT 0,
            top_items_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    _migrate_add_index(conn, "idx_closure_items_state_priority", "closure_items", "state, priority_score DESC, updated_at")
    _migrate_add_index(conn, "idx_closure_items_source", "closure_items", "source_primary, source_key")
    _migrate_add_index(conn, "idx_closure_items_kind", "closure_items", "kind, state")
    _migrate_add_index(conn, "idx_closure_items_deadline", "closure_items", "deadline_at")
    _migrate_add_index(conn, "idx_closure_sources_item", "closure_item_sources", "closure_item_id")
    _migrate_add_index(conn, "idx_closure_sources_source", "closure_item_sources", "source_type, source_id")
    _migrate_add_index(conn, "idx_closure_events_item", "closure_item_events", "closure_item_id, created_at")


def _m79_operational_closure_links_readiness(conn):
    """Upgrade Closure Plane with links and capability readiness tables."""
    _m78_operational_closure_plane(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS closure_item_links (
            id TEXT PRIMARY KEY,
            closure_item_id TEXT NOT NULL,
            link_type TEXT NOT NULL,
            link_id TEXT NOT NULL,
            relation TEXT NOT NULL DEFAULT 'related',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(closure_item_id) REFERENCES closure_items(id) ON DELETE CASCADE,
            UNIQUE(closure_item_id, link_type, link_id, relation)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS closure_capability_readiness (
            id TEXT PRIMARY KEY,
            capability TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unknown',
            reason TEXT NOT NULL DEFAULT '',
            verified_at TEXT NOT NULL,
            verification_evidence TEXT NOT NULL DEFAULT '',
            expires_at TEXT NOT NULL DEFAULT '',
            UNIQUE(capability)
        )
        """
    )
    _migrate_add_index(conn, "idx_closure_links_item", "closure_item_links", "closure_item_id")
    _migrate_add_index(conn, "idx_closure_links_target", "closure_item_links", "link_type, link_id")
    _migrate_add_index(conn, "idx_closure_readiness_capability", "closure_capability_readiness", "capability, status")


def _m80_opportunity_orchestrator(conn):
    """Opportunity Orchestrator v1: sparse, evidence-backed proactive queue."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nexo_signals (
            id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL DEFAULT '',
            entity_ref TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            signal_kind TEXT NOT NULL,
            urgency REAL NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0,
            privacy_level TEXT NOT NULL DEFAULT 'normal',
            source_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL DEFAULT '',
            UNIQUE(source_type, source_id, signal_kind)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nexo_opportunities (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            hypothesis TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT 'general',
            opportunity_type TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            impact REAL NOT NULL DEFAULT 0,
            urgency REAL NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0,
            risk REAL NOT NULL DEFAULT 0,
            effort REAL NOT NULL DEFAULT 0,
            readiness REAL NOT NULL DEFAULT 0,
            user_burden_reduction REAL NOT NULL DEFAULT 0,
            interruption_cost REAL NOT NULL DEFAULT 0,
            strategic_alignment REAL NOT NULL DEFAULT 0,
            repetition_penalty REAL NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'candidate',
            owner TEXT NOT NULL DEFAULT 'nero',
            why_now TEXT NOT NULL DEFAULT '',
            next_action TEXT NOT NULL DEFAULT '',
            action_class TEXT NOT NULL DEFAULT 'read_only',
            authorization_status TEXT NOT NULL DEFAULT 'not_required',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL DEFAULT '',
            last_proposed_at TEXT NOT NULL DEFAULT '',
            source_payload_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(dedupe_key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nexo_opportunity_evidence (
            id TEXT PRIMARY KEY,
            opportunity_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            evidence_summary TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(opportunity_id) REFERENCES nexo_opportunities(id) ON DELETE CASCADE,
            UNIQUE(opportunity_id, source_type, source_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nexo_preparations (
            id TEXT PRIMARY KEY,
            opportunity_id TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            artifact_ref TEXT NOT NULL DEFAULT '',
            safe_mode INTEGER NOT NULL DEFAULT 1,
            approval_required INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'ready',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(opportunity_id) REFERENCES nexo_opportunities(id) ON DELETE CASCADE,
            UNIQUE(opportunity_id, artifact_type, artifact_ref)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nexo_proposals (
            id TEXT PRIMARY KEY,
            opportunity_id TEXT NOT NULL,
            surface TEXT NOT NULL DEFAULT 'home',
            copy TEXT NOT NULL DEFAULT '',
            cta_primary TEXT NOT NULL DEFAULT 'Inspect evidence',
            cta_secondary TEXT NOT NULL DEFAULT 'Snooze',
            shown_at TEXT NOT NULL DEFAULT '',
            feedback TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(opportunity_id) REFERENCES nexo_opportunities(id) ON DELETE CASCADE,
            UNIQUE(opportunity_id, surface)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nexo_proposal_events (
            id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            feedback TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(proposal_id) REFERENCES nexo_proposals(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nexo_suppression_rules (
            id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            expires_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(scope_type, scope_key, reason)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nexo_action_authorizations (
            id TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            allowed_action_class TEXT NOT NULL,
            max_cost REAL NOT NULL DEFAULT 0,
            expires_at TEXT NOT NULL DEFAULT '',
            granted_by TEXT NOT NULL DEFAULT '',
            evidence_ref TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(scope, allowed_action_class, evidence_ref)
        )
        """
    )
    _migrate_add_index(conn, "idx_nexo_signals_source", "nexo_signals", "source_type, source_id")
    _migrate_add_index(conn, "idx_nexo_signals_expires", "nexo_signals", "expires_at")
    _migrate_add_index(conn, "idx_nexo_opportunities_state_score", "nexo_opportunities", "state, score DESC, updated_at")
    _migrate_add_index(conn, "idx_nexo_opportunities_type", "nexo_opportunities", "opportunity_type, state")
    _migrate_add_index(conn, "idx_nexo_opportunities_expires", "nexo_opportunities", "expires_at")
    _migrate_add_index(conn, "idx_nexo_opportunity_evidence_item", "nexo_opportunity_evidence", "opportunity_id")
    _migrate_add_index(conn, "idx_nexo_preparations_item", "nexo_preparations", "opportunity_id, status")
    _migrate_add_index(conn, "idx_nexo_proposals_surface", "nexo_proposals", "surface, feedback")
    _migrate_add_index(conn, "idx_nexo_proposal_events_proposal", "nexo_proposal_events", "proposal_id, created_at")
    _migrate_add_index(conn, "idx_nexo_suppression_scope", "nexo_suppression_rules", "scope_type, scope_key")
    _migrate_add_index(conn, "idx_nexo_authorizations_scope", "nexo_action_authorizations", "scope, allowed_action_class")


def _m81_core_rules_product_metadata(conn):
    """Add product-core provenance and protection metadata to core_rules."""
    _m15_core_rules_tables(conn)
    _migrate_add_column(conn, "core_rules", "source_artifact", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "core_rules", "source_anchor", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "core_rules", "content_hash", "TEXT DEFAULT ''")
    _migrate_add_column(conn, "core_rules", "protected", "INTEGER NOT NULL DEFAULT 1")
    _migrate_add_column(conn, "core_rules", "severity", "TEXT DEFAULT 'critical'")
    _migrate_add_column(conn, "core_rules", "replacement_rule_id", "TEXT DEFAULT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_core_rules_protected ON core_rules(protected, is_active)")


def _m82_confidence_checks(conn):
    """Persist nexo_confidence_check calls so the answer-contract gate works.

    The G1 enforcer (hooks/g1_enforcer.py) treats a verify/ask/defer contract as
    fulfilled when a confidence_checks row exists for the session created after
    the task opened. No code ever created or wrote this table, so verify
    contracts were structurally unfulfillable. handle_confidence_check now writes
    a row per call; created_at uses datetime('now') to match opened_at format.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS confidence_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            task_id TEXT,
            goal_hash TEXT,
            task_type TEXT,
            area TEXT,
            response_mode TEXT,
            confidence INTEGER,
            high_stakes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_confidence_checks_session "
        "ON confidence_checks(session_id, created_at)"
    )


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
    (36, "goal_profiles", _m36_goal_profiles),
    (37, "cortex_goal_profile_trace", _m37_cortex_goal_profile_trace),
    (38, "evolution_log_proposal_payload", _m38_evolution_log_proposal_payload),
    (39, "hook_runs", _m39_hook_runs),
    (40, "classification_columns", _m40_classification_columns),
    (41, "automation_sessions_columns", _m41_automation_sessions_columns),
    (42, "v6_0_1_hotfix", _m42_v6_0_1_hotfix),
    (43, "session_claude_aliases", _m43_session_claude_aliases),
    (44, "entities_extended_schema", _m44_entities_extended_schema),
    (45, "personal_scripts_origin", _m45_personal_scripts_origin),
    (46, "email_accounts", _m46_email_accounts),
    (47, "email_operator_accounts", _m47_email_operator_accounts),
    (48, "email_agent_contract_backfill", _m48_email_agent_contract_backfill),
    (49, "protocol_guard_ack_backfill", _m49_protocol_guard_ack_backfill),
    (50, "dedupe_nexo_product_learning_pair", _m50_dedupe_nexo_product_learning_pair),
    (51, "lifecycle_events", _m51_lifecycle_events),
    (52, "lifecycle_canonical_plan", _m52_lifecycle_canonical_plan),
    (53, "session_conversation_identity", _m53_session_conversation_identity),
    (54, "continuity_snapshots", _m54_continuity_snapshots),
    (55, "cortex_critique_trace", _m55_cortex_critique_trace),
    (56, "session_correction_requirements", _m56_session_correction_requirements),
    (57, "hook_runs_retention", _m57_hook_runs_retention),
    (58, "morning_briefing_runs", _m58_morning_briefing_runs),
    (59, "memory_events", _m59_memory_events),
    (60, "memory_observations", _m60_memory_observations),
    (61, "memory_observations_fts", _m61_memory_observations_fts),
    (62, "memory_observations_fts_trigger_fix", _m62_memory_observations_fts_trigger_fix),
    (63, "local_context_layer", _m63_local_context_layer),
    (64, "local_context_live_dirs", _m64_local_context_live_dirs),
    (65, "diary_quality", _m65_diary_quality),
    (66, "transcript_index", _m66_transcript_index),
    (67, "diary_quality_backfill_repair", _m67_diary_quality_backfill_repair),
    (68, "memory_fabric_index", _m68_memory_fabric_index),
    (69, "provider_runtime_metadata", _m69_provider_runtime_metadata),
    (70, "commitments", _m70_commitments),
    (71, "causal_edge_candidates", _m71_causal_edge_candidates),
    (72, "memory_utility", _m72_memory_utility),
    (73, "operational_state_snapshots", _m73_operational_state_snapshots),
    (74, "entity_live_profiles", _m74_entity_live_profiles),
    (75, "failure_prevention_ledger", _m75_failure_prevention_ledger),
    (76, "semantic_layers", _m76_semantic_layers),
    (77, "morning_briefing_presentation", _m77_morning_briefing_presentation),
    (78, "operational_closure_plane", _m78_operational_closure_plane),
    (79, "operational_closure_links_readiness", _m79_operational_closure_links_readiness),
    (80, "opportunity_orchestrator", _m80_opportunity_orchestrator),
    (81, "core_rules_product_metadata", _m81_core_rules_product_metadata),
    (82, "confidence_checks", _m82_confidence_checks),
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

    applied = {
        int(r[0])
        for r in conn.execute("SELECT version FROM schema_migrations").fetchall()
        if str(r[0]).strip().isdigit()
    }

    failed = []
    for version, name, fn in MIGRATIONS:
        if version not in applied:
            try:
                fn(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (?, ?)",
                    (version, name)
                )
                applied.add(version)
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
