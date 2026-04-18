"""R23d — chown/chmod -R / setfacl -R without prior ls.

Pure decision module. Part of Fase D2 (soft).

Scans the bash command for recursive ownership/permission changes
targeting root-ish paths (/, /home, /var, /etc). Requires that a `ls`
or `find` probe of the same path happened within the recent tool_call
window — otherwise warns.
"""
from __future__ import annotations

import re


INJECTION_PROMPT_TEMPLATE = (
    "R23d recursive {verb} without prior inspection: '{cmd}' targets "
    "'{target}'. Run `ls -la {target}` (or equivalent) in this session "
    "before executing a recursive ownership/permission change — silently "
    "chown-R'ing a root-ish tree is one of the fastest ways to render a "
    "box unbootable."
)


_VERB_RE = re.compile(
    r"\b(?P<verb>chown|chmod|setfacl)\s+(?P<args>[^\n;|&]*)",
    re.IGNORECASE,
)
_RECURSIVE_FLAG_RE = re.compile(r"(?<!\S)-R(?!\S)|(?<!\S)--recursive(?!\S)")
_TARGET_RE = re.compile(r"(?:^|\s)(?P<target>/[A-Za-z][\w./-]*)")

ROOT_ISH_PREFIXES = ("/", "/home", "/var", "/etc", "/opt", "/usr", "/srv")


def _is_root_ish(path: str) -> bool:
    if not path:
        return False
    if path == "/":
        return True
    # Normalize trailing slash.
    p = path.rstrip("/")
    return any(p == root or p.startswith(root + "/") for root in ROOT_ISH_PREFIXES if root != "/")


def detect_recursive_chown(cmd: str) -> tuple[bool, str, str]:
    if not cmd or not isinstance(cmd, str):
        return False, "", ""
    for match in _VERB_RE.finditer(cmd):
        args = match.group("args") or ""
        if not _RECURSIVE_FLAG_RE.search(args):
            continue
        verb = match.group("verb")
        target_match = _TARGET_RE.search(" " + args)
        if not target_match:
            continue
        target = target_match.group("target")
        if not _is_root_ish(target):
            continue
        return True, verb, target
    return False, "", ""


def should_inject_r23d(
    tool_name: str,
    tool_input,
    recent_tool_records: list,
) -> tuple[bool, str]:
    """Fire when a recursive chown/chmod hits root-ish paths without
    a prior `ls` or `find` on that path in the recent window."""
    if tool_name != "Bash":
        return False, ""
    if not isinstance(tool_input, dict):
        return False, ""
    cmd = tool_input.get("command")
    if not isinstance(cmd, str):
        return False, ""
    matched, verb, target = detect_recursive_chown(cmd)
    if not matched:
        return False, ""
    # Look backwards through recent records for a listing probe of target.
    for rec in reversed(recent_tool_records or []):
        tool = getattr(rec, "tool", None) or (rec.get("tool") if isinstance(rec, dict) else None)
        if tool != "Bash":
            continue
        previous_cmd = None
        files = getattr(rec, "files", None)
        if isinstance(files, (tuple, list)) and files:
            previous_cmd = " ".join(files)
        meta = getattr(rec, "meta", None) or (rec.get("meta") if isinstance(rec, dict) else None)
        if isinstance(meta, dict):
            previous_cmd = meta.get("command") or previous_cmd
        if not previous_cmd:
            continue
        if target in previous_cmd and re.search(r"\b(ls|find)\b", previous_cmd):
            return False, ""
    prompt = INJECTION_PROMPT_TEMPLATE.format(verb=verb, cmd=cmd.strip()[:160], target=target)
    return True, prompt
