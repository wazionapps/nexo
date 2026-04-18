"""R23b — deploy path ↔ vhost mismatch.

Pure decision module. Part of Fase D2 (hard bloqueante).

Triggers when an scp/rsync command writes to a remote path that maps to
a known vhost_mapping entity, and the surrounding context references a
different domain. The classic incident: pushing systeam.es assets to
the vicshop docroot because the mental-model-cached entity was wrong.
"""
from __future__ import annotations

import re


INJECTION_PROMPT_TEMPLATE = (
    "R23b deploy vhost mismatch: '{cmd}' writes to '{docroot}' which is "
    "mapped to domain '{mapped_domain}' — but the user context mentions "
    "'{context_domain}'. Verify the vhost_mapping entity before shipping. "
    "If '{context_domain}' is correct, the deploy path should be the "
    "docroot registered for that domain, not '{docroot}'."
)

_SCP_RSYNC_RE = re.compile(
    r"\b(scp|rsync)\s+(?P<args>[^\n;|&]+)",
    re.IGNORECASE,
)
_REMOTE_TARGET_RE = re.compile(
    r"(?P<host>[\w.-]+):(?P<path>/[\w./-]+)",
)
_DOMAIN_MENTION_RE = re.compile(
    r"\b(?P<domain>[\w-]+\.(?:com|es|app|net|org|dev|io|me|co))\b",
    re.IGNORECASE,
)


def _docroot_to_mapping(vhosts: list[dict], docroot: str) -> dict | None:
    """Longest-prefix match against known docroots."""
    best = None
    best_len = -1
    for vh in vhosts:
        meta = vh.get("metadata") or {}
        mapped_root = (meta.get("docroot") or "").rstrip("/")
        if not mapped_root:
            continue
        if docroot.startswith(mapped_root) and len(mapped_root) > best_len:
            best = vh
            best_len = len(mapped_root)
    return best


def detect_deploy_mismatch(
    cmd: str, context_text: str, vhosts: list[dict]
) -> tuple[bool, dict]:
    """Return (mismatch, info) with docroot/mapped_domain/context_domain."""
    if not cmd or not isinstance(cmd, str):
        return False, {}
    if not vhosts:
        return False, {}
    scp_match = _SCP_RSYNC_RE.search(cmd)
    if not scp_match:
        return False, {}
    args = scp_match.group("args") or ""
    remote_match = _REMOTE_TARGET_RE.search(args)
    if not remote_match:
        return False, {}
    docroot = remote_match.group("path")
    mapping = _docroot_to_mapping(vhosts, docroot)
    if not mapping:
        return False, {}
    mapped_domain = (mapping.get("metadata") or {}).get("domain", "")
    if not mapped_domain:
        return False, {}
    if not context_text:
        return False, {}
    # Find all domain mentions in the context; if any differs from the
    # mapped domain (case-insensitive) we treat it as a mismatch.
    for match in _DOMAIN_MENTION_RE.finditer(context_text):
        mentioned = match.group("domain").lower()
        if mentioned == mapped_domain.lower():
            # Context confirms mapping — no mismatch.
            return False, {}
        # A differing domain appeared → mismatch candidate. Require the
        # mentioned domain to also appear in the vhost registry to avoid
        # false positives (operator may simply discuss unrelated domain
        # in chat).
        if any(
            ((vh.get("metadata") or {}).get("domain", "").lower() == mentioned)
            for vh in vhosts
        ):
            return True, {
                "cmd": cmd.strip(),
                "docroot": docroot,
                "mapped_domain": mapped_domain,
                "context_domain": mentioned,
            }
    return False, {}


def should_inject_r23b(
    tool_name: str, tool_input, context_text: str, vhosts: list[dict]
) -> tuple[bool, str]:
    if tool_name != "Bash":
        return False, ""
    if not isinstance(tool_input, dict):
        return False, ""
    cmd = tool_input.get("command")
    if not isinstance(cmd, str):
        return False, ""
    mismatch, info = detect_deploy_mismatch(cmd, context_text, vhosts)
    if not mismatch:
        return False, ""
    prompt = INJECTION_PROMPT_TEMPLATE.format(**info)
    return True, prompt
