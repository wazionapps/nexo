# Silent Repair Before UI - Technical Spec

Created: 2026-06-14
Owner: NEXO Brain / Desktop
Status: implementation-ready draft
Related followup: NF-DS-D2DD7E22

## Decision

NEXO should attempt safe repairs before showing a user-facing prompt. The UI is
reserved for cases where the agent/runtime cannot continue without the user's
explicit consent, credentials, permissions, payment authority, or acceptance of
destructive risk.

The repair layer is not a second supervisor. It is a bounded preflight and
recovery contract used by Desktop, Brain jobs, followup-runner, email-monitor
and other local automations before they escalate to Francisco.

## Goals

- Reduce avoidable alerts for routine local failures.
- Keep the operator out of low-risk maintenance work.
- Make every automatic repair auditable.
- Prevent silent action where consent or irreversible risk is required.
- Add regression tests so "repair first" does not become "hide failures".

## Non-Goals

- No silent login, account creation, payment, purchase or subscription change.
- No silent permission granting in macOS, Windows, browser, cloud or provider
  dashboards.
- No destructive action such as delete, purge, revoke, rotate, force-push,
  production DB mutation, customer message or public publish.
- No repair on Nora/Maria infrastructure unless the current user message gives
  explicit permission for that resource.
- No bypass of provider security flows, OAuth consent screens, 2FA or legal
  acceptance.

## Repair Classes

### Safe Automatic Repair

Allowed without UI when all conditions are true:

- The action is local or already within the running process authority.
- The action is reversible or idempotent.
- The expected result is machine-verifiable.
- The action does not change billing, credentials, public content, customer
  state, external permissions or production data.
- The action writes a compact audit event with input symptom, action, result
  and evidence.

Examples:

- Restart a local helper process that is already managed by NEXO.
- Recreate a missing local cache directory.
- Retry a failed local index job with a smaller batch.
- Clear a stale lock after verifying the owning PID is gone.
- Reconnect to an MCP/runtime endpoint after confirming the configured port.
- Refresh local capability/catalog metadata.
- Re-run a deterministic health check after a transient failure.

### Prompt Required

The UI must interrupt and ask before action when any condition is true:

- Login, OAuth, 2FA, session re-authentication or provider account access is
  required.
- A macOS/Windows/browser permission must be granted or changed.
- The action can incur cost, consume paid credits, start a paid VM, buy a
  number/domain/certificate, or change billing configuration.
- The action is destructive, public, customer-visible, externally delivered or
  hard to roll back.
- The action needs a secret that is not already available in the secure store.
- The system has conflicting evidence or cannot verify the target state.

Examples:

- Ask for Full Disk Access, Accessibility, microphone, camera or automation
  permission.
- Request a Cloudflare, Stripe, OpenAI, Twilio or Chrome Web Store credential.
- Start paid infrastructure that is currently stopped.
- Publish a Chrome Web Store release, send an email to a customer, rotate a
  live key, delete DB rows, purge files or alter DNS.

## Runtime Contract

Every repair attempt uses this envelope:

```json
{
  "schema_version": 1,
  "repair_id": "uuid",
  "surface": "desktop|brain|followup-runner|email-monitor|cron",
  "symptom": "short_machine_label",
  "risk_class": "safe_auto|prompt_required|blocked",
  "preconditions": [],
  "action": "short_action_label",
  "target": "local process/cache/file/runtime endpoint",
  "evidence_before": [],
  "evidence_after": [],
  "result": "repaired|unchanged|failed|prompt_required",
  "operator_message": null
}
```

Rules:

- `risk_class=safe_auto` may execute without UI.
- `risk_class=prompt_required` must return a UI payload and execute nothing.
- `risk_class=blocked` records the blocker and executes nothing.
- `operator_message` stays null for successful automatic repairs unless a
  concise later report is useful.
- Failed automatic repair may retry once if the second attempt has a distinct
  deterministic action. After that it escalates with evidence.

## UI Contract

Desktop shows a prompt only for `prompt_required` and unrepaired failures. The
prompt must state:

- what is blocked
- what permission/credential/cost/risk is needed
- what NEXO already tried
- the exact action button
- the safe alternative, if any

No prompt should ask Francisco to run terminal commands when NEXO can perform
the safe preparation itself.

## Audit And Storage

Successful and failed repairs are logged to the normal operational ledger. The
minimum durable fields are:

- timestamp UTC
- session id or job id
- surface
- symptom
- action
- result
- evidence references
- whether user attention was required

The log must not include secrets, raw customer content, OAuth tokens, provider
keys or full private payloads.

## Regression Tests

Add tests at the repair policy boundary, not just happy paths:

- Safe local stale lock is removed only when PID is absent.
- Missing cache directory is recreated and logged.
- Runtime reconnect is attempted once, then escalates with evidence.
- Login-required provider response returns `prompt_required` and does not retry
  in a loop.
- Missing credential returns `prompt_required`; it does not create flat secret
  files.
- Cost action returns `prompt_required`; it does not start paid infrastructure.
- Destructive action returns `prompt_required`; it does not delete or mutate.
- Nora/Maria target without current explicit permission returns
  `prompt_required` or `blocked`.
- Audit event redacts secrets and private payloads.

## Acceptance Criteria

- A shared repair policy module classifies safe automatic repairs versus prompt
  required cases.
- At least Desktop startup, followup-runner and email-monitor use the policy
  before surfacing routine local failures.
- Automatic repairs produce evidence events.
- UI prompts appear only for login, permission, cost, destructive/public action,
  missing credential, unresolved conflict or unrepaired failure.
- Regression tests cover all cases above and fail closed.

## Rollout

Phase 1: Brain/followup-runner policy module and tests.

Phase 2: Desktop startup and health panel integration.

Phase 3: Email-monitor and scheduled automations.

Phase 4: Operator-facing summary in Home only for repeated repairs, unrepaired
failures or prompts that require a real decision.
