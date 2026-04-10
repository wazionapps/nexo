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
import re
import subprocess
import sys
from pathlib import Path

from db import (
    approve_skill,
    capture_outcome_pattern,
    collect_skill_improvement_candidates,
    collect_scriptable_skill_candidates,
    create_skill,
    get_featured_skills,
    get_skill,
    get_skill_outcome_evidence,
    get_skill_execution_spec,
    init_db,
    list_outcome_pattern_candidates,
    list_skill_outcome_reviews,
    materialize_personal_skill_definition,
    record_skill_usage,
    render_command_template,
    sync_skill_directories,
    update_skill,
)
from script_registry import doctor_script

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent)))
VALID_LEVELS = {"trace", "draft", "published", "stable", "archived"}


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
                "outcome_review": {
                    "has_evidence": bool((skill.get("_outcome_review") or {}).get("has_evidence")),
                    "recommended_action": (skill.get("_outcome_review") or {}).get("recommended_action", "observe"),
                    "success_rate": (skill.get("_outcome_review") or {}).get("success_rate"),
                    "resolved_outcomes": (skill.get("_outcome_review") or {}).get("resolved_outcomes", 0),
                    "ranking_weight": float(skill.get("_outcome_rank", 0.0)),
                },
            }
        )
    return featured


def _skill_json_list(skill: dict, key: str) -> list:
    try:
        value = json.loads(skill.get(key, "[]"))
    except json.JSONDecodeError:
        value = []
    return value if isinstance(value, list) else []


def _outcome_skill_id(candidate: dict) -> str:
    raw_parts = [
        candidate.get("area", ""),
        candidate.get("task_type", ""),
        candidate.get("goal_profile_id", ""),
        candidate.get("selected_choice", ""),
    ]
    chunks = []
    for part in raw_parts:
        cleaned = re.sub(r"[^A-Z0-9]+", "-", str(part or "").upper()).strip("-")
        if cleaned:
            chunks.append(cleaned[:12])
    suffix = "-".join(chunks[:4]) or "GENERAL"
    return f"SK-OUTCOME-{suffix}"


def _outcome_pattern_to_skill_payload(candidate: dict, learning_id: int) -> dict:
    selected_choice = candidate.get("selected_choice", "")
    context_label = candidate.get("context_label", "contexto general")
    evidence = candidate.get("evidence") or []
    evidence_summary = ", ".join(
        f"eval#{item.get('evaluation_id')}/outcome#{item.get('outcome_id')}:{item.get('status')}"
        for item in evidence[:5]
    )
    description = (
        f"Draft skill candidate derived from {candidate.get('resolved_outcomes', 0)} resolved outcomes "
        f"for '{selected_choice}' in {context_label}."
    )
    steps = [
        f"Confirm that the current case matches {context_label}.",
        f"Use '{selected_choice}' as the default starting strategy.",
        "Check fresh constraints or evidence that could invalidate the historical pattern.",
        "Link and evaluate the resulting outcome so the evidence base keeps improving.",
    ]
    gotchas = [
        "Do not apply this skill if current evidence contradicts the pattern.",
        "Keep outcomes linked; a pattern without fresh feedback should not gain trust forever.",
    ]
    content = "\n".join(
        [
            f"# Outcome pattern skill candidate — {selected_choice}",
            "",
            description,
            "",
            "## Evidence",
            f"- Success rate: {candidate.get('success_rate', 0.0):.3f}",
            f"- Resolved outcomes: {candidate.get('resolved_outcomes', 0)}",
            f"- Context: {context_label}",
            f"- Evidence refs: {evidence_summary or 'none'}",
            "",
            "## Steps",
            *(f"{index}. {step}" for index, step in enumerate(steps, 1)),
            "",
            "## Gotchas",
            *(f"- {gotcha}" for gotcha in gotchas),
            "",
        ]
    )
    tags = [
        "outcomes-derived",
        str(candidate.get("area") or "").strip(),
        str(candidate.get("task_type") or "").strip(),
        str(candidate.get("goal_profile_id") or "").strip(),
    ]
    trigger_patterns = [
        str(candidate.get("selected_choice") or "").strip(),
        str(candidate.get("area") or "").strip(),
        str(candidate.get("task_type") or "").strip(),
    ]
    return {
        "id": _outcome_skill_id(candidate),
        "name": f"Outcome Pattern: {selected_choice}",
        "description": description,
        "level": "draft",
        "mode": "guide",
        "tags": [item for item in tags if item],
        "trigger_patterns": [item for item in trigger_patterns if item],
        "linked_learnings": [int(learning_id)],
        "steps": steps,
        "gotchas": gotchas,
        "content": content,
    }


def test_skill(skill_id: str, params=None, mode: str = "auto", context: str = "") -> dict:
    result = apply_skill(skill_id, params=params, mode=mode, dry_run=True, context=context or "skill_test")
    result["tested"] = True
    result["test_kind"] = "dry_run"
    return result


def promote_skill(skill_id: str, target_level: str = "published", reason: str = "") -> dict:
    _ensure_ready()
    sync_skill_directories()
    skill = get_skill(skill_id)
    if not skill:
        return {"ok": False, "error": f"Skill {skill_id} not found"}
    clean_target = str(target_level or "published").strip().lower()
    if clean_target not in VALID_LEVELS:
        return {"ok": False, "error": f"Unsupported target_level: {target_level}"}
    if clean_target == "archived":
        return {"ok": False, "error": "Use retire_skill to archive skills explicitly"}
    outcome_review = get_skill_outcome_evidence(skill_id)
    if outcome_review.get("has_evidence"):
        recommended = outcome_review.get("recommended_action")
        if clean_target == "published" and recommended not in {"promote_published", "promote_stable"}:
            return {
                "ok": False,
                "error": "Outcome evidence does not yet support promotion to published",
                "outcome_review": outcome_review,
            }
        if clean_target == "stable" and recommended != "promote_stable":
            return {
                "ok": False,
                "error": "Outcome evidence does not yet support promotion to stable",
                "outcome_review": outcome_review,
            }
    updated = update_skill(skill_id, level=clean_target)
    if "error" in updated:
        return {"ok": False, "error": updated["error"]}
    return {
        "ok": True,
        "skill_id": skill_id,
        "previous_level": skill.get("level", ""),
        "level": updated.get("level", clean_target),
        "reason": str(reason or "").strip(),
        "outcome_review": outcome_review,
    }


def retire_skill(skill_id: str, replacement_id: str = "", reason: str = "") -> dict:
    _ensure_ready()
    sync_skill_directories()
    skill = get_skill(skill_id)
    if not skill:
        return {"ok": False, "error": f"Skill {skill_id} not found"}
    replacement = None
    clean_replacement = str(replacement_id or "").strip()
    if clean_replacement:
        replacement = get_skill(clean_replacement)
        if not replacement:
            return {"ok": False, "error": f"Replacement skill {clean_replacement} not found"}
    updated = update_skill(skill_id, level="archived")
    if "error" in updated:
        return {"ok": False, "error": updated["error"]}
    return {
        "ok": True,
        "skill_id": skill_id,
        "level": updated.get("level", "archived"),
        "replacement_id": clean_replacement,
        "reason": str(reason or "").strip(),
        "outcome_review": get_skill_outcome_evidence(skill_id),
    }


def review_skill_outcomes(skill_id: str, auto_apply: bool = False) -> dict:
    _ensure_ready()
    sync_skill_directories()
    skill = get_skill(skill_id)
    if not skill:
        return {"ok": False, "error": f"Skill {skill_id} not found"}

    review = get_skill_outcome_evidence(skill_id)
    if "error" in review:
        return {"ok": False, "error": review["error"]}

    result = {
        "ok": True,
        "skill_id": skill_id,
        "level": skill.get("level", ""),
        "review": review,
        "auto_applied": False,
        "applied_action": "",
    }
    if not auto_apply:
        return result

    action = review.get("recommended_action")
    target_level = ""
    if action == "promote_published":
        target_level = "published"
    elif action == "promote_stable":
        target_level = "stable"
    elif action == "retire":
        target_level = "archived"
    if not target_level:
        return result

    updated = update_skill(skill_id, level=target_level)
    if "error" in updated:
        return {"ok": False, "error": updated["error"], "review": review}
    result["auto_applied"] = True
    result["applied_action"] = action
    result["level"] = updated.get("level", target_level)
    result["skill"] = updated
    return result


def compose_skills(
    *,
    new_skill_id: str,
    name: str,
    component_ids: list[str],
    description: str = "",
    level: str = "draft",
    mode: str = "guide",
    tags: list[str] | None = None,
    trigger_patterns: list[str] | None = None,
) -> dict:
    _ensure_ready()
    sync_skill_directories()
    if get_skill(new_skill_id):
        return {"ok": False, "error": f"Skill {new_skill_id} already exists"}
    components = []
    for skill_id in component_ids:
        skill = get_skill(skill_id)
        if not skill:
            return {"ok": False, "error": f"Component skill {skill_id} not found"}
        components.append(skill)
    if not components:
        return {"ok": False, "error": "At least one component skill is required"}

    merged_steps: list[str] = []
    merged_gotchas: list[str] = []
    merged_tags = set(tags or [])
    merged_triggers = set(trigger_patterns or [])
    linked_learnings = set()
    source_sessions = set()
    content_lines = [f"# {name}", "", description or "Composite skill built from existing NEXO skills.", "", "## Components"]
    for skill in components:
        content_lines.append(f"- {skill['id']}: {skill['name']}")
        for step in _skill_json_list(skill, "steps"):
            if step and step not in merged_steps:
                merged_steps.append(step)
        for gotcha in _skill_json_list(skill, "gotchas"):
            if gotcha and gotcha not in merged_gotchas:
                merged_gotchas.append(gotcha)
        for trigger in _skill_json_list(skill, "trigger_patterns"):
            if trigger:
                merged_triggers.add(trigger)
        for tag in _skill_json_list(skill, "tags"):
            if tag:
                merged_tags.add(tag)
        for item in _skill_json_list(skill, "linked_learnings"):
            if item:
                linked_learnings.add(item)
        for item in _skill_json_list(skill, "source_sessions"):
            if item:
                source_sessions.add(item)

    if merged_steps:
        content_lines.extend(["", "## Steps"])
        for index, step in enumerate(merged_steps, 1):
            content_lines.append(f"{index}. {step}")
    if merged_gotchas:
        content_lines.extend(["", "## Gotchas"])
        for gotcha in merged_gotchas:
            content_lines.append(f"- {gotcha}")

    created = create_skill(
        skill_id=new_skill_id,
        name=name,
        description=description or f"Composite skill built from {', '.join(component_ids)}",
        level=level,
        tags=sorted(merged_tags),
        trigger_patterns=sorted(merged_triggers),
        source_sessions=sorted(source_sessions),
        linked_learnings=sorted(linked_learnings),
        steps=merged_steps,
        gotchas=merged_gotchas,
        content="\n".join(content_lines).strip() + "\n",
        mode=mode,
        source_kind="personal",
    )
    if "error" in created:
        return {"ok": False, "error": created["error"]}
    return {
        "ok": True,
        "skill_id": new_skill_id,
        "component_ids": component_ids,
        "level": created.get("level", level),
        "mode": created.get("mode", mode),
    }


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
    outcome_patterns = [
        candidate
        for candidate in list_outcome_pattern_candidates(limit=20)
        if candidate.get("candidate_type") == "reinforce_strategy" and candidate.get("suggested_skill_candidate")
    ]
    return {
        "scriptable": collect_scriptable_skill_candidates(),
        "improvements": collect_skill_improvement_candidates(),
        "outcome_patterns": outcome_patterns,
        "outcome_lifecycle": list_skill_outcome_reviews(limit=20, actionable_only=True),
    }


def materialize_outcome_pattern_skill(pattern_key: str) -> dict:
    _ensure_ready()
    sync_skill_directories()
    candidates = list_outcome_pattern_candidates(limit=200)
    candidate = next((item for item in candidates if item.get("pattern_key") == pattern_key), None)
    if not candidate:
        return {"ok": False, "error": "Outcome pattern candidate not found"}
    if candidate.get("candidate_type") != "reinforce_strategy":
        return {"ok": False, "error": "Only reinforce_strategy patterns can seed a skill"}
    if not candidate.get("suggested_skill_candidate"):
        return {"ok": False, "error": "Pattern is not strong enough yet to seed a skill draft"}

    learning_result = capture_outcome_pattern(pattern_key, target="learning", category="outcomes")
    if "error" in learning_result:
        return {"ok": False, "error": learning_result["error"]}
    learning = learning_result.get("learning") or {}
    skill_id = _outcome_skill_id(candidate)
    existing = get_skill(skill_id)
    if existing:
        return {
            "ok": True,
            "created": False,
            "skill": existing,
            "candidate": candidate,
            "learning": learning,
        }

    payload = _outcome_pattern_to_skill_payload(candidate, int(learning.get("id", 0) or 0))
    created = materialize_personal_skill_definition(payload)
    if "error" in created:
        return {"ok": False, "error": created["error"]}
    return {
        "ok": True,
        "created": True,
        "skill": created,
        "candidate": candidate,
        "learning": learning,
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
