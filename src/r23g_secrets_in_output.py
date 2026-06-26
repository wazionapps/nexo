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

from core_prompts import render_core_prompt

INJECTION_PROMPT_TEMPLATE = render_core_prompt(
    "r23g-secrets-in-output-injection",
    cmd="{cmd}",
    reason="{reason}",
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
_SAFE_SECRET_VIEW_RE = re.compile(
    r"\b(?:nexo-safe-secret-view\.py|nexo_credential_get|security\s+find-generic-password|"
    r"op\s+read|pass\s+show|gcloud\s+secrets\s+versions\s+access|"
    r"aws\s+secretsmanager\s+get-secret-value)\b",
    re.IGNORECASE,
)
_SECRET_ENV_ASSIGNMENT_RE = re.compile(
    r"(?:^|[\s;|&])(?:env\s+)?[A-Za-z_][A-Za-z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD|PASS|BEARER)"
    r"[A-Za-z0-9_]*\s*=\s*[^\s;|&]{8,}",
    re.IGNORECASE,
)
_SECRET_ARG_RE = re.compile(
    r"(?:"
    r"\b(?:--?(?:api[-_]?key|token|secret|password|passwd|pass|bearer|authorization))"
    r"(?:=|\s+)[^\s;|&]{8,}"
    r"|\b(?:Authorization:\s*)?Bearer\s+(?:\$[{]?[A-Za-z_][A-Za-z0-9_]*[}]?|[A-Za-z0-9._\-~+/]{16,})"
    r"|\s-p(?:\$[{]?[A-Za-z_][A-Za-z0-9_]*[}]?|[^\s;|&]{4,})"
    r")",
    re.IGNORECASE,
)
_PROCESS_ENV_LISTING_RE = re.compile(
    r"(?:"
    r"\b(?:env|printenv)\b"
    r"|\bps\b[^\n;|&]*(?:\b(?:aux|auxww|ef|eww|e|ww)\b|-e\b|ww)"
    r"|\bpgrep\b[^\n;|&]*(?:\s-a\b|--list-full)"
    r")",
    re.IGNORECASE,
)
_GREP_SECRET_PROBE_RE = re.compile(
    r"\b(?:grep|rg)\b[^\n;|&]*(?:TOKEN|SECRET|PASSWORD|PASS|BEARER|api[_-]?key|sk-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9_]{8,})",
    re.IGNORECASE,
)


def _redact_command(cmd: str, max_len: int = 180) -> str:
    text = str(cmd or "")
    text = _BEARER_TOKEN_RE.sub("<redacted-secret>", text)
    text = _SECRET_ENV_ASSIGNMENT_RE.sub(lambda m: re.sub(r"=.*$", "=<redacted-secret>", m.group(0)), text)
    text = _SECRET_ARG_RE.sub("<redacted-secret-argv>", text)
    text = _ECHO_SECRET_RE.sub("echo <redacted-env-ref>", text)
    if len(text) > max_len:
        text = text[:max_len] + "..."
    return text


def classify_secret_visibility_risk(tool_name: str, tool_input) -> dict | None:
    """Return a hard-block descriptor for commands that would expose secrets
    through process argv/env or unredacted ps/grep output.

    R23g's older soft reminder covered obvious local dumps. This hook-level
    classifier handles the higher-risk path: secrets placed in argv/env become
    visible to process listings and shell history, and ps/grep probes can leak
    them back to the agent/operator unless a safe redaction helper is used.
    """
    if tool_name != "Bash" or not isinstance(tool_input, dict):
        return None
    cmd = tool_input.get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    if _SAFE_SECRET_VIEW_RE.search(cmd):
        return None
    reason = ""
    pattern = ""
    if _SECRET_ENV_ASSIGNMENT_RE.search(cmd):
        reason = "secret_env_visible"
        pattern = "secret env assignment would be visible to child process/env inspection"
    elif _SECRET_ARG_RE.search(cmd):
        reason = "secret_argv_visible"
        pattern = "secret-looking value/reference would be expanded into process argv"
    elif _PROCESS_ENV_LISTING_RE.search(cmd):
        reason = "process_env_listing"
        pattern = "ps/env/pgrep output can expose argv/env secrets unless redacted"
    elif _GREP_SECRET_PROBE_RE.search(cmd):
        reason = "grep_secret_output"
        pattern = "grep/rg output can expose secret values unless passed through a redactor"
    if not reason:
        return None
    return {
        "reason_code": f"r23g_{reason}",
        "debt_type": "r23g_secret_visibility_requires_safe_manager",
        "pattern": pattern,
        "safe_command": _redact_command(cmd),
        "safe_alternative": "Use the credential manager plus scripts/nexo-safe-secret-view.py, or pass secrets through stdin/files outside process argv and redact ps/grep output before sharing it.",
    }


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


# A secret READ on its own (cat .env, env, printenv) is benign: it stays on
# the operator's machine and exposes nothing to a third party. A rotate/revoke
# followup is only warranted when the same command also EXFILTRATES the output
# to a third party — piped into a network/email/cloud/repo sink. Distinguishing
# the two is what keeps R23g from minting un-closeable "rotate credential"
# critical followups on every local read.
_EXTERNAL_SINK_RE = re.compile(
    r"(?:"
    # piped into a transmitting client (network/email)
    r"\|\s*(?:curl|wget|https?\b|nc|ncat|netcat|socat|mail|mailx|sendmail|mutt|msmtp|ssmtp|telnet)\b"
    # curl/wget that upload a body
    r"|\bcurl\b[^\n;|&]*(?:--data(?:-binary|-urlencode|-raw)?|--form|--upload-file|-d\b|-F\b|-T\b|-X\s*(?:POST|PUT|PATCH))"
    r"|\bwget\b[^\n;|&]*(?:--post-data|--post-file|--body-data|--body-file)"
    # direct mail senders
    r"|\b(?:mail|mailx|sendmail|mutt|msmtp|ssmtp)\b"
    # remote transfer / cloud upload
    r"|\bscp\b[^\n;|&]*:"
    r"|\brsync\b[^\n;|&]*[\w.-]+@[\w.-]+:"
    r"|\baws\s+s3\b[^\n;|&]*\bcp\b|\bgsutil\b[^\n;|&]*\bcp\b|\bgcloud\s+storage\b[^\n;|&]*\bcp\b"
    r"|\bgh\s+(?:gist\s+create|release\s+upload)\b"
    # secret committed/pushed into a repository
    r"|\bgit\s+(?:commit|push)\b"
    # NEXO messaging / outbound channels
    r"|\bnexo_(?:send|email_send)\b"
    r")",
    re.IGNORECASE,
)


def has_external_sink(cmd: str) -> bool:
    """True when `cmd` pipes/sends its output to a third party (network, email,
    cloud, remote host, repository). A bare local read returns False."""
    if not isinstance(cmd, str) or not cmd.strip():
        return False
    return bool(_EXTERNAL_SINK_RE.search(cmd))
