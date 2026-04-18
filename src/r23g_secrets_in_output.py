"""R23g — secrets dumped to logs/output (extension of R06).

Pure decision module. Part of Fase D2 (soft).

Fires when Bash runs a command whose output is likely to reveal
credentials (echo $TOKEN, cat *key*, env, printenv, env | grep, etc.)
and the engine is about to pipe/redirect that output into a file or
email. The trigger covers both the `echo $X` family and cat'ing files
whose name strongly suggests a key material.
"""
from __future__ import annotations

import re


INJECTION_PROMPT_TEMPLATE = (
    "R23g secrets reaching output stream: '{cmd}' is likely to expose "
    "credentials ({reason}). Redact before logging/emailing, or route "
    "the value through `nexo_credential_get` so the secret stays behind "
    "the secret manager boundary."
)


_ENV_DUMP_RE = re.compile(
    r"\b(printenv|env)(?:\s+(?!--help)[^\n;|&]*)?\b",
    re.IGNORECASE,
)
_ECHO_SECRET_RE = re.compile(
    r"\becho\s+[^\n;|&]*\$\{?[A-Za-z_]*(?:TOKEN|SECRET|KEY|PASSWORD|PASS|BEARER)[A-Za-z_]*\}?",
    re.IGNORECASE,
)
_CAT_KEY_RE = re.compile(
    r"\b(?:cat|head|tail|less)\b[^\n;|&]*"
    r"(?:[/\w.-]*(?:id_rsa|\.pem|\.p12|\.pfx|\.crt\.key|credentials(?:\.json)?|secrets?\.(?:json|yaml|env))"
    r"|\.env(?:\.[\w-]+)?)",
    re.IGNORECASE,
)
_BEARER_TOKEN_RE = re.compile(
    r"\b(?:Bearer\s+[A-Za-z0-9._\-~+/]+|sk-[A-Za-z0-9]{20,}|pk-[A-Za-z0-9]{20,}|"
    r"api[_-]?key\s*[:=]\s*[A-Za-z0-9._\-]{16,})",
    re.IGNORECASE,
)


def _detect_reason(cmd: str) -> str | None:
    if _ECHO_SECRET_RE.search(cmd):
        return "echoes a secret-looking env variable"
    if _ENV_DUMP_RE.search(cmd):
        return "dumps the environment (env/printenv) which contains secrets"
    if _CAT_KEY_RE.search(cmd):
        return "reads a known key/credential file"
    if _BEARER_TOKEN_RE.search(cmd):
        return "contains an inline bearer/api token"
    return None


def should_inject_r23g(tool_name: str, tool_input) -> tuple[bool, str]:
    if tool_name != "Bash":
        return False, ""
    if not isinstance(tool_input, dict):
        return False, ""
    cmd = tool_input.get("command")
    if not isinstance(cmd, str):
        return False, ""
    reason = _detect_reason(cmd)
    if not reason:
        return False, ""
    prompt = INJECTION_PROMPT_TEMPLATE.format(
        cmd=cmd.strip()[:160],
        reason=reason,
    )
    return True, prompt
