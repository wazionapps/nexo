"""Protocol discipline plugin — persistent task contracts for NEXO."""

from __future__ import annotations

import json
import hashlib
import os
import re
import secrets
import time

from db import (
    close_protocol_task,
    create_followup,
    latest_cortex_evaluation_for_task,
    create_protocol_debt,
    create_protocol_task,
    build_pre_action_context,
    capture_context_event,
    format_pre_action_context_bundle,
    get_db,
    get_followups,
    get_protocol_task,
    list_workflow_goals,
    list_workflow_runs,
    list_protocol_debts,
    log_change,
    resolve_protocol_debts,
    search_learnings,
    task_has_cortex_evaluation,
)
from plugins.cortex import evaluate_cortex_state
from plugins.guard import handle_guard_check
from protocol_settings import get_protocol_strictness
from tools_sessions import handle_heartbeat


ACTION_TASKS = {"edit", "execute", "delegate"}
RESPONSE_TASKS = {"answer", "analyze"}
HIGH_STAKES_KEYWORDS = {
    "medical",
    "legal",
    "financial",
    "billing",
    "invoice",
    "payment",
    "credential",
    "password",
    "security",
    "production",
    "deploy",
    "release",
    "launch",
    "delete",
    "migration",
    "pricing",
    "refund",
    "customer",
    "public",
    "brand",
    "reputation",
    "reputational",
    "roadmap",
    "revenue",
    "cost",
}
# v5.2.0: Spanish high-stakes keywords. Parity with the English set so a
# goal written in Spanish ("migrar producción a nuevo servidor") trips
# the same high-stakes gate as its English twin. Accented and unaccented
# variants are both listed because user prompts mix both freely.
HIGH_STAKES_KEYWORDS_ES = {
    "crítico",
    "critico",
    "crítica",
    "critica",
    "producción",
    "produccion",
    "cliente",
    "clientes",
    "despliegue",
    "desplegar",
    "pago",
    "pagos",
    "facturación",
    "facturacion",
    "factura",
    "credencial",
    "credenciales",
    "contraseña",
    "seguridad",
    "legal",
    "médico",
    "medico",
    "financiero",
    "financiera",
    "privacidad",
    "marca",
    "reputación",
    "reputacion",
    "ingresos",
    "borrar",
    "eliminar",
    "migración",
    "migracion",
    "migrar",
    "lanzamiento",
    "lanzar",
    "precio",
    "precios",
    "reembolso",
    "público",
    "publico",
    "riesgo",
    "riesgos",
    "coste",
    "costes",
    "ventas",
    "pedido",
    "pedidos",
}
# v5.2.0: Negation patterns that should SUPPRESS the high-stakes flag.
# Without this, a user message like "sin afectar producción" or
# "no tocar prod" triggers a false positive just because the keyword
# is physically present. Bilingual and conservative on purpose.
NEGATION_PATTERNS = (
    re.compile(r"\bno\s+tocar\s+prod(?:ucci[oó]n|uccion)?\b", re.IGNORECASE),
    re.compile(r"\bsin\s+(?:tocar|afectar|romper|modificar)\b", re.IGNORECASE),
    re.compile(r"\bnunca\s+(?:borrar|eliminar|tocar)\b", re.IGNORECASE),
    re.compile(r"\bno\s+(?:borrar|eliminar|tocar|modificar)\b", re.IGNORECASE),
    re.compile(r"\bevitar\s+(?:borrar|eliminar|tocar|romper)\b", re.IGNORECASE),
    re.compile(r"\bavoid\s+(?:deleting|touching|breaking|modifying)\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+(?:touch|break|modify|delete)\b", re.IGNORECASE),
    re.compile(r"\bwithout\s+(?:touching|breaking|affecting)\b", re.IGNORECASE),
)


def _parse_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not value:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in stripped.split(",") if item.strip()]
    return [str(value).strip()]


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _parse_int_list(value) -> list[int]:
    items = _parse_list(value)
    parsed: list[int] = []
    for item in items:
        try:
            parsed.append(int(item))
        except (TypeError, ValueError):
            continue
    return parsed


def _has_negation_context(text: str) -> bool:
    """Return True when the text explicitly disclaims touching the sensitive area.

    Used to suppress high-stakes false positives where the user is stating
    the *boundary* of safe work ("without touching production") rather than
    the *target* of a risky action ("migrate production").
    """
    if not text:
        return False
    return any(pattern.search(text) for pattern in NEGATION_PATTERNS)


def _detect_high_stakes(*parts: str) -> bool:
    combined = " ".join((part or "").strip().lower() for part in parts if part)
    if not combined:
        return False
    # Negation override: "sin afectar producción" / "don't touch prod" / etc.
    # Explicit disclaimers suppress the flag even if a high-stakes keyword
    # is physically present, otherwise boundary statements get miscategorised
    # as action targets.
    if _has_negation_context(combined):
        return False
    return any(
        keyword in combined
        for keyword in HIGH_STAKES_KEYWORDS | HIGH_STAKES_KEYWORDS_ES
    )


def _decision_support_required(*, task_type: str, high_stakes: bool) -> bool:
    return task_type in ACTION_TASKS and high_stakes


def evaluate_response_confidence(
    *,
    goal: str,
    task_type: str,
    area: str = "",
    context_hint: str = "",
    constraints=None,
    evidence_refs=None,
    unknowns=None,
    verification_step: str = "",
    stakes: str = "",
    pre_action_context_hits: int = 0,
    area_has_atlas_entry: bool = False,
) -> dict:
    evidence_refs = _parse_list(evidence_refs)
    unknowns = _parse_list(unknowns)
    constraints = _parse_list(constraints)
    explicit_stakes = (stakes or "").strip().lower()
    high_stakes = explicit_stakes == "high" or _detect_high_stakes(
        goal,
        area,
        context_hint,
        " ".join(constraints),
        explicit_stakes,
    )

    reasons: list[str] = []
    score = 85
    if unknowns:
        score -= 35
        reasons.append(f"{len(unknowns)} unknown(s) still unresolved")
    if not evidence_refs:
        score -= 25
        reasons.append("no evidence_refs supplied")
    if not verification_step.strip():
        score -= 10
        reasons.append("no verification_step defined")
    if high_stakes:
        score -= 20
        reasons.append("high-stakes context detected")

    # v5.2.0: Positive signals. Before this release the score was purely
    # a penalty accumulator — there was no way to reward tasks that had
    # meaningful prior context loaded or that sat inside a known area.
    # Cap at +10 and +5 so these can never override a real risk signal.
    if pre_action_context_hits > 0:
        boost = min(10, pre_action_context_hits * 2)
        score += boost
        reasons.append(
            f"+{boost} from {pre_action_context_hits} pre-action context hit(s)"
        )
    if area_has_atlas_entry:
        score += 5
        reasons.append("+5 from known project-atlas area")

    final_score = max(0, min(100, score))

    mode = "answer"
    if task_type in RESPONSE_TASKS:
        if high_stakes and (unknowns or not evidence_refs):
            mode = "defer"
        elif unknowns:
            mode = "ask"
        elif high_stakes or not evidence_refs or not verification_step.strip():
            mode = "verify"

        # v5.2.0: Numeric safeguard. The boolean decision tree above
        # covers every obvious case, but tasks can accumulate soft
        # penalties without tripping any single rule. When the final
        # score is critically low, downgrade the mode by one step.
        # This catches edge cases and is monotonic — it can only make
        # the response discipline stricter, never looser.
        if mode == "answer" and final_score < 50:
            mode = "verify"
            reasons.append(
                f"numeric safeguard: score {final_score} < 50 forces verify"
            )
        elif mode == "verify" and final_score < 30 and high_stakes:
            mode = "defer"
            reasons.append(
                f"numeric safeguard: high-stakes with score {final_score} forces defer"
            )

    next_action = {
        "answer": "You may answer directly, but stay within the evidence you actually have.",
        "verify": "Verify the claim with concrete evidence before answering.",
        "ask": "Ask for the missing information instead of guessing.",
        "defer": "Do not answer yet. Defer until you have evidence and a verification path.",
    }[mode]

    return {
        "mode": mode,
        "confidence": final_score,
        "high_stakes": high_stakes,
        "reasons": reasons,
        "next_action": next_action,
    }


def _guard_excerpt(text: str, max_lines: int = 12) -> str:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return "\n".join(lines[:max_lines])


def _extract_guard_blocking_ids(guard_summary: str) -> list[int]:
    ids: list[int] = []
    in_blocking = False
    for raw_line in (guard_summary or "").splitlines():
        line = raw_line.strip()
        if line.startswith("BLOCKING RULES"):
            in_blocking = True
            continue
        if in_blocking and not line:
            break
        if in_blocking:
            match = re.search(r"#(\d+)", line)
            if match:
                ids.append(int(match.group(1)))
    return ids


def _auto_followup_id() -> str:
    return f"NF-PROTOCOL-{int(time.time())}-{secrets.randbelow(100000)}"


def _ensure_followup(description: str, *, verification: str = "", reasoning: str = "") -> dict:
    conn = get_db()
    row = conn.execute(
        """SELECT id
           FROM followups
           WHERE status NOT LIKE 'COMPLETED%'
             AND status NOT IN ('DELETED', 'archived', 'blocked', 'waiting')
             AND description = ?
           LIMIT 1""",
        (description,),
    ).fetchone()
    if row:
        return {"id": row["id"], "created": False}
    # Content fingerprint for deterministic followup id — not security-sensitive.
    followup_id = f"NF-PROTOCOL-{hashlib.sha1(description.encode('utf-8'), usedforsecurity=False).hexdigest()[:10].upper()}"
    result = create_followup(
        followup_id,
        description,
        verification=verification,
        reasoning=reasoning,
    )
    if result and "error" not in result:
        return {"id": result.get("id", followup_id), "created": True}
    return {"id": "", "created": False, "error": result.get("error", "followup create failed") if isinstance(result, dict) else "followup create failed"}


def _attention_snapshot(session_id: str) -> dict:
    goals = [goal for goal in list_workflow_goals(include_closed=False, limit=50) if goal.get("session_id") == session_id]
    runs = [run for run in list_workflow_runs(include_closed=False, limit=50) if run.get("session_id") == session_id]

    active_goals = [goal for goal in goals if goal.get("status") == "active"]
    blocked_goals = [goal for goal in goals if goal.get("status") == "blocked"]
    waiting_runs = [run for run in runs if run.get("status") in {"blocked", "waiting_approval"}]

    status = "focused"
    warnings: list[str] = []
    recommended_action = "Current focus load is acceptable."

    if len(active_goals) >= 4 or len(runs) >= 5:
        status = "overloaded"
        warnings.append("Too many active goals or open workflow runs are competing for attention.")
        recommended_action = "Finish, block, or abandon one active goal before opening more execution work."
    elif len(active_goals) >= 2 or len(runs) >= 3 or len(waiting_runs) >= 2:
        status = "split"
        warnings.append("Attention is split across multiple active goals or waiting workflow runs.")
        recommended_action = "Narrow focus and make one next action explicit before expanding scope."

    return {
        "status": status,
        "active_goals": len(active_goals),
        "blocked_goals": len(blocked_goals),
        "open_runs": len(runs),
        "waiting_runs": len(waiting_runs),
        "warnings": warnings,
        "recommended_action": recommended_action,
        "top_goal_titles": [goal.get("title", "") for goal in active_goals[:3]],
    }


def _preview_prospective_triggers(goal: str, context_hint: str, files_list: list[str]) -> list[dict]:
    text = " | ".join(part for part in [goal, context_hint, " ".join(files_list)] if part).strip()
    if not text:
        return []
    try:
        import cognitive
    except Exception:
        return []
    try:
        matches = cognitive.preview_triggers(text, use_semantic=False)
    except Exception:
        return []
    return [
        {
            "id": match["id"],
            "pattern": match["pattern"],
            "action": match["action"],
            "context": match.get("context", ""),
            "match_type": match.get("match_type", "keyword"),
        }
        for match in matches
    ]


ATLAS_PATH = os.path.join(
    os.environ.get("NEXO_HOME", os.path.join(os.path.expanduser("~"), ".nexo")),
    "brain",
    "project-atlas.json",
)


def _build_area_context(area: str) -> dict:
    """Build a pre-reading context block for a known area.

    Returns project-atlas entry, recent area learnings, and active area followups
    so the agent never starts 'cold' on a known project.
    """
    clean_area = (area or "").strip().lower()
    if not clean_area:
        return {"has_context": False}

    # 1. Project-atlas lookup
    atlas_entry = None
    try:
        with open(ATLAS_PATH, "r", encoding="utf-8") as f:
            atlas = json.load(f)
        for key, entry in atlas.items():
            if key == "_meta":
                continue
            aliases = [a.lower() for a in entry.get("aliases", [])]
            if clean_area == key.lower() or clean_area in aliases:
                atlas_entry = {
                    "project_key": key,
                    "description": entry.get("description", ""),
                    "locations": entry.get("locations", {}),
                    "servers": {k: {sk: sv for sk, sv in v.items() if sk != "credential_key"} for k, v in entry.get("servers", {}).items()} if isinstance(entry.get("servers"), dict) else {},
                }
                break
    except Exception:
        pass

    # 2. Recent area learnings (top 5)
    area_learnings = []
    try:
        results = search_learnings(clean_area, category=clean_area)
        if not results:
            results = search_learnings(clean_area)
        for learning in results[:5]:
            area_learnings.append({
                "id": learning.get("id"),
                "title": (learning.get("title") or "")[:120],
                "priority": learning.get("priority", "medium"),
            })
    except Exception:
        pass

    # 3. Active followups for the area (keyword match on description)
    area_followups = []
    try:
        all_active = get_followups("active")
        for followup in all_active:
            desc = (followup.get("description") or "").lower()
            fid = (followup.get("id") or "").lower()
            if clean_area in desc or clean_area in fid:
                area_followups.append({
                    "id": followup.get("id"),
                    "description": (followup.get("description") or "")[:120],
                    "date": followup.get("date"),
                    "priority": followup.get("priority", "medium"),
                })
                if len(area_followups) >= 5:
                    break
    except Exception:
        pass

    has_context = bool(atlas_entry or area_learnings or area_followups)
    return {
        "has_context": has_context,
        "area": clean_area,
        "atlas_entry": atlas_entry,
        "learnings_count": len(area_learnings),
        "learnings": area_learnings,
        "followups_count": len(area_followups),
        "followups": area_followups,
    }


def _create_preventive_followup(goal: str, *, attention: dict, warnings: list[dict]) -> dict | None:
    warning_lines: list[str] = []
    for match in warnings[:2]:
        action = str(match.get("action") or "").strip()
        if action:
            warning_lines.append(action[:120])
    if attention.get("warnings"):
        warning_lines.append(str(attention["warnings"][0])[:120])
    warning_lines = [line for idx, line in enumerate(warning_lines) if line and line not in warning_lines[:idx]]
    if not warning_lines:
        return None
    description = (
        f"Preventive followup before continuing '{goal[:90]}': "
        + " | ".join(warning_lines[:3])
    )
    reasoning = (
        "Created automatically during task_open because NEXO detected pre-failure warning signals "
        "before execution started."
    )
    verification = (
        "Pre-failure warning resolved or explicitly acknowledged through durable goals/workflows before continuing"
    )
    return _ensure_followup(description, verification=verification, reasoning=reasoning)


def _create_missing_learning_followup(task: dict, task_id: str, effective_files: list[str]) -> dict:
    target = ", ".join(effective_files[:3]) if effective_files else (task.get("goal", "")[:120] or task_id)
    description = (
        f"Capture reusable learning from corrected task {task_id}: "
        f"turn the fix around {target} into one canonical learning and supersede conflicting rules if needed."
    )
    reasoning = (
        f"Protocol task {task_id} was marked as corrected but closed without a reusable learning. "
        f"Prevent losing the fix or leaving contradictory active rules behind."
    )
    return create_followup(
        (_auto_followup_id()).strip(),
        description,
        verification="Learning captured and conflicting rule lifecycle resolved",
        reasoning=reasoning,
    )


def _capture_learning(
    task: dict,
    task_id: str,
    effective_files: list[str],
    *,
    category: str,
    title: str,
    content: str,
    reasoning: str,
    priority: str = "high",
) -> dict:
    from tools_learnings import find_conflicting_active_learning, handle_learning_add

    clean_title = (title or "").strip()[:120]
    clean_content = (content or "").strip()
    clean_reasoning = (reasoning or f"Captured from protocol task {task_id}").strip()
    applies_to = ",".join(effective_files)
    if not clean_title or not clean_content:
        return {"ok": False, "error": "insufficient context for learning capture"}

    conflicting = find_conflicting_active_learning(
        category=category,
        title=clean_title,
        content=clean_content,
        applies_to=applies_to,
    )
    supersedes_id = int(conflicting["id"]) if conflicting else 0
    response = handle_learning_add(
        category=category,
        title=clean_title,
        content=clean_content,
        reasoning=clean_reasoning,
        applies_to=applies_to,
        priority=priority,
        supersedes_id=supersedes_id,
    )
    match = re.search(r"Learning #(\d+) added", response)
    if match:
        return {
            "ok": True,
            "id": int(match.group(1)),
            "response": response,
            "superseded_id": supersedes_id or None,
        }
    return {
        "ok": False,
        "error": response,
        "conflicting_learning_id": supersedes_id or None,
    }


def _auto_capture_learning(task: dict, task_id: str, effective_files: list[str], *,
                           clean_evidence: str, change_summary: str, change_why: str,
                           outcome_notes: str) -> dict:
    title_seed = (change_summary or task.get("goal") or f"Protocol correction {task_id}").strip()
    content_parts = []
    if change_why.strip():
        content_parts.append(change_why.strip())
    elif task.get("goal"):
        content_parts.append(str(task.get("goal", "")).strip())
    if outcome_notes.strip():
        content_parts.append(outcome_notes.strip())
    if clean_evidence.strip():
        content_parts.append(f"Verification evidence: {clean_evidence.strip()}")
    if effective_files:
        content_parts.append(f"Affected files: {', '.join(effective_files[:5])}")

    title = title_seed[:120]
    content = " ".join(part for part in content_parts if part).strip()
    return _capture_learning(
        task,
        task_id,
        effective_files,
        category=(task.get("area") or "nexo-ops"),
        title=title,
        content=content,
        reasoning=f"Auto-captured from corrected protocol task {task_id}.",
        priority="high",
    )


def _record_debt(session_id: str, task_id: str, debt_type: str, *, severity: str, evidence: str, debts: list[dict]):
    debt = create_protocol_debt(
        session_id,
        debt_type,
        severity=severity,
        task_id=task_id,
        evidence=evidence,
    )
    debts.append(
        {
            "id": debt.get("id"),
            "debt_type": debt_type,
            "severity": severity,
        }
    )


def handle_confidence_check(
    goal: str,
    task_type: str = "answer",
    area: str = "",
    context_hint: str = "",
    constraints: str = "[]",
    evidence_refs: str = "[]",
    unknowns: str = "[]",
    verification_step: str = "",
    stakes: str = "",
) -> str:
    """Return the metacognitive response mode: answer, verify, ask, or defer."""
    clean_goal = (goal or "").strip()
    if not clean_goal:
        return json.dumps({"ok": False, "error": "goal is required"}, ensure_ascii=False, indent=2)
    clean_type = task_type if task_type in {"answer", "analyze", "edit", "execute", "delegate"} else "answer"
    result = evaluate_response_confidence(
        goal=clean_goal,
        task_type=clean_type,
        area=(area or "").strip(),
        context_hint=(context_hint or "").strip(),
        constraints=_parse_list(constraints),
        evidence_refs=_parse_list(evidence_refs),
        unknowns=_parse_list(unknowns),
        verification_step=(verification_step or "").strip(),
        stakes=(stakes or "").strip(),
    )
    return json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2)


def handle_task_open(
    sid: str,
    goal: str,
    task_type: str = "answer",
    area: str = "",
    files: str = "",
    project_hint: str = "",
    plan: str = "[]",
    known_facts: str = "[]",
    unknowns: str = "[]",
    constraints: str = "[]",
    evidence_refs: str = "[]",
    verification_step: str = "",
    stakes: str = "",
    context_hint: str = "",
) -> str:
    """Open a protocol task with heartbeat, guard, rules, and Cortex already captured.

    Use this as the default entry point for any non-trivial work. For edit/execute/delegate
    tasks it becomes the contract that later must be closed with `nexo_task_close`.
    """
    clean_goal = (goal or "").strip()
    if not sid.strip():
        return json.dumps({"ok": False, "error": "sid is required"}, ensure_ascii=False, indent=2)
    if not clean_goal:
        return json.dumps({"ok": False, "error": "goal is required"}, ensure_ascii=False, indent=2)

    clean_type = task_type if task_type in {"answer", "analyze", "edit", "execute", "delegate"} else "answer"
    files_list = _parse_list(files)
    protocol_strictness = get_protocol_strictness()
    if protocol_strictness in {"strict", "learning"} and clean_type == "edit" and not files_list:
        note = (
            "Strict protocol mode requires explicit `files` for edit tasks."
            if protocol_strictness == "strict"
            else "Learning mode requires explicit `files` on edit tasks so NEXO can match the write against the open protocol task."
        )
        return json.dumps(
            {"ok": False, "error": note, "protocol_strictness": protocol_strictness},
            ensure_ascii=False,
            indent=2,
        )
    state = {
        "goal": clean_goal,
        "task_type": clean_type,
        "plan": _parse_list(plan),
        "known_facts": _parse_list(known_facts),
        "unknowns": _parse_list(unknowns),
        "constraints": _parse_list(constraints),
        "evidence_refs": _parse_list(evidence_refs),
        "verification_step": (verification_step or "").strip(),
    }
    response_contract = evaluate_response_confidence(
        goal=clean_goal,
        task_type=clean_type,
        area=area.strip(),
        context_hint=context_hint.strip(),
        constraints=state["constraints"],
        evidence_refs=state["evidence_refs"],
        unknowns=state["unknowns"],
        verification_step=state["verification_step"],
        stakes=stakes,
    )
    recent_bundle = build_pre_action_context(
        query=" | ".join(part for part in [clean_goal, context_hint.strip()] if part),
        session_id=sid.strip(),
        hours=24,
        limit=4,
    )
    area_context = _build_area_context(area.strip()) if area.strip() else {"has_context": False}
    heartbeat_result = handle_heartbeat(sid, clean_goal[:120], context_hint=context_hint[:500])
    attention = _attention_snapshot(sid.strip())
    anticipatory_warnings = _preview_prospective_triggers(clean_goal, context_hint.strip(), files_list)
    preventive_followup = None

    guard_summary = ""
    guard_has_blocking = False
    opened_with_guard = False
    debts_created: list[dict] = []
    if clean_type in ACTION_TASKS and (files_list or area.strip()):
        opened_with_guard = True
        guard_summary = handle_guard_check(files=",".join(files_list), area=area.strip())
        guard_has_blocking = (
            "[BLOCKING]" in guard_summary
            or "WARNINGS — resolve before editing" in guard_summary
            or "BLOCKING RULES" in guard_summary
        )

    cortex = evaluate_cortex_state(state)
    decision_support = {
        "required": _decision_support_required(
            task_type=clean_type,
            high_stakes=response_contract["high_stakes"],
        ),
        "tool": "nexo_cortex_decide",
        "reason": (
            "High-stakes action task detected. Rank at least 2 alternatives before acting."
            if clean_type in ACTION_TASKS and response_contract["high_stakes"]
            else "Alternative ranking not required for this task."
        ),
    }
    must_verify = clean_type in ACTION_TASKS or response_contract["mode"] == "verify"
    must_change_log = clean_type in {"edit", "execute"} and bool(files_list)
    must_learning_if_corrected = True
    must_write_diary_on_close = clean_type in ACTION_TASKS

    task = create_protocol_task(
        sid,
        clean_goal,
        task_type=clean_type,
        area=area.strip(),
        project_hint=project_hint.strip(),
        context_hint=context_hint.strip(),
        files=files_list,
        plan=state["plan"],
        known_facts=state["known_facts"],
        unknowns=state["unknowns"],
        constraints=state["constraints"],
        evidence_refs=state["evidence_refs"],
        verification_step=state["verification_step"],
        cortex_mode=cortex["mode"],
        cortex_check_id=cortex["check_id"],
        cortex_blocked_reason=cortex.get("blocked_reason") or "",
        cortex_warnings=cortex.get("warnings") or [],
        cortex_rules=cortex.get("injected_rules") or [],
        opened_with_guard=opened_with_guard,
        opened_with_rules=True,
        guard_has_blocking=guard_has_blocking,
        guard_summary=guard_summary,
        must_verify=must_verify,
        must_change_log=must_change_log,
        must_learning_if_corrected=must_learning_if_corrected,
        must_write_diary_on_close=must_write_diary_on_close,
        response_mode=response_contract["mode"],
        response_confidence=response_contract["confidence"],
        response_reasons=response_contract["reasons"],
        response_high_stakes=response_contract["high_stakes"],
    )
    protocol_context_key = f"protocol_task:{task['task_id']}"
    capture_context_event(
        event_type="protocol_task_opened",
        title=clean_goal[:160],
        summary=(context_hint or clean_goal)[:600],
        body="\n".join(state["plan"][:5])[:1600] if state["plan"] else "",
        context_key=protocol_context_key,
        context_title=clean_goal[:160],
        context_summary=(context_hint or clean_goal)[:600],
        context_type="protocol_task",
        state="active",
        owner="nexo",
        actor=sid,
        source_type="protocol_task",
        source_id=task["task_id"],
        session_id=sid,
        metadata={
            "task_type": clean_type,
            "area": area.strip(),
            "files": files_list[:8],
        },
        ttl_hours=24,
    )
    blocking_rule_ids = _extract_guard_blocking_ids(guard_summary) if guard_has_blocking else []
    if guard_has_blocking:
        _record_debt(
            task["session_id"],
            task["task_id"],
            "unacknowledged_guard_blocking",
            severity="error",
            evidence=_guard_excerpt(guard_summary),
            debts=debts_created,
        )
    elif clean_type in ACTION_TASKS and (anticipatory_warnings or attention["status"] in {"split", "overloaded"}):
        preventive_followup = _create_preventive_followup(
            clean_goal,
            attention=attention,
            warnings=anticipatory_warnings,
        )

    if guard_has_blocking:
        next_action = "Resolve the blocking guard warnings before editing."
    elif response_contract["mode"] == "defer":
        next_action = response_contract["next_action"]
    elif response_contract["mode"] == "ask" and clean_type in RESPONSE_TASKS:
        next_action = response_contract["next_action"]
    elif response_contract["mode"] == "verify" and clean_type in RESPONSE_TASKS:
        next_action = response_contract["next_action"]
    elif attention["status"] == "overloaded":
        next_action = attention["recommended_action"]
    elif anticipatory_warnings:
        next_action = "Review the anticipatory warnings before proceeding."
    elif decision_support["required"]:
        next_action = "Generate 2-3 concrete alternatives and run nexo_cortex_decide before acting."
    elif cortex["mode"] == "ask":
        next_action = "Ask for the missing information before acting."
    elif cortex["mode"] == "propose":
        next_action = "Propose the plan or verification path before acting."
    else:
        next_action = "Proceed with the task and close it with nexo_task_close before claiming completion."

    response = {
        "ok": True,
        "task_id": task["task_id"],
        "session_id": sid,
        "goal": clean_goal,
        "task_type": clean_type,
        "protocol_strictness": protocol_strictness,
        "mode": cortex["mode"],
        "check_id": cortex["check_id"],
        "blocked_reason": cortex.get("blocked_reason"),
        "warnings": cortex.get("warnings") or [],
        "applicable_rules": cortex.get("injected_rules") or [],
        "guard": {
            "ran": opened_with_guard,
            "has_blocking": guard_has_blocking,
            "blocking_rule_ids": blocking_rule_ids,
            "summary_excerpt": _guard_excerpt(guard_summary),
        },
        "attention": attention,
        "anticipation": {
            "warning_count": len(anticipatory_warnings),
            "warnings": anticipatory_warnings,
            "recommended_action": (
                "Review these anticipatory warnings before proceeding."
                if anticipatory_warnings
                else "No anticipatory warnings."
            ),
        },
        "response_contract": response_contract,
        "decision_support": decision_support,
        "recent_context": {
            "has_matches": bool(recent_bundle.get("has_matches")),
            "excerpt": format_pre_action_context_bundle(recent_bundle, compact=True) if recent_bundle.get("has_matches") else "",
        },
        "area_context": area_context if area_context.get("has_context") else None,
        "contract": {
            "must_verify": must_verify,
            "must_change_log": must_change_log,
            "must_learning_if_corrected": must_learning_if_corrected,
            "must_write_diary_on_close": must_write_diary_on_close,
            "protocol_strictness": protocol_strictness,
        },
        "session_touch": heartbeat_result.splitlines()[0] if heartbeat_result else "",
        "open_debts": debts_created,
        "preventive_followup": preventive_followup,
        "next_action": next_action,
    }
    return json.dumps(response, ensure_ascii=False, indent=2)


def handle_task_close(
    sid: str,
    task_id: str,
    outcome: str,
    evidence: str = "",
    files_changed: str = "",
    correction_happened: bool = False,
    change_summary: str = "",
    change_why: str = "",
    change_risks: str = "",
    change_verify: str = "",
    triggered_by: str = "",
    followup_needed: bool = False,
    followup_id: str = "",
    followup_description: str = "",
    followup_date: str = "",
    followup_verification: str = "",
    followup_reasoning: str = "",
    learning_category: str = "",
    learning_title: str = "",
    learning_content: str = "",
    learning_reasoning: str = "",
    outcome_notes: str = "",
) -> str:
    """Close a protocol task and automatically record the required discipline artifacts."""
    task = get_protocol_task(task_id.strip())
    if not task:
        return json.dumps({"ok": False, "error": f"Unknown task_id: {task_id}"}, ensure_ascii=False, indent=2)
    if sid.strip() and task.get("session_id") and task["session_id"] != sid.strip():
        return json.dumps(
            {"ok": False, "error": f"Task {task_id} belongs to {task['session_id']}, not {sid}"},
            ensure_ascii=False,
            indent=2,
        )

    clean_outcome = outcome if outcome in {"done", "partial", "blocked", "failed", "cancelled"} else "failed"
    clean_evidence = (evidence or "").strip()
    files_changed_list = _parse_list(files_changed)
    planned_files = _parse_list(task.get("files") or "[]")
    effective_files = files_changed_list or planned_files
    correction = _parse_bool(correction_happened)
    followup_required = _parse_bool(followup_needed)

    change_log_id = None
    learning_id = None
    created_followup_id = ""
    debts_created: list[dict] = []
    requires_decision_support = _decision_support_required(
        task_type=task.get("task_type", ""),
        high_stakes=bool(task.get("response_high_stakes")),
    )

    # ── Evidence enforcement: reject 'done' without proof in strict mode ──
    if task.get("must_verify") and clean_outcome == "done":
        if clean_evidence:
            resolve_protocol_debts(
                task_id=task_id,
                debt_types=["claimed_done_without_evidence"],
                resolution="Verification evidence supplied during task_close",
            )
        else:
            protocol_strictness = get_protocol_strictness()
            if protocol_strictness == "strict":
                return json.dumps(
                    {
                        "ok": False,
                        "error": "Cannot close task as 'done' without evidence.",
                        "hint": "Provide the `evidence` parameter with verifiable proof: test output, curl response, screenshot path, or real command output.",
                        "task_id": task_id,
                        "protocol_strictness": protocol_strictness,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            _record_debt(
                task["session_id"],
                task_id,
                "claimed_done_without_evidence",
                severity="error",
                evidence=f"Task closed as done without evidence. Goal: {task.get('goal','')}",
                debts=debts_created,
            )

    # ── Release checklist: require channel alignment evidence for release tasks ──
    RELEASE_KEYWORDS = {"release", "deploy", "version", "launch", "ship"}
    task_goal_lower = (task.get("goal") or "").lower()
    is_release = any(kw in task_goal_lower for kw in RELEASE_KEYWORDS)
    if is_release and clean_outcome == "done" and clean_evidence:
        missing_channels: list[str] = []
        evidence_lower = clean_evidence.lower()
        for channel in ["test", "staging", "production", "changelog", "version"]:
            if channel not in evidence_lower:
                missing_channels.append(channel)
        if missing_channels:
            _record_debt(
                task["session_id"],
                task_id,
                "release_channel_alignment_incomplete",
                severity="warn",
                evidence=f"Release task evidence missing channel references: {', '.join(missing_channels)}. Evidence provided: {clean_evidence[:200]}",
                debts=debts_created,
            )

    if task.get("must_change_log") and clean_outcome in {"done", "partial", "failed"}:
        if effective_files:
            change = log_change(
                task["session_id"],
                ", ".join(effective_files),
                (change_summary or f"Protocol task {task_id}: {task.get('goal', '')}")[:500],
                (change_why or task.get("goal", ""))[:500],
                (triggered_by or task_id)[:200],
                task.get("area", "")[:200],
                (change_risks or "")[:500],
                (change_verify or clean_evidence)[:500],
            )
            if "error" in change:
                _record_debt(
                    task["session_id"],
                    task_id,
                    "missing_change_log",
                    severity="warn",
                    evidence=f"change_log failed: {change['error']}",
                    debts=debts_created,
                )
            else:
                change_log_id = change.get("id")
                resolve_protocol_debts(
                    task_id=task_id,
                    debt_types=["missing_change_log"],
                    resolution="Change log created by nexo_task_close",
                )
        else:
            _record_debt(
                task["session_id"],
                task_id,
                "missing_change_log",
                severity="warn",
                evidence="Task required change_log but no changed files were supplied or recorded.",
                debts=debts_created,
            )

    if correction:
        if (learning_title or "").strip() and (learning_content or "").strip():
            learning = _capture_learning(
                task,
                task_id,
                effective_files,
                category=(learning_category or task.get("area") or "nexo-ops"),
                title=learning_title.strip(),
                content=learning_content.strip(),
                reasoning=(learning_reasoning or f"Captured from protocol task {task_id}").strip(),
                priority="high",
            )
            if not learning.get("ok"):
                _record_debt(
                    task["session_id"],
                    task_id,
                    "missing_learning_after_correction",
                    severity="warn",
                    evidence=f"learning_add failed: {learning.get('error', 'unknown error')}",
                    debts=debts_created,
                )
            else:
                learning_id = learning.get("id")
                resolve_protocol_debts(
                    task_id=task_id,
                    debt_types=["missing_learning_after_correction"],
                    resolution="Learning captured during task_close",
                )
                if learning.get("superseded_id"):
                    resolve_protocol_debts(
                        task_id=task_id,
                        debt_types=["unacknowledged_guard_blocking"],
                        resolution=f"Guard blocking rule superseded by canonical learning #{learning_id}",
                    )
        else:
            auto_learning = _auto_capture_learning(
                task,
                task_id,
                effective_files,
                clean_evidence=clean_evidence,
                change_summary=change_summary,
                change_why=change_why,
                outcome_notes=outcome_notes,
            )
            if auto_learning.get("ok"):
                learning_id = auto_learning.get("id")
                resolve_protocol_debts(
                    task_id=task_id,
                    debt_types=["missing_learning_after_correction"],
                    resolution="Learning auto-captured during task_close",
                )
                if auto_learning.get("superseded_id"):
                    resolve_protocol_debts(
                        task_id=task_id,
                        debt_types=["unacknowledged_guard_blocking"],
                        resolution=f"Guard blocking rule superseded by canonical learning #{learning_id}",
                    )
            else:
                _record_debt(
                    task["session_id"],
                    task_id,
                    "missing_learning_after_correction",
                    severity="warn",
                    evidence=f"Task was marked as corrected but reusable learning capture failed: {auto_learning.get('error', 'missing payload')}",
                    debts=debts_created,
                )
                auto_followup = _create_missing_learning_followup(task, task_id, effective_files)
                if "error" not in auto_followup and not created_followup_id:
                    created_followup_id = auto_followup.get("id", "")

    if followup_required:
        description = (followup_description or "").strip()
        if description:
            followup = create_followup(
                (followup_id or _auto_followup_id()).strip(),
                description,
                date=(followup_date or None),
                verification=(followup_verification or "").strip(),
                reasoning=(followup_reasoning or f"Created from protocol task {task_id}").strip(),
            )
            if "error" in followup:
                _record_debt(
                    task["session_id"],
                    task_id,
                    "missing_followup_payload",
                    severity="warn",
                    evidence=f"followup create failed: {followup['error']}",
                    debts=debts_created,
                )
            else:
                created_followup_id = followup.get("id", "")
        else:
            _record_debt(
                task["session_id"],
                task_id,
                "missing_followup_payload",
                severity="warn",
                evidence="followup_needed=true but no followup_description was supplied.",
                debts=debts_created,
            )

    if requires_decision_support and clean_outcome in {"done", "partial", "failed"}:
        if task_has_cortex_evaluation(task_id):
            resolve_protocol_debts(
                task_id=task_id,
                debt_types=["missing_cortex_evaluation"],
                resolution="High-stakes action task has a persisted Cortex evaluation.",
            )
        else:
            _record_debt(
                task["session_id"],
                task_id,
                "missing_cortex_evaluation",
                severity="error",
                evidence="High-stakes action task closed without nexo_cortex_decide / persisted evaluation.",
                debts=debts_created,
            )

    task = close_protocol_task(
        task_id,
        outcome=clean_outcome,
        evidence=clean_evidence,
        files_changed=effective_files,
        correction_happened=correction,
        change_log_id=change_log_id,
        learning_id=learning_id,
        followup_id=created_followup_id,
        outcome_notes=outcome_notes,
    )
    capture_context_event(
        event_type=f"protocol_task_{clean_outcome}",
        title=(task.get("goal") or task_id)[:160],
        summary=(outcome_notes or clean_evidence or clean_outcome)[:600],
        body=(change_summary or change_why or "")[:1600],
        context_key=f"protocol_task:{task_id}",
        context_title=(task.get("goal") or task_id)[:160],
        context_summary=(task.get("context_hint") or task.get("goal") or "")[:600],
        context_type="protocol_task",
        state="resolved" if clean_outcome in {"done", "cancelled"} else ("abandoned" if clean_outcome == "failed" else "blocked"),
        owner="nexo",
        actor=sid or task.get("session_id") or "nexo",
        source_type="protocol_task",
        source_id=task_id,
        session_id=task.get("session_id") or sid,
        metadata={
            "outcome": clean_outcome,
            "change_log_id": change_log_id,
            "learning_id": learning_id,
            "followup_id": created_followup_id,
        },
        ttl_hours=24,
    )
    # ── Drive/Curiosity: detect signals from task evidence (best-effort) ──
    try:
        _drive_text = " ".join(filter(None, [
            outcome_notes, clean_evidence, change_summary, change_why,
        ]))
        if _drive_text and len(_drive_text.strip()) >= 15:
            from tools_drive import detect_drive_signal as _detect_drive
            _detect_drive(
                _drive_text[:600],
                source="task_close",
                source_id=task_id,
                area=task.get("area", ""),
            )
    except Exception:
        pass  # Drive detection is best-effort

    open_debts = list_protocol_debts(status="open", task_id=task_id, limit=20)

    response = {
        "ok": True,
        "task_id": task_id,
        "outcome": clean_outcome,
        "change_log_id": change_log_id,
        "learning_id": learning_id,
        "followup_id": created_followup_id,
        "cortex_evaluation": latest_cortex_evaluation_for_task(task_id) if requires_decision_support else None,
        "debts_created": debts_created,
        "open_debts": [
            {
                "id": debt.get("id"),
                "debt_type": debt.get("debt_type"),
                "severity": debt.get("severity"),
            }
            for debt in open_debts
        ],
        "status": "clean" if not open_debts else "debt-open",
        "next_action": (
            "Do not claim completion yet. Resolve the open protocol debt first."
            if open_debts else
            "Task closed cleanly."
        ),
    }
    return json.dumps(response, ensure_ascii=False, indent=2)


def handle_task_acknowledge_guard(
    sid: str,
    task_id: str,
    learning_ids: str = "",
    note: str = "",
) -> str:
    """Acknowledge blocking guard rules for an open protocol task."""
    task = get_protocol_task(task_id.strip())
    if not task:
        return json.dumps({"ok": False, "error": f"Unknown task_id: {task_id}"}, ensure_ascii=False, indent=2)
    if sid.strip() and task.get("session_id") and task["session_id"] != sid.strip():
        return json.dumps(
            {"ok": False, "error": f"Task {task_id} belongs to {task['session_id']}, not {sid}"},
            ensure_ascii=False,
            indent=2,
        )
    if not task.get("guard_has_blocking"):
        return json.dumps(
            {"ok": False, "error": f"Task {task_id} has no blocking guard rules to acknowledge."},
            ensure_ascii=False,
            indent=2,
        )

    expected = _extract_guard_blocking_ids(task.get("guard_summary") or "")
    provided = sorted({int(item) for item in _parse_list(learning_ids) if str(item).strip().isdigit()})
    if expected and sorted(expected) != provided:
        return json.dumps(
            {
                "ok": False,
                "error": "learning_ids must acknowledge every blocking rule on the task.",
                "expected_ids": expected,
                "provided_ids": provided,
            },
            ensure_ascii=False,
            indent=2,
        )

    resolved = resolve_protocol_debts(
        task_id=task_id,
        debt_types=["unacknowledged_guard_blocking"],
        resolution=(note or f"Guard rules acknowledged: {provided}").strip(),
    )
    return json.dumps(
        {
            "ok": True,
            "task_id": task_id,
            "acknowledged_rule_ids": provided,
            "resolved_debts": resolved,
            "next_action": "Proceed with the task and close it with nexo_task_close once evidence is available.",
        },
        ensure_ascii=False,
        indent=2,
    )


def handle_protocol_debt_list(
    status: str = "open",
    task_id: str = "",
    session_id: str = "",
    debt_type: str = "",
    severity: str = "",
    limit: str = "50",
) -> str:
    rows = list_protocol_debts(
        status=status.strip() if isinstance(status, str) else "open",
        task_id=(task_id or "").strip(),
        session_id=(session_id or "").strip(),
        debt_type=(debt_type or "").strip(),
        severity=(severity or "").strip(),
        limit=max(1, min(500, int(limit or 50))),
    )
    summary: dict[str, int] = {}
    for row in rows:
        debt_key = str(row.get("debt_type") or "unknown")
        summary[debt_key] = summary.get(debt_key, 0) + 1
    return json.dumps(
        {
            "ok": True,
            "count": len(rows),
            "summary": summary,
            "items": rows,
        },
        ensure_ascii=False,
        indent=2,
    )


def handle_protocol_debt_resolve(
    debt_ids: str = "",
    task_id: str = "",
    session_id: str = "",
    debt_types: str = "",
    resolution: str = "",
) -> str:
    parsed_ids = _parse_int_list(debt_ids)
    parsed_types = _parse_list(debt_types)
    if not parsed_ids and not (task_id or "").strip() and not (session_id or "").strip() and not parsed_types:
        return json.dumps(
            {
                "ok": False,
                "error": "Provide `debt_ids`, `task_id`, `session_id`, or `debt_types` to select protocol debt.",
            },
            ensure_ascii=False,
            indent=2,
        )

    matched: list[dict] = []
    if parsed_ids:
        conn = get_db()
        placeholders = ",".join("?" for _ in parsed_ids)
        rows = conn.execute(
            f"""SELECT * FROM protocol_debt
                WHERE status = 'open' AND id IN ({placeholders})
                ORDER BY created_at DESC""",
            tuple(parsed_ids),
        ).fetchall()
        matched = [dict(row) for row in rows]
    else:
        matched = list_protocol_debts(
            status="open",
            task_id=(task_id or "").strip(),
            session_id=(session_id or "").strip(),
            limit=500,
        )
        if parsed_types:
            allowed = set(parsed_types)
            matched = [row for row in matched if str(row.get("debt_type") or "") in allowed]

    normalized_resolution = (resolution or "Resolved during protocol debt maintenance audit.").strip()
    resolved = resolve_protocol_debts(
        task_id=(task_id or "").strip(),
        session_id=(session_id or "").strip(),
        debt_ids=parsed_ids or None,
        debt_types=parsed_types or None,
        resolution=normalized_resolution,
    )
    return json.dumps(
        {
            "ok": True,
            "resolved": resolved,
            "matched_ids": [int(row["id"]) for row in matched],
            "matched_debt_types": sorted({str(row.get("debt_type") or "") for row in matched if row.get("debt_type")}),
            "resolution": normalized_resolution,
        },
        ensure_ascii=False,
        indent=2,
    )


TOOLS = [
    (handle_confidence_check, "nexo_confidence_check", "Decide whether a non-trivial answer should be answered, verified, asked, or deferred before replying."),
    (handle_task_open, "nexo_task_open", "Open a non-trivial task with heartbeat, guard, rules, and Cortex captured as one protocol contract."),
    (handle_task_acknowledge_guard, "nexo_task_acknowledge_guard", "Acknowledge blocking guard rules on an open protocol task before proceeding."),
    (handle_task_close, "nexo_task_close", "Close a protocol task, auto-record evidence/change-log/followup artifacts, and open protocol debt when discipline is missing."),
    (handle_protocol_debt_list, "nexo_protocol_debt_list", "List protocol debt records with optional status, session, task, type, or severity filters."),
    (handle_protocol_debt_resolve, "nexo_protocol_debt_resolve", "Resolve protocol debt records by id or filters once the debt has been audited and cleared."),
]
