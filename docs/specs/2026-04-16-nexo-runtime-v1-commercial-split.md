# NEXO Runtime v1 and Commercial Repo Split

Date: 2026-04-16
Status: proposed
Scope owner: NEXO

## Why This Exists

NEXO Desktop already works as a product shell, but it is still tightly coupled to the current agent engine and install path.

The product goal is higher:

- non-technical onboarding
- zero terminal for end users
- Desktop that can survive provider changes later
- commercial login, subscription, and entitlement control
- Mac and Windows support
- safe coexistence between Francisco's stable Desktop and a new in-progress architecture

Without an explicit split now, the risk is building a commercial layer directly on top of today's Claude Code coupling and then paying for that shortcut later.

## Current Repo Reality

Today there are already two separate repos:

1. `wazionapps/nexo`
   - local path: `/Users/franciscoc/Documents/_PhpstormProjects/nexo`
   - remote: `https://github.com/wazionapps/nexo.git`
   - contains the OSS runtime, MCP server, onboarding bridge, docs, blog, release assets, adapters, and packaging logic

2. `wazionapps/nexo-desktop`
   - local path: `/Users/franciscoc/Documents/_PhpstormProjects/nexo-desktop`
   - remote: `https://github.com/wazionapps/nexo-desktop.git`
   - contains the Electron app, updater, renderer, preload, local installer helpers, and Desktop UX

Important observations from the current codebase:

- `nexo` already exposes a machine-readable Desktop bridge in `src/desktop_bridge.py`
- `nexo-desktop` already renders `nexo onboard --json` dynamically
- `nexo` still declares `@anthropic-ai/claude-code` as a runtime dependency
- `nexo-desktop` still describes itself as a private chat UI on top of Claude Code
- `nexo-desktop` today is Mac-first in packaging

## Core Decision

Do not split the public OSS repo into multiple GitHub repos yet.

Instead:

- keep `wazionapps/nexo` as the public OSS source of truth
- keep `wazionapps/nexo-desktop` as the Desktop app repo
- introduce logical package boundaries inside `nexo`
- create a new private repo only when the commercial backend begins implementation

This minimizes migration risk while still enforcing the correct architecture.

## Repo Strategy

### Keep As-Is

1. `wazionapps/nexo`
   - remains the public OSS repo
   - becomes the home of `NEXO Runtime v1` contracts and engine interfaces

2. `wazionapps/nexo-desktop`
   - remains the Desktop app repo
   - becomes the consumer of the runtime facade instead of talking directly to the current engine

### Create Later, Not Now

3. `wazionapps/nexo-commercial-backend` or equivalent private repo
   - auth
   - subscriptions
   - licenses
   - entitlement flags
   - device registration
   - provider proxying
   - billing telemetry

### Explicitly Avoid Right Now

- do not create `nexo-core` as a new public repo yet
- do not rename the existing `nexo` repo
- do not split Desktop into a second app repo yet
- do not start with a private fork of OSS and manually replicate changes

The correct first move is package and contract separation, not remote sprawl.

## Logical Split Inside `nexo`

Inside the existing public repo, create a clearer boundary between runtime, install contracts, and OSS-facing wrappers.

Recommended direction:

```text
nexo/
  bin/
    nexo.js
    nexo-brain.js
  src/
    runtime/
      core/
      adapters/
      sessions/
      tools/
      provider_routing/
    install/
      status.py
      plan.py
      apply.py
      contracts.py
    desktop/
      bridge.py
    cognitive/
    db/
    doctor/
    plugins/
    rules/
  docs/specs/
  tests/
```

This is a logical split first. It does not require breaking the public repo apart.

## Runtime Model

The architecture must separate three concerns:

1. Desktop shell
   - Electron UI
   - onboarding screens
   - updater
   - notifications

2. Agent runtime
   - sessions
   - tool execution
   - filesystem reads/writes
   - shell execution
   - search
   - orchestration

3. Model provider
   - Anthropic
   - OpenAI
   - Ollama
   - future providers

This means:

- Desktop should talk to `NEXO Runtime`
- `NEXO Runtime` may use Claude Code today
- `NEXO Runtime` may use something else later
- provider choice must not be hardcoded into Desktop

## NEXO Runtime v1

### Goal

Introduce a stable interface between Desktop and the current agent engine.

### First Implementation

The first runtime implementation should still use Claude Code underneath.

That is acceptable.

The critical improvement is not changing the engine immediately.
The critical improvement is hiding the engine behind a NEXO-owned contract.

### Minimum Surface

Desktop should target a runtime facade with methods such as:

- `runtime.status()`
- `runtime.health()`
- `runtime.session.start()`
- `runtime.session.resume(session_id)`
- `runtime.session.stop(session_id)`
- `runtime.message.send(session_id, input)`
- `runtime.tools.list()`
- `runtime.install.status()`
- `runtime.install.plan()`
- `runtime.install.apply()`

The exact transport can be:

- local child process
- local HTTP server
- local IPC bridge

For v1, local process or local HTTP is enough.

## Desktop Contract Direction

Desktop should not parse CLI prompts as the main product contract.

Prompt relay can remain as a transitional fallback, but the primary path should become:

- `install-status`
- `install-plan`
- `install-apply`
- `onboard`
- `health`

All machine-readable.

## Commercial Backend Role

The commercial backend is not the runtime.

Its role is:

- login
- plan validation
- subscription checks
- entitlement flags
- device registration
- provider proxying
- usage metering

It should not be responsible for local filesystem execution.

The local agent runtime still handles:

- reading files
- writing files
- searching code
- running commands
- maintaining sessions

## Provider Strategy

The commercial product should eventually let Desktop remain unchanged while the provider stack changes underneath.

Valid intermediate states:

1. `Desktop -> NEXO Runtime -> Claude Code -> Anthropic`
2. `Desktop -> NEXO Runtime -> Claude Code -> gateway -> OpenAI`
3. `Desktop -> NEXO Runtime -> Codex adapter -> OpenAI`
4. `Desktop -> NEXO Runtime -> future native runtime -> provider adapters`

For end-user UX, the provider should be abstracted away unless product strategy explicitly wants to show it.

## Production and Development Coexistence

Francisco must be able to keep his current stable Desktop while the new architecture is built.

That requires a fully isolated development variant:

- `NEXO Desktop` for production
- `NEXO Desktop Dev` for development

These must not share:

- app ID / bundle ID
- updater feed
- `userData`
- runtime root
- logs
- sockets
- keychain service names
- background service names

Suggested runtime roots:

- production: `~/.nexo`
- development: `~/.nexo-dev`

## Cross-Platform Rule

Mac and Windows support must be designed in from the start.

Do not let runtime or install contracts expose Mac-specific implementation details.

Instead define abstract actions such as:

- `ensure_background_service`
- `store_credentials`
- `request_permission`
- `register_autostart`

Platform executors then map them to:

- macOS: Keychain, LaunchAgents, DMG
- Windows: Credential Manager, Task Scheduler or Service, EXE/MSIX/NSIS

## Recommended Next Repos and When

### Now

No new remote repo required.

Work inside:

- `nexo`
- `nexo-desktop`

### When Commercial Auth Starts

Create:

- `nexo-commercial-backend` private repo

### Only If Needed Later

Create additional repos only after package boundaries are stable and painful enough to justify them.

Examples of future optional repos:

- `nexo-runtime`
- `nexo-commercial-sdk`

But these are later decisions, not today's move.

## Implementation Order

### Phase 0

Introduce `NEXO Runtime v1` facade locally.

Deliverables:

- runtime interface definition
- first Claude Code adapter
- Desktop calls runtime facade instead of direct engine-specific logic

### Phase 1

Introduce install contracts.

Deliverables:

- `install-status`
- `install-plan`
- `install-apply`
- Desktop consumes them

### Phase 2

Create isolated Desktop Dev app.

Deliverables:

- separate bundle ID
- separate update channel
- separate runtime root
- safe production/dev coexistence

### Phase 3

Create private commercial backend.

Deliverables:

- login
- entitlements
- subscriptions
- device registration
- provider gateway

### Phase 4

Decide whether to keep Claude Code as engine, swap to a different engine, or build a native runtime.

## Success Criteria

- Desktop no longer depends directly on a single engine contract
- production and development can coexist safely on Francisco's machine
- install flow becomes machine-readable
- Mac and Windows differences are encapsulated below the contract layer
- commercial backend can be added later without rewriting Desktop again

## Immediate Recommendation

Start by writing and implementing `NEXO Runtime v1` inside the existing `nexo` repo.

Do not create multiple new repos yet.
Do not start with backend auth.
Do not fork the OSS runtime into a manually mirrored commercial copy.

The next honest move is contract separation, not repo multiplication.
