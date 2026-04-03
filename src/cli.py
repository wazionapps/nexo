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
        print(f"Error calling tool {tool_name}: {e}", file=sys.stderr)
        return 1


def _update(args):
    """Update the installed runtime.

    Modes:
    - Dev-linked runtime: sync from the source repo recorded in version.json
    - Explicit dev env: sync from NEXO_CODE/src
    - Packaged/runtime-only install: delegate to plugins.update handle_update()
    """
    import shutil

    dest = NEXO_HOME

    def _runtime_version_source() -> Path | None:
        version_file = NEXO_HOME / "version.json"
        if not version_file.is_file():
            return None
        try:
            data = json.loads(version_file.read_text())
        except Exception:
            return None
        source = str(data.get("source", "")).strip()
        if not source:
            return None
        candidate = Path(source).expanduser()
        if (candidate / "src").is_dir() and (candidate / "package.json").is_file():
            return candidate
        return None

    def _resolve_sync_source() -> tuple[Path | None, Path | None]:
        try:
            same_as_runtime = NEXO_CODE.resolve() == dest.resolve()
        except Exception:
            same_as_runtime = NEXO_CODE == dest

        # Explicit dev mode: NEXO_CODE points at repo/src, never the installed runtime itself.
        if (
            not same_as_runtime
            and (NEXO_CODE / "db").is_dir()
            and (NEXO_CODE.parent / "package.json").is_file()
        ):
            return NEXO_CODE, NEXO_CODE.parent

        # Installed runtime linked back to a source checkout
        version_source = _runtime_version_source()
        if version_source:
            return version_source / "src", version_source

        return None, None

    src_dir, repo_dir = _resolve_sync_source()

    if src_dir is not None:
        try:
            if src_dir.resolve() == dest.resolve():
                version_source = _runtime_version_source()
                if version_source:
                    src_dir = version_source / "src"
                    repo_dir = version_source
                else:
                    src_dir = None
                    repo_dir = None
        except Exception:
            pass

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

        result = handle_update()
        if args.json:
            print(json.dumps({
                "mode": "packaged",
                "message": result,
            }, indent=2, ensure_ascii=False))
        else:
            print(result)
        return 0 if "UPDATE SUCCESSFUL" in result or "Already up to date" in result else 1

    # Packages (directories with __init__.py or known structure)
    packages = ["db", "cognitive", "doctor", "dashboard", "rules", "crons", "hooks"]
    copied_packages = 0
    for pkg in packages:
        pkg_src = src_dir / pkg
        pkg_dest = dest / pkg
        if pkg_src.is_dir():
            if pkg_dest.exists():
                shutil.rmtree(str(pkg_dest), ignore_errors=True)
            shutil.copytree(
                str(pkg_src), str(pkg_dest),
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.db"),
            )
            copied_packages += 1

    # Flat Python files
    flat_files = [
        "server.py", "plugin_loader.py", "knowledge_graph.py", "kg_populate.py",
        "maintenance.py", "storage_router.py", "claim_graph.py", "hnsw_index.py",
        "evolution_cycle.py", "migrate_embeddings.py", "auto_close_sessions.py",
        "auto_update.py", "tools_sessions.py", "tools_coordination.py",
        "tools_reminders.py", "tools_reminders_crud.py", "tools_learnings.py",
        "tools_credentials.py", "tools_task_history.py", "tools_menu.py",
        "cli.py", "script_registry.py", "skills_runtime.py", "user_context.py",
        "cron_recovery.py",
        "requirements.txt",
    ]
    copied_files = 0
    for f in flat_files:
        src_f = src_dir / f
        if src_f.is_file():
            shutil.copy2(str(src_f), str(dest / f))
            copied_files += 1

    # Plugins
    plugins_src = src_dir / "plugins"
    plugins_dest = dest / "plugins"
    if plugins_src.is_dir():
        plugins_dest.mkdir(parents=True, exist_ok=True)
        for f in plugins_src.iterdir():
            if f.is_file() and f.suffix == ".py":
                shutil.copy2(str(f), str(plugins_dest / f.name))

    # Scripts
    scripts_src = src_dir / "scripts"
    scripts_dest = dest / "scripts"
    if scripts_src.is_dir():
        scripts_dest.mkdir(parents=True, exist_ok=True)
        for f in scripts_src.iterdir():
            if f.name == "__pycache__" or f.name.startswith("."):
                continue
            dst = scripts_dest / f.name
            if f.is_dir():
                if dst.exists():
                    shutil.rmtree(str(dst), ignore_errors=True)
                shutil.copytree(str(f), str(dst), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            elif f.is_file():
                shutil.copy2(str(f), str(dst))
                if f.suffix == ".sh":
                    dst.chmod(0o755)

    # Templates
    templates_src = repo_dir / "templates"
    templates_dest = dest / "templates"
    if templates_src.is_dir():
        templates_dest.mkdir(parents=True, exist_ok=True)
        for f in templates_src.iterdir():
            if f.is_file():
                shutil.copy2(str(f), str(templates_dest / f.name))

    # Runtime version metadata
    package_json = repo_dir / "package.json"
    if package_json.is_file():
        shutil.copy2(str(package_json), str(dest / "package.json"))
        try:
            pkg = json.loads(package_json.read_text())
            version_payload = {
                "version": pkg.get("version", "?"),
                "source": str(repo_dir),
            }
            (dest / "version.json").write_text(json.dumps(version_payload, indent=2))
        except Exception:
            pass

    # Core skills
    skills_src = src_dir / "skills"
    skills_dest = dest / "skills-core"
    if skills_src.is_dir():
        if skills_dest.exists():
            shutil.rmtree(str(skills_dest), ignore_errors=True)
        shutil.copytree(
            str(skills_src), str(skills_dest),
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    # Runtime CLI wrapper
    bin_dir = dest / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "nexo"
    wrapper_content = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        f'NEXO_HOME="{dest}"\n'
        'PYTHON="$NEXO_HOME/.venv/bin/python3"\n'
        'if [ ! -x "$PYTHON" ]; then\n'
        '  if command -v python3 >/dev/null 2>&1; then PYTHON="python3"; else PYTHON="python"; fi\n'
        'fi\n'
        'export NEXO_HOME\n'
        'export NEXO_CODE="$NEXO_HOME"\n'
        'exec "$PYTHON" "$NEXO_HOME/cli.py" "$@"\n'
    )
    wrapper.write_text(wrapper_content)
    wrapper.chmod(0o755)

    try:
        from db import init_db
        from script_registry import sync_personal_scripts

        init_db()
        sync_personal_scripts()
    except Exception:
        pass

    result = {
        "mode": "sync",
        "packages": copied_packages,
        "files": copied_files,
        "nexo_home": str(dest),
        "source": str(src_dir),
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Updated NEXO_HOME ({dest})")
        print(f"  {copied_packages} packages, {copied_files} files synced from {src_dir}")
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


def _chat(args):
    target = args.path or "."
    claude_bin = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
    if not claude_bin:
        print("Claude Code launcher not found in PATH. Install `claude` first.", file=sys.stderr)
        return 1

    result = subprocess.run(
        [claude_bin, "--dangerously-skip-permissions", target],
        env=os.environ.copy(),
    )
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


def _print_help():
    v = _get_version()
    print(f"""NEXO Runtime CLI v{v}

Commands:
  nexo chat [path]                                    Launch Claude Code
  nexo doctor [--tier boot|runtime|deep|all] [--fix]   System diagnostics
  nexo scripts list|create|classify|sync|reconcile|ensure-schedules|schedules|run|doctor|call|unschedule|remove
                                                      Personal scripts
  nexo skills list|apply|sync|approve                  Executable skills
  nexo update                                          Update installed runtime
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
    chat_parser = sub.add_parser("chat", help="Launch Claude Code")
    chat_parser.add_argument("path", nargs="?", default=".", help="Working directory (default: current directory)")

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

    # -- doctor --
    doctor_parser = sub.add_parser("doctor", help="Unified diagnostics")
    doctor_parser.add_argument("--tier", default="boot", choices=["boot", "runtime", "deep", "all"],
                               help="Diagnostic tier (default: boot)")
    doctor_parser.add_argument("--json", action="store_true", help="JSON output")
    doctor_parser.add_argument("--fix", action="store_true", help="Apply deterministic fixes")

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
    elif args.command == "doctor":
        return _doctor(args)
    elif args.command == "skills":
        if args.skills_command == "list":
            return _skills_list(args)
        elif args.skills_command == "get":
            return _skills_get(args)
        elif args.skills_command == "apply":
            return _skills_apply(args)
        elif args.skills_command == "sync":
            return _skills_sync(args)
        elif args.skills_command == "approve":
            return _skills_approve(args)
        elif args.skills_command == "featured":
            return _skills_featured(args)
        elif args.skills_command == "evolution":
            return _skills_evolution(args)
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
