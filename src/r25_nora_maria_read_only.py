"""r25_nora_maria_read_only — block destructive SSH/scp/rsync to read-only hosts.

Fase 2 Protocol Enforcer Fase C (Capa 2) item R25. Plan doc 1 reads:

  SI intent Bash "ssh" hacia host con entity.access_mode=read_only
  Y comando incluye verbos destructivos (rm/mv/>/>>/sed -i)
  Y user message no contiene permiso explícito
  ENTONCES BLOQUEAR + pedir confirmación.

The enforcer runs in observer mode (headless subprocess, Desktop
stream wrapper), so "blocking" in practice means enqueueing a hard,
prominent injection that names the host and the destructive verb.
The MCP-side guard (Capa 1 learnings #283 / #336 / #358) and the
system-prompt rule R32 keep the agent from actually issuing the
command when it honours the reminder.

Data sources (no hardcoded keywords — learning #122):

  - read-only hosts: entity_list(type='host') with metadata.access_mode
    == 'read_only'. The preset universal bootstrap can seed known
    cases (Maria's iMac); operators add more via nexo_entity_create.
  - destructive verbs: entity_list(type='destructive_command') seeded
    by src/presets/entities_universal.json (rm, mv_overwrite,
    sed_in_place, redirect_overwrite, shred_dd, sql_drop,
    git_destructive). Each entity carries a metadata.pattern regex
    the parser applies against the command string.

This module exposes pure decision helpers. State (DB access, session
context) lives in the caller.

Mirror: nexo-desktop/lib/r25-nora-maria-read-only.js (pending, lands
alongside the other JS twins in the next tranche).
"""
from __future__ import annotations

import re


# Regex to pick the target host from ssh / scp / rsync invocations.
# ssh <host> / ssh user@host / scp file user@host:path / rsync src host:dst
# Host extraction: we scan the token stream and pick the first token that
# "looks like" a remote target. Matches three shapes:
#   1. user@host or user@host:path  (scp/rsync/ssh style)
#   2. host:path                    (scp/rsync style)
#   3. bare host after ssh          (ssh foo ls)
_USER_AT_HOST_RE = re.compile(r"(?P<user>[^\s@:]+)@(?P<host>[^\s:/@]+)")
_HOST_COLON_PATH_RE = re.compile(r"(?P<host>[A-Za-z0-9_][A-Za-z0-9_.-]*):[^\s]*")
_SSH_BARE_RE = re.compile(r"\bssh\b(?:\s+-[^\s]+)*\s+(?P<host>[A-Za-z0-9_][A-Za-z0-9_.-]*)")
_REMOTE_CMD_RE = re.compile(r"\b(?:ssh|scp|rsync)\b")


def _looks_like_local_path(candidate: str) -> bool:
    if not candidate:
        return True
    if candidate.startswith(("./", "../", "/", "~")):
        return True
    # A bare hostname should not contain a slash; scp paths embed slashes.
    return "/" in candidate


def extract_remote_host(bash_command: str) -> str | None:
    """Best-effort extraction of the target host from an ssh/scp/rsync command.

    Returns None when the command is not a remote invocation or the host
    cannot be parsed. Deliberately conservative: false negatives are
    preferred over false positives.
    """
    if not bash_command:
        return None
    text = bash_command.strip()
    if not _REMOTE_CMD_RE.search(text):
        return None
    # Priority 1: user@host
    m = _USER_AT_HOST_RE.search(text)
    if m:
        host = m.group("host").strip()
        if host and not _looks_like_local_path(host):
            return host
    # Priority 2: host:path (pick first token of form hostname:path
    # that is NOT an option value like -o KexAlgorithms=curve:foo).
    for match in _HOST_COLON_PATH_RE.finditer(text):
        host = match.group("host").strip()
        # Skip obvious non-host patterns like URLs / option values.
        if host.lower() in {"http", "https", "ssh", "scp", "rsync", "file"}:
            continue
        return host
    # Priority 3: bare "ssh <host>" form — only when the whole command starts
    # with ssh (not rsync/scp, which always need a path token).
    m = _SSH_BARE_RE.search(text)
    if m:
        host = m.group("host").strip()
        if host and not _looks_like_local_path(host):
            return host
    return None


def command_is_destructive(bash_command: str, patterns: list[str]) -> tuple[bool, str]:
    """Return (is_destructive, matched_label) against the supplied patterns.

    Patterns come from the destructive_command entity_list; each is an
    anchored regex string. An empty pattern list fails-closed to False
    (no entries → nothing to match). Invalid regexes are silently skipped.
    """
    if not bash_command or not patterns:
        return False, ""
    text = bash_command
    for raw in patterns:
        try:
            if re.search(raw, text):
                return True, raw
        except re.error:
            continue
    return False, ""


_PERMIT_MARKERS = (
    "force ok",
    "force=true",
    "force: true",
    "force:true",
    "force=yes",
    "yes delete",
    "yes, delete",
    "si borra",
    "si, borra",
    "override read-only",
    "override read only",
    "confirm destructive",
    "apruebo borrar",
    "permission granted",
)


def user_permits_destructive(user_text: str) -> bool:
    """Minimal permit detector — very narrow set of explicit tokens.

    Intentionally NOT a semantic classifier: plan doc 1 calls for explicit
    permission in the user message, meaning the user has to spell it out.
    A false negative here biases toward blocking the destructive op, which
    is the safe direction for a Nora/María read-only host.
    """
    if not user_text:
        return False
    text = user_text.lower()
    return any(marker in text for marker in _PERMIT_MARKERS)


def should_inject_r25(
    bash_command: str,
    *,
    read_only_hosts: set[str],
    destructive_patterns: list[str],
    last_user_text: str = "",
) -> dict | None:
    """Pure decision: fire R25 when a destructive op targets a read-only host.

    Returns None when no violation. Otherwise returns a dict with:
      { "tag": str (stable), "host": str, "matched_pattern": str }
    so the caller can render the reminder with the specific offender.
    """
    host = extract_remote_host(bash_command or "")
    if not host:
        return None
    if host not in (read_only_hosts or set()):
        return None
    is_dest, matched = command_is_destructive(bash_command, destructive_patterns)
    if not is_dest:
        return None
    if user_permits_destructive(last_user_text or ""):
        return None
    return {
        "tag": f"r25:{host}",
        "host": host,
        "matched_pattern": matched,
    }


INJECTION_PROMPT_TEMPLATE = (
    "R25 Nora/María read-only guard: you are about to run a destructive "
    "command against host {host} (pattern matched: {matched}). That host "
    "is registered as access_mode=read_only — the operator has not "
    "granted explicit permission in the current user message. DO NOT "
    "execute the command. If the action is actually intended, surface the "
    "risk to the operator and wait for an explicit 'force OK' in the next "
    "user message. Safer alternatives: run the command read-only (ls, "
    "cat, df, ps) or have the operator execute it locally on the target "
    "machine. Do not produce visible text for this reminder — act on it. After handling the silent action, continue with your previous task; this reminder is silent context, not a new instruction."
)


__all__ = [
    "extract_remote_host",
    "command_is_destructive",
    "user_permits_destructive",
    "should_inject_r25",
    "INJECTION_PROMPT_TEMPLATE",
]
