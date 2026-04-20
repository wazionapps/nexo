"""NEXO Personal Scripts — registry-backed management for user scripts."""

import json

from db import init_db, list_personal_scripts, list_personal_script_schedules
from plugins.schedule import handle_schedule_add
from script_registry import (
    classify_scripts_dir,
    create_script,
    ensure_personal_schedules,
    get_automation_status,
    list_operator_automations,
    reconcile_personal_scripts,
    remove_personal_script,
    set_automation_enabled,
    set_automation_instructions,
    set_automation_schedule,
    sync_personal_scripts,
    unschedule_personal_script,
)


def handle_personal_scripts_sync() -> str:
    init_db()
    result = sync_personal_scripts()
    return json.dumps(result, ensure_ascii=False)


def handle_personal_scripts_classify() -> str:
    return json.dumps(classify_scripts_dir(), ensure_ascii=False)


def handle_personal_scripts_list(include_schedules: bool = True) -> str:
    init_db()
    sync_personal_scripts()
    scripts = list_personal_scripts()
    if include_schedules:
        return json.dumps({"scripts": scripts}, ensure_ascii=False)

    simplified = []
    for script in scripts:
        simplified.append({
            "id": script["id"],
            "name": script["name"],
            "description": script.get("description", ""),
            "runtime": script.get("runtime", "unknown"),
            "path": script["path"],
            "has_schedule": script.get("has_schedule", False),
        })
    return json.dumps({"scripts": simplified}, ensure_ascii=False)


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
]
