"""R23e — git push --force against main/master/production/release-*.

Pure decision module. Part of Fase D2 (hard bloqueante).
Fired from `HeadlessEnforcer._check_r23e` when Bash runs and the command
matches the protected push pattern.
"""
from __future__ import annotations

import re


INJECTION_PROMPT_TEMPLATE = (
    "R23e force push to {branch}: the command '{cmd}' would rewrite a "
    "protected branch. Force-push to main/master/production/release-* is "
    "never automatic — stop and confirm with the operator before proceeding. "
    "If the intent is to recover a broken state, prefer `git revert` + a "
    "fresh commit over a history rewrite."
)

# Matches `git push [options] [remote] BRANCH` where --force or -f appears
# anywhere in the option stream. Branch may come with a remote prefix
# (`origin main`) or a refspec (`HEAD:main`, `main:main`).
_FORCE_PUSH_RE = re.compile(
    r"\bgit\s+push\b(?P<rest>[^\n;|&]*)",
    re.IGNORECASE,
)
# --force WITHOUT --force-with-lease is the dangerous form. Lease pushes
# check that the remote hasn't moved since the last fetch and are considered
# best practice; R23e should only hard-block the unconditional --force.
_FORCE_FLAG_RE = re.compile(r"(?<!\w)(-f\b|--force\b(?!-with-lease))")
_PROTECTED_BRANCH_RE = re.compile(
    r"\b(main|master|production|release-[\w.\-]+)\b",
    re.IGNORECASE,
)


def _has_explicit_branch_target(rest: str) -> bool:
    """True when the argument stream carries positional(s) identifying
    a remote and/or branch (operator named the target explicitly)."""
    positionals = [tok for tok in rest.split() if not tok.startswith("-")]
    # A refspec with `:` (HEAD:branch) — target is explicit even solo.
    if any(":" in tok for tok in positionals):
        return True
    # Two positionals ⇒ remote + branch (`origin feature/x`).
    return len(positionals) >= 2


def detect_force_push_protected(cmd: str) -> tuple[bool, str, str]:
    """Return (blocked, branch, normalized_cmd).

    `blocked=True` in two cases:
      1. The force-push explicitly names a protected branch
         (main/master/production/release-*).
      2. The command is a bare `git push -f/--force` with no positional
         identifying a branch — the current branch might itself be
         protected and we cannot tell from the bash string.
    An operator-named non-protected branch (e.g. `git push -f origin
    feature/new-thing`) is allowed through.
    """
    if not cmd or not isinstance(cmd, str):
        return False, "", ""
    for match in _FORCE_PUSH_RE.finditer(cmd):
        rest = match.group("rest") or ""
        if not _FORCE_FLAG_RE.search(rest):
            continue
        branch_match = _PROTECTED_BRANCH_RE.search(rest)
        if branch_match:
            return True, branch_match.group(1), match.group(0).strip()
        if _has_explicit_branch_target(rest):
            return False, "", ""
        return True, "current-branch", match.group(0).strip()
    return False, "", ""


def should_inject_r23e(tool_name: str, tool_input) -> tuple[bool, str]:
    """Decide whether R23e should fire.

    Returns (should_inject, prompt). Fail-closed: unknown input shape →
    no injection.
    """
    if tool_name != "Bash":
        return False, ""
    if not isinstance(tool_input, dict):
        return False, ""
    cmd = tool_input.get("command")
    if not isinstance(cmd, str):
        return False, ""
    blocked, branch, normalized = detect_force_push_protected(cmd)
    if not blocked:
        return False, ""
    prompt = INJECTION_PROMPT_TEMPLATE.format(branch=branch, cmd=normalized)
    return True, prompt
