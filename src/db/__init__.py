"""NEXO DB — Modular SQLite database layer.

This package replaces the monolithic db.py. All public functions are
re-exported here for full backwards compatibility:
    from db import get_db, create_learning, ...
"""

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
    get_reminders, get_reminder,
    create_followup, update_followup, complete_followup, delete_followup,
    get_followups, get_followup,
    find_similar_followups,
)

# Learnings
from db._learnings import (
    create_learning, update_learning, delete_learning,
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
