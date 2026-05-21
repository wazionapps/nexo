"""Minimal-delta pre-mutation guardrail (NF-DS-C3E64B2B).

Purpose
-------
Block scope creep when the operator describes a *punctual* UI change
("add this text", "change the size", "adjust the color") but the agent
proposes a diff that touches many unrelated lines.

How it plugs in
---------------
Wire ``check`` from ``hook_guardrails.process_pre_tool_event`` BEFORE the
``Edit`` / ``Write`` call reaches the model. The hook should:

  1. Call ``classify_request(prompt_text)`` to detect punctual UI verbs.
  2. If punctual, capture the *target file* + *proposed new_string* from the
     pending tool payload.
  3. Read ``read_file_history(path)`` to keep the prior context available
     (it returns the last 5 commits + current text so the agent can reason
     in bullet-by-bullet form when replying).
  4. Call ``check_diff(prompt_text, old_text, new_text)`` and act on the
     returned ``GuardDecision``:

       * ``decision == "allow"``  → let the tool through, no annotation.
       * ``decision == "warn"``   → let it through but tell the agent in the
         response payload how many extra lines it touched.
       * ``decision == "block"``  → deny the tool with the included reason.
         The agent must reply with a bullet-by-bullet diff against the prior
         state and request explicit confirmation before retrying.

Why headless-friendly
---------------------
This module is *pure*. No filesystem, no subprocess, no MCP call. The hook
layer wires it in. That keeps the gate testable in isolation and means a
wrong tuning here cannot brick the pre-tool pipeline.

Origin: Deep Sleep followup NF-DS-C3E64B2B (scope creep in punctual visual
changes).
"""
from __future__ import annotations

import difflib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Verbs that signal a *punctual* UI change. Matched case-insensitive against
# the full request text. Spanish terms are intentionally included because the
# operator often reports UI issues in Spanish; output remains English.
PUNCTUAL_VERBS: tuple[str, ...] = (
    "añade",
    "anade",
    "agrega",
    "añadir",
    "agrega el texto",
    "cambia el texto",
    "cambia el color",
    "cambia el tamaño",
    "cambia el tamano",
    "ajusta el tamaño",
    "ajusta el tamano",
    "ajusta el padding",
    "ajusta el margen",
    "ajusta el color",
    "renombra",
    "rename",
    "tweak",
    "change the text",
    "change the color",
    "adjust the size",
    "fix the label",
    "fix the wording",
    "add the text",
    "swap the icon",
)

# File extensions considered UI surfaces. Anything else short-circuits to
# "not a UI mutation" so we never block server code on accident.
UI_EXTENSIONS: frozenset[str] = frozenset({
    ".tsx", ".jsx", ".ts", ".js", ".vue", ".svelte", ".astro",
    ".html", ".css", ".scss", ".sass", ".less",
    ".liquid", ".njk", ".hbs",
})

# Default threshold for "unrelated lines". Tuned conservatively — a real UI
# tweak usually changes 1-3 contiguous lines. Anything > THRESHOLD blocks.
DEFAULT_THRESHOLD = 8


@dataclass(frozen=True)
class GuardDecision:
    decision: str           # "allow" | "warn" | "block"
    matched_verb: str | None
    changed_lines: int
    threshold: int
    reason: str

    def to_payload(self) -> dict:
        return {
            "guard": "minimal-delta",
            "decision": self.decision,
            "matched_verb": self.matched_verb,
            "changed_lines": self.changed_lines,
            "threshold": self.threshold,
            "reason": self.reason,
        }


def classify_request(prompt_text: str) -> str | None:
    """Return the matched punctual verb (lowercase) or ``None``."""
    if not prompt_text:
        return None
    haystack = prompt_text.lower()
    for verb in PUNCTUAL_VERBS:
        if verb in haystack:
            return verb
    return None


def is_ui_path(path: str | Path) -> bool:
    if not path:
        return False
    suffix = Path(str(path)).suffix.lower()
    return suffix in UI_EXTENSIONS


def count_changed_lines(old_text: str, new_text: str) -> int:
    """Count *non-context* diff lines (+/− only)."""
    if old_text == new_text:
        return 0
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff = difflib.unified_diff(old_lines, new_lines, n=0, lineterm="")
    count = 0
    for line in diff:
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            count += 1
    return count


def check_diff(
    prompt_text: str,
    old_text: str,
    new_text: str,
    target_path: str | Path = "",
    *,
    threshold: int = DEFAULT_THRESHOLD,
) -> GuardDecision:
    verb = classify_request(prompt_text)
    if verb is None:
        return GuardDecision("allow", None, 0, threshold, "Not a punctual request — guard not applied.")
    if target_path and not is_ui_path(target_path):
        return GuardDecision(
            "allow", verb, 0, threshold,
            f"Target {target_path} is not a UI surface — guard not applied.",
        )
    changed = count_changed_lines(old_text, new_text)
    if changed <= 2:
        return GuardDecision(
            "allow", verb, changed, threshold,
            "Diff stays within 2 lines — within the punctual envelope.",
        )
    if changed <= threshold:
        return GuardDecision(
            "warn", verb, changed, threshold,
            (
                f"Punctual request ('{verb}') is changing {changed} lines. "
                "Inside the soft envelope but please justify each line in the reply."
            ),
        )
    return GuardDecision(
        "block", verb, changed, threshold,
        (
            f"Punctual request ('{verb}') is changing {changed} lines (>{threshold}). "
            "Read git log/blame for the file, present the prior state, list each proposed "
            "change as a bullet, and ask the operator before applying. Override only with "
            "explicit confirmation."
        ),
    )


def read_file_history(path: str | Path, *, max_commits: int = 5) -> dict:
    """Return ``{"current": str, "log": [str], "blame": [str] | None}``.

    Best-effort: missing git, missing file, or non-zero exit codes are
    swallowed so the guardrail itself never errors out. The dictionary is
    purely informational — the agent must surface it in its reply when the
    guard fires.
    """
    path_obj = Path(path)
    try:
        current = path_obj.read_text(encoding="utf-8", errors="replace")
    except OSError:
        current = ""

    log_lines: list[str] = []
    try:
        completed = subprocess.run(
            ["git", "log", f"-{max_commits}", "--pretty=format:%h %ad %s", "--date=short", "--", str(path_obj)],
            cwd=path_obj.parent if path_obj.parent.exists() else Path.cwd(),
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            log_lines = completed.stdout.splitlines()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        log_lines = []

    blame_lines: list[str] | None = None
    if current:
        try:
            completed = subprocess.run(
                ["git", "blame", "--line-porcelain", "-L", "1,40", str(path_obj)],
                cwd=path_obj.parent if path_obj.parent.exists() else Path.cwd(),
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            if completed.returncode == 0:
                blame_lines = completed.stdout.splitlines()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            blame_lines = None

    return {"current": current, "log": log_lines, "blame": blame_lines}


def evaluate(
    prompt_text: str,
    target_path: str | Path,
    old_text: str,
    new_text: str,
    *,
    threshold: int = DEFAULT_THRESHOLD,
) -> dict:
    """Convenience wrapper used by hook_guardrails.

    Returns a payload safe to JSON-serialize and embed in the deny reason
    or the pass-through note.
    """
    decision = check_diff(prompt_text, old_text, new_text, target_path, threshold=threshold)
    payload = decision.to_payload()
    payload["target_path"] = str(target_path)
    if decision.decision == "block":
        history = read_file_history(target_path)
        payload["history_log"] = history["log"]
        payload["current_excerpt"] = (history["current"][:400] + "…") if history["current"] else ""
    return payload


__all__ = [
    "DEFAULT_THRESHOLD",
    "GuardDecision",
    "PUNCTUAL_VERBS",
    "UI_EXTENSIONS",
    "check_diff",
    "classify_request",
    "count_changed_lines",
    "evaluate",
    "is_ui_path",
    "read_file_history",
]
