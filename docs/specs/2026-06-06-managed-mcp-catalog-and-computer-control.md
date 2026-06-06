# Managed MCP Catalog And Computer Control

**Date:** 2026-06-06
**Status:** Product specification
**Owner:** NEXO Brain as source of truth; NEXO Desktop as managed installer/runtime surface
**Scope:** Brain, Desktop, Claude Code, Claude Desktop, Codex

## Goal

NEXO needs an internal managed catalog of MCP servers that can be installed, configured, updated, disabled, and removed automatically across supported clients.

The catalog is not a user marketplace and does not expose a normal-user management panel. It is a product-controlled capability layer: NEXO decides which MCPs are installed, which provider is correct for each operating system, and when a managed MCP should be updated or removed.

The first normal-user target is:

1. Chrome control.
2. Desktop/computer control.
3. Power control for files, terminal, processes, and app/system operations.

## Core Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Source of truth | Brain repo | Brain already owns client sync and runtime contracts. |
| Desktop role | Package, trigger, report | Desktop should not duplicate MCP config logic. |
| User panel | None for MVP | This is a NEXO-managed internal capability, not a marketplace. |
| Install model | Declarative reconciliation | Install/update/remove should be derived from catalog state. |
| Version model | Resolved lockfile, no client-side `@latest` | Clients run exact reviewed versions; release/update refreshes lock. |
| Updates | Silent for NEXO-owned MCPs | If NEXO installed it and healthchecks pass, update without user friction. |
| Removal | Ownership-aware disable-first | Never remove manual user MCPs by name collision. |
| Platform support | Capability first, provider second | `desktop_control` means the same product capability on Mac and Windows even when package names differ. |
| Normal-user default | Chrome + desktop control + Desktop Commander | Francisco explicitly wants these installed for normal users because NEXO can control the risk better than manual MCP setup. |

## Non-Goals

- No public MCP marketplace.
- No user-facing "install any MCP" panel.
- No direct edits to installed `~/.nexo/core`.
- No unmanaged `@latest` entries in Claude or Codex config.
- No deletion of user-added MCPs that are not marked as NEXO-owned.
- No NEXO Credits, billing, support-app, or local model selector changes in this feature.

## Product Model

The product model uses stable capability IDs. Each capability can have one or more providers, selected by OS, client, risk policy, and availability.

```json
{
  "capabilities": {
    "chrome_control": {
      "label": "Chrome control",
      "default": true,
      "providers": {
        "darwin": ["chrome-devtools-mcp"],
        "win32": ["chrome-devtools-mcp"]
      }
    },
    "desktop_control": {
      "label": "Desktop control",
      "default": true,
      "providers": {
        "darwin": ["mac-use-mcp", "native-devtools-mcp"],
        "win32": ["native-devtools-mcp", "open-computer-use"]
      }
    },
    "power_control": {
      "label": "Power control",
      "default": true,
      "providers": {
        "darwin": ["desktop-commander"],
        "win32": ["desktop-commander"]
      }
    }
  }
}
```

The user experience is capability-based:

- "NEXO can use Chrome."
- "NEXO can use the computer."
- "NEXO can manage files, apps, commands, and processes when needed."

The user should not need to understand whether the current machine uses `mac-use-mcp`, `native-devtools-mcp`, or another provider.

## Initial Managed Catalog

### Required Normal-User Capabilities

| Capability | macOS provider | Windows provider | Purpose |
|---|---|---|---|
| `chrome_control` | `chrome-devtools-mcp` | `chrome-devtools-mcp` | Inspect and operate Chrome with DevTools-level browser control. |
| `desktop_control` | `mac-use-mcp` first; fallback `native-devtools-mcp` | `native-devtools-mcp` first; fallback `open-computer-use` | Use visible desktop apps through screen, accessibility tree, keyboard, mouse, windows. |
| `power_control` | `desktop-commander` | `desktop-commander` | Files, terminal, process control, app/system operations. |

### Candidate Provider Notes

| Provider | Current role | Platform notes | Risk |
|---|---|---|---|
| `chrome-devtools-mcp` | Default Chrome provider | Mac + Windows if Chrome and Node runtime are valid | Medium-high because it can inspect pages and browser state. |
| `mac-use-mcp` | Default macOS desktop provider | macOS-only; requires Accessibility and Screen Recording | High because it controls screen, keyboard, mouse, clipboard, apps. |
| `native-devtools-mcp` | Preferred Windows desktop provider candidate; macOS fallback candidate | macOS + Windows + Android; supports UIA/AX/OCR/CDP | High because it controls native apps and browser/electron surfaces. |
| `open-computer-use` | Windows fallback candidate | macOS + Linux + Windows; needs further NEXO evaluation | High because it is broad computer-use automation. |
| `desktop-commander` | Power-control provider | Cross-platform package; high terminal/files/process power | Critical because it can mutate files, run commands, and manage processes. |

### Provider Selection Rules

Provider selection is deterministic:

1. Detect OS and architecture.
2. Check package support and runtime requirements.
3. Check local prerequisites such as Node version, Chrome availability, OS permission state, and client support.
4. Select the first provider that passes preflight.
5. If no provider passes, mark capability `unavailable` with actionable diagnostics.

NEXO must be able to change providers by release without changing the user-facing capability name.

## Architecture

### Brain Components

Add a Brain package, tentatively:

```text
src/managed_mcp/
  __init__.py
  catalog.py
  lock.py
  resolver.py
  reconcile.py
  client_config.py
  health.py
  policy.py
  state.py
```

Responsibilities:

- Load shipped catalog.
- Resolve latest package versions during release/update.
- Produce and validate lockfile.
- Compute install/update/remove/reconfigure plan.
- Write Claude/Codex configs using existing client sync conventions.
- Preserve user-owned MCPs.
- Run healthchecks.
- Record state, audit events, and rollback points.

### Existing Integration Points

| Existing file | Change |
|---|---|
| `src/client_sync.py` | Merge NEXO-managed MCP entries for Claude Code, Claude Desktop, and Codex. Preserve non-managed entries. |
| `src/plugins/update.py` | Run managed MCP reconciliation after packaged client sync. |
| `bin/nexo-brain.js` | Run managed MCP install/sync during install/configure. |
| `src/mcp_live_audit.py` | Extend audit/status to report managed MCP health and drift. |
| `src/system_catalog.py` | Expose managed MCP capabilities as product capabilities, not user-installed random tools. |
| `tool-enforcement-map.json` | Add any new internal NEXO tools only after implementation. |

### Desktop Components

Desktop should not own MCP state. It can:

- Package Brain catalog and lockfile through existing `brain-bundle`.
- Trigger Brain install/update/sync.
- Show internal diagnostics if needed.
- Surface OS permission onboarding for Accessibility, Screen Recording, or Windows automation permissions.
- Show stop/kill controls for active computer-control sessions.

Desktop should not implement its own catalog resolver.

## Runtime State

State lives under runtime, not product source and not personal loose files:

```text
~/.nexo/runtime/managed-mcp/
  catalog.snapshot.json
  lock.json
  installed-state.json
  operations/
    2026-06-06T095000Z-reconcile.jsonl
  artifacts/
    chrome-devtools-mcp/
      1.1.1/
    mac-use-mcp/
      1.1.1/
    desktop-commander/
      0.2.42/
  rollback/
    <operation-id>/
```

`installed-state.json` is sufficient for MVP. SQLite can be introduced later if the state becomes query-heavy.

## Catalog Schema

The catalog is shipped in Brain source, for example:

```text
src/managed_mcp/catalog.json
```

Minimal schema:

```json
{
  "schema": "nexo.managed_mcp.catalog.v1",
  "catalog_version": "2026.06.06",
  "defaults_profile": "normal_user",
  "capabilities": [
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
          "source": {
            "type": "npm",
            "package": "mac-use-mcp"
          },
          "version_policy": "latest_on_release",
          "transport": "stdio",
          "command_template": {
            "command": "{nexo_node_runner}",
            "args": ["mac-use-mcp@{version}"]
          },
          "preflight": {
            "node": ">=22",
            "os_permissions": ["accessibility", "screen_recording"]
          },
          "healthcheck": {
            "type": "mcp_tools",
            "required_tools": ["check_permissions", "screenshot", "open_application"]
          }
        }
      ],
      "policy": {
        "session_required": true,
        "visible_session": true,
        "kill_switch_required": true,
        "sensitive_action_confirmation": true
      }
    }
  ]
}
```

The command template must not write `@latest` into client config. `{version}` comes from the lockfile.

## Lockfile Schema

The lockfile is the exact executable contract.

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
      "engines": {
        "node": "^20.19.0 || ^22.12.0 || >=23"
      }
    }
  }
}
```

The lockfile can be generated during release preparation and refreshed during product update, but installed client configs should always reference exact versions.

## Release And Update Policy

### Release-Time Resolution

Every Brain/Desktop release must include a managed MCP refresh step:

1. Read `catalog.json`.
2. Resolve the latest allowed provider versions.
3. Fetch integrity/tarball metadata.
4. Validate package engine constraints against Desktop-managed runtimes.
5. Run package-level smoke checks where possible.
6. Update `lock.json`.
7. Include the lockfile in release verification output.

This makes the release carry the newest reviewed MCP versions.

### Runtime Silent Updates

During NEXO install/update:

1. Load shipped catalog and lockfile.
2. Compare desired provider version with installed NEXO-owned version.
3. If newer, stage the new artifact.
4. Run preflight and healthcheck.
5. Reconfigure Claude/Codex.
6. Mark active state.
7. Preserve rollback state.

If update fails:

- Do not break client startup.
- Roll back to previous working provider when possible.
- Otherwise disable only that managed provider and report diagnostics.

### Background Updates

MVP does not require always-on background package polling. If added later, background polling may create candidate locks but must not silently promote a version that has not passed NEXO checks.

## Install Flow

Install is a reconcile operation:

```text
desired = catalog + lock + platform + client preferences
current = installed-state + client configs + artifact cache
plan = diff(desired, current)
apply(plan)
healthcheck(plan)
commit_state(plan)
```

Plan actions:

- `install`
- `update`
- `remove`
- `disable`
- `reconfigure`
- `healthcheck`
- `noop`

Operations must be sequential per user profile to avoid concurrent config writes.

## Removal Flow

Removal must be disable-first:

1. Confirm provider was installed by NEXO using ownership metadata.
2. Remove or disable the client config entry.
3. Stop active provider processes where applicable.
4. Keep artifacts and rollback state for retention period.
5. Purge only after retention or explicit cleanup policy.

If a provider name exists but lacks NEXO ownership metadata, treat it as user-owned and do not modify it.

## Ownership Metadata

Every managed client entry must include enough metadata to prove ownership.

### JSON Clients

For Claude Code and Claude Desktop JSON surfaces:

```json
{
  "mcpServers": {
    "nexo_desktop_control": {
      "command": "/path/to/nexo-managed-mcp-runner",
      "args": ["run", "desktop_control"],
      "env": {
        "NEXO_HOME": "/Users/example/.nexo"
      }
    }
  },
  "nexo": {
    "managed_mcp": {
      "schema": "nexo.managed_mcp.client.v1",
      "catalog_version": "2026.06.06",
      "servers": {
        "nexo_desktop_control": {
          "owner": "nexo",
          "capability_id": "desktop_control",
          "provider_id": "mac-use-mcp",
          "config_digest": "sha256:..."
        }
      }
    }
  }
}
```

If a client does not tolerate top-level metadata, store metadata in NEXO runtime state and use a deterministic server name plus digest.

### Codex TOML

For Codex:

```toml
[mcp_servers.nexo_desktop_control]
command = "/path/to/nexo-managed-mcp-runner"
args = ["run", "desktop_control"]

[nexo.managed_mcp.servers.nexo_desktop_control]
owner = "nexo"
capability_id = "desktop_control"
provider_id = "native-devtools-mcp"
config_digest = "sha256:..."
catalog_version = "2026.06.06"
```

Codex config writes must be serialized. Do not run parallel `codex mcp add/remove` mutations against the same config file.

## Client Configuration Strategy

Prefer a NEXO runner command over raw package commands in client configs.

Recommended client entry:

```json
{
  "command": "/Users/example/.nexo/runtime/bin/nexo-managed-mcp",
  "args": ["run", "desktop_control"],
  "env": {
    "NEXO_HOME": "/Users/example/.nexo",
    "NEXO_MCP_CLIENT": "claude_code"
  }
}
```

Why:

- Hides package-specific paths from client configs.
- Allows provider swaps without rewriting every client entry shape.
- Centralizes logging, preflight, permission checks, environment cleanup, and kill switch integration.
- Prevents `npx @latest` from being the live runtime contract.

The runner resolves the provider from `installed-state.json`.

## Security Policy

The product intentionally enables high-power capabilities. Therefore safety belongs in NEXO policy and runtime controls, not in user-managed MCP sprawl.

### Capability Risk Classes

| Capability | Risk | Examples | Required controls |
|---|---|---|---|
| Chrome control | High | Read web pages, inspect browser, click forms | Session visible, browser profile policy, domain awareness. |
| Desktop control | High | Click/type/read screen, operate apps | OS permissions, visible session, kill switch, sensitive-action confirmation. |
| Power control | Critical | Terminal, files, processes, installs | Command policy, path policy, confirmation for irreversible actions, audit log. |

### Sensitive Actions

Sensitive actions require explicit confirmation or an existing scoped authorization:

- Sending email/messages.
- Purchases or payments.
- Installing apps or packages.
- Deleting files outside temporary managed areas.
- Entering passwords, 2FA, recovery codes, private keys, or payment data.
- Posting publicly.
- Changing security/privacy settings.
- Running privileged commands.
- Accessing private apps such as Mail, Messages, banking, legal, health, or client data beyond the requested task.

NEXO may navigate to the step, prepare the action, and ask for confirmation before the irreversible step.

### Desktop Session Rules

Desktop/computer-control sessions must be:

- Visible to the user when operating the real desktop.
- Time-bounded.
- Stoppable through Desktop kill switch.
- Logged at tool-call level.
- Scoped to the current task.
- Blocked from password fields and hidden credential prompts where detectable.

### Filesystem Rules

For `power_control`, file operations must:

- Resolve real paths before access.
- Respect approved roots.
- Block known sensitive paths by default: `.ssh`, keychains, browser profiles, token stores, mail stores, backup vaults, `.env` files unless specifically approved.
- Preserve user-owned data unless the task requires a specific mutation.

### Command Rules

Terminal/process operations must:

- Prefer argv arrays, not free-form shell strings.
- Use fixed cwd.
- Use cleaned environment.
- Time out by default.
- Block `sudo`, credential tools, destructive recursive deletes, `curl | sh`, remote shells, and privileged installers unless explicitly approved.
- Record command, cwd, exit code, duration, and truncated output.

### Browser Rules

Chrome control should support two modes:

1. **Managed/isolated profile**: default for general browsing and testing.
2. **Real user profile**: only when the task requires existing login/session state.

Real-profile use must be explicit because cookies, sessions, email, and private websites are high-impact data.

## OS Permission Model

### macOS

Computer control providers need permissions such as:

- Accessibility.
- Screen Recording.
- Automation/Input control depending on provider behavior.

NEXO Desktop should guide permission onboarding. If permissions are missing, provider health is `permission_missing`, not `failed`.

### Windows

Windows provider should use UI Automation and standard input APIs where possible.

Rules:

- Do not require Administrator by default.
- Do not interact with elevated windows unless provider is explicitly running elevated and the task was approved.
- Prefer UIA element targeting over screenshot coordinates when available.
- Use OCR fallback only when UIA cannot see the control.

## Healthchecks

Every provider needs a provider-specific healthcheck:

| Provider | Healthcheck |
|---|---|
| `chrome-devtools-mcp` | Start server, list tools, open isolated Chrome/session or attach according to policy, screenshot/simple navigation. |
| `mac-use-mcp` | `check_permissions`, list windows, screenshot, open/focus harmless app. |
| `native-devtools-mcp` | `verify` if available, list tools, screenshot, find_text/window check. |
| `open-computer-use` | `doctor`, list apps, safe no-op call. |
| `desktop-commander` | List tools, read allowed temp dir, run safe command, verify blocklist/path policy wrapper. |

Health result states:

- `healthy`
- `needs_permission`
- `missing_dependency`
- `unsupported_platform`
- `failed`
- `disabled_by_policy`

## Audit And Observability

Each reconcile operation writes an operation log:

```json
{
  "operation_id": "mcpop-20260606-095000",
  "started_at": "2026-06-06T09:50:00Z",
  "source": "nexo_update",
  "plan": ["update chrome_control", "install desktop_control"],
  "results": {
    "chrome_control": "healthy",
    "desktop_control": "needs_permission"
  }
}
```

Runtime audit should answer:

- Which capabilities are installed?
- Which provider is selected for each capability?
- Which version is installed?
- Which clients are configured?
- Is each capability healthy?
- Why is a capability unavailable?
- What changed during the last update?

## NEXO Tools

Optional internal tools after implementation:

- `nexo_managed_mcp_status`
- `nexo_managed_mcp_reconcile`
- `nexo_managed_mcp_doctor`
- `nexo_managed_mcp_catalog`
- `nexo_computer_control_session_stop`

These are internal operational tools, not user marketplace controls.

## Release Gates

Add release verification checks:

1. Catalog schema validates.
2. Lockfile is current for catalog.
3. No client config template uses `@latest`.
4. Every default capability has at least one provider per supported OS.
5. Every provider has healthcheck definition.
6. Every high/critical capability has policy controls.
7. Desktop-managed Node runtime satisfies provider engine constraints.
8. Client config merge tests pass for Claude Code, Claude Desktop, and Codex.
9. Removal tests prove user-owned MCPs are preserved.
10. Privacy check proves no secrets or personal paths are shipped.

## Test Plan

### Unit Tests

- Catalog schema validation.
- Provider selection by OS.
- Lockfile resolution and integrity parsing.
- Client config merge for JSON and TOML.
- Ownership metadata detection.
- Remove only NEXO-owned entries.
- Policy classification for sensitive actions.

### Integration Tests

Use disposable HOME directories:

- Install into empty Claude/Codex config.
- Merge with existing user MCPs.
- Update from provider version A to B.
- Remove capability from catalog and confirm disable-first behavior.
- Simulate failed healthcheck and verify rollback.
- Simulate missing OS permissions and verify `needs_permission`.

### Manual Smoke Tests

macOS:

- Chrome opens/attaches according to policy.
- mac-use provider can list windows and screenshot after permissions.
- Desktop Commander can run safe command and is blocked from disallowed command.
- Kill switch stops active session.

Windows:

- Chrome provider works.
- Windows desktop provider can list apps/windows, click/type in a harmless app, and quit app.
- Desktop Commander safe command works and blocked command is blocked.
- No admin privilege required for normal smoke.

## Implementation Plan

### Phase 1: Spec To Product Skeleton

- Add `src/managed_mcp/`.
- Add catalog and lockfile schema.
- Add dry-run reconcile plan.
- Add tests for catalog/provider selection/client merge.

### Phase 2: Client Sync

- Extend `client_sync.py` to merge managed entries.
- Add ownership metadata support.
- Add Codex TOML writer tests.
- Add Claude JSON writer tests.

### Phase 3: Runtime Runner

- Add `nexo-managed-mcp` runner.
- Resolve provider from installed state.
- Clean env and start provider.
- Add operation logs.

### Phase 4: Install/Update Integration

- Wire into `bin/nexo-brain.js`.
- Wire into `src/plugins/update.py`.
- Add release check for catalog/lock freshness.
- Add silent update and rollback.

### Phase 5: Computer-Control Safety

- Add session policy enforcement.
- Add Desktop kill switch integration.
- Add sensitive-action gates.
- Add macOS permission onboarding status.
- Add Windows provider preflight.

### Phase 6: Product Hardening

- Add `nexo_managed_mcp_status` and doctor output.
- Add full macOS/Windows smoke matrix.
- Add release notes and operator docs.

## Open Questions

| Question | Initial answer |
|---|---|
| Should Chrome use isolated profile by default? | Yes, except when task requires existing login/session. |
| Should Desktop Commander be available to normal users? | Yes, per Francisco, but NEXO wraps and gates critical actions. |
| Should `mac-use-mcp` be installed on Windows? | No. Install equivalent provider for `desktop_control`. |
| Should NEXO expose MCP settings UI? | No for MVP. Status/diagnostics only if needed. |
| Should release update always take newest provider version? | Yes, but through resolved lock + verification, not client `@latest`. |
| Should runtime silently update providers? | Yes for NEXO-owned providers when healthchecks pass. |

## Acceptance Criteria

The feature is complete when:

1. A fresh Desktop-managed install configures NEXO, Chrome control, desktop control, and power control for the current OS.
2. Claude Code, Claude Desktop, and Codex receive equivalent managed capabilities where supported.
3. A Brain/Desktop update silently updates NEXO-owned MCP providers to locked versions.
4. Removing a provider/capability from the catalog disables only NEXO-owned entries.
5. Manual user MCPs survive install, update, and removal.
6. macOS and Windows both have a `desktop_control` provider path.
7. Missing OS permissions are reported clearly and do not break client startup.
8. Sensitive actions are gated.
9. Kill switch stops active computer-control sessions.
10. Release verification fails if catalog, lock, policy, or platform coverage is incomplete.

## References

- MCP intro and concepts: https://modelcontextprotocol.io/docs/getting-started/intro
- MCP security best practices: https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices
- Claude Code MCP docs: https://code.claude.com/docs/en/mcp
- Chrome DevTools MCP: https://developer.chrome.com/blog/chrome-devtools-mcp
- mac-use-mcp: https://github.com/antbotlab/mac-use-mcp
- Desktop Commander MCP: https://github.com/wonderwhy-er/DesktopCommanderMCP
- native-devtools-mcp: https://github.com/sh3ll3x3c/native-devtools-mcp
- open-computer-use: https://github.com/iFurySt/open-codex-computer-use
