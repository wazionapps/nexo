# Semantic reasoner — pin + design notes

Plan ONEPASS LLM Coverage, release stage. Companion to
[classifier-model-notes.md](./classifier-model-notes.md).

## What the reasoner is

`src/semantic_reasoner.py` is the second layer of the semantic stack:

```
fast_local  ->  semantic_reasoner  ->  remote_fallback
 (Zero-shot    (this module)         (call_model_raw
  mDeBERTa,                            last-resort)
  pinned)
```

`semantic_router.py` dispatches every Brain semantic decision through this
stack. Call sites name their `decision_kind` and the router applies the
per-kind policy.

## Two reasoner modes — honest pin strategy

The plan asked for a "stronger, pinned model" as the second layer. This
release ships that stronger layer using a split strategy that avoids
falsifying a pin we cannot reproducibly verify. The two modes are:

### Mode A — `multipass_local`

Used for textual decision kinds (`session_end_intent`, `r14_correction`,
`r16_declared_done`, `r17_promise_debt`, `autonomy_mandate`,
`guard_verbal_ack`, `r34_identity_coherence`, `followup_operator_attention`,
`drive_signal_type`, `drive_area`, `reply_event_type`, `query_intent`,
`sentiment_intent`).

- Reuses the already-pinned `LocalZeroShotClassifier`.
- Model id + revision: **see
  [classifier-model-notes.md](./classifier-model-notes.md)**. Pin is
  authoritative there; this module intentionally does not duplicate the
  SHA so upgrades only need to touch one file.
- Stricter behaviour than the fast path:
  1. three inference passes with mild prompt perturbations
     (`"{q}"`, `"Decide: {q}"`, `"Classify this utterance: {q}"`)
  2. majority vote across passes
  3. result only accepted if ≥2/3 passes agree **and** the averaged
     confidence for the winning label is ≥ 0.75 (policy default).
- No extra model download. No extra install state. No extra bootstrap
  risk.

The reason this is *stronger* than the fast path: single-pass zero-shot
classifiers have measurable variance on short ambiguous utterances, and
tighter thresholds applied to single samples produce too many refusals.
Three-pass majority voting kills false positives without requiring a
larger model.

### Mode B — `cached_llm`

Used for code-aware decision kinds (`r20_constant_change`, `t4_r15`,
`t4_r23e`, `t4_r23f`, `t4_r23h`) where zero-shot NLI was never the right
authority.

- Thin wrapper around `src/call_model_raw.py`.
- The backend model is resolved through `resonance_map.resolve_model_and_effort`
  with `caller='semantic_reasoner'`, `tier='muy_bajo'`. The actual model
  id + revision is therefore pinned by the resonance map, not by this
  module. This matches how every other Brain LLM caller is pinned and
  keeps a single source of truth.
- **Disk cache**: `~/.nexo/runtime/operations/semantic-reasoner-cache.json`.
  Keys are `sha256(json({decision_kind, normalized_question,
  normalized_context, labels}))`. TTL default 24h. Scope is per-decision_kind
  so a cached `t4_r15` answer cannot leak into `t4_r23f`.
- Cache bounded at 2000 entries with LRU trim to 1800 on write. Entries
  older than TTL are ignored even if not evicted.

Rationale: these decisions are expensive-per-call but highly repetitive
(the same snippet of code keeps passing through the enforcement engine).
Caching on `(decision_kind, normalized_input)` reduces remote calls
dramatically without changing correctness, and the policy stays explicit.

## Why not ship a dedicated stronger local LLM in this release

The plan contemplated pinning a local LLM (Llama 3.1 8B, Qwen 2.5 7B,
etc.) as the reasoner. We deferred that for two verifiable reasons:

1. **Install-time risk**: provisioning a 5–10 GB model during `nexo
   update` would require a new download pipeline, GPU/MPS detection, a
   separate install-state file, and the heavy-bootstrap protections the
   existing classifier needed. That surface area is a full feature, not
   a sub-step of this release.
2. **Pin verifiability**: pinning a model SHA without a live connection
   to HuggingFace at review time risks locking every operator to a
   revision that turns out to have been silently rewritten upstream.
   The fast-local pin (`classifier-model-notes.md`) paid that cost once;
   adding a second local pin without reproducible verification would
   recreate the exact footgun the pin policy exists to prevent.

A future release can graduate Mode B from `cached_llm` to a truly local
LLM without changing the `semantic_router` contract. Call sites only see
`RouterResult`; whether the reasoner ran locally or remotely is hidden.

## Environment variables

| Variable | Default | Effect |
|----------|---------|--------|
| `NEXO_SEMANTIC_REASONER_CACHE_PATH` | *(unset)* | Override cache file path. Used in tests and for debug isolation. |
| `NEXO_SEMANTIC_REASONER_TTL` | `86400` | Cache TTL in seconds. Lower for aggressive re-validation; raise for offline-heavy usage. |

There is **no** `NEXO_SEMANTIC_REASONER=0` opt-out: the reasoner is
always available when its dependencies are. Opt-out happens at the
router level via `allow_remote_fallback=False` plus the existing
`NEXO_LOCAL_CLASSIFIER` flag for Mode A.

## Upgrade policy

- Mode A: follow the upgrade policy already documented in
  `classifier-model-notes.md`. When the fast-path pin bumps, Mode A
  automatically picks it up.
- Mode B: follow the resonance-map upgrade policy. The reasoner never
  pins an LLM directly; `nexo_cortex_review` already tracks resonance
  quality and fires the next-tier eval cycle.
- Policy table in `src/semantic_router.py::_POLICY`: changing a
  decision_kind's route or threshold requires editing that dict AND
  updating this doc AND bumping the relevant tests. A drift check in
  CI (`tests/test_semantic_router.py::test_policy_kinds_are_documented`)
  fails if any kind goes undocumented.

## Wiring status

- [x] `src/semantic_router.py` — 18 decision_kinds registered.
- [x] `src/semantic_reasoner.py` — Modes A + B implemented.
- [x] `docs/semantic-reasoner-model-notes.md` — this file.
- [x] `tests/test_semantic_router.py` — policy + routing tests.
- [x] `tests/test_semantic_reasoner.py` — mode A/B tests (stubbing the
      fast classifier and `call_model_raw`).
- [ ] Per-site migration of existing callers (`session_end_intent.py`,
      `autonomy_mandate.py`, `guard_verbal_ack.py`, `r14_*`, `r16_*`,
      `r17_*`, `r20_*`, `r34_*`, `tools_drive.py`, `nexo-followup-runner.py`,
      etc.) — ships in a **follow-up PR**. That PR does not change the
      router/reasoner contract, only replaces per-site classifier policy
      trees with `semantic_router.route(decision_kind=…)` calls.
- [ ] Desktop bridge (`lib/brain-semantic-router.js` + `nexo semantic
      classify` CLI) — ships in the nexo-desktop release PR that pairs
      with this one.
