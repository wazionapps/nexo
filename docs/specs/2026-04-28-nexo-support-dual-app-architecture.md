# NEXO Support Dual-App Architecture

**Date:** 2026-04-28
**Status:** Ready for implementation
**Goal:** Ship a rescue-grade `NEXO Support` app alongside `NEXO Desktop` without creating a fourth repository, while keeping the design portable to Windows and Linux.

## Problem

`NEXO Desktop` needs a recovery path for the exact scenario where a broken update, corrupted runtime, failed permission flow, or local environment issue prevents the main app from opening or functioning correctly.

The support surface must therefore:

- install together with Desktop,
- open even when Desktop is broken,
- reuse the same user identity and subscription checks,
- expose AI chat backed by `nexo-desktop-web`,
- allow explicit user-approved remote diagnosis and repair,
- support full shell access during a time-bounded session,
- generate structured incident reports when a core bug is found,
- stay compatible with the current macOS pilot while being architecturally neutral for Windows and Linux.

## Decision Summary

| Topic | Decision | Rationale |
|------|----------|-----------|
| Repository model | Keep the current three repos | Support is a product capability, not a separate product line yet |
| Visible desktop apps | Ship two visible apps: `NEXO Desktop` and `NEXO Support` | Support must remain available when Desktop cannot boot |
| Desktop repo ownership | Put both visible apps in `nexo-desktop` | Shared installer, auth storage, native permissions and packaging belong together |
| Backend ownership | Put support control-plane APIs in `nexo-desktop-web` | Auth, subscription, legal terms, audit and incidents already live there |
| Runtime ownership | Put diagnosis/repair primitives in `nexo` | Core product knowledge and machine repair logic must stay in the source-of-truth repo |
| Shell access | Allow full shell access inside an explicit support session | This is a hard product requirement, but it must be scoped, logged and revocable |
| Remote desktop GUI | Keep as phase 2 capability, not a phase 1 blocker | Shell/files/process repair covers the fastest path to value |
| Auth sharing | Use one shared credential namespace across both apps | Support must reuse Desktop login without forcing a second sign-in |
| Support token model | Exchange the normal Desktop session for an ephemeral support session token | Avoid storing long-lived high-privilege support credentials locally |
| Support LLM admin | Expose provider/model/reasoning/max-tokens as admin-editable raw fields with a real validation button | Operators need to swap Anthropic/OpenAI and exact model settings without code edits |
| Platform strategy | Design platform adapters now, release macOS first | Avoid hardcoding macOS assumptions before Windows/Linux launch |

## Repository Boundaries

### `nexo`

Owns:

- runtime knowledge model and generic troubleshooting contracts,
- structured diagnostic and repair routines for NEXO Brain and Desktop,
- reusable doctor/fix commands invoked locally by Support,
- runtime-safe repair policies and escalation rules.

Does not own:

- Electron UI,
- user login/session exchange,
- billing/subscription checks,
- web chat/control-plane APIs,
- customer support workflow state,
- remote support consent flows,
- support incidents, internal support emails or other commercial support logic.

## Open-Source Boundary For NEXO Brain

`nexo` is the open-source Brain/runtime repo. That boundary must stay explicit during this project.

Rule:

- if a capability is generic runtime behavior that makes sense for every Brain user, it can live in `nexo`,
- if a capability only exists for the managed product, paid support, remote support operations or customer workflow, it must stay out of `nexo`.

Allowed in `nexo`:

- health checks,
- portable runtime/install diagnostics,
- version/runtime/install metadata,
- log export helpers,
- repair commands with stable machine-readable output,
- generic recovery routines that any Brain install may need.

Not allowed in `nexo`:

- subscription, billing or entitlement checks,
- support chat orchestration,
- support-session lifecycle,
- remote-consent legal gates,
- support audit/event pipelines for the SaaS product,
- customer-specific support policy,
- internal support email routing or escalation workflow,
- product-only growth, CRM or analytics logic.

Practical test:

- if a feature would still make sense in the public open-source Brain without `NEXO Desktop` or `nexo-desktop-web`, it may belong in `nexo`,
- if it only exists because NEXO runs a managed commercial support surface, it belongs in Desktop/Web, not Brain.

### `nexo-desktop`

Owns:

- both visible desktop apps,
- support bootstrap and native shell bridge,
- shared auth persistence on-device,
- shared platform adapters,
- packaging, installer and update layout,
- local support host lifecycle.

Does not own:

- the authoritative support chat logic,
- support incident workflow state,
- long-lived support session policy.

### `nexo-desktop-web`

Owns:

- support bootstrap APIs,
- subscription enforcement,
- support session creation and revocation,
- support terms acceptance tracking,
- support chat orchestration,
- command and mutation audit trail,
- incident creation and incident email fan-out.

Does not own:

- OS shell execution,
- filesystem mutation on the client,
- direct product repair primitives.

## Why A Fourth Repo Is The Wrong Move Now

Do **not** create a new `nexo-support` repository at this stage.

That split would duplicate or fragment:

- Electron packaging and release plumbing,
- local auth and `device_uuid` handling,
- keychain/credential-store migrations,
- native permissions and support-host bootstrapping,
- macOS-first now / Windows/Linux-next platform work,
- product knowledge that already belongs to NEXO repos.

The right boundary is:

- separate visible app,
- separate app entrypoint,
- separate boot path,
- same desktop repository.

Create a fourth repo later only if all three become true:

1. Support has its own team and release cadence.
2. Support no longer shares the Desktop installer/update path.
3. Support grows into a standalone commercial product beyond NEXO Desktop recovery.

## Target System Topology

```text
User
  -> NEXO Support app
       -> shared credential store
       -> support bootstrap API (web)
       -> local support host
            -> shell / files / logs / processes / installers
            -> NEXO Brain doctor/repair routines
       -> support session stream (web)
            -> AI chat + KB retrieval + policy engine
            -> incident creation + audit trail

NEXO Desktop
  -> shares auth store, device identity and installer with NEXO Support
  -> can be launched, repaired, reconfigured or reinstalled by Support
```

## Desktop Product Model

The installed product becomes a dual-app package:

- `NEXO Desktop`: primary daily-use application.
- `NEXO Support`: rescue application for diagnosis, repair and guided recovery.

Recommended internal layout in `nexo-desktop`:

```text
nexo-desktop/
  apps/
    desktop/
    support/
  packages/
    shared-auth/
    shared-http/
    shared-os/
    shared-ui/
    support-host/
  build/
  resources/
```

This does **not** require a big-bang rewrite. The current root app can remain the Desktop implementation during transition while `apps/support/` and `packages/` are introduced first.

## Support Session Model

### Session levels

- `diagnose`: read-only system, logs, config, version and health access.
- `repair`: file/process/config mutation with normal user privileges.
- `shell_full`: unrestricted shell in the logged-in user's context.
- `admin_repair`: elevated actions only after native OS elevation prompt.
- `desktop_remote`: optional future level for GUI remote control.

### Required rules

- The user must explicitly press `Conectar`.
- A versioned support-terms document must be accepted before activation.
- Full shell access is session-scoped, not permanent.
- Every command, file mutation and privileged action is logged.
- The session must expose `Cut Access` and auto-expire.
- No long-lived `support:*` token is stored in shared credential storage.

## Credential And Identity Contract

Current Desktop storage is keyed to `com.nexo.desktop`. That is too app-specific for a dual-app product.

Decision:

- introduce a shared auth namespace, for example `com.nexo.shared-auth`,
- migrate existing Desktop credentials on first run,
- store the normal account token plus `device_uuid` in the shared namespace,
- let Support reuse that token for bootstrap,
- exchange it server-side for a short-lived support session token when a support session starts.

This preserves:

- same account,
- same device identity,
- same subscription rules,
- no forced second login if Desktop already authenticated successfully.

## Cross-Platform Contract

| Capability | macOS | Windows | Linux |
|-----------|-------|---------|-------|
| Shared credentials | Keychain | Credential Manager or DPAPI | Secret Service or libsecret |
| Shell runtime | `/bin/bash -lc` | PowerShell first, `cmd.exe` fallback | `/bin/bash -lc` |
| Log sources | app logs, system logs, permission state | app logs, Event Viewer, service state | app logs, `journalctl`, service state |
| Background/runtime helper | on-demand child process or LaunchAgent if needed | on-demand process or Scheduled Task/service later | on-demand process or `systemd --user` unit later |
| Package targets | DMG now, signed/notarized later | MSI or EXE later | AppImage/deb/rpm later |
| Elevation path | native admin prompt | UAC prompt | `pkexec` or distro-appropriate escalation |

The architecture must use platform adapters from day one. No Desktop or Support code should hardcode macOS-only storage, logging or lifecycle assumptions outside the mac adapter.

## Backend Control Plane

`nexo-desktop-web` must add a support control plane that sits next to the existing auth endpoints.

Responsibilities:

- bootstrap Support with the current authenticated user,
- verify subscription and support eligibility,
- register or resume support sessions,
- persist consent acceptance,
- stream AI chat state,
- mint short-lived support session tokens,
- store command and action audit events,
- create support incidents,
- email engineering when a core defect is detected.

The control plane must also own the support-LLM admin surface:

- `provider`,
- `model`,
- `reasoning_effort`,
- `max_tokens`,
- optional provider-specific extras such as `temperature` or `thinking_enabled`.

Rules:

- these are operator-entered raw values, not hardcoded enums,
- the admin UI must provide a `Check` action that performs a real provider call before save,
- the normal save path must require a successful `Check` using the current field values,
- the last validation result must be stored and visible,
- provider-specific coercion belongs in the provider adapter layer, not in the admin contract.

The current `abilities=["web"]` contract should remain the default login response. High-privilege support abilities must only exist inside ephemeral support session tokens.

## Runtime And Repair Ownership

`nexo` must expose the routines that actually know how to diagnose and repair NEXO:

- Desktop install health,
- Brain health,
- version mismatches,
- config corruption,
- permissions problems,
- missing runtime dependencies,
- failed updates,
- service/bootstrap issues,
- known-fix workflows.

Support should call structured local routines first and raw shell second. Full shell exists as an escape hatch and power tool, but NEXO-specific repair knowledge should still be encoded as explicit routines in the product runtime.

Important boundary:

- these routines must stay generic and open-source-safe,
- Support consumes them, but Brain must not absorb Desktop-only SaaS/support workflow logic just because Support happens to call into it.

## Incident Workflow

If Support finds a core defect:

1. record the failure locally in the support session stream,
2. create a structured support incident in `nexo-desktop-web`,
3. attach logs, versions, device metadata and attempted fix steps,
4. email the configured engineering/support address,
5. keep the incident linked to the support session and user.

This is separate from broad public service-status incidents. Customer repair cases need their own support incident model.

## Implementation Phases

### Phase 0: Foundation decisions

1. Freeze the repo boundary: no fourth repo.
2. Approve the dual-app product model.
3. Approve the shared credential namespace.
4. Approve support session levels and consent model.

### Phase 1: Desktop repo restructuring

1. Introduce `apps/support/`.
2. Introduce `packages/shared-auth/`, `packages/shared-http/`, `packages/shared-os/`, `packages/support-host/`.
3. Move auth storage behind a shared contract.
4. Keep current Desktop boot path intact while Support is built separately.

### Phase 2: Backend support control plane

1. Add support bootstrap/session/consent/audit/incident tables and APIs.
2. Add support token exchange.
3. Add support chat and audit stream endpoints.
4. Add engineering incident email flow.

### Phase 3: Local support execution

1. Implement the support host with shell/files/process/log access.
2. Bind support host capabilities behind platform adapters.
3. Add structured Desktop/Brain doctor and repair routines.
4. Ensure Support can reinstall, relaunch or reconfigure Desktop.

### Phase 4: Product knowledge and AI support

1. Index approved product docs and troubleshooting knowledge.
2. Feed support chat with repo docs plus runtime telemetry.
3. Prefer structured repair routines before raw shell actions.
4. Auto-open incidents when Support identifies core defects.

### Phase 5: Cross-platform hardening

1. Keep the first shipped build macOS-only if needed.
2. Finish Windows adapters without changing session or storage contracts.
3. Finish Linux adapters without changing session or storage contracts.
4. Add platform-specific installer/update smoke tests for both apps.

## First Implementation Slice

Start with the smallest slice that proves the architecture:

1. Create `NEXO Support` as a second app entrypoint in `nexo-desktop`.
2. Migrate auth storage to a shared namespace with legacy Desktop read-through.
3. Add `POST /api/support/bootstrap` in `nexo-desktop-web`.
4. Add a local support host that can:
   - read versions,
   - read logs,
   - run explicit doctor routines,
   - run shell commands in a session.
5. Make Support open, authenticate, verify subscription, start a support session and show chat.

Do not block this first slice on remote desktop GUI control.

## Success Criteria

- [ ] `NEXO Support` installs together with Desktop.
- [ ] `NEXO Support` opens even when Desktop fails to boot.
- [ ] Existing Desktop users can reuse their session without re-login.
- [ ] Support sessions can grant full shell access with explicit consent.
- [ ] Every support action is auditable and revocable.
- [ ] Core defects create structured incidents and email engineering.
- [ ] macOS pilot can ship first without forcing architectural rework for Windows/Linux.
- [ ] Windows and Linux support only require adapter and packaging completion, not a product redesign.
