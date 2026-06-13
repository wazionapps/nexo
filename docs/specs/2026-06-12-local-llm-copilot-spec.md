# Local LLM Copilot For Brain

Created: 2026-06-12
Owner: NEXO Brain
Status: ADR draft
Related followups: NF-DS-183F625E, NF-DS-C99EBAC0

## Decision

Add a small local LLM worker as a Brain-owned helper for low-risk,
high-volume cognitive preparation. The worker is not a second agent and does
not make user-facing decisions by itself. It drafts bounded JSON facts,
summaries and triage hints that Brain verifies before they affect followups,
diaries, protocol decisions or operator-visible output.

The first implementation should use the existing local model management
contract instead of adding a parallel model downloader. Brain already owns
`src/local_model_manifest.json`; Desktop may continue bundling local model
files, but Brain remains the authority for model id, revision, hashes,
resource policy and fallback.

## Why

Several Brain jobs spend expensive remote LLM tokens on repetitive preparation:

- diary summarization
- followup prefiltering
- extraction of candidate facts from session notes
- clustering similar reminders, learnings and support items
- first-pass triage before Deep Sleep or followup-runner spends remote tokens

Those jobs do not always need frontier reasoning. They need cheap local
compression, extraction and filtering, with a strict fallback when confidence
or structure is weak.

## Non-Goals

- No autonomous local agent identity.
- No direct writes to Brain DB, followups, reminders, credentials or tickets.
- No sending emails, posting messages, deploying, deleting, billing, or
  contacting third parties from the local worker.
- No replacement for Deep Sleep, Cortex, protocol cards, closure, Local
  Context, semantic router, or existing NEXO tools.
- No silent multi-GB downloads on low-resource machines.
- No accepting local LLM output as truth without source references.

## Delegable Tasks

Local LLM output may be used as a draft input for Brain-owned tools when all
source text is local and already permitted for the running task.

Allowed in phase 1:

- Summarize one diary or transcript slice into `summary`, `decisions`,
  `pending`, `risk_notes`.
- Extract candidate facts from a bounded text chunk as
  `{subject, predicate, object, evidence_quote, confidence}`.
- Prefilter followups into buckets: `actionable_now`, `needs_operator`,
  `external_blocker`, `stale_or_duplicate`, `security_or_money`.
- Produce short labels for clusters of related reminders/followups.
- Detect likely duplicates or stale items, returning only candidate ids and
  reasons.
- Draft a compact context packet for a remote LLM, capped by token and source
  count.
- Classify whether an item deserves remote review based on explicit criteria.

Allowed only after Brain validation:

- Suggest a new learning from a repeated correction.
- Suggest closing or superseding a followup.
- Suggest an operator-facing proposal for Home or morning briefing.

## Non-Delegable Tasks

These stay with Brain tools, deterministic code, remote LLM review, or the
operator:

- Security-sensitive edits, credential rotation and secret handling.
- Production deploys, deletes, database writes and ticket/customer messages.
- Legal, medical, financial or HR judgments.
- User identity, consent, authorization and billing decisions.
- Protocol closure, task completion and "done" claims.
- Any decision that changes durable memory without source-backed validation.
- Any cross-tenant analysis unless Brain has already scoped and redacted the
  input.
- Any operation on Nora/Maria infrastructure without explicit current
  permission.

## Interface

Brain calls a local worker process through a narrow request/response contract.
The initial transport can be stdio JSON Lines; a local HTTP server is allowed
later only if it has localhost-only binding, a random session token and idle
shutdown.

Request:

```json
{
  "schema_version": 1,
  "task_id": "diary_summary_v1",
  "job_id": "uuid",
  "locale": "es",
  "mode": "extract|summarize|triage|cluster|compress",
  "input": {
    "text": "...",
    "records": []
  },
  "constraints": {
    "max_output_tokens": 800,
    "json_schema": "named_schema_id",
    "forbidden_actions": ["write_db", "send", "deploy", "delete"],
    "source_ids": ["diary:123", "followup:NF-..."]
  }
}
```

Response:

```json
{
  "schema_version": 1,
  "job_id": "uuid",
  "ok": true,
  "model": {
    "profile": "local_context_llm_small",
    "model_id": "candidate",
    "revision": "pinned_revision"
  },
  "confidence": 0.82,
  "requires_remote_review": false,
  "sources_used": ["diary:123"],
  "output": {},
  "warnings": []
}
```

Failure response:

```json
{
  "schema_version": 1,
  "job_id": "uuid",
  "ok": false,
  "error_type": "model_unavailable|timeout|invalid_json|low_confidence|policy_refusal",
  "requires_remote_review": true,
  "warnings": ["reason"]
}
```

Brain validates every response with:

- JSON schema validation.
- Source id check: every claim must map to provided source ids.
- Confidence threshold per task type.
- Size cap and timeout cap.
- Tenant/scope check before reusing the output.
- Remote fallback when validation fails or risk is above local policy.

## Cost Gating

The local worker should run before remote LLM calls only when it is likely to
reduce cost without losing reliability.

Default gates:

- Use local first for repetitive summarization/extraction under high volume.
- Skip local and go remote when the task is high-stakes, novel, sparse, or
  requires external knowledge.
- Skip local when model profile is missing, warming, too slow, or the machine
  is below policy thresholds.
- Do not start heavy local inference on battery for background jobs unless the
  user explicitly opted into performance mode.
- Cap local jobs per cycle so followup-runner and Deep Sleep do not starve
  the machine.

Suggested phase 1 thresholds:

- `timeout_ms`: 12,000 per local job.
- `max_input_chars`: 24,000 per job before chunking.
- `max_parallel_jobs`: 1 by default, 2 only on machines marked performance.
- `min_confidence`: 0.75 for summaries, 0.85 for closure/duplicate hints.
- remote review required for security, money, production, customer or legal
  items.

## Model Policy

Do not select the final model from memory during implementation. The benchmark
followup NF-DS-C99EBAC0 must evaluate at least:

- the existing `qwen3-0.6b-q4-local-presence` profile as a baseline only
- Qwen3 small instruct GGUF candidates
- Gemma small instruct candidates
- SmolLM3 small instruct candidates if licensing and runtime support are clean

Selection criteria:

- Spanish and English summary quality.
- JSON compliance under strict schema.
- factual extraction accuracy with source quotes.
- latency on Mac arm64 CPU/MPS and Windows x64 CPU.
- RAM and disk footprint.
- license/redistribution compatibility.
- immutable revision and sha256 for every required file.

The chosen model becomes a new optional profile in
`src/local_model_manifest.json`, likely `local_context_llm_small`. The existing
`qwen3-0.6b-q4-local-presence` profile remains optional and is not promoted to
general summarization unless the benchmark proves it.

## Fallback

Fallback must be boring and explicit:

1. If the local worker is unavailable, Brain runs the current deterministic
   path or remote LLM path.
2. If local output is invalid JSON, Brain discards it and increments a local
   failure counter.
3. If confidence is low, Brain uses it only as a hint for retrieval ordering,
   not as content.
4. If three failures happen in one cycle, disable local worker for that cycle.
5. If a model hash or revision check fails, block the profile and require a
   normal repo release to update the manifest.

Operator-facing output should not mention raw model errors. It should say a
plain status such as "la preparacion local avanzada no esta disponible en este
equipo; uso la ruta normal".

## Persistence And Observability

Persist only operational metadata, not raw private chunks:

- job id
- task id
- model profile and revision
- source ids
- success/failure type
- latency
- input/output size
- whether remote fallback was used

Do not store local worker prompts or completions by default. If debug capture
is needed, it must be opt-in, redacted, short-lived and scoped to the operator.

## Rollout

Phase 0: spec and benchmark.

- Write this ADR.
- Run NF-DS-C99EBAC0 benchmark on candidate models.
- Decide whether a candidate is good enough for `local_context_llm_small`.

Phase 1: read-only worker.

- Add worker CLI with stdio JSONL.
- Add schemas for `diary_summary_v1`, `fact_extract_v1`,
  `followup_prefilter_v1`.
- Add validation tests and timeout tests.
- Use local output only in logs/metrics and compare with current path.

Phase 2: assisted preparation.

- Let Deep Sleep and followup-runner consume validated local summaries as
  context compression.
- Never let local output close followups or write learnings directly.
- Measure remote token reduction and false-positive rate.

Phase 3: gated durable suggestions.

- Allow validated local worker to propose candidate learnings/followup
  closures, still requiring Brain validation and normal closure evidence.

## Acceptance Criteria

- The spec exists in `docs/specs/`.
- The worker contract has JSON schema tests before implementation.
- The model profile is optional and hash-pinned before release.
- No durable Brain write can originate from local worker output alone.
- Local failure always degrades to the current Brain path.
- Metrics show local attempts, savings estimate, fallback count and invalid
  output count.
