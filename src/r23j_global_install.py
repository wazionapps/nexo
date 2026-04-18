"""R23j — global package install without explicit operator request.

Pure decision module. Part of Fase D2 (shadow — rollout gradual).

Fires when Bash runs npm/pip/brew install with -g / --user / --global
and the recent user message does NOT contain explicit permission
phrasing. Global installs pollute the operator machine and usually
should be scoped (venv, nvm, project-local).
"""
from __future__ import annotations

import re


INJECTION_PROMPT_TEMPLATE = (
    "R23j global install without explicit request: '{cmd}' installs "
    "'{pkg}' globally. Prefer a project-scoped install (venv, nvm, "
    "`--save`, local brew cask) unless the operator asked for a global "
    "tool. If the operator explicitly asked, retry after saying "
    "`yes install globally` or equivalent."
)


_NPM_GLOBAL_RE = re.compile(
    r"\bnpm\s+install\b[^\n;|&]*\s(?:-g|--global)(?:\s|$|[^\w])[^\n;|&]*",
    re.IGNORECASE,
)
_PIP_USER_RE = re.compile(
    r"\bpip3?\s+install\b[^\n;|&]*\s(?:--user|--global)(?:\s|$|[^\w])[^\n;|&]*",
    re.IGNORECASE,
)
_PIPX_GLOBAL_RE = re.compile(
    r"\bpipx\s+install\b[^\n;|&]*\s(?:--global|--user)(?:\s|$|[^\w])[^\n;|&]*",
    re.IGNORECASE,
)
_BREW_INSTALL_RE = re.compile(
    r"\bbrew\s+install\b[^\n;|&]+",
    re.IGNORECASE,
)

_PKG_NAME_RE = re.compile(r"(?<!--)(?<!-)(?<!\w)([\w@][\w.@/-]{1,})(?=\s|$|[^\w.@/-])")

_PERMIT_MARKERS = (
    "instala global",
    "install global",
    "install globally",
    "yes install globally",
    "si instala global",
    "global install ok",
)


def _looks_global(cmd: str) -> tuple[bool, str]:
    if _NPM_GLOBAL_RE.search(cmd):
        return True, "npm"
    if _PIP_USER_RE.search(cmd):
        return True, "pip"
    if _PIPX_GLOBAL_RE.search(cmd):
        return True, "pipx"
    # brew install is always global on macOS (no --local scope), so we
    # warn when brew is used with a package argument.
    match = _BREW_INSTALL_RE.search(cmd)
    if match and any(tok and not tok.startswith("-") for tok in match.group(0).split()[2:]):
        return True, "brew"
    return False, ""


def _guess_package(cmd: str, tool: str) -> str:
    match = re.search(rf"\b{tool}\b[^\n;|&]+", cmd, re.IGNORECASE)
    segment = match.group(0) if match else cmd
    tokens = segment.split()
    for tok in tokens[1:]:
        if tok.startswith("-"):
            continue
        if tok in {"install"}:
            continue
        return tok
    return "package"


def should_inject_r23j(
    tool_name: str, tool_input, user_text: str = ""
) -> tuple[bool, str]:
    if tool_name != "Bash":
        return False, ""
    if not isinstance(tool_input, dict):
        return False, ""
    cmd = tool_input.get("command")
    if not isinstance(cmd, str):
        return False, ""
    global_install, tool = _looks_global(cmd)
    if not global_install:
        return False, ""
    if user_text:
        low = user_text.lower()
        for marker in _PERMIT_MARKERS:
            if marker in low:
                return False, ""
    pkg = _guess_package(cmd, tool)
    prompt = INJECTION_PROMPT_TEMPLATE.format(
        cmd=cmd.strip()[:160],
        pkg=pkg,
    )
    return True, prompt
