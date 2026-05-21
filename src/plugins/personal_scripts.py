"""NEXO Personal Scripts — registry-backed management for user scripts."""

import json
from collections import Counter

from db import init_db, list_personal_scripts, list_personal_script_schedules
from plugins.schedule import handle_schedule_add
from script_registry import (
    archive_agent,
    classify_scripts_dir,
    create_agent_script,
    create_script,
    ensure_personal_schedules,
    get_agent_status,
    get_automation_status,
    list_agents,
    list_operator_automations,
    reconcile_personal_scripts,
    remove_personal_script,
    set_agent_enabled,
    set_agent_schedule,
    set_automation_enabled,
    set_automation_instructions,
    set_automation_schedule,
    sync_personal_scripts,
    unschedule_personal_script,
)


def _normalize_limit(value, default: int = 0, maximum: int = 500) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(0, min(maximum, parsed))


def _script_summary_row(script: dict) -> dict:
    return {
        "id": script["id"],
        "name": script["name"],
        "description": script.get("description", ""),
        "runtime": script.get("runtime", "unknown"),
        "origin": script.get("origin", "user"),
        "path": script["path"],
        "has_schedule": script.get("has_schedule", False),
        "last_run_at": script.get("last_run_at", ""),
        "last_exit_code": script.get("last_exit_code"),
    }


def handle_personal_scripts_sync() -> str:
    init_db()
    result = sync_personal_scripts()
    return json.dumps(result, ensure_ascii=False)


def handle_personal_scripts_classify() -> str:
    return json.dumps(classify_scripts_dir(), ensure_ascii=False)


def handle_personal_scripts_list(
    include_schedules: bool = True,
    limit: int | str = 0,
    filter_runtime: str = "",
    filter_origin: str = "",
    filter_source: str = "",
    summary: bool = False,
) -> str:
    init_db()
    sync_personal_scripts()
    scripts = list_personal_scripts()
    runtime_filter = str(filter_runtime or "").strip().lower()
    origin_filter = str(filter_origin or filter_source or "").strip().lower()
    if runtime_filter:
        scripts = [script for script in scripts if str(script.get("runtime", "")).strip().lower() == runtime_filter]
    if origin_filter:
        scripts = [
            script
            for script in scripts
            if str(script.get("origin") or script.get("source") or "user").strip().lower() == origin_filter
        ]

    total = len(scripts)
    limit_value = _normalize_limit(limit)
    truncated = False
    if limit_value:
        truncated = total > limit_value
        scripts = scripts[:limit_value]

    if summary or not include_schedules:
        rendered_scripts = [_script_summary_row(script) for script in scripts]
    else:
        rendered_scripts = scripts

    runtime_counts = Counter(str(script.get("runtime", "unknown")) for script in scripts)
    origin_counts = Counter(str(script.get("origin") or script.get("source") or "user") for script in scripts)
    payload = {
        "ok": True,
        "total": total,
        "count": len(rendered_scripts),
        "limit": limit_value,
        "truncated": truncated,
        "filters": {
            "runtime": runtime_filter,
            "origin": origin_filter,
        },
        "summary": {
            "total": total,
            "shown": len(rendered_scripts),
            "by_runtime": dict(sorted(runtime_counts.items())),
            "by_origin": dict(sorted(origin_counts.items())),
        },
        "scripts": rendered_scripts,
    }
    return json.dumps(payload, ensure_ascii=False)


def handle_personal_script_create(
    name: str,
    description: str = "",
    runtime: str = "python",
    schedule: str = "",
    interval_seconds: int = 0,
) -> str:
    init_db()
    try:
        created = create_script(name, description=description, runtime=runtime)
    except (FileExistsError, ValueError) as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    if schedule or interval_seconds:
        cron_id = created["name"]
        handle_schedule_add(
            cron_id=cron_id,
            script=created["path"],
            schedule=schedule,
            interval_seconds=interval_seconds,
            description=description,
            script_type=runtime,
        )
        sync_result = sync_personal_scripts()
        created["sync"] = sync_result
    return json.dumps(created, ensure_ascii=False)


def handle_personal_script_schedules() -> str:
    init_db()
    sync_personal_scripts()
    return json.dumps({"schedules": list_personal_script_schedules()}, ensure_ascii=False)


def handle_personal_scripts_reconcile(dry_run: bool = False) -> str:
    init_db()
    return json.dumps(reconcile_personal_scripts(dry_run=dry_run), ensure_ascii=False)


def handle_personal_scripts_ensure_schedules(dry_run: bool = False) -> str:
    init_db()
    return json.dumps(ensure_personal_schedules(dry_run=dry_run), ensure_ascii=False)


def handle_personal_script_unschedule(name: str) -> str:
    init_db()
    return json.dumps(unschedule_personal_script(name), ensure_ascii=False)


def handle_personal_script_remove(name: str, keep_file: bool = False) -> str:
    init_db()
    return json.dumps(remove_personal_script(name, keep_file=keep_file), ensure_ascii=False)


def handle_automations_list(include_all: bool = False) -> str:
    init_db()
    return json.dumps({"ok": True, "automations": list_operator_automations(include_all=include_all)}, ensure_ascii=False)


def handle_agents_list(include_archived: bool = False) -> str:
    init_db()
    return json.dumps({"ok": True, "agents": list_agents(include_archived=include_archived)}, ensure_ascii=False)


def handle_agent_status(name: str) -> str:
    init_db()
    return json.dumps(get_agent_status(name), ensure_ascii=False)


def handle_agent_create(name: str, description: str = "", runtime: str = "python") -> str:
    init_db()
    try:
        return json.dumps(create_agent_script(name, description=description, runtime=runtime), ensure_ascii=False)
    except (FileExistsError, ValueError) as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


def handle_agent_enable(name: str) -> str:
    init_db()
    return json.dumps(set_agent_enabled(name, True), ensure_ascii=False)


def handle_agent_disable(name: str) -> str:
    init_db()
    return json.dumps(set_agent_enabled(name, False), ensure_ascii=False)


def handle_agent_archive(name: str, restore: bool = False) -> str:
    init_db()
    return json.dumps(archive_agent(name, archived=not bool(restore)), ensure_ascii=False)


def handle_agent_schedule(
    name: str,
    every_seconds: int = 0,
    daily_at: str = "",
    clear: bool = False,
) -> str:
    init_db()
    try:
        interval_seconds = int(every_seconds or 0) or None
    except (TypeError, ValueError):
        return json.dumps({"ok": False, "error": f"Invalid every_seconds: {every_seconds}"}, ensure_ascii=False)
    return json.dumps(
        set_agent_schedule(
            name,
            interval_seconds=interval_seconds,
            daily_at=str(daily_at or "").strip() or None,
            clear=bool(clear),
        ),
        ensure_ascii=False,
    )


def handle_automation_status(name: str) -> str:
    init_db()
    return json.dumps(get_automation_status(name), ensure_ascii=False)


def handle_automation_enable(name: str) -> str:
    init_db()
    return json.dumps(set_automation_enabled(name, True), ensure_ascii=False)


def handle_automation_disable(name: str) -> str:
    init_db()
    return json.dumps(set_automation_enabled(name, False), ensure_ascii=False)


def handle_automation_instructions(name: str, text: str = "", clear: bool = False) -> str:
    init_db()
    return json.dumps(set_automation_instructions(name, "" if clear else text), ensure_ascii=False)


def handle_automation_schedule(
    name: str,
    every_seconds: int = 0,
    daily_at: str = "",
    clear: bool = False,
) -> str:
    init_db()
    interval_seconds = int(every_seconds or 0) or None
    return json.dumps(
        set_automation_schedule(
            name,
            interval_seconds=interval_seconds,
            daily_at=str(daily_at or "").strip() or None,
            clear=bool(clear),
        ),
        ensure_ascii=False,
    )


def handle_core_schedules_list() -> str:
    from core_schedule_controls import list_core_schedules

    init_db()
    return json.dumps({"ok": True, "core_schedules": list_core_schedules()}, ensure_ascii=False)


def handle_core_schedule_status(name: str) -> str:
    from core_schedule_controls import get_core_schedule_status

    init_db()
    return json.dumps(get_core_schedule_status(name), ensure_ascii=False)


def handle_core_schedule_set(
    name: str,
    every_seconds: int = 0,
    daily_at: str = "",
    clear: bool = False,
) -> str:
    from core_schedule_controls import set_core_schedule

    init_db()
    interval_seconds = int(every_seconds or 0) or None
    return json.dumps(
        set_core_schedule(
            name,
            interval_seconds=interval_seconds,
            daily_at=str(daily_at or "").strip() or None,
            clear=bool(clear),
        ),
        ensure_ascii=False,
    )


TOOLS = [
    (handle_personal_scripts_sync, "nexo_personal_scripts_sync",
     "Sync personal scripts and personal cron schedules from filesystem and LaunchAgents into the registry."),
    (handle_personal_scripts_classify, "nexo_personal_scripts_classify",
     "Classify files in NEXO_HOME/personal/scripts into personal, core, ignored, and non-script buckets."),
    (handle_personal_scripts_list, "nexo_personal_scripts_list",
     "List personal scripts known to NEXO, optionally including attached schedules."),
    (handle_personal_script_create, "nexo_personal_script_create",
     "Create a new personal script in NEXO_HOME/personal/scripts, register it, and optionally attach a schedule."),
    (handle_personal_script_schedules, "nexo_personal_script_schedules",
     "List registered personal script schedules."),
    (handle_personal_scripts_reconcile, "nexo_personal_scripts_reconcile",
     "Classify, sync, and ensure declared personal schedules so NEXO_HOME/personal/scripts and personal crons stay aligned."),
    (handle_personal_scripts_ensure_schedules, "nexo_personal_scripts_ensure_schedules",
     "Create or repair personal script schedules declared in inline metadata."),
    (handle_personal_script_unschedule, "nexo_personal_script_unschedule",
     "Remove all personal schedules attached to a script without touching core crons."),
    (handle_personal_script_remove, "nexo_personal_script_remove",
     "Remove a personal script from the registry and optionally delete its file after unscheduling it."),
    (handle_automations_list, "nexo_automations_list",
     "List the operator-facing automations NEXO Desktop manages directly, with optional support/debug widening."),
    (handle_automation_status, "nexo_automation_status",
     "Read the composed runtime status for one automation, including availability, schedule, and operator overrides."),
    (handle_automation_enable, "nexo_automation_enable",
     "Enable one operator-facing automation."),
    (handle_automation_disable, "nexo_automation_disable",
     "Disable one operator-facing automation."),
    (handle_automation_instructions, "nexo_automation_instructions",
     "Set or clear operator-side extra instructions for one automation without editing the core prompt."),
    (handle_automation_schedule, "nexo_automation_schedule",
     "Set or clear the cadence override for one operator-facing automation."),
    (handle_agents_list, "nexo_agents_list",
     "List personal scripts marked as NEXO agents for the Desktop Home panel."),
    (handle_agent_status, "nexo_agent_status",
     "Read the composed runtime status for one personal-script-backed agent."),
    (handle_agent_create, "nexo_agent_create",
     "Create a personal script scaffold already marked as a NEXO agent."),
    (handle_agent_enable, "nexo_agent_enable",
     "Enable one personal-script-backed agent."),
    (handle_agent_disable, "nexo_agent_disable",
     "Disable one personal-script-backed agent without deleting its schedule."),
    (handle_agent_archive, "nexo_agent_archive",
     "Archive or restore one personal-script-backed agent without deleting its file."),
    (handle_agent_schedule, "nexo_agent_schedule",
     "Set or clear the cadence for one personal-script-backed agent."),
    (handle_core_schedules_list, "nexo_core_schedules_list",
     "List structural core crons whose cadence can be tuned without disabling them."),
    (handle_core_schedule_status, "nexo_core_schedule_status",
     "Read the composed runtime status for one structural core schedule."),
    (handle_core_schedule_set, "nexo_core_schedule_set",
     "Set or clear the cadence override for one structural core cron."),
]
