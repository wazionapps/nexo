# NEXO Opportunity Orchestrator MVP

Created: 2026-06-07
Owner: NEXO Brain
Status: Phase 1 implemented in v7.30.23
Related followup: NF-NEXO-FORESIGHT-AFFECTIVE-MVP-20260607

## Decision

Build NEXO Foresight as an **Opportunity Orchestrator / Proactive Review Queue**, not as a visible psychological profile layer.

The goal is to make NEXO go ahead of the user: detect relevant signals, prepare useful work before it is requested, and surface only the few opportunities that reduce real cognitive load or operational risk.

The affective part exists, but it is internal and non-clinical. It should influence pacing, brevity, timing, and decision packaging. It must not label the user with psychological states.

## Product Promise

NEXO should not say "you have 40 pending things".

It should say:

> I found 37 signals, but only 2 deserve your attention. I prepared the context for both. The first one unlocks WAzion and Desktop voice; the decision is A/B.

On days with no strong signal, the correct output is:

> No new proposal has enough evidence today. I am still watching X and Y.

Zero proposals is a valid and healthy result.

## Non-Goals

- No clinical diagnosis.
- No emotional labels such as anxious, depressed, unstable, vulnerable, or fragile.
- No permanent mood score.
- No manipulation based on inferred emotion.
- No automatic emails, payments, production changes, destructive actions, public posts, or third-party contact without explicit authorization.
- No separate agent personality.
- No rewrite of Deep Sleep, followups, outcomes, closure plane, drive signals, or Local Context.
- No notification spam.
- No "daily proposal quota".

## Core Concepts

### Signal

A raw or normalized observation from an existing NEXO surface.

Examples:

- A followup is overdue and high impact.
- An outcome deadline passed without verification.
- A user correction repeats a known failure pattern.
- Deep Sleep reports the same blocker several days in a row.
- An email thread contains an explicit commitment.
- A release was published but the original symptom was not verified.
- Local Context shows repeated work around the same project.

### Opportunity

A cluster of signals that can become useful action.

An opportunity must answer:

- Why now?
- What evidence supports it?
- What can NEXO prepare safely?
- What decision, if any, does the user need to make?
- What happens if nothing is done?
- When should it expire?

### Preparation

Safe work NEXO can do before user approval.

Allowed in v1:

- gather context
- draft emails
- draft presentations or specs
- produce A/B decisions
- prepare commands without running them
- run read-only checks
- summarize evidence
- identify likely blockers
- create a reversible plan

Not allowed without approval:

- send
- deploy
- buy
- delete
- alter production
- contact third parties
- change billing
- publish

### Proposal

The small user-facing item shown in Home, morning briefing, or chat.

A proposal must be scarce, clear, and evidence-backed.

## User Surfaces

### 1. Home: "Preparado para ti"

Default, low-interruption surface.

Shows:

- prepared drafts
- decision cards
- high-confidence opportunities
- stale prepared items that are about to expire

Controls:

- prepare more
- execute
- ignore
- snooze
- do not suggest this again
- show evidence

### 2. Morning Briefing

Daily summary with attention budget.

Recommended sections:

- "Merece tu atencion": 0 to 3 proposals
- "Ya preparado": drafts or artifacts ready for review
- "Decisiones de 30 segundos": A/B with recommendation
- "Estoy vigilando": optional, only for important watched items

Rules:

- 0 proposals is acceptable.
- 1 proposal is ideal when one thing clearly matters.
- 2 to 3 proposals only when each is important and actionable.
- More than 3 only for exceptional alerts: security, money, production, customer, legal, deadline.

### 3. Hot Interruption

Only when not interrupting has a real cost.

Allowed categories:

- security exposure
- money/billing incident
- production outage or release regression
- customer-facing failure
- deadline or external dependency window
- high-confidence opportunity with short window

Everything else waits for Home or morning briefing.

## Data Model MVP

### nexo_signals

Normalized signals from existing systems.

Fields:

- id
- source_type
- source_id
- entity_ref
- summary
- signal_kind
- urgency
- confidence
- privacy_level
- created_at
- expires_at

### Existing operational state snapshots

Non-clinical operational state already exists elsewhere in Brain and should be consumed as a source when needed, not duplicated by the MVP migration.

Allowed values should describe the work context, not the person.

Examples:

- high_decision_load
- repeated_blocker
- release_pressure
- user_prefers_short_answer
- avoid_option_dump
- reduce_noise

Forbidden values:

- anxious
- depressed
- vulnerable
- unstable
- burnout
- manipulable
- compliance_likely

### nexo_opportunities

Central queue.

Fields:

- id
- title
- hypothesis
- domain
- opportunity_type
- impact
- urgency
- confidence
- risk
- effort
- readiness
- user_burden_reduction
- interruption_cost
- strategic_alignment
- score
- state
- owner
- why_now
- next_action
- created_at
- updated_at
- expires_at

Opportunity types:

- closure
- remediation
- customer
- money
- security
- deadline
- preparation
- skill_candidate
- product
- communication

### nexo_opportunity_evidence

Links opportunities to their sources.

Fields:

- opportunity_id
- source_type
- source_id
- evidence_summary
- confidence
- created_at

### nexo_preparations

Prepared artifacts.

Fields:

- id
- opportunity_id
- artifact_type
- artifact_ref
- safe_mode
- approval_required
- status
- created_at
- expires_at

Artifact types:

- draft_email
- draft_presentation
- draft_spec
- runbook
- read_only_report
- command_plan
- decision_card
- context_packet

### nexo_proposals

User-facing proposals.

Fields:

- id
- opportunity_id
- surface
- copy
- cta_primary
- cta_secondary
- shown_at
- feedback
- created_at

Feedback:

- accepted
- ignored
- snoozed
- dismissed
- false_positive
- useful_but_later

### nexo_suppression_rules

Prevents repeated noise.

Fields:

- id
- scope_type
- scope_key
- reason
- expires_at
- created_at

Examples:

- no_more_domain:nexocoop_ga4_until_id_available
- cool_down_type:draft_presentation_7d
- suppress_person:third_party_emotional_profile

### nexo_action_authorizations

Durable authorization envelope.

Fields:

- id
- scope
- allowed_action_class
- max_cost
- expires_at
- granted_by
- evidence_ref
- created_at

Action classes:

- read_only
- prepare_artifact
- local_reversible
- external_message
- production_change
- destructive
- payment
- public_publish

## State Machine

Main flow:

candidate -> enriched -> prepared -> proposed -> accepted -> executing -> outcome_registered -> verified -> closed

Side states:

- snoozed
- suppressed
- needs_permission
- blocked_external
- stale
- discarded
- merged_duplicate

Most candidates should die before proposal. If too many reach the user, the system is failing.

## Pipeline

### Nightly

1. Ingest signals from:
   - Deep Sleep
   - followups
   - outcomes
   - closure plane
   - drive signals
   - diary
   - email monitor
   - Local Context
   - watchdog / immune
2. Normalize into nexo_signals.
3. Dedupe and cluster.
4. Convert clusters into candidate opportunities.
5. Score and assign expiry.
6. Prepare safe artifacts for high-readiness items.
7. Select 0 to 3 proposals for the morning briefing.
8. Apply suppression and feedback learning.

### Event-Driven

Triggers:

- new user message
- correction
- email thread with commitment
- followup status change
- outcome missed
- watchdog anomaly
- repeated blocker

Default behavior:

- store candidate
- enrich if cheap
- do not interrupt unless threshold is high

Hot interruption requires:

- high impact
- high confidence
- high readiness
- low risk
- high cost of not interrupting

## Scoring

Use a simple auditable formula in v1:

score = impact + urgency + confidence + readiness + user_burden_reduction + strategic_alignment - risk - interruption_cost - repetition_penalty

Each component should be visible in debug/audit mode.

Interpretation:

- impact: money, security, customer, release, reputation
- urgency: deadline, external window, age
- confidence: evidence quality
- readiness: how much is already prepared
- user_burden_reduction: decisions or time saved
- strategic_alignment: current business/product priority
- risk: production, sensitive data, third parties, irreversible action
- interruption_cost: whether surfacing it now would distract
- repetition_penalty: repeated ignored suggestions

## Privacy And Safety Rules

### Allowed

- infer operational pressure from evidence
- adapt response length and timing
- prepare read-only artifacts
- explain why a proposal exists
- store minimal factual signals with expiry
- keep explicit user preferences

### Not Allowed

- diagnose mental health
- profile personality clinically
- infer sensitive traits
- store permanent emotion labels
- use inferred state to pressure the user
- infer emotions of third parties
- train shared models on private content
- hide emotional/contextual inference

### Retention

- operational-state hints: 24 to 72 hours
- topic friction: 7 to 14 days or until closure
- explicit preferences: persistent but reviewable
- third-party context: minimum factual task context only
- sensitive raw text: avoid storing, prefer references and redaction

### Wording

Good:

> This blocker is repeating. I can reduce it to one A/B decision.

Good:

> I found enough evidence to prepare this, but I need approval before sending or changing production.

Bad:

> You seem anxious, so I decided this for you.

Bad:

> I know how you feel.

## Acceptance Criteria

MVP is acceptable when:

1. It can generate a read-only daily queue from real NEXO data. Implemented from closure-plane sources in v7.30.23.
2. It can output zero proposals without treating that as a failure. Covered by tests.
3. It shows at most 3 normal proposals in the daily briefing. Covered by tests and enforced by the queue API.
4. Every proposal has evidence refs and confidence. Covered by persistence and API tests.
5. Every preparation is read-only unless explicitly authorized. Covered by authorization tests.
6. The user can ignore, snooze, suppress, or inspect evidence. Implemented through feedback/suppression/get tools.
7. Proposals close the loop with feedback and outcomes. Feedback is implemented; outcome linking is reserved for the next phase.
8. No clinical/emotional labels are stored or shown. Visible-output guard is covered by tests.
9. A dry-run demonstrates reduced noise versus raw followups. Implemented through optional Markdown/JSON dry-run reports.

## Metrics

- proposal_acceptance_rate
- proposal_ignore_rate
- false_positive_rate
- prepared_artifact_reuse_rate
- cost_per_useful_proposal
- high_impact_followup_age
- closures_with_evidence_rate
- interruption_count_per_day
- user_correction_rate_for_bad_inference

## MVP Phases

### Phase 1: Read-Only Dry Run

- Add data model.
- Generate opportunities from existing followups/outcomes/closure plane.
- Produce a daily Markdown/JSON report.
- No Desktop UI yet.
- No autonomous execution.

### Phase 2: Home Surface

- Add "Preparado para ti".
- Add proposal actions: inspect, snooze, suppress, accept.
- Link accepted proposal to task/workflow/outcome.

### Phase 3: Preparation Artifacts

- Generate draft specs, draft emails, read-only reports, and decision cards.
- Store artifact refs with expiry.
- Enforce approval class before external effects.

### Phase 4: Adaptive Pacing

- Use non-clinical operational state to control wording, length, and timing.
- Do not expose psychological labels.
- Add opt-out controls.

## Open Questions

- Should proposals be stored in the main Brain DB or a separate queue DB?
- Should Home show "0 proposals" explicitly or stay silent?
- Which surfaces get first-class UI: Desktop Home, morning email, chat, or all three?
- How aggressive should preparation be when the artifact costs money or model tokens?
- What is the default retention for unused prepared artifacts?
