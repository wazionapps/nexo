#!/usr/bin/env python3
"""
NEXO Learning Validator — Cross-check findings against existing learnings.

The wrapper collects the finding + current learnings from SQLite, then asks the
configured automation backend whether the finding is already known, related, or
genuinely new. If the backend is unavailable, it falls back to mechanical
similarity matching.

Usage as CLI:
    python3 nexo-learning-validator.py "finding text to validate"
    python3 nexo-learning-validator.py --category project "finding text"

Usage as library:
    from nexo_learning_validator import validate_finding
    result = validate_finding("CRITICAL: message_id column is NULL")
    if result["known"]:
        print(f"Already known: {result['matching_learnings']}")

Exit codes:
    0 = Finding is NEW (not known)
    1 = Finding is KNOWN (matches existing learning)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parents[1])))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

from agent_runner import AutomationBackendUnavailableError, run_automation_prompt

try:
    from client_preferences import resolve_user_model as _resolve_user_model
    _USER_MODEL = _resolve_user_model()
except Exception:
    _USER_MODEL = ""



NEXO_DB = NEXO_HOME / "data" / "nexo.db"
JSON_ONLY_SYSTEM_PROMPT = (
    "Return exactly one valid JSON object. No markdown fences. No prose outside JSON."
)


def get_all_learnings(category: str | None = None) -> list[dict]:
    """Fetch all learnings from nexo.db."""
    conn = sqlite3.connect(str(NEXO_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    if category:
        rows = conn.execute(
            "SELECT id, category, title, content FROM learnings WHERE category = ?",
            (category,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, category, title, content FROM learnings"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        lines = text.splitlines()
        end = len(lines)
        for idx in range(len(lines) - 1, 0, -1):
            if lines[idx].strip() == "```":
                end = idx
                break
        text = "\n".join(lines[1:end]).strip()
    brace_start = text.find("{")
    if brace_start < 0:
        return None
    depth = 0
    for idx in range(brace_start, len(text)):
        if text[idx] == "{":
            depth += 1
        elif text[idx] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[brace_start:idx + 1])
                except json.JSONDecodeError:
                    return None
    return None


def validate_finding(finding: str, category: str | None = None) -> dict:
    """
    Validate a finding against existing learnings.

    Returns:
        {
            "known": bool,
            "confidence": float (0-1),
            "matching_learnings": [{"id": int, "title": str, "similarity": float}],
            "recommendation": str
        }
    """
    learnings = get_all_learnings(category)

    if not learnings:
        return {
            "known": False,
            "confidence": 0,
            "matching_learnings": [],
            "recommendation": "No learnings in DB — finding is new by default",
        }

    learnings_ref = [
        {
            "id": l["id"],
            "cat": l["category"],
            "title": l["title"],
            "content": (l["content"] or "")[:300],
        }
        for l in learnings
    ]

    prompt = f"""You are a finding deduplication engine. Compare a new finding against existing learnings and determine if it's already known.

NEW FINDING:
{finding}

EXISTING LEARNINGS ({len(learnings_ref)} total):
{json.dumps(learnings_ref, indent=1, ensure_ascii=False)}

Respond with ONLY valid JSON:
{{
  "known": true/false,
  "confidence": 0.0-1.0,
  "matching_learnings": [
    {{"id": <learning_id>, "title": "<title>", "similarity": 0.0-1.0}}
  ],
  "recommendation": "<one line: KNOWN/LIKELY KNOWN/POSSIBLY RELATED/NEW>"
}}

Rules:
- confidence >= 0.7 and same root cause = known: true
- confidence 0.55-0.7 and related topic = known: true, say LIKELY KNOWN
- confidence < 0.55 = known: false
- Max 5 matching_learnings, sorted by similarity descending
- If the finding describes the SAME bug/issue/pattern as a learning, it's known even if worded differently
- Be strict: different symptoms of different bugs are NOT the same even if they mention the same file"""

    try:
        result = run_automation_prompt(
            prompt,
            model=_USER_MODEL,
            timeout=60,
            output_format="text",
            append_system_prompt=JSON_ONLY_SYSTEM_PROMPT,
        )
        parsed = _extract_json(result.stdout)
        if result.returncode == 0 and parsed:
            return parsed
    except AutomationBackendUnavailableError:
        pass
    except Exception:
        pass

    return _mechanical_validate(finding, learnings)


def _mechanical_validate(finding: str, learnings: list[dict]) -> dict:
    """Fallback validation using SequenceMatcher when backend is unavailable."""
    from difflib import SequenceMatcher

    threshold = 0.45
    finding_kw = _extract_keywords(finding)
    matches = []

    for learning in learnings:
        title_sim = SequenceMatcher(None, finding.lower(), learning["title"].lower()).ratio()
        content_sim = SequenceMatcher(None, finding.lower(), (learning["content"] or "").lower()).ratio()

        learning_text = f"{learning['title']} {learning['content'] or ''}"
        learning_kw = _extract_keywords(learning_text)
        kw_overlap = len(finding_kw & learning_kw) / len(finding_kw) if finding_kw and learning_kw else 0

        combined = max(title_sim, content_sim) * 0.6 + kw_overlap * 0.4

        if combined >= threshold:
            matches.append({
                "id": learning["id"],
                "category": learning["category"],
                "title": learning["title"],
                "similarity": round(combined, 3),
            })

    matches.sort(key=lambda x: x["similarity"], reverse=True)
    top = matches[:5]

    if not top:
        return {"known": False, "confidence": 0, "matching_learnings": [], "recommendation": "NEW finding"}

    best = top[0]["similarity"]
    if best >= 0.7:
        return {"known": True, "confidence": best, "matching_learnings": top,
                "recommendation": f"KNOWN issue (learning #{top[0]['id']})"}
    if best >= 0.55:
        return {"known": True, "confidence": best, "matching_learnings": top,
                "recommendation": f"LIKELY KNOWN (learning #{top[0]['id']})"}
    return {"known": False, "confidence": best, "matching_learnings": top,
            "recommendation": "POSSIBLY RELATED but different enough to report"}


def _extract_keywords(text: str) -> set:
    """Extract meaningful keywords from text."""
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "must", "shall", "can", "need", "dare",
        "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
        "and", "but", "or", "nor", "not", "so", "yet", "both", "either",
        "error", "critical", "warning", "bug", "issue", "problem", "fix",
        "el", "la", "los", "las", "un", "una", "de", "en", "que", "por",
    }
    words = set()
    for word in text.lower().split():
        clean = "".join(c for c in word if c.isalnum() or c == "_")
        if clean and len(clean) > 2 and clean not in stop_words:
            words.add(clean)
    return words


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Validate findings against existing NEXO learnings")
    parser.add_argument("finding", help="The finding text to validate")
    parser.add_argument("--category", "-c", help="Filter learnings by category")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    result = validate_finding(args.finding, args.category)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        status = "KNOWN" if result["known"] else "NEW"
        print(f"Status: {status} (confidence: {result['confidence']:.0%})")
        print(f"Recommendation: {result['recommendation']}")
        if result["matching_learnings"]:
            print("Related learnings:")
            for match in result["matching_learnings"]:
                cat = match.get("category", "?")
                print(f"  #{match['id']} [{cat}] {match['title']} ({match['similarity']:.0%})")

    sys.exit(1 if result["known"] else 0)


if __name__ == "__main__":
    main()
