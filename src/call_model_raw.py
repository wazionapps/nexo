"""call_model_raw — Plain LLM invocation for the Protocol Enforcer classifier.

Fase 2 spec item 0.1 + 0.20. Provides a DIRECT SDK call that bypasses the
Claude Code CLI, the NEXO MCP server and the enforcement wrapper. Designed
for short yes/no classification (R13 pre-edit, R14 correction, R16
declared-done, R17 promise, R20 constant-change, etc.) where starting the
full automation stack would dwarf the actual cost of the model call.

Design contract (from plan doc 1 "Refactor de keywords/regex hardcoded —
Mecanismo C"):

  - Resolve (model, effort) via resonance_map.resolve_model_and_effort on
    caller "enforcer_classifier" (tier "muy_bajo"). Respects user's backend
    preference via resolve_automation_backend.
  - Direct SDK call to the resolved backend (anthropic or openai).
  - Triple reinforcement for yes/no parsing is implemented in the caller
    (enforcement_classifier.py): system prompt strict + max_tokens<=3 +
    regex parser with one retry.
  - Fail-closed: every transient error (timeout, rate limit, 5xx,
    connection) raises ClassifierUnavailableError. Upstream catches and
    degrades the rule to shadow or injects a generic reminder. Never
    fail-open. Rule #249, #294.
  - No MCP tools, no hook side-effects, no subprocess. This function is
    safe to call inside enforcement hot paths.

This module deliberately does NOT live inside agent_runner.py so that:

  1. agent_runner.py stays focused on automation subprocess orchestration.
  2. enforcement_engine.py (headless) and tools that run outside an
     automation subprocess can import call_model_raw without pulling in
     the rest of agent_runner.py.
  3. Tests for call_model_raw (test_call_model_raw.py) can mock the SDK
     entry points precisely without monkey-patching agent_runner.

Historical note: pre-Fase 2, callers sometimes reached for
run_automation_prompt() when they needed a one-shot model call. That
starts a full Claude Code session and a full NEXO MCP handshake — a
disaster for per-turn classification cost. call_model_raw closes that
gap.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class ClassifierUnavailableError(RuntimeError):
    """Signal that the enforcer classifier backend is unavailable.

    Fase 2 spec 0.20: callers MUST catch this and fall back to a safer
    default (inject generic reminder, degrade rule to shadow for the
    session, etc.). Never fail-open. Learning #249: structured protocol
    inputs must fail explicitly, never coerce silently.
    """


_ANTHROPIC_KEY_PATHS = (
    Path.home() / ".claude" / "anthropic-api-key.txt",
    Path.home() / ".nexo" / "config" / "anthropic-api-key.txt",
)

_OPENAI_KEY_PATHS = (
    Path.home() / ".nexo" / "config" / "openai-api-key.txt",
    Path.home() / ".codex" / "auth.json",
)


def _resolve_anthropic_key() -> str:
    env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env_key:
        return env_key
    for path in _ANTHROPIC_KEY_PATHS:
        try:
            if path.is_file():
                key = path.read_text().strip()
                if key:
                    return key
        except OSError:
            continue
    return ""


def _resolve_openai_key() -> str:
    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_key:
        return env_key
    for path in _OPENAI_KEY_PATHS:
        try:
            if not path.is_file():
                continue
            text = path.read_text().strip()
            if not text:
                continue
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    for candidate in ("OPENAI_API_KEY", "api_key", "openai_api_key"):
                        value = str(data.get(candidate, "") or "").strip()
                        if value:
                            return value
            except json.JSONDecodeError:
                return text
        except OSError:
            continue
    return ""


def _extract_anthropic_text(response) -> str:
    try:
        blocks = list(getattr(response, "content", None) or [])
    except Exception as _exc:  # noqa: BLE001
        # Audit-MEDIUM: log SDK drift so operators see when the Anthropic
        # response shape changes between minor versions.
        import logging as _log
        _log.getLogger("nexo.enforcer").warning(
            "anthropic extract_text failed (%s); returning empty", _exc
        )
        return ""
    for block in blocks:
        text = getattr(block, "text", None)
        if text:
            return str(text).strip()
    return ""


def _extract_openai_text(response) -> str:
    try:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        return str(content or "").strip()
    except Exception:
        return ""


def _call_anthropic_raw(
    *,
    prompt: str,
    system: str | None,
    model: str,
    max_tokens: int,
    temperature: float,
    stop_sequences: list[str],
    timeout: float,
) -> str:
    try:
        import anthropic  # type: ignore
    except ImportError as exc:
        raise ClassifierUnavailableError(f"anthropic SDK missing: {exc}") from exc

    api_key = _resolve_anthropic_key()
    if not api_key:
        raise ClassifierUnavailableError("anthropic: no ANTHROPIC_API_KEY found")

    client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stop_sequences": stop_sequences,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    try:
        response = client.messages.create(**kwargs)
    except anthropic.APITimeoutError as exc:
        raise ClassifierUnavailableError(f"anthropic timeout: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise ClassifierUnavailableError(f"anthropic rate_limit: {exc}") from exc
    except anthropic.APIConnectionError as exc:
        raise ClassifierUnavailableError(f"anthropic connection: {exc}") from exc
    except anthropic.APIStatusError as exc:
        status = getattr(exc, "status_code", 0)
        if 500 <= status < 600:
            raise ClassifierUnavailableError(f"anthropic 5xx: {status} {exc}") from exc
        raise ClassifierUnavailableError(f"anthropic {status}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001  — fail-closed wrapper
        raise ClassifierUnavailableError(f"anthropic unexpected: {exc}") from exc

    return _extract_anthropic_text(response)


def _call_openai_raw(
    *,
    prompt: str,
    system: str | None,
    model: str,
    max_tokens: int,
    temperature: float,
    stop_sequences: list[str],
    timeout: float,
) -> str:
    try:
        import openai  # type: ignore
    except ImportError as exc:
        raise ClassifierUnavailableError(f"openai SDK missing: {exc}") from exc

    api_key = _resolve_openai_key()
    if not api_key:
        raise ClassifierUnavailableError("openai: no OPENAI_API_KEY found")

    client = openai.OpenAI(api_key=api_key, timeout=timeout)
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop_sequences,
        )
    except openai.APITimeoutError as exc:
        raise ClassifierUnavailableError(f"openai timeout: {exc}") from exc
    except openai.RateLimitError as exc:
        raise ClassifierUnavailableError(f"openai rate_limit: {exc}") from exc
    except openai.APIConnectionError as exc:
        raise ClassifierUnavailableError(f"openai connection: {exc}") from exc
    except openai.APIStatusError as exc:
        status = getattr(exc, "status_code", 0)
        if 500 <= status < 600:
            raise ClassifierUnavailableError(f"openai 5xx: {status} {exc}") from exc
        raise ClassifierUnavailableError(f"openai {status}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001  — fail-closed wrapper
        raise ClassifierUnavailableError(f"openai unexpected: {exc}") from exc

    return _extract_openai_text(response)


def call_model_raw(
    prompt: str,
    *,
    tier: str = "muy_bajo",
    caller: str = "enforcer_classifier",
    max_tokens: int = 3,
    temperature: float = 0.0,
    stop_sequences: list[str] | None = None,
    timeout: float = 10.0,
    system: str | None = None,
) -> str:
    """Run a single short LLM completion for enforcement-class classification.

    Parameters follow the Fase 2 plan doc 1 spec:

        prompt         — the user-role text (English or the model's default).
        tier           — resonance tier; default "muy_bajo" → Haiku / gpt-5.4-mini.
        caller         — resonance caller label. Must be registered in
                         resonance_map.SYSTEM_OWNED_CALLERS. Default
                         "enforcer_classifier".
        max_tokens     — hard cap on output tokens. Default 3 (yes/no only).
        temperature    — sampling temperature. Default 0.0 (deterministic).
        stop_sequences — early-stop strings. Default ["\\n", ".", " "].
        timeout        — per-request timeout in seconds. Default 10.0.
        system         — optional system prompt. Default None (provider default).

    Returns the raw text response, trimmed. The CALLER is responsible for
    parsing yes/no — the "triple reinforcement" (prompt strict, max_tokens
    tiny, regex parser with retry, fallback conservative) is implemented in
    enforcement_classifier.py on top of this function.

    Raises ClassifierUnavailableError on any of:

        - automation_backend == none (user disabled automation)
        - tier not present in resonance_tiers.json for the resolved backend
        - SDK package missing
        - API key missing
        - Timeout / rate limit / 5xx / ConnectionError / any unexpected exception

    Callers MUST catch this and fall back to a safer default. Fase 2 spec
    0.20 is explicit: silence is not obedience. Never fail-open.
    """
    if stop_sequences is None:
        stop_sequences = ["\n", ".", " "]

    # Local imports to avoid circulars and keep agent_runner.py decoupled.
    from client_preferences import (  # type: ignore
        BACKEND_NONE,
        CLIENT_CLAUDE_CODE,
        CLIENT_CODEX,
        load_client_preferences,
        resolve_automation_backend,
    )
    from resonance_map import (  # type: ignore
        UnregisteredCallerError,
        resolve_model_and_effort,
    )

    prefs = load_client_preferences()
    backend = resolve_automation_backend(preferences=prefs)
    if backend == BACKEND_NONE:
        raise ClassifierUnavailableError("automation_backend=none")

    try:
        model, _effort = resolve_model_and_effort(
            caller=caller,
            backend=backend,
            explicit_tier=tier,
        )
    except UnregisteredCallerError as exc:
        raise ClassifierUnavailableError(f"caller not registered: {exc}") from exc

    if not model:
        raise ClassifierUnavailableError(
            f"no (model, effort) for tier={tier!r} backend={backend!r}; "
            f"check resonance_tiers.json"
        )

    if backend == CLIENT_CLAUDE_CODE:
        return _call_anthropic_raw(
            prompt=prompt,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            stop_sequences=stop_sequences,
            timeout=timeout,
        )
    if backend == CLIENT_CODEX:
        return _call_openai_raw(
            prompt=prompt,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            stop_sequences=stop_sequences,
            timeout=timeout,
        )

    raise ClassifierUnavailableError(f"unsupported backend: {backend}")


__all__ = ["call_model_raw", "ClassifierUnavailableError"]
