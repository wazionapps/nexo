"""R23l — create resource with already-existing name.

Pure decision module. Part of Fase D2 (hard bloqueante).

Triggers on a whitelist of "create" verbs (cPanel whmapi1 createacct,
wrangler kv:namespace create, gcloud compute instances create, etc.)
when the target name matches an entity already registered under the
same type.
"""
from __future__ import annotations

import re


INJECTION_PROMPT_TEMPLATE = (
    "R23l resource collision: '{cmd}' tries to create {resource_type} "
    "'{name}', but an entity with that name already exists "
    "(registered as type={existing_type}). If this is intentional reuse, "
    "delete or rename the existing record first; otherwise pick a "
    "distinct name."
)


# Each entry: (verb_regex, resource_type, name_regex, compatible_entity_types).
# `compatible_entity_types` scopes the collision check — a cpanel create
# verb should not false-positive on an unrelated followup or email draft
# that happens to share a username string. An empty tuple means "match
# any type" (legacy behaviour).
CREATE_VERB_PATTERNS = [
    (
        re.compile(r"\bwhmapi1\s+createacct\b[^\n;|&]*", re.IGNORECASE),
        "cpanel_account",
        r"\busername=([\w.-]+)\b",
        ("cpanel_account", "host", "user"),
    ),
    (
        re.compile(r"\bwrangler\s+kv:namespace\s+create\b[^\n;|&]*", re.IGNORECASE),
        "wrangler_kv",
        r"\bcreate\s+['\"]?([\w.-]+)['\"]?",
        ("wrangler_kv", "cloudflare_namespace"),
    ),
    (
        re.compile(r"\bgcloud\s+compute\s+instances\s+create\b[^\n;|&]*", re.IGNORECASE),
        "gcloud_instance",
        r"\bcreate\s+([\w.-]+)\b",
        ("gcloud_instance", "vm"),
    ),
    (
        re.compile(r"\baws\s+s3api\s+create-bucket\b[^\n;|&]*", re.IGNORECASE),
        "s3_bucket",
        r"--bucket\s+([\w.-]+)",
        ("s3_bucket",),
    ),
    (
        re.compile(r"\bdocker\s+(?:container\s+)?create\b[^\n;|&]*", re.IGNORECASE),
        "docker_container",
        r"--name\s+([\w.-]+)",
        ("docker_container",),
    ),
]


def detect_resource_collision(
    cmd: str, existing_entities: list[dict]
) -> tuple[bool, dict]:
    if not cmd or not isinstance(cmd, str):
        return False, {}
    for verb_re, resource_type, name_re, compatible_types in CREATE_VERB_PATTERNS:
        verb_match = verb_re.search(cmd)
        if not verb_match:
            continue
        name_match = re.search(name_re, verb_match.group(0), re.IGNORECASE)
        if not name_match:
            continue
        name = name_match.group(1)
        compat_set = {t.lower() for t in (compatible_types or ())}
        for existing in existing_entities:
            if (existing.get("name") or "").lower() != name.lower():
                continue
            existing_type = str(existing.get("type") or "").lower()
            # When compatible_types is defined, reject cross-type matches:
            # a bucket creation should not false-positive on a followup with
            # the same string name. Legacy callers pass compatible_types=()
            # which keeps the pre-fix permissive behaviour.
            if compat_set and existing_type not in compat_set:
                continue
            return True, {
                "cmd": cmd.strip()[:160],
                "resource_type": resource_type,
                "name": name,
                "existing_type": existing.get("type", "entity"),
            }
    return False, {}


def should_inject_r23l(
    tool_name: str, tool_input, existing_entities: list[dict]
) -> tuple[bool, str]:
    if tool_name != "Bash":
        return False, ""
    if not isinstance(tool_input, dict):
        return False, ""
    cmd = tool_input.get("command")
    if not isinstance(cmd, str):
        return False, ""
    if not existing_entities:
        return False, ""
    collision, info = detect_resource_collision(cmd, existing_entities)
    if not collision:
        return False, ""
    prompt = INJECTION_PROMPT_TEMPLATE.format(**info)
    return True, prompt
