from __future__ import annotations

"""Shared runtime model defaults for terminal clients and automation backends."""


DEFAULT_CLAUDE_CODE_MODEL = "claude-opus-4-6[1m]"
DEFAULT_CLAUDE_CODE_REASONING_EFFORT = ""
DEFAULT_CODEX_MODEL = "gpt-5.4"
DEFAULT_CODEX_REASONING_EFFORT = "xhigh"

DEFAULT_CLIENT_RUNTIME_PROFILES = {
    "claude_code": {
        "model": DEFAULT_CLAUDE_CODE_MODEL,
        "reasoning_effort": DEFAULT_CLAUDE_CODE_REASONING_EFFORT,
    },
    "codex": {
        "model": DEFAULT_CODEX_MODEL,
        "reasoning_effort": DEFAULT_CODEX_REASONING_EFFORT,
    },
}


def default_client_runtime_profiles() -> dict[str, dict[str, str]]:
    return {
        client_key: dict(profile)
        for client_key, profile in DEFAULT_CLIENT_RUNTIME_PROFILES.items()
    }
