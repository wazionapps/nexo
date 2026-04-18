#!/usr/bin/env python3
"""NEXO Runtime CLI — operational commands for scripts and diagnostics.

Entry points:
  nexo chat [PATH]
  nexo export [PATH] [--json]
  nexo import PATH [--json]
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
  nexo doctor [--tier boot|runtime|deep|all] [--plane runtime_personal|installation_live|database_real] [--json] [--fix]
  nexo uninstall [--dry-run] [--delete-data] [--json]
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
import time
from pathlib import Path

from runtime_home import export_resolved_nexo_home

NEXO_HOME = export_resolved_nexo_home()
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent)))
TERMINAL_CLIENT_LABELS = {
    "claude_code": "Claude Code",
    "codex": "Codex",
}
TERMINAL_CLIENT_ORDER = ("claude_code", "codex")
VERSION_STATUS_CACHE = NEXO_HOME / "config" / "cli-version-status.json"
LATEST_NPM_PACKAGE = "nexo-brain"


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


def _load_latest_version_cache(max_age_seconds: int = 6 * 3600) -> str | None:
    try:
        payload = json.loads(VERSION_STATUS_CACHE.read_text())
    except Exception:
        return None
    version = str(payload.get("latest", "")).strip()
    checked_at = float(payload.get("checked_at", 0) or 0)
    if not version:
        return None
    if checked_at and (time.time() - checked_at) > max_age_seconds:
        return None
    return version


def _save_latest_version_cache(version: str) -> None:
    VERSION_STATUS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    VERSION_STATUS_CACHE.write_text(json.dumps({
        "latest": version,
        "checked_at": time.time(),
    }))


def _fetch_latest_version(timeout_seconds: int = 2) -> str | None:
    try:
        result = subprocess.run(
            ["npm", "view", LATEST_NPM_PACKAGE, "version"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    latest = result.stdout.strip()
    if not latest:
        return None
    try:
        _save_latest_version_cache(latest)
    except Exception:
        pass
    return latest


def _should_refresh_latest_version() -> bool:
    """Decide whether to hit the npm registry to refresh `latest` version.

    Prior behaviour gated this on `isatty()` so `nexo --help` never made
    a network call outside an interactive terminal. That also meant NEXO
    Desktop — which spawns `nexo` via subprocess with piped stdio — could
    never populate the version cache, so the Desktop update banner for
    Brain never saw a newer `Latest: vX` line in the help output and no
    Brain update was ever offered automatically (v6.1.1 fix).

    The 6-hour `max_age_seconds` at `_load_latest_version_cache()` is the
    real rate-limit. This function now returns True unconditionally so
    missing/stale cache entries are always refreshed, regardless of tty
    context. Fail-closed: `_fetch_latest_version` still catches every
    subprocess error and returns None, so the help line falls back to
    installed-only when npm is unreachable.
    """
    return True


def _version_sort_key(raw: str) -> tuple[tuple[int, ...], int, str]:
    value = str(raw or "").strip()
    base, _, suffix = value.partition("-")
    parts: list[int] = []
    for piece in base.split("."):
        try:
            parts.append(int(piece))
        except Exception:
            parts.append(-1)
    while len(parts) < 3:
        parts.append(0)
    return (tuple(parts), 1 if not suffix else 0, suffix)


def _version_status_line() -> str:
    installed = _get_version()
    latest = _load_latest_version_cache()
    if latest is None and _should_refresh_latest_version():
        latest = _fetch_latest_version()
    if latest and installed and _version_sort_key(latest) < _version_sort_key(installed):
        latest = installed
        try:
            _save_latest_version_cache(installed)
        except Exception:
            pass
    if latest:
        return f"NEXO Latest: v{latest} | Installed: v{installed}"
    return f"NEXO Installed: v{installed}"

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


def _export_bundle(args):
    from user_data_portability import export_user_bundle

    result = export_user_bundle(args.path or "")
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if not result.get("ok"):
            print(result.get("error", "Export failed"), file=sys.stderr)
            return 1
        sections = result.get("sections", {})
        script_count = sections.get("personal_scripts", {}).get("files", 0)
        print(f"User data export written to {result['path']}")
        print(f"  Personal scripts: {script_count}")
        print(f"  Sections: {', '.join(sorted(sections))}")
    return 0 if result.get("ok") else 1


def _import_bundle(args):
    from user_data_portability import import_user_bundle

    result = import_user_bundle(args.path)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if not result.get("ok"):
            print(result.get("error", "Import failed"), file=sys.stderr)
            return 1
        restored = result.get("restored", {})
        script_count = restored.get("personal_scripts", {}).get("files", 0)
        print(f"User data imported from {result['path']}")
        print(f"  Safety backup: {result['safety_backup']}")
        print(f"  Personal scripts restored: {script_count}")
        print(f"  Sections: {', '.join(sorted(restored))}")
    return 0 if result.get("ok") else 1


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


def _recover(args):
    """Delegate to plugins.recover.cli_main so the logic lives in one place."""
    from plugins.recover import cli_main as _recover_cli_main
    argv: list[str] = []
    if getattr(args, "source", None):
        argv.extend(["--from", args.source])
    if getattr(args, "list", False):
        argv.append("--list")
    if getattr(args, "dry_run", False):
        argv.append("--dry-run")
    if getattr(args, "force", False):
        argv.append("--force")
    if getattr(args, "yes", False):
        argv.append("--yes")
    if getattr(args, "json", False):
        argv.append("--json")
    return _recover_cli_main(argv)


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
    include_clis = not getattr(args, "no_clis", False)

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

        result = handle_update(progress_fn=progress, include_clis=include_clis)
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

    result = manual_sync_update(
        interactive=interactive,
        allow_source_pull=True,
        progress_fn=progress,
        include_clis=include_clis,
    )
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
            for dep in result.get("runtime_dependencies") or []:
                dep_name = dep.get("name", "")
                dep_status = dep.get("status", "")
                if dep_status == "updated":
                    print(f"  Dependencies: {dep_name} {dep.get('old_version')} -> {dep.get('new_version')}")
                elif dep_status == "installed":
                    print(f"  Dependencies: {dep_name} installed ({dep.get('new_version')})")
                elif dep_status == "already_latest":
                    print(f"  Dependencies: {dep_name} {dep.get('old_version')} (latest)")
                elif dep_status == "failed":
                    print(f"  WARNING: {dep_name} update failed: {dep.get('error', 'unknown')}")
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
            # One-time model-recommendation upgrade prompt (interactive only).
            try:
                _prompt_model_recommendations(interactive=interactive)
            except Exception as exc:
                print(f"  Model recommendation check skipped: {exc}", file=sys.stderr)
        else:
            print(f"UPDATE FAILED: {result.get('error', 'sync failed')}", file=sys.stderr)

    # Auto-migrate calibration.json flat → nested once per user. Silent on
    # no-op; logs a line if an actual migration happened.
    try:
        from calibration_migration import detect as _cal_detect, apply_migration as _cal_apply
        if _cal_detect()["shape"] == "flat":
            mig = _cal_apply()
            if mig.get("status") == "migrated" and not args.json:
                print(f"[NEXO] calibration.json migrated flat → nested (backup: {mig.get('backup')})",
                      flush=True)
    except Exception:
        pass

    # v5.4.1 one-time hygiene: purge pre-fix "tool":"unknown" entries from
    # the sensory-register buffer. Keeps a .pre-v5.4.1.bak backup the first
    # time it runs on a given host.
    try:
        buf = NEXO_HOME / "brain" / "session_buffer.jsonl"
        marker = buf.with_suffix(".jsonl.pre-v5.4.1.bak")
        if buf.is_file() and not marker.is_file():
            raw = buf.read_text(errors="ignore").splitlines()
            unknown = sum(1 for ln in raw if '"tool":"unknown"' in ln)
            if unknown > 0:
                buf.rename(marker)
                cleaned = "\n".join(ln for ln in raw if '"tool":"unknown"' not in ln)
                if cleaned:
                    cleaned += "\n"
                buf.write_text(cleaned)
                if not args.json:
                    print(f"[NEXO] session_buffer.jsonl: purged {unknown} legacy "
                          f"\"tool\":\"unknown\" entries (backup: {marker.name})", flush=True)
    except Exception:
        pass

    return 0 if result.get("ok") else 1


def _prompt_model_recommendations(*, interactive: bool) -> None:
    """If model_defaults.json has bumped recommendation_version beyond what
    the user has acknowledged, offer a one-time upgrade prompt. In
    non-interactive (cron/headless) contexts, only log a hint. Honours
    customized models by skipping silently (see was_nexo_default)."""
    from client_preferences import (
        load_client_preferences,
        save_client_preferences,
        normalize_client_key,
    )
    from model_defaults import detect_outdated_recommendations, client_default

    preferences = load_client_preferences()
    result = detect_outdated_recommendations(preferences)
    pending = result.get("pending") or []
    auto_ack = result.get("auto_ack") or {}

    # Apply silent acknowledgements first (user already on current model, or
    # has customized their model — either way no prompt needed). This avoids
    # repeated stderr hints in cron/headless updates.
    if auto_ack:
        try:
            existing_ack = dict(preferences.get("acknowledged_model_recommendations") or {})
            existing_ack.update({k: int(v) for k, v in auto_ack.items()})
            save_client_preferences(acknowledged_model_recommendations=existing_ack)
        except Exception:
            pass

    if not pending:
        return

    is_tty = bool(interactive and sys.stdin.isatty() and sys.stdout.isatty())
    if not is_tty:
        for entry in pending:
            print(
                f"  ⭐ Model recommendation available for "
                f"{entry['client']}: {entry['display_name']}. "
                f"Run `nexo update` interactively to review and apply.",
                file=sys.stderr,
            )
        return

    updated_profiles = dict(preferences.get("client_runtime_profiles") or {})
    updated_ack = dict(preferences.get("acknowledged_model_recommendations") or {})
    updated_ack.update({k: int(v) for k, v in auto_ack.items()})
    changed = bool(auto_ack)
    for entry in pending:
        client = entry["client"]
        effort_str = f" / {entry['current_effort']}" if entry["current_effort"] else ""
        prev_effort = f" / {entry['user_effort']}" if entry["user_effort"] else ""
        print()
        print(f"[NEXO] ⭐ Nueva recomendación de modelo para {client}:")
        print(
            f"       {entry['current_model']}{effort_str}  "
            f"(antes: {entry['user_model']}{prev_effort})"
        )
        print(f"       {entry['display_name']} — recomendado por NEXO.")
        answer = input("       ¿Migrar tu configuración? [y/N/later]: ").strip().lower()
        client_key = normalize_client_key(client) or client
        if answer in {"y", "yes", "s", "si", "sí"}:
            updated_profiles[client_key] = {
                "model": entry["current_model"],
                "reasoning_effort": entry["current_effort"],
            }
            updated_ack[client_key] = entry["current_version"]
            print(f"       ✅ Migrado a {entry['current_model']}.")
            changed = True
        elif answer in {"later", "l", "luego"}:
            # Do NOT acknowledge — will prompt again next interactive update.
            print("       ↻ Te lo preguntaré en el próximo update.")
        else:
            # "N" / empty / anything else → record ack so we don't re-ask.
            updated_ack[client_key] = entry["current_version"]
            print(f"       Mantenido {entry['user_model']}. No te preguntaré de nuevo para esta versión.")
            changed = True

    if changed:
        save_client_preferences(
            client_runtime_profiles=updated_profiles,
            acknowledged_model_recommendations=updated_ack,
        )
        # Re-sync clients so config.toml / settings.json reflect the new model.
        try:
            from client_sync import sync_all_clients
            sync_all_clients(
                nexo_home=NEXO_HOME,
                runtime_root=NEXO_CODE,
                preferences=load_client_preferences(),
            )
        except Exception:
            pass


def _clients_sync(args):
    from client_sync import format_sync_summary, sync_all_clients

    result = sync_all_clients(nexo_home=NEXO_HOME, runtime_root=NEXO_CODE)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(format_sync_summary(result))
    return 0 if result.get("ok") else 1


def _write_calibration_default_resonance(tier: str) -> None:
    """Persist ``preferences.default_resonance`` in ``brain/calibration.json``.

    NEXO Desktop's preferences UI reads from calibration.json (matches the
    rest of the user-facing knobs — autonomy, communication, assistant_name,
    …). This helper keeps the CLI path writing to both calibration.json
    AND schedule.json so the two surfaces never disagree.
    """
    cal_path = NEXO_HOME / "brain" / "calibration.json"
    try:
        cal_path.parent.mkdir(parents=True, exist_ok=True)
        if cal_path.exists():
            data = json.loads(cal_path.read_text())
            if not isinstance(data, dict):
                data = {}
        else:
            data = {}
        prefs = data.get("preferences")
        if not isinstance(prefs, dict):
            prefs = {}
        prefs["default_resonance"] = tier
        data["preferences"] = prefs
        cal_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    except Exception as exc:  # best-effort; schedule.json still has the value
        print(f"[NEXO] Warning: could not update calibration.json: {exc}",
              file=sys.stderr)


def _preferences(args):
    """Read or change user preferences stored in schedule.json.

    Today this manages ``default_resonance``. Other knobs (default_client,
    autonomy level, etc.) can be added here instead of spreading across
    one-off flags.
    """
    from client_preferences import (
        load_client_preferences,
        save_client_preferences,
    )
    from resonance_map import (
        DEFAULT_RESONANCE,
        TIERS,
        _load_user_default_resonance,
    )

    prefs = load_client_preferences()
    if not isinstance(prefs, dict):
        prefs = {}

    if args.resonance:
        tier = args.resonance.lower()
        if tier not in TIERS:
            print(
                f"[NEXO] Unknown resonance tier '{args.resonance}'. "
                f"Valid values: {', '.join(TIERS)}.",
                file=sys.stderr,
            )
            return 2
        # Write to schedule.json (legacy CLI location)…
        save_client_preferences(default_resonance=tier)
        # …and to calibration.json (where NEXO Desktop's preferences UI
        # reads/writes). Keeping both in sync means the two surfaces agree.
        _write_calibration_default_resonance(tier)
        prefs = load_client_preferences()

    calibration_value = _load_user_default_resonance()
    schedule_value = str(
        (prefs.get("default_resonance") if isinstance(prefs, dict) else "")
        or ""
    ).strip().lower()
    current_resonance = calibration_value or schedule_value or DEFAULT_RESONANCE

    if args.show or args.resonance:
        is_explicit = bool(calibration_value or schedule_value)
        payload = {
            "default_resonance": current_resonance,
            "default_resonance_is_explicit": is_explicit,
            "default_resonance_source": (
                "calibration.json" if calibration_value
                else ("schedule.json" if schedule_value else "default")
            ),
            "available_tiers": list(TIERS),
        }
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(f"default_resonance = {current_resonance}")
            print(f"  source: {payload['default_resonance_source']}")
            if not is_explicit:
                print(f"  (inherited from DEFAULT_RESONANCE; run "
                      f"`nexo preferences --resonance alto` to set explicitly)")
        return 0

    # No flag: print usage
    print("Usage: nexo preferences [--resonance TIER] [--show] [--json]")
    print(f"  resonance tiers: {', '.join(TIERS)}")
    print(f"  current default: {current_resonance}")
    return 0


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
    print(f"[NEXO] {_version_status_line()}", file=sys.stderr)

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


def _notify(args):
    """Emit an event to the runtime events bus (events.ndjson)."""
    from events_bus import emit
    try:
        event = emit(
            args.type,
            text=getattr(args, "text", "") or "",
            reason=getattr(args, "reason", "") or "",
            priority=getattr(args, "priority", "normal"),
            source=getattr(args, "source", "nexo-brain"),
        )
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"ok": True, "event": event}, ensure_ascii=False, indent=2))
    else:
        print(f"notify: id={event['id']} type={event['type']} priority={event['priority']}")
    return 0


def _health(args):
    """Collect a health snapshot and print it."""
    from health_check import collect
    report = collect()
    if getattr(args, "json", True) is False:
        # minimal text mode
        print(f"status: {report.get('status', 'unknown')}")
        for name, sub in report.get("subsystems", {}).items():
            print(f"  {name:10s} {sub.get('status', '?')}")
        return 0 if report.get("status") == "ok" else 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "ok" else 1


def _logs(args):
    """Tail recent logs: events bus + operations/*.log."""
    import glob as _glob
    lines_want = max(1, int(getattr(args, "lines", 100)))
    source = (getattr(args, "source", "all") or "all").lower()

    results: dict = {"source": source, "lines": lines_want, "entries": []}
    home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))

    def _collect_events(n: int) -> list[dict]:
        try:
            from events_bus import tail as _tail
            return _tail(lines=n)
        except Exception as exc:
            return [{"error": f"events tail failed: {exc}"}]

    def _collect_ops(n: int) -> list[dict]:
        ops_dir = home / "operations"
        if not ops_dir.is_dir():
            return []
        files = sorted(
            ops_dir.glob("*.log"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )[:5]
        out: list[dict] = []
        for log in files:
            try:
                text = log.read_text(errors="ignore").splitlines()[-n:]
            except Exception as exc:
                out.append({"file": str(log), "error": str(exc)})
                continue
            for line in text:
                out.append({"file": log.name, "line": line})
        return out[-n:]

    if source in ("all", "events"):
        results["entries"].extend({"kind": "event", **e} for e in _collect_events(lines_want))
    if source in ("all", "operations"):
        results["entries"].extend({"kind": "log", **e} for e in _collect_ops(lines_want))
    if source not in ("all", "events", "operations"):
        # Treat as filename match inside operations/
        specific = home / "operations" / source
        if specific.is_file():
            try:
                text = specific.read_text(errors="ignore").splitlines()[-lines_want:]
                results["entries"] = [{"kind": "log", "file": specific.name, "line": ln} for ln in text]
            except Exception as exc:
                results["error"] = str(exc)
        else:
            results["error"] = f"unknown log source: {source}"

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for entry in results["entries"][-lines_want:]:
            if entry.get("kind") == "event":
                print(f"[event] id={entry.get('id')} {entry.get('type')} "
                      f"{entry.get('priority')} {entry.get('text','')}")
            else:
                print(f"[{entry.get('file','?')}] {entry.get('line','')}")
    return 0


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

    # Calibration migration hook — runs before the orchestrator so the rest
    # of doctor sees the canonical nested shape.
    want_migrate = getattr(args, "migrate_calibration", False) or args.fix
    dry = getattr(args, "calibration_dry_run", False)
    if want_migrate or dry:
        from calibration_migration import detect, apply_migration
        shape = detect()
        if shape["shape"] == "flat":
            mig = apply_migration(dry_run=dry)
            if args.json:
                print(json.dumps({"calibration_migration": mig}, ensure_ascii=False, indent=2))
            else:
                print(f"[NEXO] calibration migration: {mig.get('status')} — {mig.get('reason','')}",
                      file=sys.stderr, flush=True)

    tier_label = getattr(args, "tier", "boot") or "boot"
    print(f"[NEXO] Inspecting {tier_label} diagnostics... please wait.", file=sys.stderr, flush=True)
    report = run_doctor(tier=args.tier, fix=args.fix, plane=getattr(args, "plane", ""))
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


def _uninstall(args):
    """Stop all crons, remove MCP config and hooks, preserve user data."""
    from pathlib import Path

    nexo_home = Path(os.environ.get("NEXO_HOME", Path.home() / ".nexo"))
    dry_run = args.dry_run
    delete_data = args.delete_data
    use_json = args.json
    platform = sys.platform

    actions: list[dict] = []
    errors: list[str] = []

    def log_action(category: str, detail: str, path: str = ""):
        actions.append({"category": category, "detail": detail, "path": path})
        if not use_json:
            tag = "[DRY-RUN] " if dry_run else ""
            print(f"  {tag}{category}: {detail}")

    # ── 1. Stop and remove LaunchAgents (macOS) ──
    if platform == "darwin":
        la_dir = Path.home() / "Library" / "LaunchAgents"
        if la_dir.exists():
            uid = os.getuid()
            for plist in sorted(la_dir.glob("com.nexo.*.plist")):
                label = plist.stem
                # Stop the agent
                if not dry_run:
                    subprocess.run(
                        ["launchctl", "bootout", f"gui/{uid}", str(plist)],
                        capture_output=True,
                    )
                log_action("stop-cron", f"launchctl bootout {label}", str(plist))
                # Remove plist file
                if not dry_run:
                    plist.unlink(missing_ok=True)
                log_action("remove-plist", label, str(plist))
    # ── systemd (Linux) ──
    elif platform == "linux":
        systemd_dir = Path.home() / ".config" / "systemd" / "user"
        if systemd_dir.exists():
            for unit in sorted(list(systemd_dir.glob("nexo-*.timer")) + list(systemd_dir.glob("nexo-*.service"))):
                if not dry_run:
                    if unit.suffix == ".timer":
                        subprocess.run(["systemctl", "--user", "stop", unit.name], capture_output=True)
                        subprocess.run(["systemctl", "--user", "disable", unit.name], capture_output=True)
                    unit.unlink(missing_ok=True)
                log_action("remove-systemd", unit.name, str(unit))
            if not dry_run:
                subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)

    # ── 2. Remove MCP server and hooks from Claude Code settings ──
    claude_settings = Path.home() / ".claude" / "settings.json"
    if claude_settings.exists():
        try:
            settings = json.loads(claude_settings.read_text())
            changed = False

            # Remove nexo MCP server
            mcp = settings.get("mcpServers", {})
            if "nexo" in mcp:
                if not dry_run:
                    del mcp["nexo"]
                    changed = True
                log_action("remove-mcp", "nexo server from settings.json", str(claude_settings))

            # Remove NEXO hooks (hooks referencing NEXO_HOME)
            hooks = settings.get("hooks", {})
            nexo_home_str = str(nexo_home)
            for event_name in list(hooks.keys()):
                hook_list = hooks[event_name]
                if isinstance(hook_list, list):
                    original_len = len(hook_list)
                    filtered = [
                        h for h in hook_list
                        if not (
                            isinstance(h, dict)
                            and nexo_home_str in (h.get("command", "") + " ".join(h.get("args", [])))
                        )
                    ]
                    if len(filtered) < original_len:
                        if not dry_run:
                            hooks[event_name] = filtered
                            changed = True
                        removed_count = original_len - len(filtered)
                        log_action("remove-hooks", f"{removed_count} hook(s) from {event_name}", str(claude_settings))
                # Clean up empty hook lists
                if isinstance(hooks.get(event_name), list) and len(hooks.get(event_name, [])) == 0:
                    if not dry_run:
                        del hooks[event_name]
                        changed = True

            if changed and not dry_run:
                claude_settings.write_text(json.dumps(settings, indent=2, ensure_ascii=False))
        except Exception as exc:
            errors.append(f"Failed to clean settings.json: {exc}")

    # ── 3. Remove Codex AGENTS.md bootstrap ──
    codex_agents = Path.home() / "AGENTS.md"
    if codex_agents.exists():
        try:
            content = codex_agents.read_text()
            if "NEXO" in content or "nexo" in content:
                log_action("preserve-note", "AGENTS.md contains NEXO references — remove manually if desired", str(codex_agents))
        except Exception:
            pass

    # ── 4. Remove runtime files, PRESERVE user data ──
    # User data directories that are NEVER deleted (unless --delete-data)
    user_data_dirs = {"data", "brain", "operations", "coordination", "config", "logs", "backups"}
    user_data_files = {"version.json"}  # keeps reinstall detection working

    # Runtime directories that get removed
    runtime_dirs = {"plugins", "hooks", "dashboard", "cognitive", "db", "rules", "crons", "doctor", "skills"}
    # Runtime flat files: any .py/.txt at NEXO_HOME root is core runtime.
    # User data always lives in subdirectories (data/, brain/, scripts/, etc.)
    # so this is safe and doesn't need updating when new core files are added.
    runtime_file_extensions = {".py", ".txt"}

    if nexo_home.exists():
        # Remove runtime directories
        for d in sorted(runtime_dirs):
            dir_path = nexo_home / d
            if dir_path.is_dir():
                if not dry_run:
                    shutil.rmtree(dir_path, ignore_errors=True)
                log_action("remove-runtime-dir", d, str(dir_path))

        # Remove runtime flat files (any .py/.txt at root level)
        for file_path in sorted(nexo_home.iterdir()):
            if file_path.is_file() and file_path.suffix in runtime_file_extensions:
                if not dry_run:
                    file_path.unlink(missing_ok=True)
                log_action("remove-runtime-file", file_path.name, str(file_path))

        # Remove core scripts (nexo-*.py/sh in scripts/)
        scripts_dir = nexo_home / "scripts"
        if scripts_dir.is_dir():
            for script in sorted(scripts_dir.glob("nexo-*")):
                if script.is_file():
                    if not dry_run:
                        script.unlink(missing_ok=True)
                    log_action("remove-core-script", script.name, str(script))
            # Remove deep-sleep directory (core)
            ds_dir = scripts_dir / "deep-sleep"
            if ds_dir.is_dir():
                if not dry_run:
                    shutil.rmtree(ds_dir, ignore_errors=True)
                log_action("remove-runtime-dir", "scripts/deep-sleep", str(ds_dir))

        # List preserved user data
        for d in sorted(user_data_dirs):
            dir_path = nexo_home / d
            if dir_path.is_dir():
                if delete_data:
                    if not dry_run:
                        shutil.rmtree(dir_path, ignore_errors=True)
                    log_action("DELETE-user-data", d, str(dir_path))
                else:
                    log_action("preserve-data", d, str(dir_path))

        # Preserve personal scripts (non nexo-* files in scripts/)
        if scripts_dir.is_dir():
            personal = [f.name for f in scripts_dir.iterdir() if f.is_file() and not f.name.startswith("nexo-")]
            if personal:
                log_action("preserve-scripts", f"{len(personal)} personal script(s)", str(scripts_dir))

        # Preserve templates/
        templates_dir = nexo_home / "templates"
        if templates_dir.is_dir():
            log_action("preserve-data", "templates", str(templates_dir))

    # ── 5. Write uninstall marker for reinstall detection ──
    if not dry_run and nexo_home.exists():
        marker = nexo_home / ".uninstalled"
        marker.write_text(json.dumps({
            "uninstalled_at": __import__("datetime").datetime.now().isoformat(),
            "nexo_home": str(nexo_home),
            "data_preserved": not delete_data,
        }, indent=2))
        log_action("write-marker", ".uninstalled marker for reinstall detection", str(marker))

    # ── Summary ──
    result = {
        "ok": len(errors) == 0,
        "dry_run": dry_run,
        "nexo_home": str(nexo_home),
        "actions": actions,
        "errors": errors,
        "data_preserved": not delete_data,
    }

    if use_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print()
        if dry_run:
            print(f"  DRY RUN complete. {len(actions)} action(s) would be taken.")
            print("  Run without --dry-run to execute.")
        else:
            print(f"  Uninstall complete. {len(actions)} action(s) taken.")
            if not delete_data:
                print(f"\n  Your data is preserved in: {nexo_home}")
                print("  To reinstall: npm install -g nexo-brain && nexo-brain")
            if errors:
                print(f"\n  {len(errors)} error(s):")
                for e in errors:
                    print(f"    - {e}")

    return 1 if errors else 0


def _print_help():
    v = _get_version()
    print(f"""NEXO Runtime CLI v{v}
{_version_status_line()}

Commands:
  nexo chat [path] [--client claude_code|codex]      Launch a NEXO terminal client
  nexo export [path]                                 Export a portable user-data bundle
  nexo import PATH                                   Import a portable user-data bundle
  nexo doctor [--tier boot|runtime|deep|all] [--fix]   System diagnostics
  nexo scripts list|create|classify|sync|reconcile|ensure-schedules|schedules|run|doctor|call|unschedule|remove
                                                      Personal scripts
  nexo skills list|apply|sync|approve                  Executable skills
  nexo clients sync                                    Sync Claude/Codex shared-brain configs and bootstrap files
  nexo update                                          Update installed runtime
  nexo uninstall [--dry-run] [--delete-data]            Stop crons, remove runtime (keeps data)
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

    # -- email (Plan F1 — interactive wizard for email accounts) --
    try:
        from cli_email import register_email_parser
        register_email_parser(sub)
    except Exception as _exc_email:  # pragma: no cover
        pass

    # -- chat --
    chat_parser = sub.add_parser("chat", help="Launch a NEXO terminal client")
    chat_parser.add_argument("path", nargs="?", default=".", help="Working directory (default: current directory)")
    chat_parser.add_argument(
        "--client",
        choices=["claude_code", "codex"],
        help="Override the chat picker and launch a specific terminal client",
    )

    # -- export --
    export_parser = sub.add_parser("export", help="Export a portable user-data bundle")
    export_parser.add_argument("path", nargs="?", default="", help="Output bundle path (default: NEXO_HOME/exports/...)")
    export_parser.add_argument("--json", action="store_true", help="JSON output")

    # -- import --
    import_parser = sub.add_parser("import", help="Import a portable user-data bundle")
    import_parser.add_argument("path", help="Bundle path created by `nexo export`")
    import_parser.add_argument("--json", action="store_true", help="JSON output")

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
    update_parser.add_argument(
        "--no-clis",
        dest="no_clis",
        action="store_true",
        help="Skip auto-updating external terminal CLIs (Claude Code, Codex)",
    )

    # -- recover --
    recover_parser = sub.add_parser(
        "recover",
        help="Restore ~/.nexo/data/nexo.db from a hourly backup (data-loss recovery)",
    )
    recover_parser.add_argument("--from", dest="source", default=None,
                                help="Explicit backup path (file or snapshot directory)")
    recover_parser.add_argument("--list", action="store_true",
                                help="List available backups and exit")
    recover_parser.add_argument("--dry-run", action="store_true",
                                help="Report the plan but do not touch the DB")
    recover_parser.add_argument("--force", action="store_true",
                                help="Overwrite the current DB even if it does not look wiped")
    recover_parser.add_argument("--yes", action="store_true",
                                help="Skip the interactive confirmation prompt")
    recover_parser.add_argument("--json", action="store_true", help="JSON output")

    # -- clients --
    clients_parser = sub.add_parser("clients", help="Shared client config management")
    clients_sub = clients_parser.add_subparsers(dest="clients_command")
    clients_sync_p = clients_sub.add_parser("sync", help="Sync Claude Code, Claude Desktop, and Codex to the same NEXO brain")
    clients_sync_p.add_argument("--json", action="store_true", help="JSON output")

    # -- preferences --
    preferences_parser = sub.add_parser(
        "preferences",
        help="Read or change NEXO user preferences (resonance, default client, ...)",
    )
    preferences_parser.add_argument(
        "--resonance",
        choices=["maximo", "alto", "medio", "bajo"],
        help="Set the default resonance tier for interactive sessions "
             "(nexo chat, Desktop new conversation, interactive nexo update). "
             "System-owned callers (deep-sleep, catchup, etc.) ignore this value "
             "and use the tier hard-coded in resonance_map.py. Default: alto.",
    )
    preferences_parser.add_argument(
        "--show",
        action="store_true",
        help="Print the current preferences as JSON and exit.",
    )
    preferences_parser.add_argument("--json", action="store_true", help="JSON output")

    # -- doctor --
    doctor_parser = sub.add_parser("doctor", help="Unified diagnostics")
    doctor_parser.add_argument("--tier", default="boot", choices=["boot", "runtime", "deep", "all"],
                               help="Diagnostic tier (default: boot)")
    doctor_parser.add_argument(
        "--plane",
        default="",
        choices=["", "runtime_personal", "installation_live", "database_real", "product_public", "cooperator"],
        help="Diagnostic plane. Doctor only runs on runtime_personal, installation_live, or database_real.",
    )
    doctor_parser.add_argument("--json", action="store_true", help="JSON output")
    doctor_parser.add_argument("--fix", action="store_true", help="Apply deterministic fixes")
    doctor_parser.add_argument("--migrate-calibration", action="store_true",
                               help="Force calibration.json flat → nested migration")
    doctor_parser.add_argument("--calibration-dry-run", action="store_true",
                               help="Preview the calibration migration without writing")

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

    # -- uninstall --
    uninstall_parser = sub.add_parser("uninstall", help="Stop all crons, remove runtime, keep user data")
    uninstall_parser.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")
    uninstall_parser.add_argument("--delete-data", action="store_true", help="Also delete databases and user data (DESTRUCTIVE)")
    uninstall_parser.add_argument("--json", action="store_true", help="JSON output")

    # -- dashboard --
    dashboard_parser = sub.add_parser("dashboard", help="Web dashboard control")
    dashboard_parser.add_argument("action", choices=["on", "off", "status"], help="Start, stop, or check dashboard")

    # -- desktop bridge (read-only, for NEXO Desktop and any external UI) --
    # Fase E.5 — quarantine ops surfaced via Desktop Guardian Proposals panel.
    quarantine_parser = sub.add_parser("quarantine", help="Quarantine proposals (Fase E.5 Desktop UI)")
    quarantine_sub = quarantine_parser.add_subparsers(dest="quarantine_command")

    qlist_p = quarantine_sub.add_parser("list", help="List quarantine items")
    qlist_p.add_argument("--status", default="pending",
                         choices=["pending", "promoted", "rejected", "expired", "all"])
    qlist_p.add_argument("--limit", type=int, default=20)
    qlist_p.add_argument("--json", action="store_true", help="JSON output (default)")

    qpromote_p = quarantine_sub.add_parser("promote", help="Promote a quarantine item to STM")
    qpromote_p.add_argument("id", help="Quarantine item id")
    qpromote_p.add_argument("--json", action="store_true", help="JSON output (default)")

    qreject_p = quarantine_sub.add_parser("reject", help="Reject a quarantine item")
    qreject_p.add_argument("id", help="Quarantine item id")
    qreject_p.add_argument("--reason", default="", help="Optional rejection reason")
    qreject_p.add_argument("--json", action="store_true", help="JSON output (default)")

    schema_parser = sub.add_parser("schema", help="Editable-field schema for Preferences UI")
    schema_parser.add_argument("--json", action="store_true", help="JSON output (default)")

    identity_parser = sub.add_parser("identity", help="Canonical assistant identity")
    identity_parser.add_argument("--json", action="store_true", help="JSON output (default)")

    onboard_parser = sub.add_parser("onboard", help="Onboarding wizard steps")
    onboard_parser.add_argument("--json", action="store_true", help="JSON output (default)")

    scan_profile_parser = sub.add_parser("scan-profile", help="Build profile.json from CLAUDE.md + calibration")
    scan_profile_parser.add_argument("--json", action="store_true", help="JSON output")
    scan_profile_parser.add_argument("--apply", action="store_true", help="Write profile.json (default is preview)")
    scan_profile_parser.add_argument("--force", action="store_true", help="Overwrite existing profile.json on --apply")

    # -- runtime events bus + operational observability --
    notify_parser = sub.add_parser("notify", help="Emit an event to the runtime event bus")
    notify_parser.add_argument("type", choices=[
        "attention_required", "proactive_message", "followup_alert",
        "health_alert", "info",
    ], help="Event type")
    notify_parser.add_argument("--text", default="", help="Short user-facing message")
    notify_parser.add_argument("--reason", default="", help="Internal reason / trigger")
    notify_parser.add_argument("--priority", choices=["low", "normal", "high", "urgent"], default="normal")
    notify_parser.add_argument("--source", default="nexo-brain", help="Who emitted this event")
    notify_parser.add_argument("--json", action="store_true", help="JSON output")

    health_parser = sub.add_parser("health", help="Snapshot of NEXO Brain subsystem health")
    health_parser.add_argument("--json", action="store_true", help="JSON output (default)")

    logs_parser = sub.add_parser("logs", help="Tail recent operational logs")
    logs_parser.add_argument("--tail", action="store_true", help="Tail mode (default)")
    logs_parser.add_argument("--lines", type=int, default=100, help="How many lines to return")
    logs_parser.add_argument("--source", default="all",
                             help="Log source: all | events | operations | <logname>")
    logs_parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    if args.help or (not args.command and not args.version):
        _print_help()
        return 0
    if args.version:
        print(f"nexo v{_get_version()}")
        return 0

    if args.command == "email":
        # Plan F1 — setup / list / test / remove cuentas email.
        fn = getattr(args, "func", None)
        if fn is None:
            print("usage: nexo email {setup,list,test,remove}")
            return 1
        return int(fn(args) or 0)

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
    elif args.command == "export":
        return _export_bundle(args)
    elif args.command == "import":
        return _import_bundle(args)
    elif args.command == "update":
        return _update(args)
    elif args.command == "recover":
        return _recover(args)
    elif args.command == "clients":
        if args.clients_command == "sync":
            return _clients_sync(args)
        clients_parser.print_help()
        return 0
    elif args.command == "preferences":
        return _preferences(args)
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
    elif args.command == "uninstall":
        return _uninstall(args)
    elif args.command == "dashboard":
        return _dashboard(args)
    elif args.command == "quarantine":
        from desktop_bridge import cmd_quarantine_list, cmd_quarantine_promote, cmd_quarantine_reject
        if args.quarantine_command == "list":
            return cmd_quarantine_list(args)
        if args.quarantine_command == "promote":
            return cmd_quarantine_promote(args)
        if args.quarantine_command == "reject":
            return cmd_quarantine_reject(args)
        # No subcommand — show help.
        quarantine_parser.print_help()
        return 1
    elif args.command in ("schema", "identity", "onboard", "scan-profile"):
        from desktop_bridge import cmd_schema, cmd_identity, cmd_onboard, cmd_scan_profile
        return {
            "schema": cmd_schema,
            "identity": cmd_identity,
            "onboard": cmd_onboard,
            "scan-profile": cmd_scan_profile,
        }[args.command](args)
    elif args.command == "notify":
        return _notify(args)
    elif args.command == "health":
        return _health(args)
    elif args.command == "logs":
        return _logs(args)
    else:
        _print_help()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
