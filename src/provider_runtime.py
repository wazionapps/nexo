from __future__ import annotations

"""Provider-runtime contract for Anthropic/Claude Code and OpenAI/Codex."""

from client_preferences import (
    BACKEND_NONE,
    CLIENT_CLAUDE_CODE,
    CLIENT_CODEX,
    CLIENT_TO_PROVIDER,
    PROVIDER_ANTHROPIC,
    PROVIDER_NONE,
    PROVIDER_OPENAI,
    PROVIDER_TO_CLIENT,
    client_to_provider,
    default_provider_runtime,
    normalize_provider_key,
    normalize_provider_runtime,
    provider_to_client,
    resolve_automation_provider,
    resolve_selected_chat_provider,
)

__all__ = [
    "BACKEND_NONE",
    "CLIENT_CLAUDE_CODE",
    "CLIENT_CODEX",
    "CLIENT_TO_PROVIDER",
    "PROVIDER_ANTHROPIC",
    "PROVIDER_NONE",
    "PROVIDER_OPENAI",
    "PROVIDER_TO_CLIENT",
    "client_to_provider",
    "default_provider_runtime",
    "normalize_provider_key",
    "normalize_provider_runtime",
    "provider_to_client",
    "resolve_automation_provider",
    "resolve_selected_chat_provider",
]
