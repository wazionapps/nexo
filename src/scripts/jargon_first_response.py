#!/usr/bin/env python3
"""First-response jargon checker (Communication Guardrail enforcement).

Implements followup NF-DS-32D6E12E: a linter that scans the FIRST visible
message the agent emits in a turn for NEXO internal jargon that violates
the Communication Guardrail in CLAUDE.md.

Token list (case-insensitive substring match), taken verbatim from the
followup spec:

    Learning #, protocol debt, cortex eval, runtime-core, guard_check,
    heartbeat, pre-emptive guard, enforcer, task_open, task_close, NF-,
    Subscription inactive, WSL, scorer, match, cortex

Use as:

* Library:
    from src.scripts.jargon_first_response import scan_text, register_debt_if_violations

* CLI (manual review of a recent reply):
    echo "Learning #42 applied via task_open" \
        | python3 src/scripts/jargon_first_response.py --stdin --session-id NEXO-SID

* Future hook integration (PostToolUse / Stop) loads the transcript with
  `transcript_utils.load_transcript` and pipes the latest assistant
  message through `register_debt_if_violations`. Hook wiring is left as
  a separate change so the linter ships independently testable.

Exit codes (CLI):
    0 — no violations detected (or only allowed because user requested detail)
    1 — at least one prohibited token found
    2 — invocation error (missing input)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Iterable, List, Sequence

# Order matters only for stable output. Longer / multi-word phrases first
# so the snippet boundary is clearer in the matches view.
PROHIBITED_TOKENS: Sequence[str] = (
    "Learning #",
    "protocol debt",
    "cortex eval",
    "runtime-core",
    "guard_check",
    "pre-emptive guard",
    "enforcer",
    "task_open",
    "task_close",
    "heartbeat",
    "NF-",
    "Subscription inactive",
    "WSL",
    "scorer",
    "match",
    "cortex",
)

# Heuristic signals from the *user* message that mean "operator asked for
# technical detail, jargon is allowed in the reply". The first answer
# only counts as a guardrail violation when none of these are present.
_USER_DETAIL_PATTERNS = (
    re.compile(r"\bdebugg(?:ing|er)?\b", re.IGNORECASE),
    re.compile(r"\bstack\s*trace\b", re.IGNORECASE),
    re.compile(r"\b(?:protocol\s+debt|guard_check|task_open|task_close|enforcer|heartbeat)\b", re.IGNORECASE),
    re.compile(r"\b(?:nf-[a-z0-9-]+|learning\s+#?\d+)\b", re.IGNORECASE),
    re.compile(r"\b(internal|runtime|architecture|cortex|drive)\b", re.IGNORECASE),
    re.compile(r"\b(?:explica|explain|deep[\s-]*dive|d[ée]tailled|d[ée]tail)\b", re.IGNORECASE),
)


def _first_visible_paragraph(text: str) -> str:
    """Return the first ~600 chars of operator-visible text.

    Strips leading whitespace, blank lines, and ignores fenced code blocks
    at the very top (release notes / code-only first messages are not
    "first response prose" for the guardrail purpose).
    """
    if not text:
        return ""
    cleaned = text.lstrip()
    if cleaned.startswith("```"):
        end = cleaned.find("```", 3)
        if end != -1:
            cleaned = cleaned[end + 3 :].lstrip()
    # First two paragraphs is usually plenty for the linter; longer
    # replies have time to recover into plain language.
    paragraphs = re.split(r"\n\s*\n", cleaned)
    head = "\n\n".join(paragraphs[:2])
    return head[:600]


def user_requested_detail(user_message: str) -> bool:
    """True if the operator explicitly asked for technical / NEXO-internal detail."""
    if not user_message:
        return False
    return any(pat.search(user_message) for pat in _USER_DETAIL_PATTERNS)


def scan_text(text: str, *, tokens: Iterable[str] = PROHIBITED_TOKENS) -> List[dict]:
    """Return a list of `{token, index, snippet}` for each prohibited match."""
    if not text:
        return []
    target = _first_visible_paragraph(text)
    if not target:
        return []
    found: List[dict] = []
    for token in tokens:
        pattern = re.escape(token)
        if token.isalpha() and len(token) <= 6:
            pattern = rf"\b{pattern}\b"
        for match in re.finditer(pattern, target, re.IGNORECASE):
            idx = match.start()
            snippet_start = max(0, idx - 25)
            snippet_end = min(len(target), idx + len(token) + 25)
            found.append({
                "token": token,
                "index": idx,
                "snippet": target[snippet_start:snippet_end].replace("\n", " "),
            })
    found.sort(key=lambda row: row["index"])
    return found


def register_debt_if_violations(
    session_id: str,
    assistant_text: str,
    *,
    user_message: str = "",
    task_id: str = "",
    evidence_prefix: str = "",
) -> dict:
    """Register a protocol_debt of type `communication_guardrail` when the
    first response uses prohibited NEXO jargon and the user did NOT ask
    for technical detail.

    Returns a result dict with keys:
        - `violations`: list of matches from `scan_text`
        - `skipped`: bool — true if checker did not register (no SID, user
            requested detail, or no violations)
        - `debt_id`: int|None — id of the created debt, when applicable
        - `reason`: short human-readable status
    """
    matches = scan_text(assistant_text)
    if not matches:
        return {"violations": [], "skipped": True, "debt_id": None, "reason": "no_violations"}
    if user_requested_detail(user_message):
        return {"violations": matches, "skipped": True, "debt_id": None, "reason": "user_requested_detail"}
    sid = (session_id or "").strip()
    if not sid:
        return {"violations": matches, "skipped": True, "debt_id": None, "reason": "missing_session_id"}
    try:
        # Local import keeps this module importable in isolated test
        # contexts (no NEXO DB) when callers only need `scan_text`.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from db import create_protocol_debt  # type: ignore
    except Exception as err:  # pragma: no cover — defensive in tests
        return {"violations": matches, "skipped": True, "debt_id": None, "reason": f"db_unavailable:{err}"}
    evidence_lines = [evidence_prefix] if evidence_prefix else []
    for match in matches[:5]:
        evidence_lines.append(f"- '{match['token']}' @ {match['index']}: ...{match['snippet']}...")
    evidence = "\n".join(line for line in evidence_lines if line)[:4000]
    debt = create_protocol_debt(
        sid,
        "communication_guardrail",
        severity="warn",
        task_id=task_id,
        evidence=evidence,
    )
    return {
        "violations": matches,
        "skipped": False,
        "debt_id": debt.get("id") if isinstance(debt, dict) else None,
        "reason": "debt_registered",
    }


def _read_text_from_args(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    if args.stdin:
        return sys.stdin.read()
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    return ""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="First-response jargon checker (NEXO Communication Guardrail).")
    parser.add_argument("--text", help="Assistant text to scan (inline).")
    parser.add_argument("--stdin", action="store_true", help="Read assistant text from stdin.")
    parser.add_argument("--file", help="Read assistant text from file.")
    parser.add_argument("--user-message", default="", help="Optional user message preceding the reply.")
    parser.add_argument("--session-id", default=os.environ.get("NEXO_SID", ""), help="Session ID for protocol_debt registration.")
    parser.add_argument("--task-id", default="", help="Optional task ID to attach to the debt.")
    parser.add_argument("--register-debt", action="store_true", help="Register a protocol_debt when violations are found.")
    parser.add_argument("--evidence-prefix", default="", help="Optional prefix for the evidence string.")
    args = parser.parse_args(argv)

    text = _read_text_from_args(args)
    if not text:
        parser.error("provide --text, --stdin, or --file")
        return 2

    if args.register_debt:
        result = register_debt_if_violations(
            args.session_id,
            text,
            user_message=args.user_message,
            task_id=args.task_id,
            evidence_prefix=args.evidence_prefix,
        )
        violations = result["violations"]
    else:
        violations = scan_text(text)
        result = {
            "violations": violations,
            "skipped": True,
            "debt_id": None,
            "reason": "dry_run",
        }

    if not violations:
        print("[jargon-checker] OK — no prohibited tokens in first response.")
        return 0
    print(f"[jargon-checker] {len(violations)} match(es) detected:")
    for match in violations:
        print(f"  - {match['token']!r} @ {match['index']}  …{match['snippet']}…")
    if result.get("debt_id"):
        print(f"[jargon-checker] protocol_debt registered id={result['debt_id']} reason=communication_guardrail")
    elif args.register_debt:
        print(f"[jargon-checker] no debt registered: {result.get('reason')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
