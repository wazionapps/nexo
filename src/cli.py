#!/usr/bin/env python3
"""NEXO Runtime CLI — operational commands for scripts and diagnostics.

Entry points:
  nexo scripts list [--all] [--json]
  nexo scripts run NAME [-- args...]
  nexo scripts doctor [NAME] [--json]
  nexo scripts call TOOL --input JSON [--json-output]
  nexo doctor [--tier boot|runtime|deep|all] [--json] [--fix]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent)))

# Ensure src/ is on path for imports
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))


def _scripts_list(args):
    from script_registry import list_scripts
    scripts = list_scripts(include_core=args.all)
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
            print(f"  {s['name']:<{name_w}}  {s['runtime']:<{rt_w}}  {s['description']}{tag}")
    return 0


def _scripts_run(args):
    from script_registry import resolve_script, classify_runtime, load_core_script_names

    info = resolve_script(args.name)
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
    else:
        # Try to execute directly
        cmd = [str(path)] + args.script_args

    try:
        result = subprocess.run(cmd, env=env, timeout=timeout)
        return result.returncode
    except subprocess.TimeoutExpired:
        print(f"Script timed out after {timeout}s", file=sys.stderr)
        return 124
    except Exception as e:
        print(f"Error running script: {e}", file=sys.stderr)
        return 1


def _scripts_doctor(args):
    from script_registry import doctor_script, doctor_all_scripts

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


def _scripts_call(args):
    """Call a NEXO MCP tool via in-process fastmcp client."""
    tool_name = args.tool
    try:
        payload = json.loads(args.input) if args.input else {}
    except json.JSONDecodeError as e:
        print(f"Invalid JSON input: {e}", file=sys.stderr)
        return 1

    # In-process call: bootstrap server in CLI mode
    os.environ["NEXO_CLI_MODE"] = "1"
    try:
        from server import mcp
        import asyncio

        async def _call():
            tools = mcp._tool_manager._tools
            if tool_name not in tools:
                print(f"Tool not found: {tool_name}", file=sys.stderr)
                print("Available tools:", ", ".join(sorted(tools.keys())), file=sys.stderr)
                return 1
            tool = tools[tool_name]
            result = await tool.run(payload)
            return result

        result = asyncio.run(_call())
        if isinstance(result, dict) or isinstance(result, list):
            output = json.dumps(result, indent=2, ensure_ascii=False)
        else:
            output = str(result)

        if args.json_output:
            # Ensure valid JSON
            try:
                parsed = json.loads(output)
                print(json.dumps(parsed, indent=2, ensure_ascii=False))
            except (json.JSONDecodeError, TypeError):
                print(json.dumps({"result": output}, ensure_ascii=False))
        else:
            print(output)
        return 0

    except Exception as e:
        print(f"Error calling tool {tool_name}: {e}", file=sys.stderr)
        return 1


def _doctor(args):
    """Run unified doctor diagnostics."""
    try:
        from doctor.orchestrator import run_doctor
        from doctor.formatters import format_report
    except ImportError:
        print("Doctor module not found. Ensure NEXO is properly installed.", file=sys.stderr)
        return 1

    report = run_doctor(tier=args.tier, fix=args.fix)
    output = format_report(report, fmt="json" if args.json else "text")
    print(output)

    if report.overall_status == "critical":
        return 2
    elif report.overall_status == "degraded":
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(prog="nexo", description="NEXO Runtime CLI")
    sub = parser.add_subparsers(dest="command")

    # -- scripts --
    scripts_parser = sub.add_parser("scripts", help="Manage personal scripts")
    scripts_sub = scripts_parser.add_subparsers(dest="scripts_command")

    # scripts list
    list_p = scripts_sub.add_parser("list", help="List scripts")
    list_p.add_argument("--all", action="store_true", help="Include core/internal scripts")
    list_p.add_argument("--json", action="store_true", help="JSON output")

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

    # -- doctor --
    doctor_parser = sub.add_parser("doctor", help="Unified diagnostics")
    doctor_parser.add_argument("--tier", default="boot", choices=["boot", "runtime", "deep", "all"],
                               help="Diagnostic tier (default: boot)")
    doctor_parser.add_argument("--json", action="store_true", help="JSON output")
    doctor_parser.add_argument("--fix", action="store_true", help="Apply deterministic fixes")

    args = parser.parse_args()

    if args.command == "scripts":
        if args.scripts_command == "list":
            return _scripts_list(args)
        elif args.scripts_command == "run":
            return _scripts_run(args)
        elif args.scripts_command == "doctor":
            return _scripts_doctor(args)
        elif args.scripts_command == "call":
            return _scripts_call(args)
        else:
            scripts_parser.print_help()
            return 0
    elif args.command == "doctor":
        return _doctor(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
