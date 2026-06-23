# Support Second Ticket Parallel Sweep

Use this skill when a production support flow gets a second customer failure in the same area within 72 hours. Areas include cloud, credits, provisioning, voice, and image flows.

## Mandatory Workflow
1. Stop ticket-by-ticket handling and open a P1 workflow for the whole affected flow.
2. Read the two most recent tickets and group them by area, client/account, timestamp, exact symptom, and shared components.
3. Launch 2-3 parallel subagents with non-overlapping scopes:
   - idempotency, reservations, retries, and duplicate side effects
   - scope tokens, credentials, quotas, provider/config drift
   - cached errors, stale state, logs, and smoke test design
4. Require each subagent to return evidence, affected files/services, reproduction status, and a stop condition if evidence is insufficient.
5. Merge findings into one root-cause matrix, apply the smallest verified fix, and run a full-flow smoke before replying to individual tickets.
6. Close the P1 in the same batch with ticket IDs, commit/deploy refs when applicable, and live smoke evidence.

## Guardrails
- Do not ask the operator whether to audit the whole flow; the second customer failure is the trigger.
- Do not close one customer ticket while the shared flow remains unverified.
- Do not mutate production from subagents. They investigate and report; the parent agent applies verified changes through the normal protocol.
