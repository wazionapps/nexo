from __future__ import annotations
"""Runtime helpers for Skills v2.

This module is the single execution gate for skills. It decides:
- guide vs execute vs hybrid mode
- whether a skill is allowed to run
- how parameters are validated and rendered
- how execution is routed through the stable `nexo scripts run` CLI
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from db import (
    approve_skill,
    collect_skill_improvement_candidates,
    collect_scriptable_skill_candidates,
    get_featured_skills,
    get_skill,
    get_skill_execution_spec,
    init_db,
    materialize_personal_skill_definition,
    record_skill_usage,
    render_command_template,
    sync_skill_directories,
    update_skill,
)
from script_registry import doctor_script

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent)))


def _parse_params(params) -> dict:
    if isinstance(params, dict):
        return params
    if isinstance(params, str):
        text = params.strip()
        if not text:
            return {}
        return json.loads(text)
    return {}


def _ensure_ready():
    init_db()


def _resolve_mode(requested: str, skill: dict) -> str:
    mode = (requested or "auto").strip().lower()
    if mode in {"guide", "execute", "hybrid"}:
        return mode
    effective = str(skill.get("mode", "") or "").strip().lower()
    if effective in {"guide", "execute", "hybrid"}:
        return effective
    if skill.get("file_path") and skill.get("content"):
        return "hybrid"
    if skill.get("file_path"):
        return "execute"
    return "guide"


def _summarize_skill(skill: dict) -> str:
    steps = []
    gotchas = []
    try:
        steps = json.loads(skill.get("steps", "[]"))
    except json.JSONDecodeError:
        pass
    try:
        gotchas = json.loads(skill.get("gotchas", "[]"))
    except json.JSONDecodeError:
        pass

    lines = [
        f"[{skill['id']}] {skill['name']}",
        skill.get("description", "") or "(no description)",
    ]
    if steps:
        lines.append("Steps:")
        for index, step in enumerate(steps[:6], 1):
            lines.append(f"{index}. {step}")
    elif skill.get("content"):
        lines.append(skill["content"][:800])
    if gotchas:
        lines.append("Gotchas:")
        for gotcha in gotchas[:4]:
            lines.append(f"- {gotcha}")
    return "\n".join(lines).strip()


def _resolve_cli_command() -> list[str]:
    installed = NEXO_HOME / "bin" / "nexo"
    if installed.is_file():
        return [str(installed)]
    return [sys.executable, str(NEXO_CODE / "cli.py")]


def _run_skill_script(skill: dict, argv: list[str], timeout: int = 300) -> dict:
    if not argv:
        return {"returncode": 1, "stdout": "", "stderr": "No command to execute"}

    env = {
        **os.environ,
        "NEXO_HOME": str(NEXO_HOME),
        "NEXO_CODE": str(NEXO_CODE),
        "NEXO_SKILL_ID": skill["id"],
        "NEXO_SKILL_NAME": skill["name"],
    }

    cli_cmd = _resolve_cli_command()
    cmd = [*cli_cmd, "scripts", "run", argv[0], *argv[1:]]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "command": cmd,
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": 124,
            "stdout": "",
            "stderr": f"Skill execution timed out after {timeout}s",
            "command": cmd,
        }


def get_featured_skill_summaries(limit: int = 5) -> list[dict]:
    _ensure_ready()
    sync_skill_directories()
    featured = []
    for skill in get_featured_skills(limit=limit):
        triggers = []
        try:
            triggers = json.loads(skill.get("trigger_patterns", "[]"))
        except json.JSONDecodeError:
            pass
        featured.append(
            {
                "id": skill["id"],
                "name": skill["name"],
                "mode": skill.get("mode", "guide"),
                "execution_level": skill.get("execution_level", "none"),
                "source_kind": skill.get("source_kind", "personal"),
                "trust_score": skill.get("trust_score", 0),
                "trigger_patterns": triggers[:3],
            }
        )
    return featured


def apply_skill(skill_id: str, params=None, mode: str = "auto", dry_run: bool = False, context: str = "") -> dict:
    _ensure_ready()
    sync_skill_directories()
    skill = get_skill(skill_id)
    if not skill:
        return {"ok": False, "error": f"Skill {skill_id} not found"}

    effective_mode = _resolve_mode(mode, skill)
    response = {
        "ok": True,
        "skill_id": skill["id"],
        "skill_name": skill["name"],
        "requested_mode": mode,
        "resolved_mode": effective_mode,
        "approval_state": {
            "approval_required": bool(skill.get("approval_required", 0)),
            "approved_at": skill.get("approved_at", ""),
            "execution_level": skill.get("execution_level", "none"),
        },
    }

    if effective_mode in {"guide", "hybrid"}:
        response["guide_summary"] = _summarize_skill(skill)

    if effective_mode in {"execute", "hybrid"}:
        exec_spec = get_skill_execution_spec(skill_id)
        if "error" in exec_spec:
            response["ok"] = False
            response["error"] = exec_spec["error"]
            return response

        if not skill.get("file_path"):
            response["ok"] = False
            response["error"] = f"Skill {skill_id} has no executable script"
            return response

        if exec_spec["execution_level"] in {"read-only", "local", "remote"} and not skill.get("approved_at"):
            skill = approve_skill(skill_id, execution_level=exec_spec["execution_level"], approved_by="system:auto")
            response["approval_state"] = {
                "approval_required": bool(skill.get("approval_required", 0)),
                "approved_at": skill.get("approved_at", ""),
                "execution_level": skill.get("execution_level", exec_spec["execution_level"]),
            }

        doctor = doctor_script(skill["file_path"])
        response["script_doctor"] = doctor
        if doctor["status"] == "fail":
            response["ok"] = False
            response["error"] = "Skill script failed validation"
            return response

        rendered = render_command_template(skill, _parse_params(params))
        if not rendered.get("ok"):
            response["ok"] = False
            response["error"] = "Invalid skill parameters"
            response["param_errors"] = rendered.get("errors", [])
            return response

        argv = rendered["argv"] or [skill["file_path"]]
        response["resolved_params"] = rendered["params"]
        response["script_command"] = argv
        if dry_run:
            response["dry_run"] = True
            return response

        execution = _run_skill_script(skill, argv)
        response["execution_result"] = execution
        success = execution["returncode"] == 0
        record = record_skill_usage(
            skill_id=skill_id,
            success=success,
            context=context or skill["name"],
            notes=(execution["stderr"] or execution["stdout"])[:500],
        )
        response["usage_recorded"] = {
            "success": success,
            "trust_score": record.get("trust_score"),
            "level": record.get("level"),
            "promotion": record.get("_promotion"),
        }
        if not success:
            response["ok"] = False
            response["error"] = f"Skill execution failed with exit {execution['returncode']}"

    return response


def sync_skills() -> dict:
    _ensure_ready()
    return sync_skill_directories()


def approve_skill_execution(skill_id: str, execution_level: str = "", approved_by: str = "") -> dict:
    _ensure_ready()
    return approve_skill(skill_id, execution_level=execution_level, approved_by=approved_by)


def list_evolution_candidates() -> dict:
    _ensure_ready()
    sync_skill_directories()
    return {
        "scriptable": collect_scriptable_skill_candidates(),
        "improvements": collect_skill_improvement_candidates(),
    }


def auto_promote_skill_evolution(approved_by: str = "system:auto") -> dict:
    """Convert mature guide skills into executable drafts without manual approval."""
    _ensure_ready()
    sync_skill_directories()
    promoted = []
    skipped = []
    for candidate in collect_scriptable_skill_candidates():
        skill = get_skill(candidate["id"])
        if not skill or skill.get("file_path"):
            continue

        steps = candidate.get("steps") or []
        gotchas = candidate.get("gotchas") or []
        description = candidate.get("description", "") or "Automated skill generated from repeated successful usage."
        lines = [
            "#!/usr/bin/env python3",
            '"""Auto-generated executable skill draft."""',
            "import json",
            "import sys",
            "",
            "def main() -> int:",
            "    payload = {",
            f"        'skill_id': {json.dumps(candidate['id'])},",
            f"        'skill_name': {json.dumps(candidate['name'])},",
            f"        'description': {json.dumps(description)},",
            f"        'steps': {json.dumps(steps, ensure_ascii=False)},",
            f"        'gotchas': {json.dumps(gotchas, ensure_ascii=False)},",
            "        'argv': sys.argv[1:],",
            "    }",
            "    print(json.dumps(payload, ensure_ascii=False))",
            "    return 0",
            "",
            'if __name__ == "__main__":',
            "    raise SystemExit(main())",
            "",
        ]
        update = update_skill(
            candidate["id"],
            mode=candidate.get("suggested_mode", "hybrid"),
            execution_level=candidate.get("suggested_execution_level", "read-only"),
            approval_required=0,
            approved_by=approved_by,
        )
        if "error" in update:
            skipped.append({"id": candidate["id"], "reason": update["error"]})
            continue

        materialized = materialize_personal_skill_definition(
            {
                "id": candidate["id"],
                "name": candidate["name"],
                "description": description,
                "level": skill.get("level", "published"),
                "mode": candidate.get("suggested_mode", "hybrid"),
                "execution_level": candidate.get("suggested_execution_level", "read-only"),
                "approved_by": approved_by,
                "tags": json.loads(skill.get("tags", "[]")) if skill.get("tags") else [],
                "trigger_patterns": candidate.get("trigger_patterns", []),
                "source_sessions": candidate.get("source_sessions", []),
                "steps": steps,
                "gotchas": gotchas,
                "content": skill.get("content", ""),
                "command_template": {"argv": ["{{file_path}}"]},
                "executable_entry": "script.py",
                "script_body": "\n".join(lines),
            }
        )
        if "error" in materialized:
            skipped.append({"id": candidate["id"], "reason": materialized["error"]})
            continue

        promoted.append(
            {
                "id": candidate["id"],
                "mode": candidate.get("suggested_mode", "hybrid"),
                "execution_level": candidate.get("suggested_execution_level", "read-only"),
            }
        )
    return {"promoted": promoted, "skipped": skipped}
