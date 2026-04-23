# Semantic Router — Per-Site Migration Roadmap

Owner: NEXO Brain + Desktop
Origin: Deep Sleep followup NF-DS-123D6587
Parent plan: `~/Desktop/NEXO-ONEPASS-LLM-COVERAGE-RELEASE-PLAN.md`
Scaffold shipped: `src/semantic_router.py` (v7.9.0) + `lib/brain-semantic-router.js`
Scope: migrate 13 Brain call-sites + 7 Desktop call-sites off ad-hoc policy into the router, in mini-waves with explicit entry/exit criteria and a per-wave obedience metric.

## Obedience metric (applies to every wave)

For each wave Wₙ, the metric is reported from runtime telemetry (router `meta` field + `route_used`):

- `coverage_wave(Wₙ) = migrated_callers(Wₙ) / planned_callers(Wₙ)` — target 1.0
- `route_used_distribution(Wₙ)` — fast_local, semantic_reasoner, remote_fallback, no_route
- `degraded_rate(Wₙ)` — rate of `degraded=true` decisions (target <5% after 48h)
- `regression_delta(Wₙ)` — delta vs pre-migration labels on a replay corpus (target |Δ| < 2%)
- `obedience(Wₙ)` = callers emitting named `decision_kind` / planned_callers — target 1.0

Telemetry is captured by the router and logged per decision; an end-of-wave roll-up lives in `~/.nexo/runtime/operations/router/wave_{n}.json`.

---

## Wave 0 — Router landed (baseline, already green)

- Scaffold: `src/semantic_router.py`, `RouterResult` dataclass, stack `fast_local → semantic_reasoner → remote_fallback`.
- Desktop bridge file exists: `lib/brain-semantic-router.js`.
- Tests present: `tests/test_semantic_router.py`, `tests/test_semantic_router_site_migration.py`.

Status: DONE.

---

## Wave 1 — Durable mandate + verbal ack (highest leverage)

Files: Brain.

- [x] `src/session_end_intent.py` — already routes through `semantic_router.route(decision_kind="session_end_intent")` (verified).
- [x] `src/autonomy_mandate.py` — already routes through `semantic_router.route(decision_kind="autonomy_mandate")` (verified).
- [x] `src/guard_verbal_ack.py` — already routes through `semantic_router.route(decision_kind="guard_verbal_ack")` (verified).

Entry criteria: none (this wave is the baseline).
Exit criteria: 3/3 files migrated; each `decision_kind` produces a `route_used` other than `no_route` in ≥1 live session.
Status: DONE. Obedience: 3/3.

Why high leverage: these three control (a) when NEXO decides the session is closing, (b) whether the durable autonomy mandate is active, and (c) whether the operator actually acknowledged a guarded step. They gate the most frequent correction loops.

---

## Wave 2 — Wire-enforcement (R20, R34, T4)

Files: Brain. Status: DONE in v7.9.2 branch.

- [x] `src/r20_constant_change.py` — routes through `semantic_router.route(decision_kind="r20_constant_change")`.
- [x] `src/r34_identity_coherence.py` / `src/enforcement_engine.py::on_assistant_message` — regex prefilter remains, semantic confirmation routes through `decision_kind="r34_identity_coherence"`.
- [x] `src/enforcement_engine.py` T4 gate — routes per sub-rule with `decision_kind`s: `t4_r15`, `t4_r23e`, `t4_r23f`, `t4_r23h`.

Entry criteria: Wave 1 exit green; router `decision_kind` table extended with the six new kinds.
Exit criteria:
- 3/3 files migrated; 6/6 `decision_kind`s registered.
- On a replay corpus (most recent 200 decisions per kind) regression_delta < 2% vs legacy labels.
- `degraded_rate` < 5% after 48 live hours.
Obedience target: 3/3 and 6/6.

Order inside the wave (lowest blast radius first):
1. `r20_constant_change` (narrow, isolated caller).
2. `r34_identity_coherence` (regex prefilter protects recall).
3. `enforcement_engine` T4 gate (highest blast radius — do last, with fail-closed preserved).

Why wire-enforcement next: these gates run on every edit/command step; moving them off legacy classifiers collapses four different policy trees into one policy owner (the router).

---

## Wave 3 — Email/content triage classifiers

Files: Brain. Status: PARTIAL in v7.9.2 branch.

- [ ] `src/tools_email_guard.py` — semantic helper exists; live caller not grep-confirmed. Action: (a) wire a real live caller in the email-send path, OR (b) mark non-runtime and remove product implication. If wired: `fast_local → semantic_reasoner → remote_fallback` with `decision_kind="email_guard_secret_risk"`.
- [x] `src/tools_drive.py` — routes through `decision_kind="drive_signal_type"` and `decision_kind="drive_area"`; regex/keyword remains as degraded tail.
- [x] `src/scripts/nexo-followup-runner.py` — routes operator attention through `decision_kind="followup_operator_attention"`.

Entry criteria: Wave 2 exit green.
Exit criteria:
- 3/3 files migrated; 4 new `decision_kind`s live.
- Ship a grep that fails CI if any of these files still import `enforcement_classifier` / `call_model_raw` directly.
- regression_delta < 2% on a replay corpus per kind.

Why: these classifiers shape operator-facing output (email guard, drive alerts, followup surfacing). Regressions here are visible to Francisco.

---

## Wave 4 — Enforcement/authority cleanup

Files: Brain. Status: NOT STARTED.

- [ ] Cap Brain callers of `enforcement_classifier` / `call_model_raw` to only the router stack. No other importer allowed.
- [x] Register the remaining named decision kinds from the ONEPASS plan that are not yet covered by Waves 1–3: `r14_correction`, `r16_declared_done`, `r17_promise_debt`, `reply_event_type`, `query_intent`, `sentiment_intent`. The files `r14_correction_learning.py`, `r16_declared_done.py`, `r17_promise_debt.py`, `src/scripts/nexo-send-reply.py`, `src/cognitive/_search.py`, and `src/cognitive/_trust.py` now pass named `decision_kind`s.
- [ ] Router emits telemetry for `route_used`, `degraded`, `model_unavailable`. Expose counters via `nexo doctor --tier runtime`.
- [ ] Latent truthfulness sweep: `src/enforcement_engine.py::on_assistant_message`, `src/tools_drive.py::detect_drive_signal`, and any site with no live caller — either wire or mark `non-runtime` so coverage is not over-claimed.

Entry criteria: Wave 3 exit green.
Exit criteria:
- `grep -rn "enforcement_classifier\|call_model_raw" src` outside the router, the classifier module itself, and explicit compatibility-fallback modules returns 0 matches.
- `nexo doctor --tier runtime` surfaces per-kind telemetry.
- All "latent/dormant" sites either wired OR labeled in-source with a clear non-runtime comment.

---

## Wave D1 — Desktop bridge consumers (R14–R17, R34, session-end)

Files: Desktop (`nexo-desktop/lib/`). Status: NOT STARTED. Current state: the bridge `brain-semantic-router.js` exists but has zero production consumers (0/7 grep'd outside `tests/`).

- [ ] `lib/session-end-intent.js` → `fast_local → semantic_reasoner → remote_fallback` via bridge.
- [ ] `lib/r14-correction-learning.js` → same.
- [ ] `lib/r16-declared-done.js` → same.
- [ ] `lib/r17-promise-debt.js` → same.
- [ ] `lib/r34-identity-coherence.js` → same.

Entry criteria: Wave 1 exit green on the Brain side (analogues).
Exit criteria:
- 5/5 files migrated; each passes an explicit `decision_kind` to the bridge.
- Bridge call coverage ≥95% on replay of the last week of Desktop sessions (spot-checked via telemetry).
- `_defaultClassifier()` in `enforcement-engine.js` no longer defaults to JS-only remote enforcement — reroutes through the bridge.

Why first on Desktop: these are the same durable-mandate / verbal-ack concerns as Brain Wave 1, mirrored on the Desktop surface.

---

## Wave D2 — Desktop higher-risk gates (R20 + T4)

Files: Desktop. Status: NOT STARTED.

- [ ] `lib/r20-constant-change.js` → `semantic_reasoner → remote_fallback` via bridge.
- [ ] `lib/t4-llm-gate.js` — four explicit decision kinds: `t4_r15`, `t4_r23e`, `t4_r23f`, `t4_r23h`. Remove JS-only remote enforcement path.

Entry criteria: Wave D1 exit green AND Brain Wave 2 exit green (to avoid Desktop routing to unmigrated Brain kinds).
Exit criteria:
- 2/2 files migrated.
- T4 fail-closed behaviour preserved (simulated offline bridge still blocks the gate).
- Telemetry shows 0 decisions routed through `lib/enforcement-classifier.js` for these kinds.

---

## Wave D3 — Desktop cleanup + packaging contract

Files: Desktop. Status: NOT STARTED.

- [ ] `lib/enforcement-classifier.js` and `lib/call-model-raw.js`: downgrade to compatibility fallback OR remove in the same pass if fully superseded.
- [ ] Desktop DMG packaging: ship a Brain bundle that contains the router and reasoner surfaces.
- [ ] Desktop update flow: must emit `degraded=true` with explicit `route_used` when the reasoner is not yet provisioned. No silent fallback.
- [ ] Do not introduce a second model download path independent of Brain update logic.

Entry criteria: Wave D2 exit green.
Exit criteria:
- DMG contains the new surfaces (verified via a `dmg --contents` check).
- Fresh-install smoke test confirms degraded mode is explicit while the reasoner provisions.

---

## Cross-cutting exit criteria for the whole migration

- Router is the only owner of decision policy for the named kinds. No caller decides thresholds or model routes locally for a migrated kind.
- Every migrated caller emits `decision_kind`. CI grep fails on any migrated caller that still calls `enforcement_classifier` / `call_model_raw` directly.
- Telemetry dashboard (`nexo doctor --tier runtime` and the DMG flow) reports per-kind `route_used_distribution`, `degraded_rate`, `regression_delta`.
- `enforcement_classifier.py` and `call_model_raw.py` remain as managed fallback infra — referenced only by the router.

## Related work

- Idea: `llm-para-hardcodes` (87 findings catalog) — to be consumed as per-kind decision-kind proposals as waves land.
- Idea: `obedience-matrix-by-area` — dashboard that aggregates the per-wave obedience metric by area (Brain vs Desktop vs rule family).
- Followup `NF-DS-3C5E2318` (post-release live smoke) will add a gate that checks router telemetry is emitting for the kinds migrated by that release — so the obedience metric becomes part of release promotion.

## Change log

- 2026-04-23 — First cut of this roadmap. Confirmed Wave 1 wired in repo; Wave 2 still on legacy classifiers; Desktop bridge has zero production consumers.
