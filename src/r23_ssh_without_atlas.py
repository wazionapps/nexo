"""r23_ssh_without_atlas — demand atlas lookup before SSHing to an unknown host.

Fase 2 Protocol Enforcer Fase D item R23. Plan doc 1 reads:

  SI intent Bash con ssh|scp|rsync|curl hacia host
  Y host no está en entity_list type=host
  ENTONCES inyectar obligación leer atlas.

Reuses the host-extraction primitive from r25 (imported lazily to avoid
a hard dependency — same mirror is provided on the JS side via the r25
module). No LLM — structural entity lookup.

State (known host set) is resolved by the caller via
db.list_entities(type='host'); this module exposes the pure decision.
"""
from __future__ import annotations

from r25_nora_maria_read_only import extract_remote_host as _extract_ssh_host


INJECTION_PROMPT_TEMPLATE = (
    "R23 SSH/curl without atlas: the command targets host '{host}' but "
    "that host is not registered in entity_list(type='host') nor in "
    "project-atlas.json. Before connecting, verify the host: (1) check "
    "project-atlas.json and nexo_entity_list(type='host') for a matching "
    "record; (2) if it is not there, create it via nexo_entity_create "
    "with access_mode and credentials pointer; (3) only then run the "
    "ssh/curl. Learning #151 / #156 — assuming servers and DNS without "
    "verifying leads to confabulation. Do not produce visible text for "
    "this reminder — act on it. After handling the silent action, continue with your previous task; this reminder is silent context, not a new instruction."
)


import re


# Match a URL (https?://host[:port][/path]) or a bare host only when
# immediately preceded by curl/wget/fetch and optional flag+VALUE pairs.
# The prior regex `\s+[\"']?(https?://)?([^\s/:\"']+)` captured the first
# non-space token after `curl -H ...`, which for `curl -H "Authorization:
# Bearer xyz" https://api.foo.com` captured "Authorization" as the host.
# We now REQUIRE a URL form (http(s)://) so header values cannot leak in.
_CURL_URL_RE = re.compile(
    r"\b(?:curl|wget|fetch)\b[^\n;|&]*?"
    r"(?:^|\s)['\"]?https?://(?P<host>[^\s/:'\"?#]+)",
    re.IGNORECASE,
)


def extract_curl_host(bash_command: str) -> str | None:
    """Extract the hostname from a curl / wget invocation, URL form only."""
    if not bash_command:
        return None
    match = _CURL_URL_RE.search(bash_command)
    if not match:
        return None
    host = (match.group("host") or "").strip()
    if not host:
        return None
    return host


def extract_remote_host(bash_command: str) -> str | None:
    """Combined extractor: ssh/scp/rsync (via r25 module) + curl/wget."""
    ssh_host = _extract_ssh_host(bash_command or "")
    if ssh_host:
        return ssh_host
    return extract_curl_host(bash_command or "")


def should_inject_r23(
    bash_command: str,
    *,
    known_hosts: set[str],
) -> dict | None:
    """Return {tag, host} when R23 should fire, else None.

    Known hosts come from entity_list(type='host') joined with any alias
    in the host entity metadata; R23 fires when the parsed host is NOT
    in the known set. Empty known_hosts → R23 is silent (fail-closed:
    without data we do not warn).
    """
    host = extract_remote_host(bash_command or "")
    if not host:
        return None
    normalised = host.lower().strip()
    known_lower = {str(h).lower().strip() for h in (known_hosts or set())}
    if not known_lower:
        # No registered hosts at all — silent. An operator with zero
        # registered hosts is a fresh NEXO install; spamming R23 would
        # be pure noise.
        return None
    if normalised in known_lower:
        return None
    return {"tag": f"r23:{normalised}", "host": host}


__all__ = [
    "extract_remote_host",
    "extract_curl_host",
    "should_inject_r23",
    "INJECTION_PROMPT_TEMPLATE",
]
