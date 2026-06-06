# Managed Default MCPs

**Date:** 2026-06-06
**Status:** Product spec for implementation
**Scope:** NEXO Brain, NEXO Desktop, Claude Code, Claude Desktop, Codex

## Goal

NEXO must ship with a small set of managed MCP capabilities that are installed, updated, configured, healthchecked, and removed automatically during NEXO install/update.

This is not a public MCP marketplace and not a user settings panel. The user should experience it as "NEXO can use Chrome and the computer", not as "NEXO installed MCP servers".

## Default Managed Set

| Capability | User-facing meaning | macOS provider | Windows provider | Install by default |
|---|---|---|---|---|
| `nexo_core` | NEXO Brain tools and memory | existing `nexo` MCP | existing `nexo` MCP | yes |
| `chrome_control` | Use Chrome/browser | `chrome-devtools-mcp` | `chrome-devtools-mcp` | yes |
| `desktop_control` | Use visible apps like a person | `mac-use-mcp` | `native-devtools-mcp` first, `open-computer-use` fallback | yes |
| `power_control` | Files, terminal, processes, app/system actions | `desktop-commander` | `desktop-commander` | yes |

`nexo_core` already exists. The feature adds managed lifecycle and client parity for the three extra capabilities.

## Core Product Decisions

| Decision | Contract |
|---|---|
| Brain owns the catalog | Desktop packages Brain and triggers install/update; Desktop does not duplicate resolver logic. |
| Capabilities are stable | Provider packages may change; capability IDs do not. |
| macOS and Windows are first-class | If macOS uses `mac-use-mcp`, Windows must use an equivalent provider for `desktop_control`. |
| No client `@latest` | Release/update resolves exact versions into a lockfile. Client config runs exact locked versions through a NEXO runner. |
| Silent updates | NEXO-owned MCPs update silently when lockfile has newer verified versions. |
| Ownership-aware removal | NEXO removes only entries it owns. Manual user MCPs are preserved. |
| No normal-user MCP UI | Diagnostics can exist, but users should not manage technical MCP lists. |

## Architecture

Add a managed MCP package in Brain:

```text
src/managed_mcp/
  catalog.py
  lock.py
  resolver.py
  reconcile.py
  client_config.py
  health.py
  policy.py
  state.py
```

Add shipped catalog and lock files:

```text
src/managed_mcp/catalog.json
src/managed_mcp/lock.json
```

Add runtime state:

```text
~/.nexo/runtime/managed-mcp/
  installed-state.json
  operations/*.jsonl
  artifacts/<provider>/<version>/
  rollback/<operation-id>/
```

Add a single runner used by Claude/Codex configs:

```text
~/.nexo/runtime/bin/nexo-managed-mcp run <capability_id>
```

The runner reads installed state, selects the active provider, cleans environment, starts the provider, and logs health/exit status.

## Integration Points

| File | Required change |
|---|---|
| `src/client_sync.py` | Merge managed MCP entries into Claude Code, Claude Desktop, and Codex configs. |
| `src/plugins/update.py` | Run managed MCP reconcile after normal client sync during NEXO update. |
| `bin/nexo-brain.js` | Run managed MCP reconcile during install/configure. |
| `src/mcp_live_audit.py` | Report managed MCP status, provider, version, health, and drift. |
| `src/system_catalog.py` | Expose capabilities as product capabilities, not as user-installed MCP trivia. |
| Desktop update flow | Call Brain update/sync and show restart/permission status if needed. |

## Catalog Schema

Catalog defines desired capabilities and providers, not installed versions:

```json
{
  "schema": "nexo.managed_mcp.catalog.v1",
  "catalog_version": "2026.06.06",
  "defaults_profile": "normal_user",
  "capabilities": [
    {
      "id": "chrome_control",
      "display_name": "Chrome control",
      "enabled_by_default": true,
      "risk": "high",
      "clients": ["claude_code", "claude_desktop", "codex"],
      "providers": [
        {
          "id": "chrome-devtools-mcp",
          "platforms": ["darwin", "win32"],
          "source": {"type": "npm", "package": "chrome-devtools-mcp"},
          "version_policy": "latest_on_release",
          "transport": "stdio",
          "preflight": {
            "node": "^20.19.0 || ^22.12.0 || >=23",
            "binaries": ["chrome"]
          }
        }
      ]
    },
    {
      "id": "desktop_control",
      "display_name": "Desktop control",
      "enabled_by_default": true,
      "risk": "high",
      "clients": ["claude_code", "claude_desktop", "codex"],
      "providers": [
        {
          "id": "mac-use-mcp",
          "platforms": ["darwin"],
          "source": {"type": "npm", "package": "mac-use-mcp"},
          "version_policy": "latest_on_release",
          "preflight": {
            "os_permissions": ["accessibility", "screen_recording"]
          }
        },
        {
          "id": "native-devtools-mcp",
          "platforms": ["win32"],
          "source": {"type": "npm", "package": "native-devtools-mcp"},
          "version_policy": "latest_on_release"
        },
        {
          "id": "open-computer-use",
          "platforms": ["win32"],
          "source": {"type": "npm", "package": "open-computer-use"},
          "version_policy": "latest_on_release",
          "fallback": true
        }
      ]
    },
    {
      "id": "power_control",
      "display_name": "Power control",
      "enabled_by_default": true,
      "risk": "critical",
      "clients": ["claude_code", "claude_desktop", "codex"],
      "providers": [
        {
          "id": "desktop-commander",
          "platforms": ["darwin", "win32"],
          "source": {"type": "npm", "package": "@wonderwhy-er/desktop-commander"},
          "version_policy": "latest_on_release"
        }
      ]
    }
  ]
}
```

The live client config must never contain package `@latest`. It should call `nexo-managed-mcp run <capability_id>`.

## Lockfile

Release/update resolves exact provider versions:

```json
{
  "schema": "nexo.managed_mcp.lock.v1",
  "catalog_version": "2026.06.06",
  "generated_at": "2026-06-06T00:00:00Z",
  "providers": {
    "chrome-devtools-mcp": {
      "source_type": "npm",
      "package": "chrome-devtools-mcp",
      "version": "1.1.1",
      "integrity": "sha512-...",
      "tarball": "https://registry.npmjs.org/chrome-devtools-mcp/-/chrome-devtools-mcp-1.1.1.tgz",
      "engines": {"node": "^20.19.0 || ^22.12.0 || >=23"}
    }
  }
}
```

Release checks must fail if catalog and lockfile are out of sync.

## Install And Update Flow

Install/update is a reconcile operation:

```text
desired = catalog + lock + OS + enabled clients
current = installed-state + client configs + artifact cache
plan = diff(desired, current)
stage artifacts
write client configs
healthcheck providers
commit installed-state
```

Actions:

- `install`
- `update`
- `reconfigure`
- `disable`
- `remove`
- `healthcheck`
- `noop`

Rules:

1. If a NEXO-owned provider has a newer locked version, update silently.
2. If update fails healthcheck, roll back to the previous healthy version.
3. If rollback is impossible, disable only that capability and report diagnostics.
4. If the user manually added an MCP with the same name but no NEXO ownership marker, do not touch it.
5. Changes that require client restart should be marked for Desktop/Brain restart notification.

## Removal Flow

When a provider or capability leaves the catalog:

1. Confirm the client entry is NEXO-owned.
2. Remove or disable the client entry.
3. Stop active provider processes.
4. Keep artifacts and rollback state for retention.
5. Purge later by cleanup policy.

Default is disable-first, not immediate deletion.

## Client Config Ownership

Every managed entry must be attributable to NEXO.

Claude JSON-style config:

```json
{
  "mcpServers": {
    "nexo_chrome_control": {
      "command": "/Users/example/.nexo/runtime/bin/nexo-managed-mcp",
      "args": ["run", "chrome_control"],
      "env": {"NEXO_HOME": "/Users/example/.nexo"}
    }
  },
  "nexo": {
    "managed_mcp": {
      "schema": "nexo.managed_mcp.client.v1",
      "servers": {
        "nexo_chrome_control": {
          "owner": "nexo",
          "capability_id": "chrome_control",
          "provider_id": "chrome-devtools-mcp",
          "config_digest": "sha256:..."
        }
      }
    }
  }
}
```

Codex TOML-style config:

```toml
[mcp_servers.nexo_chrome_control]
command = "/Users/example/.nexo/runtime/bin/nexo-managed-mcp"
args = ["run", "chrome_control"]

[nexo.managed_mcp.servers.nexo_chrome_control]
owner = "nexo"
capability_id = "chrome_control"
provider_id = "chrome-devtools-mcp"
config_digest = "sha256:..."
```

If a client does not tolerate metadata in config, store ownership in `installed-state.json` and use deterministic server names.

## Security Policy

The defaults are powerful, so policy must be implemented in NEXO, not delegated to users.

### Risk Levels

| Capability | Risk | Required controls |
|---|---|---|
| `chrome_control` | high | Browser profile policy, visible session when acting on real accounts, domain awareness. |
| `desktop_control` | high | OS permission check, visible session, task-scoped operation, kill switch. |
| `power_control` | critical | Command/path policy, action audit, confirmation for destructive/irreversible actions. |

### Sensitive Actions

Require explicit user confirmation unless already covered by a scoped authorization:

- Send email/messages.
- Purchase/pay/post publicly.
- Install apps/packages.
- Delete or overwrite user files.
- Change budgets/campaigns/accounts/security settings.
- Enter passwords, 2FA, private keys, payment data.
- Run privileged commands.

NEXO may prepare the action, but must ask before executing the irreversible step.

### Command And File Rules

`power_control` must be wrapped:

- Prefer argv arrays over free-form shell strings.
- Use fixed cwd and cleaned env.
- Apply timeout.
- Block `sudo`, `rm -rf`, `curl | sh`, remote shells, credential tools, and privileged installers unless approved.
- Resolve real paths and block sensitive paths such as `.ssh`, keychains, browser profiles, token stores, mail stores, backups, and `.env` unless explicitly approved.

## OS Permission Model

macOS:

- `desktop_control` needs Accessibility and Screen Recording for real desktop operation.
- Missing permission state is `needs_permission`, not provider failure.
- Desktop should guide the user through permission onboarding.

Windows:

- Do not require Administrator by default.
- Prefer UI Automation over raw coordinates.
- Do not operate elevated windows unless explicitly approved.
- OCR/screenshot fallback is allowed when UIA cannot see a control.

## Healthchecks

| Capability | Provider | Healthcheck |
|---|---|---|
| `chrome_control` | `chrome-devtools-mcp` | Start, list tools, open/attach Chrome according to policy, simple page check. |
| `desktop_control` | `mac-use-mcp` | Permission check, screenshot, list/focus harmless app. |
| `desktop_control` | `native-devtools-mcp` | Start, list tools, screenshot/find_text/window check. |
| `desktop_control` | `open-computer-use` | Start/doctor if available, list apps, safe no-op. |
| `power_control` | `desktop-commander` | Start, safe command in temp dir, prove blocked command/path policy. |

Health states:

- `healthy`
- `needs_permission`
- `missing_dependency`
- `unsupported_platform`
- `failed`
- `disabled_by_policy`

## Release Gates

Release verification must check:

1. Catalog schema valid.
2. Lockfile current.
3. Every default capability has a provider for macOS and Windows.
4. Every provider has healthcheck and risk policy.
5. Desktop-managed Node satisfies provider engine constraints.
6. No client config uses `@latest`.
7. Client merge tests pass for Claude Code, Claude Desktop, and Codex.
8. Removal tests preserve user-owned MCPs.
9. Privacy check finds no secrets or personal paths.

## Tests

Unit tests:

- Provider selection by OS.
- Lockfile parsing.
- Client config merge.
- Ownership metadata.
- Remove only NEXO-owned entries.
- Sensitive action classification.

Integration tests with temp HOME:

- Fresh install configures all default capabilities.
- Existing user MCPs survive.
- Provider update rolls forward.
- Failed update rolls back.
- Catalog removal disables only NEXO-owned entries.
- Missing OS permission reports `needs_permission`.

Manual smoke:

- macOS Chrome + mac-use + Desktop Commander.
- Windows Chrome + native-devtools/open-computer-use + Desktop Commander.
- Kill switch stops active desktop session.
- Dangerous commands are blocked.

## Implementation Phases

1. Add `src/managed_mcp/` with catalog/lock schema and dry-run reconcile.
2. Add client config merge for Claude and Codex.
3. Add `nexo-managed-mcp` runner.
4. Wire install/update through `bin/nexo-brain.js` and `src/plugins/update.py`.
5. Add healthchecks, rollback, and installed-state.
6. Add Desktop permission/restart/kill-switch surface.
7. Add release gates and full macOS/Windows smoke.

## Acceptance Criteria

The feature is ready when:

1. Fresh install configures `nexo_core`, `chrome_control`, `desktop_control`, and `power_control`.
2. Update silently upgrades NEXO-owned managed providers to locked versions.
3. Removal from catalog disables/removes only NEXO-owned entries.
4. Manual MCPs survive.
5. macOS and Windows both have supported provider paths.
6. Missing permissions do not break client startup.
7. Sensitive actions are gated.
8. Kill switch works for desktop sessions.
9. Release fails if catalog/lock/platform coverage is incomplete.
