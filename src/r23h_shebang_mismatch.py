"""R23h — script shebang vs interpreter version mismatch.

Pure decision module. Part of Fase D2 (shadow — rollout gradual).

When Bash executes a script (e.g. `./deploy.sh`, `python my_script.py`),
the engine peeks at the shebang of the script file on disk and compares
it with the interpreter that Bash would actually invoke. A mismatch
between the declared interpreter (e.g. `#!/usr/bin/env python3.11`) and
the one resolved by `which python` (e.g. `3.14`) can silently change
semantics.
"""
from __future__ import annotations

import os
import re
import shutil


INJECTION_PROMPT_TEMPLATE = (
    "R23h shebang mismatch: '{script}' declares interpreter '{shebang}' "
    "but the shell resolves it to '{actual}'. Align versions before "
    "running — interpreter drift produces the hardest-to-diagnose "
    "runtime bugs."
)


_INVOCATION_RE = re.compile(
    r"(?:^|\s|;|\||&)"
    r"(?:"
    r"(?P<interp>python(?:\d(?:\.\d+)?)?|node|ruby|perl|bash|sh|zsh|fish)\s+"
    r"(?P<script_with_interp>[\w./-]+\.(?:py|js|rb|pl|sh))"
    r"|"
    r"(?P<bare>\.?\/?[\w./-]+\.(?:py|js|rb|pl|sh))"
    r")(?=\s|$|;|\||&)",
    re.IGNORECASE,
)


def _read_shebang(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            first = fh.readline().strip()
    except (OSError, UnicodeDecodeError):
        return ""
    if not first.startswith("#!"):
        return ""
    return first[2:].strip()


def _resolve_interpreter(name: str) -> str:
    if not name:
        return ""
    return shutil.which(name) or ""


def detect_shebang_mismatch(cmd: str, cwd: str | None = None) -> tuple[bool, dict]:
    if not cmd or not isinstance(cmd, str):
        return False, {}
    base = cwd or os.getcwd()
    for match in _INVOCATION_RE.finditer(cmd):
        script = match.group("script_with_interp") or match.group("bare")
        if not script:
            continue
        explicit_interp = match.group("interp") or ""
        # Resolve script path.
        abs_script = script
        if not os.path.isabs(script):
            abs_script = os.path.abspath(os.path.join(base, script))
        if not os.path.isfile(abs_script):
            continue
        shebang = _read_shebang(abs_script)
        if not shebang:
            continue
        # Derive the interpreter named in the shebang.
        declared = shebang
        if declared.startswith("/usr/bin/env "):
            declared = declared.split(None, 1)[1].split()[0]
        declared_name = os.path.basename(declared.split()[0]) if declared else ""
        invoked_name = explicit_interp or declared_name
        actual = _resolve_interpreter(invoked_name)
        # Normalize both to (name, version) tuples as best we can.
        declared_base = re.sub(r"\d+(?:\.\d+)?$", "", declared_name) or declared_name
        actual_base = os.path.basename(actual)
        actual_base = re.sub(r"\d+(?:\.\d+)?$", "", actual_base) or actual_base
        if not actual:
            return True, {
                "script": abs_script,
                "shebang": shebang,
                "actual": "unresolved",
            }
        # Mismatch if same family (python, node, etc) but versions differ.
        if declared_base.lower() == actual_base.lower():
            if declared_name != os.path.basename(actual):
                return True, {
                    "script": abs_script,
                    "shebang": shebang,
                    "actual": actual,
                }
    return False, {}


def should_inject_r23h(tool_name: str, tool_input) -> tuple[bool, str]:
    if tool_name != "Bash":
        return False, ""
    if not isinstance(tool_input, dict):
        return False, ""
    cmd = tool_input.get("command")
    if not isinstance(cmd, str):
        return False, ""
    cwd = str(tool_input.get("cwd") or "") or None
    mismatch, info = detect_shebang_mismatch(cmd, cwd)
    if not mismatch:
        return False, ""
    prompt = INJECTION_PROMPT_TEMPLATE.format(**info)
    return True, prompt
