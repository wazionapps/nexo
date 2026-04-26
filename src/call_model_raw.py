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
import logging
import os
import subprocess
import sys
import uuid
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

# ---------------------------------------------------------------------------
# Optional override files (~/.nexo/config/)
# ---------------------------------------------------------------------------
# Two forward-compatible JSON files let third-party orchestrators (such as an
# Anthropic-compatible proxy) redirect the LLM endpoint and delegate token
# resolution to a local helper. Pattern is analogous to git's `core.editor`
# and `credential.helper`.
#
#   ~/.nexo/config/llm_endpoint.json
#       {
#         "version": 1,
#         "anthropic_base_url": "https://my-proxy.example.com/api/proxy"
#       }
#
#   ~/.nexo/config/auth_provider.json
#       {
#         "version": 1,
#         "command": "/path/to/auth-helper",
#         "args": ["--for", "anthropic"],
#         "timeout_sec": 5
#       }
#
# If neither file exists the caller falls back to standalone behaviour:
# direct call to api.anthropic.com using ANTHROPIC_API_KEY from environment
# or filesystem. NEXO Brain's open-source distribution is unaffected.

def _resolve_brain_config_dir() -> Path:
    """Honour ``NEXO_HOME`` so tests, devcontainers and non-default
    installs (Maria iMac, Codex sandboxes, etc.) hit the right
    ``config/`` directory. Resolved at every call so a process that
    sets ``NEXO_HOME`` after this module is imported still picks up
    the right path on the next request — relevant for LaunchAgent
    crons that rely on env exported by their wrapper script. Falls
    back to ``~/.nexo/config/``."""
    nexo_home = os.environ.get("NEXO_HOME", "").strip()
    if nexo_home:
        return Path(nexo_home).expanduser() / "config"
    return Path.home() / ".nexo" / "config"


# Tests monkeypatch this attribute to redirect overrides to a tmp dir.
# Production code MUST NOT read this directly — use ``_brain_config_dir()``.
# Default ``None`` lets ``_brain_config_dir()`` fall through to the live
# ``_resolve_brain_config_dir()`` so call-time NEXO_HOME changes are honoured.
_BRAIN_CONFIG_DIR: Path | None = None


def _brain_config_dir() -> Path:
    """Production-side resolver. Honours the test monkeypatch hook above
    when set, otherwise resolves from the live environment on every call."""
    if _BRAIN_CONFIG_DIR is not None:
        return _BRAIN_CONFIG_DIR
    return _resolve_brain_config_dir()


_SUPPORTED_OVERRIDE_VERSION = 1
_LLM_ENDPOINT_FILENAME = "llm_endpoint.json"
_AUTH_PROVIDER_FILENAME = "auth_provider.json"
_DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
_DEFAULT_AUTH_PROVIDER_TIMEOUT = 5

# Internal map: (concrete_model, effort) -> wire alias accepted by an
# Anthropic-compatible proxy. ONLY consulted when override mode is active.
# Standalone mode never reads this map and keeps using the concrete model.
#
# Add entries here in lockstep with new tiers added to resonance_tiers.json.
# Failing fast on an unmapped (model, effort) is preferable to letting the
# proxy reject the request with a 400 — the operator gets a clear local
# error instead of a remote one.
_CONCRETE_TO_ALIAS: dict[tuple[str, str], str] = {
    ("claude-opus-4-7[1m]", "max"):    "nexo-max",
    ("claude-opus-4-7[1m]", "xhigh"):  "nexo-high",
    ("claude-opus-4-7[1m]", "high"):   "nexo-medium",
    ("claude-opus-4-7[1m]", "medium"): "nexo-low",
    ("claude-haiku-4-5-20251001", ""): "nexo-mini",
}


def _read_versioned_config(filename: str) -> dict | None:
    """Load a versioned override file from the Brain config directory.

    Calls ``_brain_config_dir()`` on every invocation so a process that
    sets ``NEXO_HOME`` after importing the module picks up the new path
    immediately. Tests can monkeypatch ``_BRAIN_CONFIG_DIR`` to redirect
    to a tmp dir.

    Returns the dict iff the file exists, parses as JSON and declares
    ``version: 1``. Any other case (missing, malformed, unsupported version)
    returns None and emits a stderr warning so operators can see why the
    override was ignored. Never raises.
    """
    path = _brain_config_dir() / filename
    try:
        if not path.is_file():
            return None
        cfg = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(
            f"[brain] failed to read override {filename}: {exc}; ignoring\n"
        )
        return None
    if not isinstance(cfg, dict):
        sys.stderr.write(
            f"[brain] override {filename} is not a JSON object; ignoring\n"
        )
        return None
    version = cfg.get("version", 0)
    if version != _SUPPORTED_OVERRIDE_VERSION:
        sys.stderr.write(
            f"[brain] override {filename} version {version!r} not supported "
            f"(expected {_SUPPORTED_OVERRIDE_VERSION}); ignoring\n"
        )
        return None
    return cfg


def resolve_api_base_url() -> str:
    """Return the Anthropic API base URL.

    Resolution order:
        1) ``~/.nexo/config/llm_endpoint.json`` with ``anthropic_base_url``.
        2) ``NEXO_LLM_ENDPOINT`` env var.
        3) Default ``https://api.anthropic.com`` (standalone).
    """
    cfg = _read_versioned_config(_LLM_ENDPOINT_FILENAME)
    if cfg:
        url = str(cfg.get("anthropic_base_url", "") or "").strip()
        if url:
            return url
    env_url = os.environ.get("NEXO_LLM_ENDPOINT", "").strip()
    if env_url:
        return env_url
    return _DEFAULT_ANTHROPIC_BASE_URL


def _override_force_disabled() -> bool:
    # Internal escape hatch used by the test suite and by maintainers when
    # they need to validate a regression against the upstream Anthropic API
    # without renaming the override files on disk. Intentionally undocumented
    # outside the source so that the canonical override-mode contract stays
    # purely file-driven for everybody else.
    raw = os.environ.get("NEXO_RAW_ANTHROPIC", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def is_override_mode() -> bool:
    """True iff a valid ``llm_endpoint.json`` is present and selects a custom
    base URL. The override gate is the file (not an env var) so that
    env-only configurations remain transparent to standalone callers."""
    if _override_force_disabled():
        return False
    cfg = _read_versioned_config(_LLM_ENDPOINT_FILENAME)
    if not cfg:
        return False
    url = str(cfg.get("anthropic_base_url", "") or "").strip()
    return bool(url)


def _resolve_auth_provider_token() -> str:
    """Resolve the bearer token strictly from ``auth_provider.json``.

    Returns the trimmed stdout of the configured command on success.
    Returns ``""`` if the file is absent, malformed, or the command
    times out / fails / exits non-zero / produces empty stdout. Never
    falls back to environment or filesystem keys; that decision is
    made by the caller based on whether override mode is active.
    """
    cfg = _read_versioned_config(_AUTH_PROVIDER_FILENAME)
    if not cfg:
        return ""
    cmd = str(cfg.get("command", "") or "").strip()
    if not cmd:
        return ""
    args_raw = cfg.get("args", []) or []
    args = [str(a) for a in args_raw if isinstance(a, (str, int, float))]
    try:
        timeout_sec = int(cfg.get("timeout_sec", _DEFAULT_AUTH_PROVIDER_TIMEOUT))
    except (TypeError, ValueError):
        timeout_sec = _DEFAULT_AUTH_PROVIDER_TIMEOUT
    try:
        result = subprocess.run(
            [cmd, *args],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # Learning #294: subprocess timeouts must be captured explicitly so
        # the operator sees the helper hung instead of a generic
        # "auth missing" downstream.
        sys.stderr.write(
            f"[brain] auth_provider command timed out after {timeout_sec}s: "
            f"{exc}\n"
        )
        return ""
    except (FileNotFoundError, PermissionError, OSError) as exc:
        sys.stderr.write(f"[brain] auth_provider command failed: {exc}\n")
        return ""
    if result.returncode != 0:
        stderr_excerpt = (result.stderr or "").strip()[:200]
        sys.stderr.write(
            f"[brain] auth_provider command exit={result.returncode}: "
            f"{stderr_excerpt}\n"
        )
        return ""
    return (result.stdout or "").strip()


def resolve_auth_token() -> str:
    """Return the bearer token to use against the resolved base URL.

    The resolution depends on whether override mode is active:

    * **Override mode** (``llm_endpoint.json`` valid): the token MUST
      come from ``auth_provider.json``. Falling back to
      ``ANTHROPIC_API_KEY`` (a real ``sk-ant-...`` key bound to the
      operator's Anthropic account) and sending it as the bearer to a
      third-party proxy would leak that credential. If the helper
      command fails or is not configured, returns ``""`` so the caller
      raises ``ClassifierUnavailableError``.
    * **Standalone mode** (no override file): cascade
      ``auth_provider.json`` → ``ANTHROPIC_API_KEY`` env →
      ``~/.claude/anthropic-api-key.txt`` → ``~/.nexo/config/anthropic-api-key.txt``.
      The legacy fallbacks exist so an operator that scripted bearer
      resolution via the helper can still rely on the env var when
      Brain is not redirected anywhere.
    """
    if is_override_mode():
        # Strict: the bearer must come from the configured helper. If
        # the helper is missing or fails, refuse to authenticate rather
        # than leak a real Anthropic key to a custom proxy.
        return _resolve_auth_provider_token()

    # Standalone: helper first (if scripted), env/files otherwise.
    helper_token = _resolve_auth_provider_token()
    if helper_token:
        return helper_token
    return _resolve_anthropic_key()


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


def _resolve_override_alias(model: str, effort: str) -> str:
    """In override mode the proxy speaks aliases, not concrete model names.
    Translate ``(model, effort)`` into the wire alias the proxy validates.
    Unmapped pairs fail-closed: better to surface a local config error than
    let the proxy reject the request remotely.
    """
    key = (model, effort)
    alias = _CONCRETE_TO_ALIAS.get(key)
    if not alias:
        raise ClassifierUnavailableError(
            f"override mode: no alias mapped for (model={model!r}, "
            f"effort={effort!r}); update _CONCRETE_TO_ALIAS in call_model_raw.py"
        )
    return alias


def _call_anthropic_raw(
    *,
    prompt: str,
    system: str | None,
    model: str,
    effort: str,
    max_tokens: int,
    temperature: float,
    stop_sequences: list[str],
    timeout: float,
    idempotency_key: str | None = None,
) -> str:
    try:
        import anthropic  # type: ignore
    except ImportError as exc:
        raise ClassifierUnavailableError(f"anthropic SDK missing: {exc}") from exc

    override = is_override_mode()
    if override:
        # Proxy mode. The Anthropic SDK distinguishes:
        #   api_key=...    -> header "X-Api-Key: <value>"   (Anthropic-style)
        #   auth_token=... -> header "Authorization: Bearer <value>" (OAuth-style)
        # NEXO Desktop and any compatible proxy parse the standard
        # "Authorization: Bearer" header, so we MUST pass the resolved
        # bearer through ``auth_token`` — passing it as ``api_key`` would
        # send "X-Api-Key" which the proxy would reject with 401.
        wire_model = _resolve_override_alias(model, effort)
        base_url = resolve_api_base_url()
        bearer = resolve_auth_token()
        if not bearer:
            raise ClassifierUnavailableError(
                "anthropic override: no bearer resolved from auth_provider.json; "
                "override mode requires a configured auth helper to avoid leaking "
                "a real ANTHROPIC_API_KEY to a custom proxy"
            )
        # The SDK ``__init__`` resolves ``api_key`` from
        # ``ANTHROPIC_API_KEY`` whenever the kwarg is ``None`` (the
        # parameter default). It then sends BOTH ``X-Api-Key`` (from the
        # env-resolved api_key) and ``Authorization: Bearer`` (from
        # auth_token) on every request. A custom proxy would see and
        # potentially log the operator's real ``sk-ant-...`` key. Passing
        # ``api_key=""`` does not fix it either: the SDK's auth_headers
        # check is ``if api_key is None`` (strict ``is``, not falsy), so
        # the empty string still produces an ``X-Api-Key:`` header.
        # Solution: pop the env var around the constructor call so the
        # SDK records ``api_key=None`` and skips the X-Api-Key header
        # entirely. Then restore the original env so we don't break
        # other code paths in the same Python process.
        _saved_anthropic_env = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            client = anthropic.Anthropic(
                auth_token=bearer,
                base_url=base_url,
                timeout=timeout,
            )
        finally:
            if _saved_anthropic_env is not None:
                os.environ["ANTHROPIC_API_KEY"] = _saved_anthropic_env
    else:
        # Standalone: behaviour identical to pre-V11. No override, no alias
        # translation, no extra headers — direct hit to api.anthropic.com.
        wire_model = model
        api_key = _resolve_anthropic_key()
        if not api_key:
            raise ClassifierUnavailableError("anthropic: no ANTHROPIC_API_KEY found")
        client = anthropic.Anthropic(api_key=api_key, timeout=timeout)

    kwargs: dict = {
        "model": wire_model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if stop_sequences:
        # Anthropic API rejects whitespace-only stop sequences with
        # 400 ``each stop sequence must contain non-whitespace``. The
        # caller-validation in call_model_raw filters these out before
        # we reach this point; the empty/None case is also covered by
        # the truthy check above so we omit the field entirely instead
        # of sending ``stop_sequences: null`` to the wire.
        kwargs["stop_sequences"] = stop_sequences
    if system:
        kwargs["system"] = system

    if override:
        # Idempotency-Key: opaque per-request token. The proxy dedups on
        # (token_id + idempotency_key) for 24h, so network-level retries
        # do not double-bill the user. The caller is encouraged to pass
        # an explicit ``idempotency_key`` and reuse it across application-
        # level retries (e.g. enforcement_classifier retrying after a
        # ClassifierUnavailableError) so the proxy treats the second
        # attempt as a duplicate of the first instead of a brand-new
        # billable request. If the caller omits it we generate a fresh
        # UUID4, which still covers SDK-level transparent retries since
        # the SDK reuses the same ``kwargs`` across them.
        if idempotency_key is None:
            idempotency_key = uuid.uuid4().hex
        kwargs["extra_headers"] = {"Idempotency-Key": idempotency_key}

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
    idempotency_key: str | None = None,
) -> str:
    """Run a single short LLM completion for enforcement-class classification.

    Parameters follow the Fase 2 plan doc 1 spec:

        prompt          — the user-role text (English or the model's default).
        tier            — resonance tier; default "muy_bajo" → Haiku / gpt-5.4-mini.
        caller          — resonance caller label. Must be registered in
                          resonance_map.SYSTEM_OWNED_CALLERS. Default
                          "enforcer_classifier".
        max_tokens      — hard cap on output tokens. Default 3 (yes/no only).
        temperature     — sampling temperature. Default 0.0 (deterministic).
        stop_sequences  — early-stop strings. Default ``None`` (no stop
                          sequence sent on the wire). Anthropic's API
                          rejects whitespace-only entries with
                          ``each stop sequence must contain
                          non-whitespace`` (HTTP 400), so the previous
                          default of ``["\\n", ".", " "]`` made every
                          ``enforcer_classifier`` request fail in
                          production. ``max_tokens=3`` already serves as
                          the hard cap for yes/no classification, so a
                          stop sequence is unnecessary by default.
                          Callers that want a deterministic stop can
                          pass e.g. ``["."]``; whitespace-only entries
                          are rejected locally with
                          ``ClassifierUnavailableError``.
        timeout         — per-request timeout in seconds. Default 10.0.
        system          — optional system prompt. Default None (provider default).
        idempotency_key — optional opaque token attached as
                          ``Idempotency-Key`` header in override mode. Reuse
                          the same value across application-level retries
                          (e.g. when the caller catches
                          ``ClassifierUnavailableError`` and tries again)
                          so the proxy treats the retry as a duplicate of
                          the first request and does not double-bill.
                          Ignored in standalone mode. If omitted in
                          override mode, a fresh UUID4 is generated which
                          still covers transparent SDK-level retries.

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
    if stop_sequences is not None:
        # Anthropic API: ``each stop sequence must contain
        # non-whitespace`` (HTTP 400). Surface the configuration error
        # locally instead of letting Anthropic 400 the request — and,
        # in override mode, instead of letting the proxy translate that
        # 400 into a misleading ``503 all_providers_down``.
        invalid = [
            repr(s) for s in stop_sequences
            if not isinstance(s, str) or not s.strip()
        ]
        if invalid:
            raise ClassifierUnavailableError(
                "stop_sequences contains whitespace-only or non-string "
                f"entries: {', '.join(invalid)}; Anthropic API requires "
                "every stop sequence to contain non-whitespace"
            )

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
        model, effort = resolve_model_and_effort(
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
            effort=effort,
            max_tokens=max_tokens,
            temperature=temperature,
            stop_sequences=stop_sequences,
            timeout=timeout,
            idempotency_key=idempotency_key,
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


__all__ = [
    "call_model_raw",
    "ClassifierUnavailableError",
    "is_override_mode",
    "resolve_api_base_url",
    "resolve_auth_token",
]
