"""NEXO DB — Modular SQLite database layer.

This package replaces the monolithic db.py. All public functions are
re-exported here for full backwards compatibility:
    from db import get_db, create_learning, ...

Important:
`importlib.reload(db)` must also refresh the concrete submodules. The test
suite and several runtime repair flows rely on switching database paths or
runtime roots mid-process. If the package only re-exported functions from
already-imported submodules, those callables would keep pointing at stale
module state (especially old `db._core` connection globals).
"""

from __future__ import annotations

import importlib
import sys


def _load_submodule(name: str):
    """Import or reload a db submodule and expose it on the package."""
    module = sys.modules.get(name)
    if module is None:
        return importlib.import_module(name)
    return importlib.reload(module)


def _module(name: str):
    module = sys.modules.get(name)
    if module is None:
        module = importlib.import_module(name)
    return module


_core = _load_submodule("db._core")
_fts = _load_submodule("db._fts")
_schema = _load_submodule("db._schema")
_sessions = _load_submodule("db._sessions")
_reminders = _load_submodule("db._reminders")
_learnings = _load_submodule("db._learnings")
_credentials = _load_submodule("db._credentials")
_tasks = _load_submodule("db._tasks")
_entities = _load_submodule("db._entities")
_episodic = _load_submodule("db._episodic")
_evolution = _load_submodule("db._evolution")
_cron_runs = _load_submodule("db._cron_runs")
_protocol = _load_submodule("db._protocol")
_workflow = _load_submodule("db._workflow")
_watchers = _load_submodule("db._watchers")
_personal_scripts = _load_submodule("db._personal_scripts")
_skills = _load_submodule("db._skills")
_hot_context = _load_submodule("db._hot_context")
_drive = _load_submodule("db._drive")
_outcomes = _load_submodule("db._outcomes")

# Core: connection, constants, init, utils
from db._core import (
    DB_PATH, SESSION_STALE_SECONDS, MESSAGE_TTL_SECONDS, QUESTION_TTL_SECONDS,
    get_db, close_db, _get_raw_conn, _SerializedConnection,
    _shared_conn, _write_lock,
    init_db, _gen_id, now_epoch, local_time_str, _multi_word_like,
)

# FTS5 search
from db._fts import (
    fts_add_dir, fts_remove_dir, fts_list_dirs,
    rebuild_fts_index, fts_search, fts_upsert,
)

# Schema migrations
from db._schema import (
    run_migrations, get_schema_version,
)

# Sessions, file tracking, messages, questions
from db._sessions import (
    register_session, update_session, complete_session,
    get_active_sessions, clean_stale_sessions, search_sessions,
    track_files, untrack_files, get_all_tracked_files,
    send_message, get_inbox,
    ask_question, answer_question, get_pending_questions, check_answer,
)

# Reminders and followups
from db._reminders import (
    create_reminder, update_reminder, complete_reminder, delete_reminder,
    restore_reminder, add_reminder_note, get_reminders, get_reminder, get_reminder_history,
    create_followup, update_followup, complete_followup, delete_followup,
    restore_followup, add_followup_note, get_followups, get_followup, get_followup_history,
    find_similar_followups,
    add_item_history, get_item_history, validate_item_read_token,
)

# Learnings
from db._learnings import (
    create_learning, update_learning, supersede_learning, delete_learning,
    search_learnings, list_learnings,
    extract_keywords, find_similar_learnings,
)

# Credentials
from db._credentials import (
    create_credential, update_credential, delete_credential,
    get_credential, list_credentials,
)

# Task history
from db._tasks import (
    log_task, list_task_history, set_task_frequency,
    get_overdue_tasks, get_task_frequencies,
)

# Entities, preferences, agents
from db._entities import (
    create_entity, search_entities, list_entities, update_entity, delete_entity,
    set_preference, get_preference, list_preferences, delete_preference,
    create_agent, get_agent, list_agents, update_agent, delete_agent,
)

# Episodic memory
from db._episodic import (
    cleanup_old_changes, log_change, search_changes, update_change_commit, auto_resolve_followups,
    cleanup_old_decisions, log_decision, update_decision_outcome,
    get_memory_review_queue, find_decisions_by_context_ref, search_decisions,
    cleanup_old_diaries, write_session_diary,
    diary_archive_search, diary_archive_read, diary_archive_stats,
    check_session_has_diary,
    upsert_diary_draft, get_diary_draft, delete_diary_draft,
    save_checkpoint, read_checkpoint, increment_compaction_count,
    get_orphan_sessions, read_session_diary,
    recall,
)

# Evolution
from db._evolution import (
    insert_evolution_metric, get_latest_metrics,
    insert_evolution_log, get_evolution_history, update_evolution_log_status,
)

# Cron execution history
from db._cron_runs import (
    cron_run_start, cron_run_end, cron_runs_recent, cron_runs_summary,
)

# Protocol discipline runtime
from db._protocol import (
    create_protocol_task, get_protocol_task, close_protocol_task,
    create_protocol_debt, resolve_protocol_debts, list_protocol_debts,
    protocol_compliance_summary,
)

# Durable workflow runtime
from db._workflow import (
    create_workflow_run, get_workflow_run, list_workflow_runs,
    list_workflow_steps, record_workflow_transition,
    get_workflow_replay, get_workflow_resume_state,
    create_workflow_goal, get_workflow_goal, list_workflow_goals,
    update_workflow_goal,
)

# State watchers
from db._watchers import (
    create_state_watcher, get_state_watcher, list_state_watchers,
    update_state_watcher, update_state_watcher_result,
)

# Personal scripts registry
from db._personal_scripts import (
    upsert_personal_script, list_personal_scripts, get_personal_script,
    delete_missing_personal_scripts, register_personal_script_schedule,
    delete_missing_personal_schedules, list_personal_script_schedules,
    get_personal_script_schedule, delete_personal_script_schedule,
    delete_personal_script,
    record_personal_script_run, sync_personal_scripts_registry,
    get_personal_script_health_report,
)

# Skills
from db._skills import (
    create_skill, get_skill, list_skills, search_skills,
    update_skill, delete_skill,
    record_usage as record_skill_usage,
    match_skills, merge_skills, get_skill_stats, decay_unused_skills,
    get_featured_skills, get_skill_execution_spec, resolve_skill_paths,
    validate_skill_params, render_command_template, sync_skill_directories,
    import_skill_from_directory, approve_skill, collect_scriptable_skill_candidates,
    collect_skill_improvement_candidates, materialize_personal_skill_definition,
    get_skill_health_report,
)

# Drive / Curiosity signals
from db._drive import (
    create_drive_signal, reinforce_drive_signal, get_drive_signals,
    get_drive_signal, update_drive_signal_status, decay_drive_signals,
    find_similar_drive_signal, drive_signal_stats,
)

# Hot context / recent continuity
from db._hot_context import (
    DEFAULT_CONTEXT_TTL_HOURS,
    derive_context_key, clamp_ttl_hours,
    cleanup_expired_hot_context,
    remember_hot_context, record_recent_event, capture_context_event,
    get_hot_context, search_hot_context, search_recent_events,
    build_pre_action_context, format_pre_action_context_bundle,
    resolve_hot_context,
)

# Outcomes
from db._outcomes import (
    VALID_METRIC_SOURCES as OUTCOME_METRIC_SOURCES,
    VALID_TARGET_OPERATORS as OUTCOME_TARGET_OPERATORS,
    create_outcome, get_outcome, list_outcomes,
    cancel_outcome, evaluate_outcome, pending_outcomes_due,
    find_pending_outcomes_by_action, set_linked_outcomes_met,
)


def get_db():
    return _module("db._core").get_db()


def close_db():
    return _module("db._core").close_db()


def init_db():
    return _module("db._core").init_db()


def now_epoch():
    return _module("db._core").now_epoch()


def run_migrations():
    return _module("db._schema").run_migrations()


def get_schema_version():
    return _module("db._schema").get_schema_version()
