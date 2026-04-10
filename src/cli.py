#!/usr/bin/env python3
"""NEXO Runtime CLI — operational commands for scripts and diagnostics.

Entry points:
  nexo chat [PATH]
  nexo scripts list [--all] [--json]
  nexo scripts create NAME [--runtime python|shell] [--description TEXT]
  nexo scripts classify [--json]
  nexo scripts sync [--json]
  nexo scripts reconcile [--dry-run] [--json]
  nexo scripts ensure-schedules [--dry-run] [--json]
  nexo scripts schedules [--json]
  nexo scripts unschedule NAME [--json]
  nexo scripts remove NAME [--keep-file] [--json]
  nexo scripts run NAME_OR_PATH [-- args...]
  nexo scripts doctor [NAME_OR_PATH] [--json]
  nexo scripts call TOOL --input JSON [--json-output]
  nexo skills list [--level ...] [--source-kind ...] [--json]
  nexo skills get ID [--json]
  nexo skills apply ID [--params JSON] [--mode ...] [--dry-run] [--json]
  nexo skills sync [--json]
  nexo skills approve ID [--execution-level ...] [--approved-by ...] [--json]
  nexo skills featured [--limit N] [--json]
  nexo skills evolution [--json]
  nexo clients sync [--json]
  nexo contributor status|on|off [--json]
  nexo doctor [--tier boot|runtime|deep|all] [--json] [--fix]
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent)))
TERMINAL_CLIENT_LABELS = {
    "claude_code": "Claude Code",
    "codex": "Codex",
}
TERMINAL_CLIENT_ORDER = ("claude_code", "codex")


def _get_version() -> str:
    """Read version from runtime version.json or package.json automatically."""
    json_candidates = [
        (NEXO_HOME / "version.json", "version"),
        (NEXO_CODE.parent / "version.json", "version"),
        (NEXO_CODE.parent / "package.json", "version"),
        (NEXO_HOME / "package.json", "version"),
    ]
    for candidate, key in json_candidates:
        try:
            if candidate.is_file():
                return json.loads(candidate.read_text()).get(key, "?")
        except Exception:
            continue
    return "?"

# Ensure src/ is on path for imports
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))


def _missing_runtime_module_message(module_name: str, exc: Exception) -> str:
    missing = getattr(exc, "name", None) or module_name
    return (
        f"{module_name} is unavailable in the current runtime ({missing}). "
        "Continuing with safe defaults so `nexo update` can repair the installation."
    )


def _load_runtime_power_support() -> dict:
    try:
        from runtime_power import (
            ensure_power_policy_choice,
            apply_power_policy,
            format_power_policy_label,
            ensure_full_disk_access_choice,
            format_full_disk_access_label,
        )
        return {
            "available": True,
            "message": "",
            "ensure_power_policy_choice": ensure_power_policy_choice,
            "apply_power_policy": apply_power_policy,
            "format_power_policy_label": format_power_policy_label,
            "ensure_full_disk_access_choice": ensure_full_disk_access_choice,
            "format_full_disk_access_label": format_full_disk_access_label,
        }
    except ImportError as exc:
        message = _missing_runtime_module_message("runtime_power", exc)

        def ensure_power_policy_choice(**kwargs):
            return {"policy": "disabled", "prompted": False, "message": message}

        def apply_power_policy(policy=None):
            return {"ok": True, "action": "skipped", "details": [], "message": message}

        def format_power_policy_label(policy):
            return policy or "disabled"

        def ensure_full_disk_access_choice(**kwargs):
            return {"status": "unset", "prompted": False, "reasons": [], "message": message}

        def format_full_disk_access_label(status):
            return status or "unset"

        return {
            "available": False,
            "message": message,
            "ensure_power_policy_choice": ensure_power_policy_choice,
            "apply_power_policy": apply_power_policy,
            "format_power_policy_label": format_power_policy_label,
            "ensure_full_disk_access_choice": ensure_full_disk_access_choice,
            "format_full_disk_access_label": format_full_disk_access_label,
        }


def _load_public_contribution_support() -> dict:
    try:
        from public_contribution import (
            ensure_public_contribution_choice,
            format_public_contribution_label,
            load_public_contribution_config,
            refresh_public_contribution_state,
            disable_public_contribution,
        )
        return {
            "available": True,
            "message": "",
            "ensure_public_contribution_choice": ensure_public_contribution_choice,
            "format_public_contribution_label": format_public_contribution_label,
            "load_public_contribution_config": load_public_contribution_config,
            "refresh_public_contribution_state": refresh_public_contribution_state,
            "disable_public_contribution": disable_public_contribution,
        }
    except ImportError as exc:
        message = _missing_runtime_module_message("public_contribution", exc)

        def _default_config(config=None):
            payload = {
                "enabled": False,
                "mode": "disabled",
                "status": "unavailable",
                "prompted": False,
                "message": message,
            }
            if isinstance(config, dict):
                payload.update(config)
            return payload

        def ensure_public_contribution_choice(**kwargs):
            return _default_config()

        def format_public_contribution_label(config=None):
            cfg = _default_config(config)
            if cfg.get("status") == "unavailable":
                return "disabled (runtime repair needed)"
            return cfg.get("mode") or "disabled"

        def load_public_contribution_config():
            return _default_config()

        def refresh_public_contribution_state(config=None):
            return _default_config(config)

        def disable_public_contribution():
            return _default_config()

        return {
            "available": False,
            "message": message,
            "ensure_public_contribution_choice": ensure_public_contribution_choice,
            "format_public_contribution_label": format_public_contribution_label,
            "load_public_contribution_config": load_public_contribution_config,
            "refresh_public_contribution_state": refresh_public_contribution_state,
            "disable_public_contribution": disable_public_contribution,
        }


def _scripts_list(args):
    from db import init_db, list_personal_scripts
    from script_registry import list_scripts, sync_personal_scripts

    init_db()
    sync_personal_scripts()
    if args.all:
        scripts = list_scripts(include_core=True)
    else:
        scripts = list_personal_scripts()

    if args.json:
        print(json.dumps(scripts, indent=2))
    else:
        if not scripts:
            print("No personal scripts found in", NEXO_HOME / "scripts")
            return 0
        # Table output
        name_w = max(len(s["name"]) for s in scripts)
        rt_w = max(len(s["runtime"]) for s in scripts)
        for s in scripts:
            tag = " [core]" if s.get("core") else ""
            schedule_tag = ""
            if s.get("has_schedule"):
                schedule_labels = [sch.get("schedule_label", "") for sch in s.get("schedules", []) if sch.get("schedule_label")]
                if schedule_labels:
                    schedule_tag = f" [{'; '.join(schedule_labels[:2])}]"
            print(f"  {s['name']:<{name_w}}  {s['runtime']:<{rt_w}}  {s.get('description', '')}{schedule_tag}{tag}")
    return 0


def _scripts_sync(args):
    from db import init_db
    from script_registry import sync_personal_scripts

    init_db()
    result = sync_personal_scripts()
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(
            f"Synced personal scripts: {result['scripts_upserted']} script(s), "
            f"{result['schedules_upserted']} schedule(s), "
            f"{result['scripts_pruned']} script(s) pruned, "
            f"{result['schedules_pruned']} schedule(s) pruned."
        )
    return 0


def _scripts_classify(args):
    from script_registry import classify_scripts_dir

    report = classify_scripts_dir()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    entries = report.get("entries", [])
    if not entries:
        print("No scripts directory found:", report.get("scripts_dir", NEXO_HOME / "scripts"))
        return 0

    path_w = max(len(Path(entry["path"]).name) for entry in entries)
    for entry in entries:
        reason = f" — {entry['reason']}" if entry.get("reason") else ""
        print(f"  {Path(entry['path']).name:<{path_w}}  {entry['classification']}{reason}")
    return 0


def _scripts_reconcile(args):
    from script_registry import reconcile_personal_scripts

    result = reconcile_personal_scripts(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        sync = result.get("sync", {})
        ensured = result.get("ensure_schedules", {})
        print(
            f"Reconciled personal scripts: {sync.get('registered_scripts', 0)} registered, "
            f"{len(ensured.get('created', []))} schedule(s) created, "
            f"{len(ensured.get('repaired', []))} repaired, "
            f"{len(ensured.get('invalid', []))} invalid."
        )
        if args.dry_run:
            print("  Dry run only — no schedules changed.")
    return 0 if not result.get("ensure_schedules", {}).get("invalid") else 1


def _scripts_ensure_schedules(args):
    from script_registry import ensure_personal_schedules

    result = ensure_personal_schedules(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(
            f"Ensured schedules: {len(result.get('created', []))} created, "
            f"{len(result.get('repaired', []))} repaired, "
            f"{len(result.get('already_present', []))} already present, "
            f"{len(result.get('invalid', []))} invalid."
        )
        if args.dry_run:
            print("  Dry run only — no schedules changed.")
    return 0 if not result.get("invalid") else 1


def _scripts_create(args):
    from script_registry import create_script

    try:
        result = create_script(
            args.name,
            description=args.description,
            runtime=args.runtime,
            force=args.force,
        )
    except FileExistsError as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Created personal script: {result['path']}")
    return 0


def _scripts_schedules(args):
    from db import init_db, list_personal_script_schedules
    from script_registry import sync_personal_scripts

    init_db()
    sync_personal_scripts()
    schedules = list_personal_script_schedules()
    if args.json:
        print(json.dumps(schedules, indent=2, ensure_ascii=False))
        return 0

    if not schedules:
        print("No personal script schedules registered.")
        return 0

    cron_w = max(len(s["cron_id"]) for s in schedules)
    for schedule in schedules:
        label = schedule.get("schedule_label") or schedule.get("schedule_value") or schedule.get("schedule_type")
        print(f"  {schedule['cron_id']:<{cron_w}}  {label}")
    return 0


def _scripts_unschedule(args):
    from script_registry import unschedule_personal_script

    result = unschedule_personal_script(args.name)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if not result.get("ok"):
            print(result.get("error", "Failed to unschedule script"), file=sys.stderr)
            return 1
        print(f"Removed {len(result.get('removed_schedules', []))} schedule(s) from {result['script']}")
    return 0 if result.get("ok") else 1


def _scripts_remove(args):
    from script_registry import remove_personal_script

    result = remove_personal_script(args.name, keep_file=args.keep_file)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if not result.get("ok"):
            print(result.get("error", "Failed to remove script"), file=sys.stderr)
            return 1
        action = "unregistered" if args.keep_file else "removed"
        print(f"Script {result['script']} {action}")
    return 0 if result.get("ok") else 1


def _scripts_run(args):
    from db import init_db, record_personal_script_run
    from script_registry import resolve_script_reference, sync_personal_scripts

    init_db()
    sync_personal_scripts()
    info = resolve_script_reference(args.name)
    if not info:
        print(f"Script not found: {args.name}", file=sys.stderr)
        return 1

    path = Path(info["path"])
    runtime = info["runtime"]
    meta = info["metadata"]
    is_core = info.get("core", False)

    # Build environment
    env = {
        **os.environ,
        "NEXO_HOME": str(NEXO_HOME),
        "NEXO_CODE": str(NEXO_CODE),
        "NEXO_SCRIPT_NAME": info["name"],
        "NEXO_SCRIPT_PATH": str(path),
        "NEXO_CLI": "nexo",
    }

    # Only inject DB paths for core scripts
    if is_core:
        env["NEXO_DB"] = str(NEXO_HOME / "data" / "nexo.db")
        env["NEXO_COGNITIVE_DB"] = str(NEXO_HOME / "data" / "cognitive.db")

    # Timeout
    timeout = None
    timeout_str = meta.get("timeout", "")
    if timeout_str:
        try:
            timeout = int(timeout_str)
        except ValueError:
            pass

    # Build command
    if runtime == "python":
        cmd = [sys.executable, str(path)] + args.script_args
    elif runtime == "shell":
        cmd = ["bash", str(path)] + args.script_args
    elif runtime == "node":
        cmd = ["node", str(path)] + args.script_args
    elif runtime == "php":
        cmd = ["php", str(path)] + args.script_args
    else:
        # Try to execute directly
        cmd = [str(path)] + args.script_args

    try:
        result = subprocess.run(cmd, env=env, timeout=timeout)
        if not is_core:
            record_personal_script_run(str(path), result.returncode)
        return result.returncode
    except subprocess.TimeoutExpired:
        if not is_core:
            record_personal_script_run(str(path), 124)
        print(f"Script timed out after {timeout}s", file=sys.stderr)
        return 124
    except Exception as e:
        if not is_core:
            record_personal_script_run(str(path), 1)
        print(f"Error running script: {e}", file=sys.stderr)
        return 1


def _scripts_doctor(args):
    from db import init_db
    from script_registry import doctor_script, doctor_all_scripts, sync_personal_scripts

    init_db()
    sync_personal_scripts()

    if args.name:
        results = [doctor_script(args.name)]
    else:
        results = doctor_all_scripts()

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        if not results:
            print("No personal scripts to check.")
            return 0
        any_fail = False
        for r in results:
            name = r.get("name", "?")
            status = r.get("status", "?")
            icon = {"pass": "✓", "warn": "⚠", "fail": "✗"}.get(status, "?")
            print(f"\n{icon} {name} [{status}]")
            for item in r.get("items", []):
                lvl = item["level"]
                prefix = {"pass": "  ✓", "warn": "  ⚠", "fail": "  ✗"}.get(lvl, "  ?")
                print(f"{prefix} {item['msg']}")
            if status == "fail":
                any_fail = True
        print()
        return 1 if any_fail else 0

    return 0


def _runtime_python_candidates() -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(value: str | None) -> None:
        if not value:
            return
        text = str(value).strip()
        if not text or text in seen:
            return
        seen.add(text)
        candidates.append(text)

    _add(os.environ.get("NEXO_RUNTIME_PYTHON"))
    _add(os.environ.get("NEXO_PYTHON"))
    _add(sys.executable)

    for root in {NEXO_HOME, NEXO_CODE, NEXO_CODE.parent}:
        _add(str(root / ".venv" / "bin" / "python3"))
        _add(str(root / ".venv" / "bin" / "python"))

    if sys.platform == "darwin":
        _add("/opt/homebrew/bin/python3")
        _add("/usr/local/bin/python3")
    else:
        _add("/usr/local/bin/python3")
        _add("/usr/bin/python3")

    _add(shutil.which("python3"))
    _add(shutil.which("python"))
    return candidates


def _python_supports_module(python_bin: str, module_name: str) -> bool:
    path = Path(python_bin)
    if "/" in python_bin and not path.exists():
        return False
    try:
        result = subprocess.run(
            [python_bin, "-c", f"import {module_name}"],
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "NEXO_HOME": str(NEXO_HOME), "NEXO_CODE": str(NEXO_CODE)},
        )
        return result.returncode == 0
    except Exception:
        return False


def _recover_scripts_call_runtime(tool_name: str, exc: ModuleNotFoundError) -> int | None:
    missing = getattr(exc, "name", "") or ""
    if missing != "fastmcp":
        return None
    if os.environ.get("NEXO_CLI_REEXECED") == "1":
        return None

    current = str(Path(sys.executable).resolve())
    for candidate in _runtime_python_candidates():
        try:
            resolved = str(Path(candidate).resolve()) if "/" in candidate else candidate
        except Exception:
            resolved = candidate
        if resolved == current:
            continue
        if not _python_supports_module(candidate, "fastmcp"):
            continue
        env = {
            **os.environ,
            "NEXO_HOME": str(NEXO_HOME),
            "NEXO_CODE": str(NEXO_CODE),
            "NEXO_CLI_REEXECED": "1",
        }
        result = subprocess.run(
            [candidate, str(Path(__file__).resolve()), *sys.argv[1:]],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        return result.returncode
    return None


def _scripts_call(args):
    """Call a NEXO MCP tool via in-process fastmcp client."""
    tool_name = args.tool
    try:
        payload = json.loads(args.input) if args.input else {}
    except json.JSONDecodeError as e:
        print(f"Invalid JSON input: {e}", file=sys.stderr)
        return 1

    def _bootstrap_mcp():
        os.environ["NEXO_CLI_MODE"] = "1"
        from db import init_db
        from plugin_loader import load_all_plugins
        from server import mcp

        init_db()

        # Plugin loading is required so scripts can call plugin tools such as
        # nexo_doctor, but the loader is noisy on stderr and would pollute CLI output.
        with contextlib.redirect_stderr(io.StringIO()):
            load_all_plugins(mcp)

        return mcp

    def _extract_tool_value(result):
        structured = getattr(result, "structured_content", None)
        if structured not in (None, {}):
            return structured

        content = getattr(result, "content", None)
        if isinstance(content, list):
            texts = [item.text for item in content if hasattr(item, "text")]
            if texts:
                return "\n".join(texts)

        dumped = getattr(result, "model_dump", None)
        if callable(dumped):
            data = dumped()
            if isinstance(data, dict):
                return data.get("structured_content") or data.get("content") or data

        return str(result)

    try:
        mcp = _bootstrap_mcp()

        async def _call():
            tool = await mcp.get_tool(tool_name)
            if tool is None:
                tools = await mcp.list_tools()
                available = sorted(t.name for t in tools)
                raise LookupError(
                    f"Tool not found: {tool_name}\nAvailable tools: {', '.join(available)}"
                )
            return await mcp.call_tool(tool_name, payload)

        result = asyncio.run(_call())
        value = _extract_tool_value(result)

        if args.json_output:
            if (
                isinstance(value, dict)
                and set(value.keys()) == {"result"}
                and isinstance(value["result"], str)
            ):
                try:
                    value = json.loads(value["result"])
                except json.JSONDecodeError:
                    pass
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = {"result": value}
            elif not isinstance(value, (dict, list)):
                value = {"result": value}
            print(json.dumps(value, indent=2, ensure_ascii=False))
            return 0

        if isinstance(value, dict) and set(value.keys()) == {"result"} and isinstance(value["result"], str):
            print(value["result"])
        elif isinstance(value, (dict, list)):
            print(json.dumps(value, indent=2, ensure_ascii=False))
        else:
            print(value)
        return 0

    except LookupError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        if isinstance(e, ModuleNotFoundError):
            recovered = _recover_scripts_call_runtime(tool_name, e)
            if recovered is not None:
                return recovered
        print(f"Error calling tool {tool_name}: {e}", file=sys.stderr)
        return 1


def _update(args):
    """Update the installed runtime.

    Modes:
    - Dev-linked runtime: sync from the source repo recorded in version.json
    - Explicit dev env: sync from NEXO_CODE/src
    - Packaged/runtime-only install: delegate to plugins.update handle_update()
    """
    from auto_update import manual_sync_update, _resolve_sync_source

    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    progress_messages: list[str] = []

    def progress(message: str) -> None:
        progress_messages.append(message)
        if not args.json:
            print(f"[NEXO] {message}", flush=True)

    dest = NEXO_HOME
    src_dir, repo_dir = _resolve_sync_source()

    if src_dir is None or repo_dir is None:
        try:
            from plugins.update import handle_update
        except Exception as e:
            print(
                "No source repo recorded for this runtime and packaged updater is unavailable: "
                f"{e}",
                file=sys.stderr,
            )
            return 1

        result = handle_update(progress_fn=progress)
        runtime_power = _load_runtime_power_support()
        public_contribution = _load_public_contribution_support()
        choice = runtime_power["ensure_power_policy_choice"](interactive=interactive, reason="update")
        power_result = runtime_power["apply_power_policy"](choice.get("policy"))
        fda_choice = runtime_power["ensure_full_disk_access_choice"](interactive=interactive, reason="update")
        contrib_choice = public_contribution["ensure_public_contribution_choice"](interactive=interactive, reason="update")
        if args.json:
            print(json.dumps({
                "mode": "packaged",
                "message": result,
                "progress": progress_messages,
                "power_policy": choice.get("policy"),
                "power_action": power_result.get("action"),
                "power_details": power_result.get("details"),
                "full_disk_access_status": fda_choice.get("status"),
                "full_disk_access_reasons": fda_choice.get("reasons"),
                "full_disk_access_message": fda_choice.get("message"),
                "public_contribution_mode": contrib_choice.get("mode"),
                "public_contribution_status": contrib_choice.get("status"),
                "public_contribution_message": contrib_choice.get("message"),
            }, indent=2, ensure_ascii=False))
        else:
            print(result)
            if choice.get("prompted"):
                print(f"Power policy: {runtime_power['format_power_policy_label'](choice.get('policy'))}")
            if power_result.get("message"):
                print(f"Power helper: {power_result.get('message')}")
            if fda_choice.get("prompted"):
                print(f"Full Disk Access: {runtime_power['format_full_disk_access_label'](fda_choice.get('status'))}")
            if fda_choice.get("message"):
                print(f"Full Disk Access: {fda_choice.get('message')}")
            if contrib_choice.get("prompted"):
                print(f"Contributor mode: {public_contribution['format_public_contribution_label'](contrib_choice)}")
            if contrib_choice.get("message"):
                print(f"Contributor mode: {contrib_choice.get('message')}")
        return 0 if "UPDATE SUCCESSFUL" in result or "Already up to date" in result else 1

    result = manual_sync_update(interactive=interactive, allow_source_pull=True, progress_fn=progress)
    runtime_power = _load_runtime_power_support()
    public_contribution = _load_public_contribution_support()
    choice = runtime_power["ensure_power_policy_choice"](interactive=interactive, reason="update")
    power_result = runtime_power["apply_power_policy"](choice.get("policy"))
    fda_choice = runtime_power["ensure_full_disk_access_choice"](interactive=interactive, reason="update")
    contrib_choice = public_contribution["ensure_public_contribution_choice"](interactive=interactive, reason="update")
    result["power_policy"] = choice.get("policy")
    result["power_action"] = power_result.get("action")
    result["power_details"] = power_result.get("details")
    result["full_disk_access_status"] = fda_choice.get("status")
    result["full_disk_access_reasons"] = fda_choice.get("reasons")
    result["public_contribution_mode"] = contrib_choice.get("mode")
    result["public_contribution_status"] = contrib_choice.get("status")
    result["progress"] = progress_messages
    if power_result.get("message"):
        result["power_message"] = power_result.get("message")
    if fda_choice.get("message"):
        result["full_disk_access_message"] = fda_choice.get("message")
    if contrib_choice.get("message"):
        result["public_contribution_message"] = contrib_choice.get("message")
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if result.get("ok"):
            print(f"Updated NEXO_HOME ({dest})")
            print(
                f"  {result.get('packages', 0)} packages, {result.get('files', 0)} files synced from "
                f"{result.get('source', src_dir)}"
            )
            healed = 0
            invalid = 0
            for action in result.get("actions", []):
                if action.startswith("personal-schedules-healed:"):
                    try:
                        healed += int(action.split(":", 1)[1])
                    except ValueError:
                        pass
                elif action.startswith("personal-schedules-invalid:"):
                    try:
                        invalid += int(action.split(":", 1)[1])
                    except ValueError:
                        pass
            if healed:
                print(f"  Personal schedules: self-healed {healed}")
            if invalid:
                print(f"  Personal schedules: {invalid} declarations need review")
            if result.get("pulled_source"):
                print("  Source repo: pulled latest fast-forward before sync")
            if choice.get("prompted"):
                print(f"  Power policy: {runtime_power['format_power_policy_label'](choice.get('policy'))}")
            if power_result.get("message"):
                print(f"  Power helper: {power_result.get('message')}")
            if fda_choice.get("prompted"):
                print(f"  Full Disk Access: {runtime_power['format_full_disk_access_label'](fda_choice.get('status'))}")
            if fda_choice.get("message"):
                print(f"  Full Disk Access: {fda_choice.get('message')}")
            if contrib_choice.get("prompted"):
                print(f"  Contributor mode: {public_contribution['format_public_contribution_label'](contrib_choice)}")
            if contrib_choice.get("message"):
                print(f"  Contributor mode: {contrib_choice.get('message')}")
        else:
            print(f"UPDATE FAILED: {result.get('error', 'sync failed')}", file=sys.stderr)
    return 0 if result.get("ok") else 1


def _clients_sync(args):
    from client_sync import format_sync_summary, sync_all_clients

    result = sync_all_clients(nexo_home=NEXO_HOME, runtime_root=NEXO_CODE)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(format_sync_summary(result))
    return 0 if result.get("ok") else 1


def _contributor_status(args):
    public_contribution = _load_public_contribution_support()
    config = public_contribution["refresh_public_contribution_state"](
        public_contribution["load_public_contribution_config"]()
    )
    payload = {
        "enabled": bool(config.get("enabled")),
        "mode": config.get("mode"),
        "status": config.get("status"),
        "label": public_contribution["format_public_contribution_label"](config),
        "github_user": config.get("github_user"),
        "fork_repo": config.get("fork_repo"),
        "active_pr_url": config.get("active_pr_url"),
        "active_branch": config.get("active_branch"),
        "cooldown_until": config.get("cooldown_until"),
        "last_result": config.get("last_result"),
        "message": config.get("message") or public_contribution.get("message"),
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"Contributor mode: {payload['label']}")
        if payload["message"]:
            print(f"  {payload['message']}")
        if payload["github_user"]:
            print(f"  GitHub user: {payload['github_user']}")
        if payload["fork_repo"]:
            print(f"  Fork: {payload['fork_repo']}")
        if payload["active_pr_url"]:
            print(f"  Active Draft PR: {payload['active_pr_url']}")
        if payload["cooldown_until"]:
            print(f"  Cooldown until: {payload['cooldown_until']}")
        if payload["last_result"]:
            print(f"  Last result: {payload['last_result']}")
    return 0


def _contributor_on(args):
    public_contribution = _load_public_contribution_support()

    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not interactive:
        print("Contributor mode requires an interactive terminal to confirm GitHub Draft PR consent.", file=sys.stderr)
        return 1
    if not public_contribution["available"]:
        print(public_contribution["message"], file=sys.stderr)
        return 1
    config = public_contribution["ensure_public_contribution_choice"](
        interactive=True,
        reason="contributor",
        force_prompt=True,
    )
    if args.json:
        print(json.dumps(config, indent=2, ensure_ascii=False))
    else:
        print(f"Contributor mode: {public_contribution['format_public_contribution_label'](config)}")
        if config.get("message"):
            print(config.get("message"))
    return 0 if config.get("mode") == "draft_prs" else 1


def _contributor_off(args):
    public_contribution = _load_public_contribution_support()

    if not public_contribution["available"]:
        print(public_contribution["message"], file=sys.stderr)
        return 1
    config = public_contribution["disable_public_contribution"]()
    if args.json:
        print(json.dumps(config, indent=2, ensure_ascii=False))
    else:
        print(f"Contributor mode: {public_contribution['format_public_contribution_label'](config)}")
    return 0


def _service_control(service_name: str, action: str) -> int:
    """Control a LaunchAgent/systemd service: on, off, status."""
    import platform as plat

    label = f"com.nexo.{service_name}"

    if plat.system() != "Darwin":
        print(f"Service control only supported on macOS for now.", file=sys.stderr)
        return 1

    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    uid = os.getuid()

    if action == "status":
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True,
        )
        running = label in (result.stdout or "")
        if running:
            print(f"{service_name}: running")
        else:
            print(f"{service_name}: stopped")
        return 0

    if action == "on":
        if not plist_path.is_file():
            print(f"LaunchAgent not found: {plist_path}", file=sys.stderr)
            print(f"Run 'nexo-brain' to install it, or enable it during setup.", file=sys.stderr)
            return 1
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}", str(plist_path)],
            capture_output=True,
        )
        result = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"{service_name}: started")
        else:
            print(f"Failed to start {service_name}: {result.stderr.strip()}", file=sys.stderr)
            return 1
        return 0

    if action == "off":
        result = subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}", str(plist_path)],
            capture_output=True, text=True,
        )
        print(f"{service_name}: stopped")
        return 0

    print(f"Unknown action: {action}. Use on, off, or status.", file=sys.stderr)
    return 1


def _dashboard(args):
    return _service_control("dashboard", args.action)


def _terminal_client_label(client: str) -> str:
    return TERMINAL_CLIENT_LABELS.get(client, client.replace("_", " ").title())


def _ordered_available_terminal_clients(preferences: dict, detected: dict) -> list[str]:
    enabled = preferences.get("interactive_clients", {})
    last_used = str(preferences.get("last_terminal_client", "")).strip()
    preferred = str(preferences.get("default_terminal_client", "")).strip()
    ordered: list[str] = []

    for client in (last_used, preferred, *TERMINAL_CLIENT_ORDER):
        if client in TERMINAL_CLIENT_ORDER and client not in ordered:
            ordered.append(client)

    return [
        client
        for client in ordered
        if enabled.get(client, False) and detected.get(client, {}).get("installed", False)
    ]


def _preferred_terminal_client_label(preferences: dict, clients: list[str]) -> str:
    last_used = str(preferences.get("last_terminal_client", "")).strip()
    if clients and clients[0] == last_used:
        return "last choice"
    return "default"


def _prompt_for_terminal_client(
    clients: list[str],
    normalize_client_key,
    *,
    preferred_label: str = "default",
) -> str | None:
    if not clients:
        return None
    if len(clients) == 1:
        return clients[0]

    while True:
        print("Select terminal client for this chat:")
        for index, client in enumerate(clients, start=1):
            suffix = f" [{preferred_label}]" if index == 1 else ""
            print(f"  {index}. {_terminal_client_label(client)}{suffix}")

        try:
            response = input(f"Choose 1-{len(clients)} [1]: ").strip()
        except EOFError:
            return clients[0]

        if not response:
            return clients[0]
        if response.isdigit():
            choice = int(response)
            if 1 <= choice <= len(clients):
                return clients[choice - 1]

        client_key = normalize_client_key(response)
        if client_key in clients:
            return client_key

        print("Invalid choice. Try again.", file=sys.stderr)


def _chat(args):
    target = args.path or "."
    selected_client = getattr(args, "client", None)

    try:
        from auto_update import startup_preflight

        preflight = startup_preflight(entrypoint="chat", interactive=False)
        if preflight.get("updated"):
            print("[NEXO] Startup update applied before chat.", file=sys.stderr)
        elif preflight.get("deferred_reason"):
            print(f"[NEXO] Update deferred: {preflight['deferred_reason']}", file=sys.stderr)
        elif preflight.get("git_update"):
            print(f"[NEXO] {preflight['git_update']}", file=sys.stderr)
        elif preflight.get("npm_notice"):
            print(f"[NEXO] {preflight['npm_notice']}", file=sys.stderr)
        for message in preflight.get("client_bootstrap_updates", []):
            print(f"[NEXO] {message}", file=sys.stderr)
        if preflight.get("error"):
            print(f"[NEXO] Startup preflight warning: {preflight['error']}", file=sys.stderr)
    except Exception:
        pass

    try:
        from client_preferences import (
            detect_installed_clients,
            load_client_preferences,
            normalize_client_key,
            save_client_preferences,
        )
        from agent_runner import TerminalClientUnavailableError, launch_interactive_client
    except ImportError:
        print("Agent runner module not found. Ensure NEXO is properly installed.", file=sys.stderr)
        return 1

    if not selected_client:
        try:
            preferences = load_client_preferences()
            detected = detect_installed_clients()
            clients = _ordered_available_terminal_clients(preferences, detected)
            selected_client = _prompt_for_terminal_client(
                clients,
                normalize_client_key,
                preferred_label=_preferred_terminal_client_label(preferences, clients),
            )
        except Exception:
            selected_client = None

    try:
        result = launch_interactive_client(
            target=target,
            client=selected_client,
            env=os.environ.copy(),
        )
    except TerminalClientUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if result.returncode == 0 and selected_client:
        try:
            save_client_preferences(last_terminal_client=normalize_client_key(selected_client))
        except Exception:
            pass
    return int(result.returncode)


def _doctor(args):
    """Run unified doctor diagnostics."""
    try:
        from db import init_db
        from doctor.orchestrator import run_doctor
        from doctor.formatters import format_report
    except ImportError:
        print("Doctor module not found. Ensure NEXO is properly installed.", file=sys.stderr)
        return 1

    init_db()
    report = run_doctor(tier=args.tier, fix=args.fix)
    output = format_report(report, fmt="json" if args.json else "text")
    print(output)

    if report.overall_status == "critical":
        return 2
    elif report.overall_status == "degraded":
        return 1
    return 0


def _skills_list(args):
    from db import init_db, list_skills, sync_skill_directories

    init_db()
    sync_skill_directories()
    skills = list_skills(level=args.level, tag=args.tag, source_kind=args.source_kind)
    if args.json:
        print(json.dumps(skills, indent=2, ensure_ascii=False))
        return 0

    if not skills:
        print("No skills found.")
        return 0

    for skill in skills:
        print(
            f"[{skill['id']}] {skill['name']} "
            f"({skill['level']}, {skill.get('mode', 'guide')}, {skill.get('source_kind', 'personal')}, "
            f"trust={skill['trust_score']}, used={skill['use_count']}x)"
        )
    return 0


def _skills_get(args):
    from db import get_skill, init_db, sync_skill_directories

    init_db()
    sync_skill_directories()
    skill = get_skill(args.id)
    if not skill:
        print(f"Skill not found: {args.id}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(skill, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(skill, indent=2, ensure_ascii=False))
    return 0


def _skills_apply(args):
    from skills_runtime import apply_skill

    try:
        params = json.loads(args.params) if args.params else {}
    except json.JSONDecodeError as e:
        print(f"Invalid params JSON: {e}", file=sys.stderr)
        return 1

    result = apply_skill(args.id, params=params, mode=args.mode, dry_run=args.dry_run, context=args.context)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def _skills_test(args):
    from skills_runtime import test_skill

    try:
        params = json.loads(args.params) if args.params else {}
    except json.JSONDecodeError as e:
        print(f"Invalid params JSON: {e}", file=sys.stderr)
        return 1

    result = test_skill(args.id, params=params, mode=args.mode, context=args.context)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def _skills_sync(args):
    from skills_runtime import sync_skills

    result = sync_skills()
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if not result.get("issues") else 1


def _skills_approve(args):
    from skills_runtime import approve_skill_execution

    result = approve_skill_execution(args.id, execution_level=args.execution_level, approved_by=args.approved_by)
    if "error" in result:
        print(result["error"], file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _skills_featured(args):
    from skills_runtime import get_featured_skill_summaries

    result = get_featured_skill_summaries(limit=args.limit)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _skills_evolution(args):
    from skills_runtime import list_evolution_candidates

    result = list_evolution_candidates()
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _skills_outcome_review(args):
    from skills_runtime import review_skill_outcomes

    result = review_skill_outcomes(args.id, auto_apply=args.auto_apply)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def _skills_promote(args):
    from skills_runtime import promote_skill

    result = promote_skill(args.id, target_level=args.target_level, reason=args.reason)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def _skills_retire(args):
    from skills_runtime import retire_skill

    result = retire_skill(args.id, replacement_id=args.replacement_id, reason=args.reason)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def _skills_compose(args):
    from skills_runtime import compose_skills

    try:
        component_ids = json.loads(args.component_ids) if args.component_ids.strip().startswith("[") else [
            item.strip() for item in args.component_ids.split(",") if item.strip()
        ]
        tags = json.loads(args.tags) if args.tags.strip().startswith("[") else [
            item.strip() for item in args.tags.split(",") if item.strip()
        ]
        trigger_patterns = json.loads(args.trigger_patterns) if args.trigger_patterns.strip().startswith("[") else [
            item.strip() for item in args.trigger_patterns.split(",") if item.strip()
        ]
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        return 1

    result = compose_skills(
        new_skill_id=args.new_id,
        name=args.name,
        component_ids=component_ids,
        description=args.description,
        level=args.level,
        mode=args.mode,
        tags=tags,
        trigger_patterns=trigger_patterns,
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def _print_help():
    v = _get_version()
    print(f"""NEXO Runtime CLI v{v}

Commands:
  nexo chat [path] [--client claude_code|codex]      Launch a NEXO terminal client
  nexo doctor [--tier boot|runtime|deep|all] [--fix]   System diagnostics
  nexo scripts list|create|classify|sync|reconcile|ensure-schedules|schedules|run|doctor|call|unschedule|remove
                                                      Personal scripts
  nexo skills list|apply|sync|approve                  Executable skills
  nexo clients sync                                    Sync Claude/Codex shared-brain configs and bootstrap files
  nexo update                                          Update installed runtime
  nexo contributor status|on|off                       Public Draft PR contribution mode
  nexo dashboard on|off|status                         Web dashboard control

Run 'nexo <command> --help' for details.
Homepage: https://nexo-brain.com
GitHub:   https://github.com/wazionapps/nexo""")


def main():
    parser = argparse.ArgumentParser(prog="nexo", description="NEXO Runtime CLI", add_help=False)
    parser.add_argument("-h", "--help", action="store_true", help="Show help")
    parser.add_argument("-v", "--version", action="store_true", help="Show version")
    sub = parser.add_subparsers(dest="command")

    # -- chat --
    chat_parser = sub.add_parser("chat", help="Launch a NEXO terminal client")
    chat_parser.add_argument("path", nargs="?", default=".", help="Working directory (default: current directory)")
    chat_parser.add_argument(
        "--client",
        choices=["claude_code", "codex"],
        help="Override the chat picker and launch a specific terminal client",
    )

    # -- scripts --
    scripts_parser = sub.add_parser("scripts", help="Manage personal scripts")
    scripts_sub = scripts_parser.add_subparsers(dest="scripts_command")

    # scripts list
    list_p = scripts_sub.add_parser("list", help="List scripts")
    list_p.add_argument("--all", action="store_true", help="Include core/internal scripts")
    list_p.add_argument("--json", action="store_true", help="JSON output")

    # scripts create
    create_p = scripts_sub.add_parser("create", help="Create a personal script scaffold")
    create_p.add_argument("name", help="Human/script name")
    create_p.add_argument("--description", default="", help="One-line description")
    create_p.add_argument("--runtime", default="python", choices=["python", "shell"], help="Script runtime")
    create_p.add_argument("--force", action="store_true", help="Overwrite if the target file exists")
    create_p.add_argument("--json", action="store_true", help="JSON output")

    # scripts classify
    classify_p = scripts_sub.add_parser("classify", help="Classify all files in NEXO_HOME/scripts")
    classify_p.add_argument("--json", action="store_true", help="JSON output")

    # scripts sync
    sync_p = scripts_sub.add_parser("sync", help="Sync script registry from filesystem and personal LaunchAgents")
    sync_p.add_argument("--json", action="store_true", help="JSON output")

    # scripts reconcile
    reconcile_p = scripts_sub.add_parser("reconcile", help="Classify, sync, and ensure declared schedules")
    reconcile_p.add_argument("--dry-run", action="store_true", help="Show what would change without editing schedules")
    reconcile_p.add_argument("--json", action="store_true", help="JSON output")

    # scripts ensure-schedules
    ensure_p = scripts_sub.add_parser("ensure-schedules", help="Create or repair declared personal schedules")
    ensure_p.add_argument("--dry-run", action="store_true", help="Show what would change without editing schedules")
    ensure_p.add_argument("--json", action="store_true", help="JSON output")

    # scripts schedules
    schedules_p = scripts_sub.add_parser("schedules", help="List registered personal script schedules")
    schedules_p.add_argument("--json", action="store_true", help="JSON output")

    # scripts unschedule
    unschedule_p = scripts_sub.add_parser("unschedule", help="Remove all personal schedules from a script")
    unschedule_p.add_argument("name", help="Script name or path")
    unschedule_p.add_argument("--json", action="store_true", help="JSON output")

    # scripts remove
    remove_p = scripts_sub.add_parser("remove", help="Remove a personal script and any attached schedules")
    remove_p.add_argument("name", help="Script name or path")
    remove_p.add_argument("--keep-file", action="store_true", help="Keep the script file and only unregister/unschedule it")
    remove_p.add_argument("--json", action="store_true", help="JSON output")

    # scripts run
    run_p = scripts_sub.add_parser("run", help="Run a script by name")
    run_p.add_argument("name", help="Script name")
    run_p.add_argument("script_args", nargs="*", help="Arguments to pass to the script")

    # scripts doctor
    doc_p = scripts_sub.add_parser("doctor", help="Validate scripts")
    doc_p.add_argument("name", nargs="?", help="Specific script to check")
    doc_p.add_argument("--json", action="store_true", help="JSON output")

    # scripts call
    call_p = scripts_sub.add_parser("call", help="Call a NEXO MCP tool")
    call_p.add_argument("tool", help="MCP tool name")
    call_p.add_argument("--input", default="{}", help="JSON input payload")
    call_p.add_argument("--json-output", action="store_true", help="Force JSON output")

    # -- update --
    update_parser = sub.add_parser("update", help="Update installed runtime")
    update_parser.add_argument("--json", action="store_true", help="JSON output")

    # -- clients --
    clients_parser = sub.add_parser("clients", help="Shared client config management")
    clients_sub = clients_parser.add_subparsers(dest="clients_command")
    clients_sync_p = clients_sub.add_parser("sync", help="Sync Claude Code, Claude Desktop, and Codex to the same NEXO brain")
    clients_sync_p.add_argument("--json", action="store_true", help="JSON output")

    # -- doctor --
    doctor_parser = sub.add_parser("doctor", help="Unified diagnostics")
    doctor_parser.add_argument("--tier", default="boot", choices=["boot", "runtime", "deep", "all"],
                               help="Diagnostic tier (default: boot)")
    doctor_parser.add_argument("--json", action="store_true", help="JSON output")
    doctor_parser.add_argument("--fix", action="store_true", help="Apply deterministic fixes")

    # -- contributor --
    contributor_parser = sub.add_parser("contributor", help="Public Draft PR contribution mode")
    contributor_parser.add_argument("action", choices=["status", "on", "off"], help="Manage contributor mode")
    contributor_parser.add_argument("--json", action="store_true", help="JSON output")

    # -- skills --
    skills_parser = sub.add_parser("skills", help="Skills v2 runtime")
    skills_sub = skills_parser.add_subparsers(dest="skills_command")

    skills_list_p = skills_sub.add_parser("list", help="List skills")
    skills_list_p.add_argument("--level", default="", help="Filter by level")
    skills_list_p.add_argument("--tag", default="", help="Filter by tag")
    skills_list_p.add_argument("--source-kind", default="", help="Filter by source kind")
    skills_list_p.add_argument("--json", action="store_true", help="JSON output")

    skills_get_p = skills_sub.add_parser("get", help="Get skill")
    skills_get_p.add_argument("id", help="Skill ID")
    skills_get_p.add_argument("--json", action="store_true", help="JSON output")

    skills_apply_p = skills_sub.add_parser("apply", help="Apply a skill")
    skills_apply_p.add_argument("id", help="Skill ID")
    skills_apply_p.add_argument("--params", default="{}", help="JSON parameters")
    skills_apply_p.add_argument("--mode", default="auto", choices=["auto", "guide", "execute", "hybrid"])
    skills_apply_p.add_argument("--dry-run", action="store_true", help="Render without executing")
    skills_apply_p.add_argument("--context", default="", help="Usage context for feedback loop")
    skills_apply_p.add_argument("--json", action="store_true", help="JSON output")

    skills_test_p = skills_sub.add_parser("test", help="Dry-run test a skill")
    skills_test_p.add_argument("id", help="Skill ID")
    skills_test_p.add_argument("--params", default="{}", help="JSON parameters")
    skills_test_p.add_argument("--mode", default="auto", choices=["auto", "guide", "execute", "hybrid"])
    skills_test_p.add_argument("--context", default="", help="Testing context")
    skills_test_p.add_argument("--json", action="store_true", help="JSON output")

    skills_sync_p = skills_sub.add_parser("sync", help="Sync filesystem skills")
    skills_sync_p.add_argument("--json", action="store_true", help="JSON output")

    skills_approve_p = skills_sub.add_parser("approve", help="Approve an executable skill")
    skills_approve_p.add_argument("id", help="Skill ID")
    skills_approve_p.add_argument("--execution-level", default="", choices=["", "read-only", "local", "remote"])
    skills_approve_p.add_argument("--approved-by", default="", help="Approver name")
    skills_approve_p.add_argument("--json", action="store_true", help="JSON output")

    skills_featured_p = skills_sub.add_parser("featured", help="Featured startup skills")
    skills_featured_p.add_argument("--limit", type=int, default=5)
    skills_featured_p.add_argument("--json", action="store_true", help="JSON output")

    skills_evolution_p = skills_sub.add_parser("evolution", help="Evolution candidates")
    skills_evolution_p.add_argument("--json", action="store_true", help="JSON output")

    skills_outcome_review_p = skills_sub.add_parser("outcome-review", help="Review skill lifecycle against sustained outcomes")
    skills_outcome_review_p.add_argument("id", help="Skill ID")
    skills_outcome_review_p.add_argument("--auto-apply", action="store_true", help="Apply the recommended promotion/retirement when it is strong enough")
    skills_outcome_review_p.add_argument("--json", action="store_true", help="JSON output")

    skills_promote_p = skills_sub.add_parser("promote", help="Promote a skill lifecycle level")
    skills_promote_p.add_argument("id", help="Skill ID")
    skills_promote_p.add_argument("--target-level", default="published", choices=["draft", "published", "stable"])
    skills_promote_p.add_argument("--reason", default="", help="Why promote this skill")
    skills_promote_p.add_argument("--json", action="store_true", help="JSON output")

    skills_retire_p = skills_sub.add_parser("retire", help="Archive a skill")
    skills_retire_p.add_argument("id", help="Skill ID")
    skills_retire_p.add_argument("--replacement-id", default="", help="Optional replacement skill ID")
    skills_retire_p.add_argument("--reason", default="", help="Why retire this skill")
    skills_retire_p.add_argument("--json", action="store_true", help="JSON output")

    skills_compose_p = skills_sub.add_parser("compose", help="Compose multiple skills into one")
    skills_compose_p.add_argument("new_id", help="New skill ID")
    skills_compose_p.add_argument("name", help="New skill name")
    skills_compose_p.add_argument("--component-ids", required=True, help="JSON array or comma-separated skill IDs")
    skills_compose_p.add_argument("--description", default="", help="Composite skill description")
    skills_compose_p.add_argument("--level", default="draft", choices=["trace", "draft", "published", "stable"])
    skills_compose_p.add_argument("--mode", default="guide", choices=["guide", "hybrid"])
    skills_compose_p.add_argument("--tags", default="[]", help="JSON array or comma-separated tags")
    skills_compose_p.add_argument("--trigger-patterns", default="[]", help="JSON array or comma-separated trigger patterns")
    skills_compose_p.add_argument("--json", action="store_true", help="JSON output")

    # -- dashboard --
    dashboard_parser = sub.add_parser("dashboard", help="Web dashboard control")
    dashboard_parser.add_argument("action", choices=["on", "off", "status"], help="Start, stop, or check dashboard")

    args = parser.parse_args()

    if args.help or (not args.command and not args.version):
        _print_help()
        return 0
    if args.version:
        print(f"nexo v{_get_version()}")
        return 0

    if args.command == "scripts":
        if args.scripts_command == "list":
            return _scripts_list(args)
        elif args.scripts_command == "create":
            return _scripts_create(args)
        elif args.scripts_command == "classify":
            return _scripts_classify(args)
        elif args.scripts_command == "sync":
            return _scripts_sync(args)
        elif args.scripts_command == "reconcile":
            return _scripts_reconcile(args)
        elif args.scripts_command == "ensure-schedules":
            return _scripts_ensure_schedules(args)
        elif args.scripts_command == "schedules":
            return _scripts_schedules(args)
        elif args.scripts_command == "unschedule":
            return _scripts_unschedule(args)
        elif args.scripts_command == "remove":
            return _scripts_remove(args)
        elif args.scripts_command == "run":
            return _scripts_run(args)
        elif args.scripts_command == "doctor":
            return _scripts_doctor(args)
        elif args.scripts_command == "call":
            return _scripts_call(args)
        else:
            scripts_parser.print_help()
            return 0
    elif args.command == "chat":
        return _chat(args)
    elif args.command == "update":
        return _update(args)
    elif args.command == "clients":
        if args.clients_command == "sync":
            return _clients_sync(args)
        clients_parser.print_help()
        return 0
    elif args.command == "doctor":
        return _doctor(args)
    elif args.command == "contributor":
        if args.action == "status":
            return _contributor_status(args)
        elif args.action == "on":
            return _contributor_on(args)
        elif args.action == "off":
            return _contributor_off(args)
        contributor_parser.print_help()
        return 0
    elif args.command == "skills":
        if args.skills_command == "list":
            return _skills_list(args)
        elif args.skills_command == "get":
            return _skills_get(args)
        elif args.skills_command == "apply":
            return _skills_apply(args)
        elif args.skills_command == "test":
            return _skills_test(args)
        elif args.skills_command == "sync":
            return _skills_sync(args)
        elif args.skills_command == "approve":
            return _skills_approve(args)
        elif args.skills_command == "featured":
            return _skills_featured(args)
        elif args.skills_command == "evolution":
            return _skills_evolution(args)
        elif args.skills_command == "outcome-review":
            return _skills_outcome_review(args)
        elif args.skills_command == "promote":
            return _skills_promote(args)
        elif args.skills_command == "retire":
            return _skills_retire(args)
        elif args.skills_command == "compose":
            return _skills_compose(args)
        else:
            skills_parser.print_help()
            return 0
    elif args.command == "dashboard":
        return _dashboard(args)
    else:
        _print_help()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
