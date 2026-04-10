"""Cognitive Cortex plugin — middleware cognitive layer for NEXO Brain.

Provides structured pre-action reasoning with architectural inhibitory control.
The Cortex does NOT generate answers — it gates, plans, and validates actions.

Activation: event-driven, not on every turn. Only on:
- Tool intent (edit, execute, delegate)
- Ambiguity in user request
- Destructive actions
- Multi-step tasks
- Retry after failure
- Contradictions with known facts

v0.1: Single MCP tool + middleware validation.
"""

import json
import re
import secrets
import time


def _get_db():
    from db import get_db
    return get_db()


def _get_core_rules_for_task(task_type: str) -> list[str]:
    """Get relevant Core Rules for the given task type."""
    conn = _get_db()
    try:
        # Map task type to rule categories
        category_map = {
            "edit": ["integrity", "execution"],
            "execute": ["integrity", "execution", "delegation"],
            "delegate": ["delegation"],
            "analyze": ["execution", "memory"],
            "answer": ["communication"],
        }
        categories = category_map.get(task_type, ["integrity", "execution"])
        placeholders = ",".join("?" * len(categories))

        rows = conn.execute(
            f"SELECT id, rule FROM core_rules WHERE category IN ({placeholders}) AND is_active = 1 AND type = 'blocking' ORDER BY importance DESC LIMIT 5",
            categories
        ).fetchall()
        return [f"{r['id']}: {r['rule']}" for r in rows]
    except Exception:
        return []


def _get_trust_score() -> float:
    """Get current trust score from cognitive.db."""
    try:
        import cognitive
        return cognitive.get_trust_score()
    except Exception:
        return 50.0


SAFE_TERMS = {
    "verify", "verification", "test", "smoke", "rollback", "monitor",
    "staged", "stage", "incremental", "safe", "guard", "contract",
    "document", "docs", "reconcile", "doctor",
}
RISK_TERMS = {
    "force", "delete", "bypass", "skip", "manual", "direct", "hotfix",
    "reset", "hardcode", "production", "launchagent", "plist",
}
DIRECT_IMPACT_TERMS = {
    "fix", "close", "resolve", "ship", "release", "deploy", "migrate",
    "automate", "integrate", "register", "repair",
}
POSITIVE_OUTCOME_TERMS = {
    "met", "success", "resolved", "clean", "improved", "green", "healthy", "done",
}
NEGATIVE_OUTCOME_TERMS = {
    "missed", "failed", "failure", "regressed", "blocked", "error", "degraded",
}
STOP_WORDS = {
    "about", "after", "again", "before", "being", "between", "could", "should",
    "there", "their", "would", "while", "using", "used", "from", "with",
    "that", "this", "into", "over", "have", "must", "will", "your",
}


def _term_hits(text: str, terms: set[str]) -> int:
    lowered = (text or "").lower()
    return sum(1 for term in terms if term in lowered)


def _validate_state(state: dict) -> dict:
    """Validate cognitive state and determine action mode.

    Returns dict with: mode, warnings, injected_rules, blocked_reason
    """
    warnings = []
    mode = "act"  # default: allow action
    blocked_reason = None

    task_type = state.get("task_type", "answer")
    plan = state.get("plan", [])
    unknowns = state.get("unknowns", [])
    evidence = state.get("evidence_refs", [])
    verification = state.get("verification_step", "")
    constraints = state.get("constraints", [])
    goal = state.get("goal", "")

    # === INHIBITION RULES (architectural, not advisory) ===

    # Rule 1: unknowns exist → force ASK mode
    if unknowns:
        mode = "ask"
        blocked_reason = f"Cannot act with {len(unknowns)} unknown(s). Resolve first."
        warnings.append(f"UNKNOWNS: {', '.join(unknowns[:3])}")

    # Rule 2: edit/execute without plan → force PROPOSE
    if task_type in ("edit", "execute", "delegate") and not plan and mode == "act":
        mode = "propose"
        blocked_reason = "No plan defined for action task. Propose plan first."
        warnings.append("MISSING PLAN: define steps before executing")

    # Rule 3: edit/execute without verification → force PROPOSE
    if task_type in ("edit", "execute") and not verification and mode == "act":
        mode = "propose"
        blocked_reason = "No verification step. How will you confirm it worked?"
        warnings.append("MISSING VERIFICATION: define how to verify")

    # Rule 4: execute without evidence → force PROPOSE
    if task_type == "execute" and not evidence and mode == "act":
        mode = "propose"
        blocked_reason = "No evidence supporting this action."
        warnings.append("MISSING EVIDENCE: what supports this action?")

    # Rule 5: no goal → force ASK
    if not goal:
        mode = "ask"
        blocked_reason = "No goal defined."
        warnings.append("NO GOAL: what are you trying to achieve?")

    # === TRUST-BASED ADJUSTMENTS ===
    trust = _get_trust_score()
    if trust < 30 and mode == "act" and task_type in ("edit", "execute"):
        mode = "propose"
        blocked_reason = f"Trust score {trust:.0f}/100 — propose before acting."
        warnings.append(f"LOW TRUST ({trust:.0f}): extra verification required")

    # === INJECT RELEVANT RULES ===
    rules = _get_core_rules_for_task(task_type)

    return {
        "mode": mode,
        "tools_available": _tools_for_mode(mode),
        "warnings": warnings,
        "blocked_reason": blocked_reason,
        "injected_rules": rules,
        "trust_score": round(trust),
    }


def _tools_for_mode(mode: str) -> list[str]:
    """Define which tool categories are available per mode."""
    if mode == "ask":
        return ["read", "search", "ask_user"]
    elif mode == "propose":
        return ["read", "search", "analyze", "propose_plan"]
    else:  # act
        return ["all"]


def _parse_json_list(value) -> list:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_alternatives(value) -> list[dict]:
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            raw_items = parsed
        else:
            lines = [line.strip("-* \t") for line in stripped.splitlines() if line.strip()]
            raw_items = lines if lines else [item.strip() for item in stripped.split("|") if item.strip()]
    else:
        raw_items = [value]

    normalized = []
    for idx, item in enumerate(raw_items, start=1):
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("title") or f"alternative_{idx}").strip()
            description = str(item.get("description") or "").strip()
            pros = item.get("pros") or []
            cons = item.get("cons") or []
            if isinstance(pros, str):
                pros = [pros]
            if isinstance(cons, str):
                cons = [cons]
            normalized.append({
                "name": name,
                "description": description,
                "pros": [str(x).strip() for x in pros if str(x).strip()],
                "cons": [str(x).strip() for x in cons if str(x).strip()],
            })
            continue
        text = str(item).strip()
        if not text:
            continue
        normalized.append({
            "name": f"alternative_{idx}",
            "description": text,
            "pros": [],
            "cons": [],
        })
    return normalized


def _tokenize(text: str, limit: int = 12) -> list[str]:
    tokens = []
    for token in re.findall(r"[a-z0-9_]{4,}", (text or "").lower()):
        if token in STOP_WORDS:
            continue
        if token not in tokens:
            tokens.append(token)
        if len(tokens) >= limit:
            break
    return tokens


def _contains_any(text: str, terms: set[str]) -> bool:
    lowered = (text or "").lower()
    return any(term in lowered for term in terms)


def _impact_base(impact_level: str) -> float:
    return {
        "critical": 8.5,
        "high": 7.0,
        "medium": 5.5,
    }.get((impact_level or "").lower(), 7.0)


def _constraint_penalty(text: str, constraints: list[str]) -> tuple[float, list[str]]:
    penalty = 0.0
    reasons: list[str] = []
    lowered = (text or "").lower()
    for constraint in constraints[:8]:
        item = (constraint or "").strip()
        lowered_constraint = item.lower()
        if not item:
            continue
        if any(marker in lowered_constraint for marker in ("no ", "never", "must not", "do not", "without")):
            tokens = _tokenize(lowered_constraint, limit=4)
            if tokens and any(token in lowered for token in tokens):
                penalty += 1.5
                reasons.append(f"rozando constraint: {item[:80]}")
    return penalty, reasons[:2]


def _history_signal(text: str, *, area: str = "", goal: str = "") -> dict:
    conn = _get_db()
    tokens = _tokenize(" ".join(part for part in [text, area, goal] if part), limit=6)
    if not tokens:
        return {"positive": 0.0, "negative": 0.0, "matched_decisions": 0, "matched_outcomes": 0}

    decision_positive = 0
    decision_negative = 0
    matched_decisions = 0
    for token in tokens[:3]:
        rows = conn.execute(
            """SELECT outcome FROM decisions
               WHERE lower(decision) LIKE ? OR lower(alternatives) LIKE ? OR lower(based_on) LIKE ?
               ORDER BY created_at DESC LIMIT 6""",
            tuple(f"%{token}%" for _ in range(3)),
        ).fetchall()
        for row in rows:
            matched_decisions += 1
            outcome = (row["outcome"] or "").lower()
            if _contains_any(outcome, NEGATIVE_OUTCOME_TERMS):
                decision_negative += 1
            elif _contains_any(outcome, POSITIVE_OUTCOME_TERMS):
                decision_positive += 1

    outcome_positive = 0
    outcome_negative = 0
    matched_outcomes = 0
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outcomes'").fetchone():
        for token in tokens[:3]:
            rows = conn.execute(
                """SELECT status FROM outcomes
                   WHERE lower(description) LIKE ? OR lower(expected_result) LIKE ? OR lower(action_type) LIKE ?
                   ORDER BY created_at DESC LIMIT 6""",
                tuple(f"%{token}%" for _ in range(3)),
            ).fetchall()
            for row in rows:
                matched_outcomes += 1
                status = (row["status"] or "").lower()
                if status == "met":
                    outcome_positive += 1
                elif status in {"missed", "expired"}:
                    outcome_negative += 1

    return {
        "positive": min(2.5, (decision_positive * 0.4) + (outcome_positive * 0.5)),
        "negative": min(3.0, (decision_negative * 0.6) + (outcome_negative * 0.7)),
        "matched_decisions": matched_decisions,
        "matched_outcomes": matched_outcomes,
    }


def _somatic_penalty(*parts: str) -> float:
    conn = _get_db()
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='somatic_events'").fetchone():
        return 0.0

    query_terms = [token for token in _tokenize(" ".join(parts), limit=4) if token]
    if not query_terms:
        return 0.0

    penalty = 0.0
    for term in query_terms[:3]:
        rows = conn.execute(
            """SELECT delta FROM somatic_events
               WHERE projected = 0 AND lower(target) LIKE ?
               ORDER BY timestamp DESC LIMIT 8""",
            (f"%{term}%",),
        ).fetchall()
        for row in rows:
            delta = float(row["delta"] or 0.0)
            if delta < 0:
                penalty += abs(delta)
    return round(min(5.0, penalty), 2)


def _resolve_linked_outcome_id(*, linked_outcome_id: int | str | None = None, task_id: str = "") -> int | None:
    try:
        explicit = int(linked_outcome_id or 0)
    except (TypeError, ValueError):
        explicit = 0
    if explicit > 0:
        return explicit

    clean_task_id = (task_id or "").strip()
    if not clean_task_id:
        return None

    conn = _get_db()
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outcomes'").fetchone():
        return None

    row = conn.execute(
        """SELECT id FROM outcomes
           WHERE action_id = ? AND status = 'pending'
           ORDER BY
             CASE metric_source
               WHEN 'protocol_task_status' THEN 0
               WHEN 'decision_outcome' THEN 1
               ELSE 2
             END,
             deadline ASC,
             created_at DESC
           LIMIT 1""",
        (clean_task_id,),
    ).fetchone()
    return int(row["id"]) if row else None


def _score_alternative(
    alternative: dict,
    *,
    goal: str,
    area: str,
    task_type: str,
    impact_level: str,
    constraints: list[str],
    evidence_refs: list[str],
    goal_profile: dict,
) -> dict:
    text = " ".join([
        alternative.get("name", ""),
        alternative.get("description", ""),
        " ".join(alternative.get("pros") or []),
        " ".join(alternative.get("cons") or []),
    ]).strip()
    lowered = text.lower()
    impact = _impact_base(impact_level)
    success = 5.0 + min(2.0, len(evidence_refs) * 0.4)
    risk = 2.5
    reasons: list[str] = []
    weights = goal_profile.get("weights") or {}
    direct_hits = _term_hits(lowered, DIRECT_IMPACT_TERMS)
    safe_hits = _term_hits(lowered, SAFE_TERMS)
    risk_hits = _term_hits(lowered, RISK_TERMS)
    focus = max(weights, key=weights.get) if weights else "impact"

    if direct_hits:
        impact += min(1.6, direct_hits * 0.4)
        reasons.append("apunta directo al objetivo")
    if safe_hits:
        success += min(1.8, safe_hits * 0.45)
        risk = max(1.0, risk - min(1.1, safe_hits * 0.35))
        reasons.append("incluye verificación o despliegue seguro")
    if not safe_hits and task_type in {"edit", "execute"}:
        risk += 1.2
        reasons.append("no explicita verificación")
    if risk_hits:
        risk += min(2.8, risk_hits * 0.7)
        reasons.append("contiene señales de alto riesgo")

    if focus == "impact" and direct_hits:
        impact += 0.45
        risk = max(1.0, risk - 0.35)
        reasons.append("el perfil activo prioriza impacto")
    elif focus == "impact":
        impact = max(1.0, impact - 0.35)
        reasons.append("el perfil activo penaliza opciones de bajo empuje")
    elif focus == "success" and safe_hits:
        success += 0.45
        reasons.append("el perfil activo prioriza exito verificable")
    elif focus == "risk":
        if safe_hits:
            risk = max(1.0, risk - 0.4)
        if risk_hits:
            risk += 0.8
        reasons.append("el perfil activo penaliza riesgo")
    elif focus == "somatic":
        reasons.append("el perfil activo da peso a la huella somática")

    history = _history_signal(lowered, area=area, goal=goal)
    success += history["positive"]
    risk += history["negative"]
    if history["positive"]:
        reasons.append("histórico parecido favorable")
    if history["negative"]:
        reasons.append("histórico parecido conflictivo")

    constraint_penalty, constraint_reasons = _constraint_penalty(lowered, constraints)
    if constraint_penalty:
        risk += constraint_penalty
        reasons.extend(constraint_reasons)

    somatic = _somatic_penalty(area, goal, lowered)
    total = round(
        (impact * float(weights.get("impact", 0.35)))
        + (success * float(weights.get("success", 0.30)))
        - (risk * float(weights.get("risk", 0.20)))
        - (somatic * float(weights.get("somatic", 0.15))),
        3,
    )
    return {
        "name": alternative.get("name", ""),
        "impact": round(max(1.0, min(10.0, impact)), 2),
        "success_probability": round(max(1.0, min(10.0, success)), 2),
        "risk_level": round(max(1.0, min(10.0, risk)), 2),
        "somatic_penalty": round(max(0.0, min(5.0, somatic)), 2),
        "total_score": total,
        "notes": reasons[:4],
        "goal_profile_focus": focus,
        "history_matches": {
            "decisions": history["matched_decisions"],
            "outcomes": history["matched_outcomes"],
        },
    }


def evaluate_cortex_state(state: dict) -> dict:
    """Return structured Cortex evaluation for internal callers."""
    result = _validate_state(state)
    result["check_id"] = f"CTX-{int(time.time())}-{secrets.randbelow(100000)}"
    result["expires_at_epoch"] = int(time.time()) + 1200
    return result


def _log_cortex_activation(goal: str, task_type: str, result: dict):
    try:
        conn = _get_db()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS cortex_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal TEXT,
                task_type TEXT,
                mode TEXT,
                warnings TEXT,
                trust_score INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            )"""
        )
        conn.execute(
            "INSERT INTO cortex_log (goal, task_type, mode, warnings, trust_score) VALUES (?, ?, ?, ?, ?)",
            (
                goal[:200],
                task_type,
                result["mode"],
                json.dumps(result["warnings"]),
                result["trust_score"],
            ),
        )
        conn.commit()
    except Exception:
        pass


def _format_decision_summary(recommended: dict, alternatives_scored: list[dict]) -> str:
    notes = ", ".join(recommended.get("notes") or []) or "balance general más sólido"
    second_gap = 0.0
    if len(alternatives_scored) > 1:
        second_gap = recommended["total_score"] - alternatives_scored[1]["total_score"]
    if second_gap > 0.2:
        return f"Recomendada por margen claro ({second_gap:.2f}) y porque {notes}."
    return f"Recomendada por el mejor balance entre impacto, éxito, riesgo y huella somática; {notes}."


def handle_cortex_check(
    goal: str,
    task_type: str = "answer",
    plan: str = "[]",
    known_facts: str = "[]",
    unknowns: str = "[]",
    constraints: str = "[]",
    evidence_refs: str = "[]",
    verification_step: str = "",
) -> str:
    """Cognitive Cortex pre-action check. Call BEFORE significant actions.

    Validates your reasoning state and determines if you can act, should propose,
    or need to ask for clarification first. Implements architectural inhibitory control.

    WHEN TO CALL:
    - Before editing files or running commands
    - Before delegating to subagents
    - When the task has multiple possible approaches
    - After a failed attempt (before retrying)
    - When user instruction seems to conflict with known facts

    DO NOT CALL for simple chat responses, greetings, or explanations.

    Args:
        goal: What you are trying to achieve (required)
        task_type: One of: answer, analyze, edit, execute, delegate
        plan: JSON array of planned steps (e.g. '["read file", "edit function", "test"]')
        known_facts: JSON array of facts you have (from user, memory, files)
        unknowns: JSON array of things you don't know yet but need
        constraints: JSON array of rules or limitations that apply
        evidence_refs: JSON array of evidence supporting your plan (learnings, user statements, file contents)
        verification_step: How you will verify the action worked

    Returns:
        Mode (ask/propose/act), available tools, warnings, and relevant Core Rules
    """
    state = {
        "goal": goal.strip() if goal else "",
        "task_type": task_type if task_type in ("answer", "analyze", "edit", "execute", "delegate") else "answer",
        "plan": _parse_json_list(plan),
        "known_facts": _parse_json_list(known_facts),
        "unknowns": _parse_json_list(unknowns),
        "constraints": _parse_json_list(constraints),
        "evidence_refs": _parse_json_list(evidence_refs),
        "verification_step": verification_step.strip() if verification_step else "",
    }

    result = evaluate_cortex_state(state)

    # Format response
    lines = [
        f"CORTEX CHECK — mode: {result['mode'].upper()}",
        f"Trust: {result['trust_score']}/100",
        f"Check ID: {result['check_id']}",
        f"Valid until epoch: {result['expires_at_epoch']}",
    ]

    if result["mode"] == "act":
        lines.append("CLEARED: You may proceed with the action.")
    elif result["mode"] == "propose":
        lines.append(f"PROPOSE ONLY: {result['blocked_reason']}")
        lines.append("Show the user your plan and get approval before executing.")
    elif result["mode"] == "ask":
        lines.append(f"ASK FIRST: {result['blocked_reason']}")
        lines.append("Gather the missing information before proceeding.")

    if result["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        for w in result["warnings"]:
            lines.append(f"  - {w}")

    if result["injected_rules"]:
        lines.append("")
        lines.append("Applicable Core Rules:")
        for r in result["injected_rules"]:
            lines.append(f"  - {r}")

    lines.append("")
    lines.append(f"Tools available: {', '.join(result['tools_available'])}")

    _log_cortex_activation(goal, task_type, result)

    return "\n".join(lines)


def handle_cortex_stats(days: int = 7) -> str:
    """View Cortex activation statistics — how often it activates, modes, warnings.

    Args:
        days: Period to analyze (default 7)
    """
    conn = _get_db()
    try:
        conn.execute("SELECT 1 FROM cortex_log LIMIT 1")
    except Exception:
        return "No Cortex data yet. The Cortex activates on significant actions."

    cutoff = f"datetime('now', '-{days} days')"

    total = conn.execute(f"SELECT COUNT(*) FROM cortex_log WHERE created_at >= {cutoff}").fetchone()[0]
    by_mode = conn.execute(
        f"SELECT mode, COUNT(*) as c FROM cortex_log WHERE created_at >= {cutoff} GROUP BY mode ORDER BY c DESC"
    ).fetchall()
    by_type = conn.execute(
        f"SELECT task_type, COUNT(*) as c FROM cortex_log WHERE created_at >= {cutoff} GROUP BY task_type ORDER BY c DESC"
    ).fetchall()

    lines = [
        f"CORTEX STATS — last {days} days",
        f"Total activations: {total}",
        "",
        "By mode:",
    ]
    for r in by_mode:
        pct = (r["c"] / total * 100) if total > 0 else 0
        lines.append(f"  {r['mode']}: {r['c']} ({pct:.0f}%)")

    lines.append("")
    lines.append("By task type:")
    for r in by_type:
        lines.append(f"  {r['task_type']}: {r['c']}")

    # Inhibition rate = % of activations that resulted in ask or propose (not act)
    inhibited = sum(r["c"] for r in by_mode if r["mode"] != "act")
    inhibition_rate = (inhibited / total * 100) if total > 0 else 0
    lines.append(f"\nInhibition rate: {inhibition_rate:.0f}% (target: 30-60%)")

    return "\n".join(lines)


def handle_cortex_decide(
    goal: str,
    alternatives: str,
    task_type: str = "execute",
    impact_level: str = "high",
    context_hint: str = "",
    area: str = "",
    constraints: str = "[]",
    evidence_refs: str = "[]",
    session_id: str = "",
    task_id: str = "",
    linked_outcome_id: int = 0,
    goal_profile_id: str = "",
    goal_id: str = "",
) -> str:
    """Evaluate concrete alternatives for a high-impact task using the existing Cortex."""
    clean_goal = (goal or "").strip()
    if not clean_goal:
        return json.dumps({"ok": False, "error": "goal is required"}, ensure_ascii=False, indent=2)

    parsed_alternatives = _parse_alternatives(alternatives)
    if len(parsed_alternatives) < 2:
        return json.dumps(
            {
                "ok": False,
                "error": "Provide at least 2 alternatives so the Cortex can rank tradeoffs.",
            },
            ensure_ascii=False,
            indent=2,
        )

    clean_type = task_type if task_type in {"answer", "analyze", "edit", "execute", "delegate"} else "execute"
    clean_level = impact_level if impact_level in {"medium", "high", "critical"} else "high"
    parsed_constraints = _parse_json_list(constraints)
    parsed_evidence = _parse_json_list(evidence_refs)
    try:
        from db import resolve_goal_profile

        resolved_goal_profile = resolve_goal_profile(
            profile_id=goal_profile_id,
            area=area.strip(),
            task_type=clean_type,
            goal_id=goal_id,
        )
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"Failed to resolve goal profile: {exc}"}, ensure_ascii=False, indent=2)

    scored = [
        _score_alternative(
            item,
            goal=clean_goal,
            area=area.strip(),
            task_type=clean_type,
            impact_level=clean_level,
            constraints=parsed_constraints,
            evidence_refs=parsed_evidence,
            goal_profile=resolved_goal_profile,
        )
        for item in parsed_alternatives
    ]
    scored.sort(key=lambda item: item["total_score"], reverse=True)
    recommended = scored[0]
    reasoning = _format_decision_summary(recommended, scored)
    resolved_outcome_id = _resolve_linked_outcome_id(
        linked_outcome_id=linked_outcome_id,
        task_id=task_id,
    )

    try:
        from db import create_cortex_evaluation

        record = create_cortex_evaluation(
            session_id=session_id,
            task_id=task_id,
            goal=clean_goal,
            task_type=clean_type,
            area=area,
            impact_level=clean_level,
            context_hint=context_hint,
            alternatives=parsed_alternatives,
            scores=scored,
            recommended_choice=recommended["name"],
            recommended_reasoning=reasoning,
            linked_outcome_id=resolved_outcome_id,
            goal_profile_id=resolved_goal_profile.get("profile_id", ""),
            goal_profile_labels=resolved_goal_profile.get("goal_labels", []),
            goal_profile_weights=resolved_goal_profile.get("weights", {}),
            selected_choice=recommended["name"],
            selection_reason=reasoning,
            selection_source="recommended",
        )
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "error": f"Failed to persist cortex evaluation: {exc}",
            },
            ensure_ascii=False,
            indent=2,
        )

    return json.dumps(
        {
            "ok": True,
            "evaluation_id": record.get("id"),
            "task_id": task_id,
            "goal": clean_goal,
            "impact_level": clean_level,
            "recommendation": recommended["name"],
            "reasoning": reasoning,
            "selected_choice": record.get("selected_choice"),
            "selection_source": record.get("selection_source"),
            "linked_outcome_id": record.get("linked_outcome_id"),
            "goal_profile": {
                "profile_id": resolved_goal_profile.get("profile_id", ""),
                "profile_name": resolved_goal_profile.get("profile_name", ""),
                "resolved_by": resolved_goal_profile.get("resolved_by", ""),
                "goal_labels": resolved_goal_profile.get("goal_labels", []),
                "weights": resolved_goal_profile.get("weights", {}),
            },
            "alternatives": parsed_alternatives,
            "scores": scored,
            "next_action": "Apply the recommended choice or call nexo_cortex_override if you intentionally choose another option.",
        },
        ensure_ascii=False,
        indent=2,
    )


def handle_cortex_review(evaluation_id: int = 0, task_id: str = "", session_id: str = "", limit: int = 10) -> str:
    """Review stored Cortex alternative evaluations."""
    from db import get_cortex_evaluation, list_cortex_evaluations

    if evaluation_id:
        item = get_cortex_evaluation(evaluation_id)
        if not item:
            return json.dumps({"ok": False, "error": f"Unknown evaluation_id: {evaluation_id}"}, ensure_ascii=False, indent=2)
        return json.dumps({"ok": True, "evaluation": item}, ensure_ascii=False, indent=2)

    items = list_cortex_evaluations(session_id=session_id, task_id=task_id, limit=limit)
    return json.dumps({"ok": True, "evaluations": items}, ensure_ascii=False, indent=2)


def handle_cortex_override(evaluation_id: int, chosen: str, reason: str) -> str:
    """Override the Cortex recommendation while leaving the recommendation trail intact."""
    if not chosen.strip():
        return json.dumps({"ok": False, "error": "chosen is required"}, ensure_ascii=False, indent=2)
    if not reason.strip():
        return json.dumps({"ok": False, "error": "reason is required"}, ensure_ascii=False, indent=2)

    from db import get_cortex_evaluation, override_cortex_evaluation

    current = get_cortex_evaluation(evaluation_id)
    if not current:
        return json.dumps({"ok": False, "error": f"Unknown evaluation_id: {evaluation_id}"}, ensure_ascii=False, indent=2)

    alternatives = _parse_json_list(current.get("alternatives") or "[]")
    valid_names = {str(item.get("name", "")).strip() for item in alternatives if isinstance(item, dict)}
    if chosen.strip() not in valid_names:
        return json.dumps(
            {
                "ok": False,
                "error": "chosen must match one of the stored alternative names",
                "valid_choices": sorted(valid_names),
            },
            ensure_ascii=False,
            indent=2,
        )

    updated = override_cortex_evaluation(
        evaluation_id,
        selected_choice=chosen,
        selection_reason=reason,
    )
    return json.dumps({"ok": True, "evaluation": updated}, ensure_ascii=False, indent=2)


def handle_cortex_quality(days: int = 30) -> str:
    """Summarise recommendation quality, overrides, and linked outcome results."""
    from db import cortex_evaluation_summary

    summary = cortex_evaluation_summary(days=days)
    return json.dumps({"ok": True, "summary": summary}, ensure_ascii=False, indent=2)


TOOLS = [
    (handle_cortex_check, "nexo_cortex_check", "Cognitive pre-action check. Validates reasoning and determines if you can act, should propose, or need to ask first. Call before significant actions."),
    (handle_cortex_decide, "nexo_cortex_decide", "Evaluate 2+ alternatives for a high-impact task and persist the recommendation on top of the existing Cortex."),
    (handle_cortex_review, "nexo_cortex_review", "Review persisted Cortex alternative evaluations by ID, task, or session."),
    (handle_cortex_override, "nexo_cortex_override", "Override a stored Cortex recommendation while preserving the recommendation trail."),
    (handle_cortex_quality, "nexo_cortex_quality", "Summarise recommendation accept rate, override rate, and linked outcome success for Cortex evaluations."),
    (handle_cortex_stats, "nexo_cortex_stats", "View Cortex activation statistics — modes, task types, inhibition rate."),
]
