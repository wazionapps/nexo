# Optional override files

NEXO Brain reads two optional JSON files at `~/.nexo/config/` to redirect its
Anthropic SDK calls and delegate bearer token resolution to a local command.
These files are **opt-in**: if neither exists, Brain runs in standalone mode
against `https://api.anthropic.com` exactly as it did before.

The pattern is intentionally analogous to git's `core.editor` and
`credential.helper`: a generic plug-in surface, not tied to any specific
proxy product. Any third-party orchestrator can plug in.

---

## Override mode

Override mode is on iff `~/.nexo/config/llm_endpoint.json` exists, parses
as JSON, declares `version: 1`, and contains a non-empty
`anthropic_base_url`. In this mode:

- The Anthropic SDK is instantiated with `base_url=anthropic_base_url`.
- The bearer is resolved via `auth_provider.json` (preferred) or the
  legacy env/filesystem fallbacks.
- Concrete model names from `resonance_tiers.json` (e.g.
  `claude-opus-4-7[1m]`) are translated to **wire aliases** that the
  receiving proxy validates (e.g. `nexo-max`). Translation lives in
  `_CONCRETE_TO_ALIAS` inside `src/call_model_raw.py`.
- Every request carries an `Idempotency-Key` header (UUID4 hex, 32
  chars) so the proxy can dedup transparent retries.

If `llm_endpoint.json` is absent, malformed, or declares an unsupported
version, override mode stays off and Brain logs a warning to stderr.

The `NEXO_LLM_ENDPOINT` environment variable can also point Brain at a
different base URL, but **it does not flip override mode on**. Env-only
configurations remain transparent to standalone bearer resolution. The
file is the gate.

To opt out of an installed override (e.g. when uninstalling the
companion that wrote the files), simply rename `llm_endpoint.json` to
`llm_endpoint.json.disabled` or delete it. Brain detects the absence on
the next call and falls back to standalone immediately.

---

## `~/.nexo/config/llm_endpoint.json`

Schema:

```json
{
  "version": 1,
  "anthropic_base_url": "https://my-proxy.example.com/api/proxy"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `version` | int | yes | Must be `1`. Other values are ignored with a stderr warning. |
| `anthropic_base_url` | string | yes | Replaces `https://api.anthropic.com`. Must be Anthropic Messages API compatible. |

Behaviour matrix:

| State | Effect |
|---|---|
| File absent | Standalone mode. `https://api.anthropic.com` is used. |
| File present, `version != 1` | Standalone mode. stderr warning. |
| File present, `version == 1`, `anthropic_base_url` empty | Standalone mode. |
| File present, `version == 1`, `anthropic_base_url` set | **Override mode active.** |
| File present, JSON malformed | Standalone mode. stderr warning. |

---

## `~/.nexo/config/auth_provider.json`

Schema:

```json
{
  "version": 1,
  "command": "/path/to/auth-helper",
  "args": ["--for", "anthropic"],
  "timeout_sec": 5
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `version` | int | yes | Must be `1`. |
| `command` | string | yes | Absolute path to an executable. |
| `args` | array of string | no | Passed verbatim to `command`. Defaults to `[]`. |
| `timeout_sec` | int | no | Subprocess timeout. Defaults to `5`. |

Brain runs `command + args` and captures stdout. The trimmed stdout is
the bearer token used in the `Authorization: Bearer ...` header.

Fallback rules (in order):

1. `auth_provider.json` ⇒ run command, return trimmed stdout if it is
   non-empty and exit code is 0.
2. On any failure (`TimeoutExpired`, `FileNotFoundError`,
   `PermissionError`, non-zero exit, empty stdout), Brain logs the cause
   to stderr and falls back to step (3).
3. `ANTHROPIC_API_KEY` environment variable.
4. `~/.claude/anthropic-api-key.txt`.
5. `~/.nexo/config/anthropic-api-key.txt`.

Brain raises `ClassifierUnavailableError` only if all five steps yield
an empty bearer.

---

## Coverage of CLI children

Override mode does not stop at the SDK direct path. `src/agent_runner.py`
spawns the Anthropic-compatible CLI for every headless cron (deep-sleep,
evolution, followup-runner, morning-agent, email-monitor) and for
interactive launches (`nexo chat`, Desktop "new session"). Each spawn
runs through `_apply_llm_endpoint_override(env)` before it forks, so the
child inherits `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` derived from
the same override files. This is required because LaunchAgent crons do
not inherit environment from a UI process: without the in-Brain
injection, every cron of a user with NEXO Desktop installed would still
hit `api.anthropic.com` directly with whatever stale `ANTHROPIC_API_KEY`
was in the keychain — wrong destination, wrong bearer.

The `--bare` branch (an internal CLI optimisation that skips keychain
auth and forces a fresh `ANTHROPIC_API_KEY` into the child) is aware of
override mode: when override is active it reuses the proxy bearer that
`_apply_llm_endpoint_override` already injected, instead of asking the
keychain helper for the operator's raw Anthropic key (which the proxy
would reject anyway).

In standalone mode (no override file) all of this is a no-op: the
spawned environment is identical to what previous Brain releases would
produce.

---

## Idempotency-Key

When override mode is active, every Brain SDK request to the proxy
carries `Idempotency-Key: <uuid4 hex>` via the SDK's `extra_headers`.

Contract:

- Brain generates a fresh UUID4 hex per logical request.
- On transparent SDK retries (timeout, connection reset), the SDK is
  expected to reuse the same value (consult your SDK's retry policy).
- The proxy SHOULD dedup on `(token_id, idempotency_key)` for at least
  24h: hit + completed → cached response; hit + in-progress → 409
  `request_in_progress` with `Retry-After`; miss → process normal.
- In standalone mode no `Idempotency-Key` header is sent. The default
  Anthropic API ignores the header anyway, so omitting it keeps the
  wire request bit-for-bit identical to pre-V11.

The header is opaque: any URL-safe ASCII string of reasonable length
works. UUID4 is the chosen default because it ships with stdlib and
provides 122 bits of entropy.

---

## Concrete-to-alias map

In override mode the proxy speaks **aliases**, not concrete Anthropic
model names. Brain translates `(model, effort)` from
`resonance_tiers.json` into the wire alias just before the SDK call:

| `(model, effort)` | Wire alias |
|---|---|
| `("claude-opus-4-7[1m]", "max")` | `nexo-max` |
| `("claude-opus-4-7[1m]", "xhigh")` | `nexo-high` |
| `("claude-opus-4-7[1m]", "high")` | `nexo-medium` |
| `("claude-opus-4-7[1m]", "medium")` | `nexo-low` |
| `("claude-haiku-4-5-20251001", "")` | `nexo-mini` |

Pairs not present in the map raise `ClassifierUnavailableError`
locally. This is intentional: the proxy would reject the request with a
remote `400` anyway, and a local error gives the operator a clearer
trail.

If you add a tier to `resonance_tiers.json`, also add the matching
alias entry in `_CONCRETE_TO_ALIAS` inside `src/call_model_raw.py` and
extend the proxy's allow-list with the new alias.

---

## End-to-end example

Suppose `corp-ai-proxy.example.com/api/proxy` exposes an
Anthropic-compatible Messages endpoint that accepts the five aliases
listed above and resolves auth via per-team API keys obtained from a
local helper.

`~/.nexo/config/llm_endpoint.json`:

```json
{
  "version": 1,
  "anthropic_base_url": "https://corp-ai-proxy.example.com/api/proxy"
}
```

`~/.nexo/config/auth_provider.json`:

```json
{
  "version": 1,
  "command": "/usr/local/bin/corp-ai-token",
  "args": ["--team", "platform"],
  "timeout_sec": 5
}
```

`/usr/local/bin/corp-ai-token` — minimal helper:

```bash
#!/bin/sh
# Reads a team-scoped token from your secret store.
exec security find-generic-password -s "corp-ai-token-platform" -w
```

What Brain does on the next classifier call:

1. `is_override_mode()` returns `true` (file present, `version: 1`,
   non-empty URL).
2. `resolve_auth_token()` runs `/usr/local/bin/corp-ai-token --team
   platform`, captures stdout, returns it.
3. `resolve_api_base_url()` returns
   `https://corp-ai-proxy.example.com/api/proxy`.
4. The SDK is instantiated with that base URL and bearer.
5. `(model, effort)` from `resonance_tiers.json` (e.g.
   `("claude-haiku-4-5-20251001", "")` for the enforcer classifier
   tier `muy_bajo`) is translated to `nexo-mini`.
6. The SDK call sends `model="nexo-mini"` plus `Idempotency-Key:
   <uuid4 hex>` to the proxy.

Brain libre clones from GitHub keep working without either file: they
hit `https://api.anthropic.com` directly with `ANTHROPIC_API_KEY` and
the concrete model name, exactly as before.
