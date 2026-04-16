#!/usr/bin/env python3
from __future__ import annotations
"""
Deep Sleep v2 -- Phase 3: Synthesize extractions into actionable findings.

One Claude call that reads all per-session extractions and produces a
unified synthesis with cross-session patterns, morning agenda, context
packets, and deduplicated actions.

Environment variables:
  NEXO_HOME  -- root of the NEXO installation (default: ~/.nexo)
"""
import json
import os
import subprocess
import sys
import hashlib
from datetime import datetime
from pathlib import Path


try:
    from client_preferences import resolve_user_model as _resolve_user_model
    _USER_MODEL = _resolve_user_model()
except Exception:
    _USER_MODEL = ""

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parents[2])))
DEEP_SLEEP_DIR = NEXO_HOME / "operations" / "deep-sleep"
PROMPT_FILE = Path(__file__).parent / "synthesize-prompt.md"

if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

from agent_runner import AutomationBackendUnavailableError, run_automation_prompt
from constants import AUTOMATION_SUBPROCESS_TIMEOUT

CLAUDE_TIMEOUT = AUTOMATION_SUBPROCESS_TIMEOUT
ACTION_VERBS = {"add", "implement", "create", "write", "build", "enforce", "automate", "validate", "guard", "fix", "review"}


def extract_json_from_response(text: str) -> dict | None:
    """Parse JSON from Claude's response, handling markdown fences."""
    text = text.strip()

    if text.startswith("```"):
        lines = text.split("\n")
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[1:end]).strip()

    brace_start = text.find("{")
    if brace_start < 0:
        return None

    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[brace_start:i + 1])
                except json.JSONDecodeError:
                    break
    return None


def collect_skill_runtime_candidates(target_date: str) -> tuple[Path, dict]:
    """Collect mature skill candidates from DB usage so Deep Sleep can evolve them."""
    output_file = DEEP_SLEEP_DIR / f"{target_date}-skill-runtime-candidates.json"
    payload = {
        "scriptable": [],
        "improvements": [],
    }
    try:
        from db import (
            collect_scriptable_skill_candidates,
            collect_skill_improvement_candidates,
            init_db,
        )

        init_db()
        payload["scriptable"] = collect_scriptable_skill_candidates()
        payload["improvements"] = collect_skill_improvement_candidates()
    except Exception as e:
        payload["error"] = str(e)

    with open(output_file, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return output_file, payload


def _normalize_action_text(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _looks_concrete_action(text: str) -> bool:
    words = {word.strip(".,:;()[]{}").lower() for word in str(text or "").split()}
    return bool(words & ACTION_VERBS)


def _pattern_followup_from_fix(pattern: dict) -> dict | None:
    severity = str(pattern.get("severity", "") or "").lower()
    sessions = pattern.get("sessions", []) or []
    if severity not in {"medium", "high"} and len(sessions) < 2:
        return None

    proposed_fix = pattern.get("proposed_fix") or {}
    pattern_text = str(pattern.get("pattern", "") or "").strip()
    title = str(proposed_fix.get("title", "") or "").strip()
    description = str(proposed_fix.get("description", "") or "").strip()
    deliverable = str(proposed_fix.get("deliverable", "") or proposed_fix.get("artifact", "") or "").strip()

    if title and description:
        if _looks_concrete_action(description):
            followup_description = description
        else:
            followup_description = f"{title}: {description}"
    elif description:
        followup_description = description
    elif title:
        followup_description = title
    elif pattern_text:
        followup_description = (
            f"Implement a concrete guardrail for recurring issue: {pattern_text}. "
            "Deliverable should be a script, hook, checklist, or automated validation that prevents the same failure from repeating."
        )
    else:
        return None

    if deliverable and deliverable.lower() not in followup_description.lower():
        followup_description = f"{followup_description} Deliverable: {deliverable}."
    if not _looks_concrete_action(followup_description):
        followup_description = f"Implement this fix: {followup_description}"

    return {
        "action_type": "followup_create",
        "action_class": "auto_apply" if severity == "high" else "draft_for_morning",
        "confidence": round(max(float(proposed_fix.get("confidence", 0.0) or 0.0), 0.86 if severity == "high" else 0.78), 2),
        "impact": "high" if severity == "high" else "medium",
        "reversibility": "reversible",
        "evidence": pattern.get("evidence", []) or [],
        # Content fingerprint, not security-sensitive.
        "dedupe_key": "engineering-fix:" + hashlib.md5(
            _normalize_action_text(followup_description).encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()[:16],
        "content": {
            "title": title or f"Engineering fix for: {pattern_text[:90]}",
            "description": followup_description,
            "date": "",
            "reasoning": f"Deep Sleep engineering followup from recurring pattern: {pattern_text}",
        },
    }


def backfill_engineering_actions(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return payload
    actions = payload.get("actions")
    if not isinstance(actions, list):
        actions = []
        payload["actions"] = actions

    existing_keys = {str(action.get("dedupe_key", "") or "") for action in actions}
    existing_descriptions = {
        _normalize_action_text(action.get("content", {}).get("description", ""))
        for action in actions
        if isinstance(action, dict)
    }

    for pattern in payload.get("cross_session_patterns", []) or []:
        action = _pattern_followup_from_fix(pattern)
        if not action:
            continue
        description = _normalize_action_text(action["content"]["description"])
        if action["dedupe_key"] in existing_keys or description in existing_descriptions:
            continue
        actions.append(action)
        existing_keys.add(action["dedupe_key"])
        existing_descriptions.add(description)
    return payload


def main():
    target_date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")

    extractions_file = DEEP_SLEEP_DIR / f"{target_date}-extractions.json"
    context_file = DEEP_SLEEP_DIR / f"{target_date}-context.txt"
    long_horizon_file = DEEP_SLEEP_DIR / target_date / "long-horizon-context.json"

    if not extractions_file.exists():
        print(f"[synthesize] No extractions file for {target_date}. Run extract.py first.")
        sys.exit(1)

    # Check if there are any findings worth synthesizing
    with open(extractions_file) as f:
        extractions = json.load(f)

    total_findings = extractions.get("total_findings", 0)
    runtime_candidates_file, runtime_candidates = collect_skill_runtime_candidates(target_date)
    runtime_candidate_count = len(runtime_candidates.get("scriptable", [])) + len(runtime_candidates.get("improvements", []))

    if total_findings == 0 and runtime_candidate_count == 0:
        print(f"[synthesize] No findings to synthesize for {target_date}.")
        # Write minimal synthesis
        output = {
            "date": target_date,
            "sessions_analyzed": extractions.get("sessions_analyzed", 0),
            "cross_session_patterns": [],
            "morning_agenda": [],
            "context_packets": [],
            "skills": [],
            "skill_evolution_candidates": [],
            "actions": [],
            "summary": f"No significant findings for {target_date}."
        }
        output_file = DEEP_SLEEP_DIR / f"{target_date}-synthesis.json"
        with open(output_file, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"[synthesize] Output: {output_file}")
        return

    # Build prompt
    prompt_template = PROMPT_FILE.read_text()
    prompt = prompt_template.replace("{{EXTRACTIONS_FILE}}", str(extractions_file))
    prompt = prompt.replace("{{CONTEXT_FILE}}", str(context_file))
    prompt = prompt.replace("{{SKILL_RUNTIME_FILE}}", str(runtime_candidates_file))
    prompt = prompt.replace("{{LONG_HORIZON_FILE}}", str(long_horizon_file))

    print(f"[synthesize] Phase 3: Synthesizing {total_findings} findings from {target_date}")
    print(f"[synthesize] Skill runtime candidates: {runtime_candidate_count}")
    print("[synthesize] Automation backend: schedule-configured")

    try:
        result = run_automation_prompt(
            prompt,
            model=_USER_MODEL,
            timeout=CLAUDE_TIMEOUT,
            output_format="text",
            allowed_tools="Read,Grep,Bash",
        )

        if result.returncode != 0:
            print(f"[synthesize] Automation backend error (exit {result.returncode}): {result.stderr[:300]}", file=sys.stderr)
            sys.exit(1)

        # Filter hook contamination
        output_text = "\n".join(
            l for l in result.stdout.strip().splitlines()
            if not l.strip().startswith("Post-mortem")
        )
        parsed = extract_json_from_response(output_text)

        # Fallback: Opus might have written the file directly via Write tool
        if not parsed:
            for candidate in [
                DEEP_SLEEP_DIR / f"{target_date}-analysis.json",
                DEEP_SLEEP_DIR / f"{target_date}-synthesis.json",
                DEEP_SLEEP_DIR / target_date / "synthesis.json",
            ]:
                if candidate.exists() and candidate.stat().st_size > 100:
                    try:
                        parsed = json.load(open(candidate))
                        print(f"[synthesize] Opus wrote file directly: {candidate}")
                        break
                    except Exception:
                        continue

        if not parsed:
            debug_file = DEEP_SLEEP_DIR / f"debug-synthesize-{target_date}.txt"
            debug_file.write_text(result.stdout[:10000])
            print(f"[synthesize] Failed to parse JSON. Raw output saved to {debug_file}", file=sys.stderr)
            sys.exit(1)

        parsed = backfill_engineering_actions(parsed)

        # Write synthesis output
        output_file = DEEP_SLEEP_DIR / f"{target_date}-synthesis.json"
        with open(output_file, "w") as f:
            json.dump(parsed, f, indent=2, ensure_ascii=False)

        n_actions = len(parsed.get("actions", []))
        n_patterns = len(parsed.get("cross_session_patterns", []))
        n_agenda = len(parsed.get("morning_agenda", []))
        n_packets = len(parsed.get("context_packets", []))

        print(f"[synthesize] Done.")
        print(f"  Actions: {n_actions}")
        print(f"  Cross-session patterns: {n_patterns}")
        print(f"  Morning agenda items: {n_agenda}")
        print(f"  Context packets: {n_packets}")
        print(f"[synthesize] Output: {output_file}")

    except AutomationBackendUnavailableError as exc:
        print(f"[synthesize] Automation backend unavailable: {exc}", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(f"[synthesize] Automation backend timeout ({CLAUDE_TIMEOUT}s)", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("[synthesize] Automation backend binary not found.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
