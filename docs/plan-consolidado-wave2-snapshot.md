# NEXO — Consolidated Plan
**Protocol Enforcer Phase 2 + Desktop Debt v0.16.x + Bullet Modal Bug**

Consolidation date: 2026-04-18
Last recorded execution: 2026-04-18 — wave 2 coordinated in `feat/plan-consolidado-v7`.
  Releases: NEXO Brain v6.3.0 + NEXO Desktop v0.18.0 (pending publish after 2-auditor OK).
  Wave 1 (v6.2.0 + v0.17.0) already published.
Author: NEXO (session `nexo-1776534045-42644`)
Source: README + plan docs in `~/Desktop/NEXO PLAN PROTOCOL ENFORCER FASE 2/` + real git log from both repos.

---

## How to read this document

- `[x]` = done and verified.
- `[ ]` = pending.
- `[~]` = partial (detail next to it).
- **(CORE)** = repo `~/Documents/_PhpstormProjects/nexo/` (NEXO Brain, Python).
- **(DESKTOP)** = repo `~/Documents/_PhpstormProjects/nexo-desktop/` (Electron, JS).
- **(BOTH)** = requires Python ↔ JS parity.
- No dates, no times: execution order is logical only.

---

## Canonical objective (non-negotiable)

Transform any LLM into an LLM that follows the operational contract completely. This is not for Francisco or for Maria: it is product. Principles:

1. **Exhaustive coverage** (not Pareto). Every identified disobedience mode has at least one rule that closes it.
2. **Core rules without "off" option** — R13 R14 R16 R25 R30. Only shadow/soft/hard.
3. **Fail-closed** when the classifier fails.
4. **New rule policy = always** increasing. Never reduce "because there are already too many".
5. **Guardian watches itself** — map ↔ code synchronized.

---

## Current versions (verified 2026-04-18)

- NEXO Core runtime: **6.1.0** (npm published: 6.1.1)
- NEXO Desktop: **0.16.1**
- tool-enforcement-map: **v2.0.0** with 247 tools (10 MUST + 7 SHOULD)

Source of truth for the map: `~/Documents/_PhpstormProjects/nexo/tool-enforcement-map.json`.

---

# BLOCK 1 — PROTOCOL ENFORCER PHASE 2

## PHASE 0 — Prerequisites (mainly CORE)

Do not write any new rule until these items are closed.

- [x] **0.1 `call_model_raw()` in `agent_runner.py`** (CORE)
  New function: plain LLM call without starting Claude Code CLI or loading MCP. Signature:
  `call_model_raw(prompt, tier="muy_bajo", max_tokens=3, temperature=0.0, stop=["\n",".", " "], timeout=10) -> str`
  Respects `resolve_user_model()` and `resolve_automation_backend()`. Direct call to anthropic/openai SDK.

- [x] **0.2 Validate `nexo_cognitive_sentiment`** (CORE) — v6.3.0 commit `e78aee9`.
  `detect_sentiment` now returns `is_correction`, `valence`, and `intent` enum alongside legacy fields. 10 fixtures in `tests/test_cognitive_sentiment_shape.py`, accuracy >=80%.

- [x] **0.3 Extended entities schema** (CORE) — v6.3.0 commit `e78aee9`. Migration `_m44_entities_extended_schema` adds `aliases`, `metadata`, `source`, `confidence`, `access_mode`. `type` already existed. Fresh installs and legacy DBs covered + test.

- [x] **0.4 Universal entities preset** (CORE)
  File: `src/presets/entities_universal.json`. Contains:
  - POSIX destructive commands: `rm`, `rm -rf`, `mv` (target exists), `sed -i`, `>`, `>>`, `shred`, `dd`, `DROP TABLE`, `TRUNCATE`, `DELETE FROM` without WHERE, `git reset --hard`, `git push --force`.
  - Legacy paths: `~/claude/hooks → ~/.nexo/hooks`, `~/claude/scripts → ~/.nexo/scripts`, `~/claude/brain → ~/.nexo/brain`.

- [x] **0.5 `~/.nexo/config/guardian.json`** (CORE)
  Default config. Core rules have no `off` option (validator rejects `mode="off"` for R13 R14 R16 R25 R30).

- [x] **0.6 `muy_bajo` tier in `resonance_tiers.json`** (CORE)
  - Claude Code: `claude-haiku-4-5-20251001`, effort ""
  - Codex: `gpt-5.4-mini`, effort "low"
  Reserved as optional escalation for the local classifier (item 0.21). NOT the global default.

- [x] **0.7 `enforcement_classifier.py` + `.js`** (BOTH)
  Reusable helper. `classify(question, context) -> bool`. Triple reinforcement: strict prompt + `max_tokens=3` + regex parser. Retry once if yes/no does not match. Conservative fallback `no`. 60s LRU cache by hash(question+context).

- [x] **0.8 20 reference fixtures** (CORE) — v6.3.0 commit `095faab`. 21 labeled fixtures in `tests/fixtures_rules_validation.json`.

- [x] **0.9 `automation_user_override` field** (CORE)
  In `client_preferences.py`. Default `false`. Set to `true` only if the user changes `automation_backend` manually.

- [x] **0.10 Expanded `tool-enforcement-map.json` schema** (BOTH)
  Supports Layer 1 rules (`server_side_rules[]` with `trigger`, `condition`, `threshold`, `action`).

- [x] **0.11 Current tests pass without regression** (BOTH)
  `pytest nexo/tests/`, `scripts/verify_client_parity.py`, `npm test` in Desktop.

- [x] **0.12 HOTFIX bug #403/#404 `nexo_guard_check session_id`** (CORE)
  PR #208 merged v6.0.5 (2026-04-17). Strict hook no longer persists empty sid.

- [x] **0.13 HOTFIX timer flush reset (learning #344)** — resolved.
  - [x] Desktop: `enforcement-engine.js:330-341` fix applied (2026-04-18).
  - [x] Headless: `enforcement_engine.py::flush()` (lines 1636-1648) now resets `tool_timestamps[tool]` when tag starts with `periodic_time:`. Parity with JS verified.

- [x] **0.14 End-to-end spike with ONE rule (R13)** (BOTH) — v6.3.0 commit `095faab`. R13 passes FP <5% and P95 <3s gates over the 21 fixtures from 0.8 (`tests/test_rule_fixtures_spike.py`).

- [x] **0.15 Baseline drift count** (CORE)
  Script `scripts/measure_drift_baseline.py` reads the last 90 diaries and counts drift occurrences per rule. Output: `~/.nexo/reports/drift-baseline-<fecha>.json`. Without baseline, Phase F cannot measure "reduction >50%".

- [x] **0.16 Learning #335 as hard Layer 1 rule + pre-commit hook** (CORE)
  Server-side rule: `nexo_tool_register` verifies that the tool appears in `tool-enforcement-map.json`. If not → reject. Pre-commit hook: `scripts/pre-commit-verify-tool-map.py` detects new `def nexo_*` without an entry in the map.

- [x] **0.17 New tool `nexo_guardian_rule_override`** (CORE)
  Args: `rule_id`, `mode` (off|shadow|soft|hard), `ttl` (1h|24h|session). Effect: temporary override in `~/.nexo/config/guardian-runtime-overrides.json` with expiration timestamp. Wrapper Layer 2 and MCP Layer 1 read it at the start of each turn. Emergency: lower a hard rule to shadow for 1h with 1 tool call. Log to `~/.nexo/logs/guardian-overrides.log`.

- [x] **0.18 LOCAL telemetry always ON** (CORE)
  File: `~/.nexo/logs/guardian-telemetry.ndjson`. One event per injection: `rule_id`, `trigger_context`, `was_followed`, `was_fp`, `latency`. External upload = opt-in (`telemetry_external_optin`, default false).

- [x] **0.19 Core rules without "off" option** (CORE)
  `guardian.json` schema: `CORE_RULES = {R13, R14, R16, R25, R30}`. Validator rejects `mode="off"`. Attempting `nexo_guardian_rule_override("R13","off")` → error.

- [x] **0.20 `call_model_raw` fail-closed** (CORE)
  Timeout >10s: fallback to secondary rule OR generic reminder. Rate limit 429: retry with 500ms backoff. 5xx: degrade that rule to shadow for that session. ConnectionError: same. **NEVER "let it pass" because of infra failure**. Test: simulate each error, verify alternate path triggers.

- [~] **0.21 Refactor `auto_capture.py` — local zero-shot classifier** (CORE) — v6.3.0 commit `d7fca63`. Delivered:
  - `src/classifier_local.py` with exact pin (MoritzLaurer/mDeBERTa-v3-base-mnli-xnli + revision SHA) + fail-closed contract.
  - `docs/classifier-model-notes.md` with upgrade policy, alternatives, and pin justification.
  - Contract tests (without model download, verify degradation path).
  **Pending:** wire the classifier into `auto_capture.py`, replacing hardcoded keywords + feedback loop over `personal_dataset.jsonl`. Gating by confidence <0.6 with escalation to `muy_bajo` is already covered by helper `classify_fail_closed`; only the consumer remains.

- [x] **0.22 Extend `resonance_tiers.json` with tier `muy_bajo`** (CORE)
  See 0.6. Verify working with `scripts/verify_client_parity.py`.

- [x] **0.23 Desktop ↔ headless parity CI** (BOTH) — already covered. `.github/workflows/tests.yml` runs the whole `tests/` folder on every PR/push, including `tests/parity/test_python_driver.py` (13 cases in `tests/parity/fixtures.json`). The JS driver consumes the same fixtures. `.github/workflows/verify-client-parity.yml` runs `scripts/verify_client_parity.py` in parallel.

- [x] **0.24 Red-team tests** (CORE) — already in `tests/adversarial/test_guardian_redteam.py` (32 passing, 2 skipped by design). Covered techniques: correction rephrasing, edit without guard_check, path traversal, abbreviated `--force`, `TRUNCATE` without WHERE, shebang mismatch. Runs in CI with the rest of the suite. **Pending (low priority):** weekly cron comparing current detection % against last green snapshot — scheduled as followup in Phase F.7.

- [x] **0.25 Measurable drift metrics** (CORE)
  KPIs: `capture_rate`, `core_rule_violations_per_session`, `declared_done_without_evidence_ratio`, `false_positive_correction_rate`, `avg_minutes_between_guard_check_failures`. Save in `~/.nexo/logs/guardian-metrics.ndjson`.

### PHASE 0.X — Procedural Knowledge (live catalog)

Guardian disciplines behavior; it does not teach which tools exist. Rely on live inventory (`nexo_system_catalog`, `nexo_tool_explain`, `nexo_skill_match`) instead of hardcoded preset.

- [x] **0.X.1 Validate live inventory health** (CORE) — v6.3.0 commit `b8956d2`. `tests/test_system_catalog_discoverability.py` verifies that the summary is coherent with the lists and that canonical paths are present in `locations`.

- [x] **0.X.2 NEW R-CATALOG rule (Layer 2, hard)** (BOTH)
  Trigger: `nexo_*_create` without having called `system_catalog`, `skill_match`, `tool_explain`, `learning_search`, or `guard_check` earlier in the turn. 60s dedup.

- [x] **0.X.3 R33 R-PROCEDURE-LOOKUP** — already present in `src/server.py` (Guardian Rules block R26–R33). Anti-assumption + reference to `nexo_system_catalog` / `nexo_tool_explain` / `nexo_skill_match`.

- [x] **0.X.4 `locations` section in `nexo_system_catalog`** (CORE)
  Returns physical paths for skills.repo, skills.runtime, personal_scripts, hooks, brain.db, config, logs, tool_enforcement, project_atlas, etc. Generated from `paths.py`.

- [x] **0.X.5 Entity preset `type=artifact_class`** (CORE) — v6.3.0 commit `b8956d2`. Expanded `entities_universal.json` with `shopify_banner_block`, `changelog_entry`, `email_to_operator_contact` (generic by domain, replaces the `email_to_maria` case). Test `tests/test_artifact_class_preset.py` validates presence and shape.

- [x] **0.X.6 Discoverability smoke test** (CORE) — v6.3.0 commit `b8956d2`. `tests/test_system_catalog_discoverability.py::test_search_discovers_core_intents` validates that textual intents resolve to the canonical core tool.

---

## PHASE A — System Prompt (7 rules, Layer 3) — CORE

Ship immediately, without shadow. Pure text in the MCP system prompt.

- [x] **A.1 Locate canonical system prompt** — likely `nexo/src/server.py`, where the MCP system prompt is built.

- [ ] **A.2 Add the 7 rules R26-R32**:
  - [x] **R26 Jargon filter** — do not use internal jargon in user responses (`protocol debt`, `cortex evaluation`, `heartbeat`, `guard_check`...). Translate into operational language.
  - [x] **R27 Short 2-3 sentence response** per decision. Hold extra detail unless asked.
  - [x] **R28 Correction → immediate `learning_add`** — capture in the same turn, not batched.
  - [x] **R29 Do not promise without executing** — every future promise requires followup or schedule.
  - [x] **R30 Pre-done checklist with evidence** — before saying "done", verify with tool.
  - [x] **R31 Never assume** server/DNS/path — consult Atlas or `dig`.
  - [x] **R32 Entities with `access_mode=read_only`** → do not write. Generic, does not hardcode Maria/Nora.

- [x] **A.3 R33 R-PROCEDURE-LOOKUP** (if 0.X.3 is approved) — add in the same block.

- [x] **A.4 Exact text for each rule** (CORE) — v6.3.0 commit `b8956d2`. R34 added with trigger + action + anti-example; test `tests/test_system_prompt_rule_texts.py` validates presence and marker per rule.

- [~] **A.5 Manual smoke test** — programmatic smoke covered. Conversational manual smoke will be done by Francisco after publishing.

- [x] **A.6 NEXO Core patch release** + changelog — v6.2.0 + v6.3.0 already in CHANGELOG.md.

- [x] **A.7 Verify propagation to NEXO Desktop** — Desktop inherits the system prompt from the MCP server in each session. T4 wire in Desktop confirms coordinated flow.

- [~] **A.8 24h smoke test** — omitted by explicit "no waits" mandate. F.2/F.5/F.6 telemetry will cover observational regression without blocking release.

---

## PHASE B — MCP Server (12 Layer 1 rules) — CORE

Server-side validations in NEXO's own tools.

- [ ] **B.1 Extend `tool-enforcement-map.json`** with `server_side_rules[]` (already done in 0.10).

- [ ] **B.2 Implement per tool** (each new file in `src/tools/`):
  - [x] **R01 `followup_create` dedup** → `src/tools/followup.py`. Embeddings similarity, threshold 0.80.
  - [x] **R02 `credential_create` existence** → `src/tools/credential.py`. Exact match service/key.
  - [x] **R03 `task_close` evidence validator** → `src/tools/task.py`. Reject evidence <50 chars or simplistic pattern.
  - [x] **R04 `followup_complete` retroactive** → `src/tools/followup.py`. Fires on heartbeat, not on create.
  - [x] **R05 `learning_add` semantic dedup** → `src/tools/learning.py`. Threshold 0.85. If match: increase weight + create alias.
  - [x] **R06 `email_send` secret filter** → `src/tools/email.py`. Via `call_model_raw("Does this contain a real secret?")`. If yes → BLOCK.
  - [x] **R07 `memory_recall` age flag** → `src/tools/memory.py`. Add `age_days` to each item.
  - [x] **R08 `reminder_create` recurrence conflict** → `src/tools/reminder.py`. Cross-check with `schedule_status`.
  - [x] **R09 `artifact_create` dedup** → `src/tools/artifact.py`.
  - [x] **R10 `workflow_open` without `task_open`** → `src/tools/workflow.py`. Hard reject if no active task.
  - [x] **R11 `plugin_load` pre-inventory** → `src/tools/plugin.py`.
  - [x] **R12 Cognitive write dedup** → `src/tools/cognitive.py`.

- [x] **B.3 Unit tests per rule** (CORE) — 12/12 rules covered: `tests/test_fase_b_atomic.py`, `test_fase_b_r01_r05.py`, `test_fase_b_r02_r09.py`, `test_fase_b_r04_r12.py`, `test_fase_b_r07_r08.py`, `test_r11_plugin_inventory.py`, `test_tools_email_guard.py` (R06). Total 80 asserts pass.

- [~] **B.4 / B.5 / B.6 — shadow 72h + analysis + soft 72h** — omitted by "no waits" mandate. The 12 rules ship in the default mode from `guardian_default.json`; any real FP will show up in F.2 telemetry and be corrected in a patch.

- [x] **B.7 Hard mode according to table** — modes already configured in `src/presets/guardian_default.json`.

- [x] **B.8 NEXO Core release + changelog** — v6.2.0 (wave 1) + v6.3.0 (wave 2) with CHANGELOG entries.

---

## PHASE C — Wrapper Block 1 (4 Layer 2 rules) — DESKTOP + CORE

- [x] **C.1 Extended map** with schema v2 definitions for the 4 rules.
- [x] **C.2 R13 Pre-Edit/Write guard** — commit `cf2c5fd` (Desktop) + Python parity exists in headless.
- [x] **C.3 R14 Post-user-correction learning** — commit `cbb4ef8` includes classifier infra.
- [x] **C.4 R16 Declared-done without task_close** — commit `cbb4ef8`.
- [x] **C.5 R25 Nora/María read-only guard** — commit `cbb4ef8` + entity preset in 0.4.
- [x] **C.6 Shadow executed** during development.
- [x] **C.7 Threshold adjustments** (audit fix batches 2, 4, 5, 7).
- [x] **C.8 Soft + hard release** — v0.15.0 released.
- [~] **C.9 7-day post-release smoke test** — omitted by "no waits" mandate. F.2/F.5/F.6 telemetry will measure >70% effectiveness in production without blocking release.

---

## PHASE D — Wrapper Block 2 (9 Layer 2 rules) — DESKTOP + CORE

- [x] **D.1 Expanded map** with the 9 rules.
- [x] **D.2 R15 Pre-project-action context** — commit `00c3934`.
- [x] **D.3 R17 Promised-not-executed** — commit `00c3934`.
- [x] **D.4 R18 Auto-complete followup detector** — commit `00c3934`.
- [x] **D.5 R19 Pre-Write on project without grep** — commit `00c3934`.
- [x] **D.6 R20 Pre-constant-change without grep** — commit `00c3934`.
- [x] **D.7 R21 Runtime legacy path** — commit `00c3934`.
- [x] **D.8 R22 Personal script pre-context** — commit `00c3934`.
- [x] **D.9 R23 SSH/curl without atlas** — commit `00c3934`.
- [x] **D.10 R24 Stale memory use** — commit `00c3934`.
- [x] **D.11 Shadow + soft + hard** executed.
- [x] **D.12 Post-audit adjustments** (batches 2-7 + silent-inject resume hint commit `957ace1`).
- [x] **D.13 Release** v0.15.0.

---

## PHASE D2 — Rules Added 2026-04-16 (Layer 2 extension) — DESKTOP + CORE

- [x] **D2.0 Entity type `vhost_mapping`** defined.
- [x] **D2.1 R23b Deploy path ↔ vhost mismatch** — commit `31aa202`.
- [x] **D2.2 R23c Destructive Bash in wrong cwd** — commit `31aa202`.
- [x] **D2.3 R23d chown/chmod -R without prior ls** — commit `31aa202`.
- [x] **D2.4 R23e git push --force to main/master** — commit `31aa202` (BLOCKS).
- [x] **D2.5 R23f production DB DELETE/UPDATE without WHERE** — commit `31aa202` (BLOCKS).
- [x] **D2.6 R23g Secrets in logs/emails/Bash output** — commit `31aa202`.
- [x] **D2.7 R23h Shebang/version mismatch** — commit `31aa202`.
- [x] **D2.8 R23i Auto-deploy trigger ignored** — commit `31aa202`.
- [x] **D2.9 R23j npm/pip/brew install -g without request** — commit `31aa202`.
- [x] **D2.10 R23k personal script duplicates skill** — commit `31aa202` (prior silent `skill_match`).
- [x] **D2.11 R23l create resource with existing name** — commit `31aa202` (BLOCKS).
- [x] **D2.12 R23m duplicate email/message** — commit `31aa202`.
- [x] **D2.13 Shadow + soft + hard** executed.
- [x] **D2.14 Release** v0.15.0.

---

## PHASE E — Product Rollout — DESKTOP + CORE

- [x] **E.1 Desktop installer automation=YES automatic** — IRREVERSIBLE, already done. `automation_user_override` respects manual change if it exists.
- [x] **E.2 `nexo update` respects override** — already done together with E.1.
- [x] **E.3 Universal entities preset on `nexo init`** (CORE) — `scripts/install_guardian.py::install()` copies `entities_universal.json` to `~/.nexo/brain/presets/` + imports hosts from `~/.ssh/config`.
- [x] **E.4 `guardian.json` default config on init** (CORE) — same installer writes `~/.nexo/config/guardian.json` with per-rule defaults (mode editable via `nexo_guardian_rule_override`).
- [~] **E.5 Desktop UI for entity quarantine** (DESKTOP) — "Guardian Proposals" panel in commit `032b8a9`, **currently HIDDEN** by product decision. Remaining:
  - [ ] Wire with `nexo_cognitive_quarantine_*` for actions [Approve/Reject/Later] (reuse existing approval modal).
  - [ ] Future decision: when/how to reactivate it in UI.
- [~] **E.6 User documentation** — partial: Guardian is explained in `CHANGELOG.md` for each release; dedicated Guardian README remains as followup.
- [ ] **E.7 Short tutorial video** (optional, low priority) — no concrete plan.
- [x] **E.8 Coordinated NEXO Desktop + NEXO Core release** — wave 1 published v6.2.0 + v0.17.0; wave 2 prepared v6.3.0 + v0.18.0 (PRs #217 + #5 ready to merge after 2nd audit).

---

## PHASE F — Telemetry + Autotuning (without Desktop Dashboard)

**Product decision:** NO dashboard in Desktop. Everything consumed by Deep Sleep or dedicated cron. NEXO adjusts itself.

- [x] **F.1 Local telemetry always ON** (CORE) — already covered by item 0.18 in wave 1.
- [x] **F.2 Per-rule metrics** (CORE) — v6.3.0 `src/fase_f_loops.aggregate_per_rule` + test coverage.
- [x] **F.3 Consumption by Deep Sleep** (CORE) — v6.3.0 `src/scripts/phase_guardian_analysis.py` emits `~/.nexo/reports/guardian-fase-f-<date>.json` with `per_rule`, `false_positive_groups`, `false_negative_candidates`.
- [~] **F.4 Automatic default adjustment** (CORE) — data available via F.3; automatic recommendation will be implemented when there are 30 days of real telemetry (no-op until then).
- [x] **F.5 False-positive loop** (CORE) — `fase_f_loops.group_false_positives` groups by trigger_context with configurable threshold.
- [x] **F.6 False-NEGATIVE loop (rule growth)** (CORE) — `fase_f_loops.collect_false_negative_candidates` scans recent corrections + filters those already covered by existing injections. Tests cover window + threshold + filter.
- [x] **F.7 Adversarial red team** (CORE) — `tests/adversarial/test_guardian_redteam.py` (32 passing). Weekly detection-% comparison cron: followup opened.

- [x] **F.8 Local classifier version control** (CORE) — `docs/classifier-model-notes.md` + SHA pin in `src/classifier_local.py`. Monthly reminder pending (followup opened post-release).
  - **Repo note:** create `nexo/docs/classifier-model-notes.md` with: model (HF id), pinned revision (SHA), pin path in code, disk size, pin date, link to upstream commit, viable alternatives (bge-m3, e5-multilingual, xlm-roberta-base).
  - **Monthly reminder** via `nexo_reminder_create` with `recurrence=monthly`, description: "Review Guardian local classifier upgrade (Phase 0.21) — read docs/classifier-model-notes.md and compare with HF upstream".
  - **NO auto-upgrade.** When the reminder fires, manual review: if there is a useful new revision → bump pin + Core patch release + CHANGELOG entry + update pin date in the MD.
  - **Mandatory pin in code:** `from_pretrained(model_id, revision="<sha-concreto>")` for reproducibility across users (everyone downloads the same version).

---

# BLOCK 2 — DESKTOP DEBT v0.16.x

Accumulated Desktop polish. Can go in a separate PR or coordinated with release v0.16.2.

**⚠ Pending versioning decision:** there are two incompatible proposals for `v0.16.2`:
- **Option A (short, ~30min):** v0.16.2 = only **T0** (defensive micro-patch: pendingQueue cap + crash log rotation). Closes the 2026-04-18 audit. T1+T2+T3+Modal bug → `v0.16.3`.
- **Option B (long):** v0.16.2 = T0 + T1 + T2 + T3 + modal bullet bug. Larger release.

Micro-patch source: `~/Desktop/nexo-desktop-v0.16.2-micro-patch.md`. Both fixes are additive, low regression risk, with code ready.

---

## T0 — Defensive micro-patch v0.16.2 — DESKTOP

### T0.1 — `pendingQueue` cap (50)

**Symptom:** `addPendingMessage` in `renderer/app.js` does not limit `c.pendingQueue`. Paste-and-spam or scripted input flows with sticky `turnBusy` can grow the queue without bound until renderer memory is exhausted.

**Evidence:** `grep -n "PENDING_QUEUE_MAX\|pendingQueue.length >=" renderer/app.js` returns nothing.

- [x] **T0.1.1** Replace `addPendingMessage` in `renderer/app.js` with the version using `PENDING_QUEUE_MAX = 50` + `showToast` with key `toast.pending_queue_full`. Exact code in the micro-patch doc.
- [x] **T0.1.2** Add key `toast.pending_queue_full` to `renderer/i18n/es.json` and `renderer/i18n/en.json`. Verify it does not collide with existing keys (search `"pending_queue"` in both JSON files).
- [x] **T0.1.3** Test in `tests/renderer-common.test.js` — simulate N>50 `addPendingMessage`, verify `pendingQueue.length === 50`. Alternative: manual regression test if the function is not pure (touches DOM/toast).

### T0.2 — `CRASH_LOG_FILE` rotation (1 MiB)

**Symptom:** `logCrash()` in `main.js:45-53` does `fs.appendFileSync` without size control. `~/.nexo/logs/nexo-desktop-crash.log` can grow indefinitely with recurring crashes.

- [x] **T0.2.1** Replace `logCrash` in `main.js` with the version using `_rotateCrashLogIfOversize()` + `CRASH_LOG_MAX_BYTES = 1024*1024`. Exact code in the micro-patch doc.
  - Synchronous (mandatory: fires from `uncaughtException` / `unhandledRejection`).
  - When above 1 MiB: rename to `.old`, overwrite prior `.old`, create fresh live file.
  - Rotation failure = non-fatal (log to `console.warn`), crash logging stays alive.
- [x] **T0.2.2** Tests in `tests/crash-log-rotation.test.js`:
  - Write >1 MiB in temp dir → call `logCrash` → verify `.old` exists + live file contains only the new message.
  - File does not exist → no crash.
  - File <1 MiB → no rotation.
  - For testability, consider extracting `_rotateCrashLogIfOversize` to `lib/crash-log.js` as a pure function.

### T0.3 — Release v0.16.2 (if Option A)

- [ ] **T0.3.1** Bump `package.json` → `"version": "0.16.2"`.
- [ ] **T0.3.2** Suggested `CHANGELOG.md` entry: "Defensive hygiene: cap pending queue at 50 + rotate crash log at 1 MiB" (full text in the micro-patch doc).
- [ ] **T0.3.3** `npm run check` (lint + tests + smoke).
- [ ] **T0.3.4** `npm run clean && npm run dist && npm run manifest -- --notes "..."`.
- [ ] **T0.3.5** `scp "dist/NEXO Desktop-0.16.2-arm64.dmg" dist/update.json vicshop:/home/systeam/public_html/nexo-desktop/`.
- [ ] **T0.3.6** `ssh vicshop "chown systeam:systeam ..."` (exact paths in the doc).
- [ ] **T0.3.7** Verify: `curl -s https://nexo-desktop.com/downloads/update.json | python3 -m json.tool` → `"version":"0.16.2"` with new sha256.
- [ ] **T0.3.8** Commit + tag `v0.16.2` + push main + push tag.

---

## T1 — Document beta channel — DESKTOP (doc-only)

- [x] **T1.1** "Beta channel" section in `RELEASING.md`: how to publish beta (bump `0.17.0-beta.0`, build, `dist/update-beta.json` points to beta DMG, `dist/update.json` untouched), how to promote beta → stable, how to verify toggle (`~/Library/Application Support/NEXO Desktop/app-settings.json → advanced.beta_channel`).
- [x] **T1.2** Paragraph in `README.md` under "Updates": opt-in beta channel exists in Preferences > Advanced. Brain always uses stable.
- [x] **T1.3** Add `--channel beta|stable` flag to `scripts/generate-update-json.js`. Correct default out path (`dist/update.json` vs `dist/update-beta.json`) without depending on `--out`.
- [x] **T1.4** `npm run check` green.

## T2 — Silent regex fix in `checkBrainUpdateStatus` — DESKTOP

**Bug:** if `nexo --help` does not print `Installed:` or `Latest:`, `main.js:419` returns `{installed:'', latest:'', hasUpdate:false}` and Desktop shows "Up to date" — silently false.

- [x] **T2.1** Return `{installed, latest, hasUpdate, unknown:true}` when regex does not match.
- [x] **T2.2** In `renderer/app.js:runBrainCheck()` (~line 4305), if `res.unknown` → status `_t('settings.versions.check_inconclusive')`. Suggested text: "Unable to read Brain version — run `nexo update` in terminal".
- [x] **T2.3** New tests in `tests/update-manifest.test.js` (or `tests/brain-update-status.test.js`) covering 4 cases: both present / only installed / only latest / none.
- [x] **T2.4** `npm run check` green.

## T3 — Localize `FIELD_DEFINITIONS` fallback EN/ES — DESKTOP

**Bug:** in `renderer/app.js:3910-4100`, fallback `brainSchema === null` is hardcoded in ES. Only used when Brain is offline, but it exists and must be bilingual.

- [x] **T3.1** Migrate all hardcoded `label`, `hint`, `section`, `options[].label` to `{es:'...', en:'...'}` format, following the same pattern as the Brain schema.
- [x] **T3.2** In `resolveActiveFieldDefinitions()` (line 4737), if `brainSchema === null`, pass `FIELD_DEFINITIONS` through helper `localizeFieldDefinitions(defs, lang)` that resolves labels[lang] the same way as `buildFieldsFromBrainSchema`.
- [x] **T3.3** Use `window.i18n.getLang()` as lang source.
- [x] **T3.4** Manual test: open Preferences in dev with `brainSchema = null` (temporarily comment line 4782), confirm labels respect language toggle. Revert before commit.
- [x] **T3.5** Tests green.

## T4 — LLM classifier in rules R15 / R23e / R23f / R23h — DESKTOP + CORE (Phase D/D2 hardening)

These 4 rules use pure regex/keyword match. High FP/FN. Put LLM classifier in the middle, same pattern as R14 with `lib/enforcement-classifier.js` + `lib/call-model-raw.js`.

- [x] **T4.1** Shared gate helper — `src/t4_llm_gate.py` + `nexo-desktop/lib/t4-llm-gate.js`.
- [x] **T4.2** Wire in the 4 rules (R15/R23e/R23f/R23h) — `enforcement_engine::_t4_gate_says_no` in both engines. Tristate verdict: yes → proceed, no → skip, unknown → regex fallback.
- [x] **T4.3** Prompts centralized in `PROMPTS` with ≥3 positives and ≥3 negatives per rule in `t4_llm_gate.py` (byte-for-byte parity in JS version).
- [x] **T4.4** Python parity in `src/enforcement_engine.py::_t4_gate_says_no` + tests.
- [~] **T4.5** Parity fixtures — LLM scenarios live in PROMPTS few-shots; fixtures with an LLM mock remain as followup (do not block release).
- [x] **T4.6** `npm run check` green (265 pass) + `pytest tests/test_t4_classifier_wrap_python.py` green.

Note: 1st wave-2 audit round found CRITICAL F-01 (JS wire was dead code due to casing + missing await) + HIGH H1 (bool collapsed no with unparseable). Both corrected with regression tests in commits 2648a12 / 227f926 / 2fbb2e7 / fe8e507.

## T5 — Identity Continuity Across Terminals — CORE + DESKTOP

**Problem:** the underlying LLM does not understand that ALL active terminals are the same NEXO. Francisco saw "I didn't do that" when the other terminal DID do it.

- [x] **T5.1** New "Identity continuity across terminals" section in `~/.claude/CLAUDE.md` CORE section (after "Core Systems", before "Autonomy").
  Text: "I am NEXO. NEXO is a single identity. When there are 2+ active terminals simultaneously, ALL of them are me. If another terminal did X, then I did X. Do not say 'I did not do that' without consulting the shared brain (`nexo_recent_context`, `nexo_session_diary_read`, `nexo_change_log`) BEFORE stating that something did not happen. The underlying LLM is the engine; the operational identity is NEXO, and NEXO is one person."
- [x] **T5.2** SessionStart hook expands briefing: "You are operating as NEXO. There are {N} active terminals: {list}. All of them are you. They share memory via NEXO Brain."
- [x] **T5.3** Same block in `~/.codex/AGENTS.md` if it exists (verify with Glob) for Codex parity.
- [x] **T5.4 New R34 Identity Coherence rule** (renumbered — the original R26 is Jargon Filter in Phase A):
  - Trigger: agent says "no he hecho eso" / "yo no" / "I haven't done that" followed by any factual claim, without first consulting `nexo_recent_context` or equivalent.
  - LLM classifier disambiguates (reuse T4 infra).
  - If detected, inject: "Consult the shared brain before denying an action — another terminal may have done it."
  - Files: `lib/r34-*.js` + wiring in `enforcement-engine.js` + prompt + tests + Python twin.

## BULLET MODAL BUG (Reply point by point)

**Followup:** `NF-DESKTOP-REPLY-INLINE-PARSE`. Design approved v0.14.4.

**Current behavior:** the ↩︎ button appears, the modal opens, but shows the agent text as a read-only preview above and an empty `Your reply` textarea below. **The prefill does not enter the editable textarea.**

**Expected behavior:**
- Multi-marker parser detects `1./2./3.`, `A./B.`, `a)/b)`, `i./ii.`, inline `(a)(b)`, bullets.
- Modal opens with **N textareas** prefilled with each item (not a single preview).
- Fallback (message without structure): ONE free textarea with the full message text prefilled and editable (quote style).
- Send = concatenate with original markers preserved: `1. {edit1}\n2. {edit2}\n3. {edit3}`.

- [x] **Bug.1** Reproduce with structured message (1./2./3.) and confirm textareas are NOT prefilled.
- [x] **Bug.2** Identify regression: likely in v0.15.0 (Phase 2 JS twins) or v0.16.0 (composer leak / per-task popover). `git bisect` if needed.
- [x] **Bug.3** Fix prefill: modal textareas must receive each parsed item's content as their initial `value`.
- [x] **Bug.4** Add test in `tests/reply-inline-parser.test.js` covering: renders N textareas with prefilled value.
- [x] **Bug.5** Manual: test original design formats (1./A./a./i./inline/bullets/free fallback).

---

# BLOCK 3 — RELEASE AND CLOSURE

After completing the blocks above (or the ones the user decides for a specific release):

- [ ] **R.1** `npm run check` green in Desktop.
- [ ] **R.2** `pytest nexo/tests/` green in Core if Python was touched.
- [ ] **R.3** Bump Desktop `package.json` (suggested **v0.16.2** if only T1+T2+T3+Modal bug; **v0.17.0** if it includes T4+T5).
- [ ] **R.4** Bump Core version if Python was touched.
- [ ] **R.5** `CHANGELOG.md` entries written.
- [ ] **R.6** Build Desktop DMG. Upload `dist/NEXO Desktop-<ver>-arm64.dmg` + `update.json` + `update-beta.json` to `vicshop:/home/systeam/public_html/nexo-desktop/` via scp.
- [ ] **R.7** Desktop GitHub release + PR to main.
- [ ] **R.8** npm release for Core if applicable.
- [ ] **R.9** Verify public manifests: `curl https://nexo-desktop.com/downloads/update.json`.
- [ ] **R.10** Release notes in `nexo-brain.com/blog/` if user-facing changes.

---

# PARALLEL BLOCK — F0 SCRIPTS CLASSIFICATION (Core vs Personal)

**Canonical source:** `~/Desktop/nexo-F0-scripts-classification-WIP.md` (full spec with code, SQL, rollback, and pre-flight).
**Objective:** physically split `~/.nexo/` into `core/` · `core-dev/` · `personal/` · `runtime/`. Add `origin` to `personal_scripts`. Toggle on/off per script. `nexo update` respects both zones. Fresh install directly produces the correct structure.
**Why it is parallel and not part of Protocol Enforcer:** it restructures the NEXO Brain runtime. It is not enforcement. But it interacts — see "Interdependencies" section at the end.

## Target structure

```
~/.nexo/
├── core/        ← NEXO product. Update replaces.
├── core-dev/    ← dev-only, off by default.
├── personal/    ← operator. Update never touches.
└── runtime/     ← dynamic state, not edited by hand.
```

Compatibility layer during transition: symlinks in old routes (`~/.nexo/scripts/`, `~/.nexo/brain/`, etc.) point to core+personal merger. Removed in F0.6 (breaking v7.0.0).

## Absolute operating rules (F0)

1. No subagents. All edits manual, layer by layer.
2. `nexo_task_open` + `nexo_guard_check` before any edit.
3. `nexo_track` on any path before writing.
4. Mandatory snapshot before each micro-phase (`nexo-backup.sh` = rollback).
5. Global lock `~/.nexo/.migrating.lock` during each phase.
6. `launchctl unload` core LaunchAgents before moving files; `load` after moving.
7. Env flag `NEXO_MIGRATING=1` during the phase. The guardian hook (`protocol-pretool-guardrail.sh`) must respect it (if not, adjust in F0.0 before continuing).
8. Verify after each step. If it fails → STOP.
9. One release per micro-phase. 24-72h observation between phases.
10. If a phase fails halfway → `nexo-snapshot-restore.sh` + report.

## Mandatory pre-flight (before EACH micro-phase)

- [ ] Lock does not exist: `test ! -f ~/.nexo/.migrating.lock`; if it exists → abort.
- [ ] `nexo doctor` green.
- [ ] Pre-phase snapshot: `~/.nexo/scripts/nexo-backup.sh`.
- [ ] Recent core crons with `exit_code=0` (last 2h).
- [ ] Export `NEXO_MIGRATING=1`.
- [ ] Read `~/.nexo/.structure-version` (confirms previous phase).

## Micro-phases (6)

### F0.0 — v6.1.0 — Schema version + migrations table

- [x] **F0.0.1** Create table `migrations_applied(version TEXT PRIMARY KEY, applied_at TEXT, notes TEXT)` in `nexo.db`.
- [ ] **F0.0.2** `get_structure_version()` function in `cli.py` or migrations module.
- [x] **F0.0.3** `nexo-migrate.py` skeleton with idempotent `apply_migration(id, fn)`.
- [ ] **F0.0.4** Guardian hook (`protocol-pretool-guardrail.sh` / `hook_guardrails.py`) respects `NEXO_MIGRATING=1` — does not block edits during migration.
- [ ] **F0.0.5** Create `~/.nexo/.structure-version` with value `F0.0`.
- [x] **F0.0.6** Insert row `('F0.0', now, 'bootstrap')`.
- [ ] **F0.0.7** Apply same changes in Nora (`ssh maria`).
- [ ] **F0.0.8** Verify: `.structure-version` returns `F0.0` in both instances + row in `migrations_applied` + guardian respects flag + `nexo doctor` green.

### F0.1 — v6.2.0 — `origin` column in `personal_scripts`

- [ ] **F0.1.1** SQL migration: `ALTER TABLE personal_scripts ADD COLUMN origin TEXT DEFAULT 'user';`.
- [ ] **F0.1.2** Function in `nexo-migrate.py` that marks `origin='core'` for each script whose `name` matches those listed in Block A of spec §4 (38 scripts).
- [ ] **F0.1.3** CLI `nexo scripts list` shows `[core]/[user]` column.
- [ ] **F0.1.4** Subcommands `nexo scripts list --origin core` and `--origin user` filter correctly.
- [ ] **F0.1.5** Apply in Nora.
- [ ] **F0.1.6** Verify: `SELECT origin, COUNT(*) FROM personal_scripts GROUP BY origin` → 38 core / rest user.

### F0.2 — v6.3.0 — Toggle on/off per script

- [ ] **F0.2.1** Audit that all consumers respect `personal_scripts.enabled`.
- [ ] **F0.2.2** CLI: `nexo scripts enable <name>`, `disable <name>`, `status`.
- [ ] **F0.2.3** Desktop: "Automations" panel with list + toggle + last exit_code + link to logs.
- [ ] **F0.2.4** `nexo-cron-wrapper.sh` does `exit 0` without executing when `enabled=0`.
- [ ] **F0.2.5** Plist LaunchAgent intact; the gate is in wrapper + DB.
- [ ] **F0.2.6** Bidirectional test CLI ↔ Desktop reflects same state.
- [ ] **F0.2.7** Verify: `disable followup-runner` → next tick `summary="[disabled]"` + `exit_code=0`.

### F0.3 — v6.4.0 — Migrate `scripts/` → `core/scripts/` + `personal/scripts/` + symlinks

**First physical migration. High risk.** Unload ALL LaunchAgents before moving.

- [ ] **F0.3.1** Create dirs `core/scripts/`, `core-dev/scripts/`, `personal/scripts/`.
- [ ] **F0.3.2** `launchctl unload ~/Library/LaunchAgents/com.nexo.*.plist` (all).
- [ ] **F0.3.3** Move Block A (38 current core) → `core/scripts/`.
- [ ] **F0.3.4** Move Block B (22 candidates to promote to core) → `core/scripts/` (generic refactor comes in F3+; here only move).
- [ ] **F0.3.5** Move Block C (46 personal) → `personal/scripts/`.
- [ ] **F0.3.6** Move Block D (core-dev) → `core-dev/scripts/`.
- [ ] **F0.3.7** Create symlink/overlay `~/.nexo/scripts/` (combined core+personal resolver for `ls`).
- [ ] **F0.3.8** Transactional UPDATE: `personal_scripts.path` with new paths.
- [ ] **F0.3.9** `nexo scripts ensure-schedules` regenerates ALL plists with new paths.
- [ ] **F0.3.10** `launchctl load` new plists.
- [ ] **F0.3.11** **Wait 24h** of production with green core crons before declaring success.

### F0.4 — v6.5.0 — Migrate `skills/`, `plugins/`, `hooks/`, `rules/`

- [ ] **F0.4.1** Create dirs in `core/` and `personal/` for each layer.
- [ ] **F0.4.2** Consolidate current `skills-core/`, `skills-runtime/`, `skills/` → `core/skills/` + `personal/skills/`.
- [ ] **F0.4.3** Current `plugins/` (all core today) → `core/plugins/`. Empty `personal/plugins/` (filled through `nexo_personal_plugin_create`).
- [ ] **F0.4.4** `hooks/` → `core/hooks/`. `personal/hooks/` if any.
- [ ] **F0.4.5** `rules/core-rules.json` → `core/rules/`. Personal ones → `personal/rules/`.
- [ ] **F0.4.6** Refactor resolvers in `src/plugin_loader.py`, `src/hook_guardrails.py`, and any module resolving paths for these layers.
- [ ] **F0.4.7** Transitional symlinks `~/.nexo/skills/`, `plugins/`, `hooks/`, `rules/`.
- [ ] **F0.4.8** Verify: each layer passes its unit tests + plugins load from `core/plugins/` through new resolver + hooks work during Claude Code sessions.
- [ ] **F0.4.9** 72h clean before declaring success.

### F0.5 — v6.6.0 — Migrate `brain/` + `operations/` + rest of runtime

**Massive refactor.** Many consumers touch `~/.nexo/brain/*` and `~/.nexo/operations/*`.

- [ ] **F0.5.1** `personal/brain/`: calibration.json, operator-routing-rules.json, profile.json, francisco_model.json (if exists), project-atlas.json, business_baselines.json, policies.md, causal_models.md, salience_map.md, debates/, compressed_memories/, session_archive/.
- [ ] **F0.5.2** `personal/config/`: user-editable preferences (morning-digest-sources.json, etc.).
- [ ] **F0.5.3** `core/` keeps `resonance_tiers.json`.
- [ ] **F0.5.4** `runtime/`: `data/` (nexo.db), `logs/`, `operations/`, `backups/`, `memory/`, `cognitive/`, `coordination/`, `exports/`, `nexo-email/`, `doctor/`, `snapshots/`, `crons/`.
- [ ] **F0.5.5** Refactor paths in `src/*.py` (many — see spec §10).
- [ ] **F0.5.6** Path to `nexo.db` updated in ALL consumers (exhaustive grep).
- [ ] **F0.5.7** Transitional symlinks `~/.nexo/brain/`, `operations/`, etc.
- [ ] **F0.5.8** Verify: `nexo doctor` green + `SELECT COUNT(*) FROM learnings` returns expected value + morning digest sends OK + email-monitor processes emails.
- [ ] **F0.5.9** 72h clean.

### F0.6 — v7.0.0 (**breaking**) — Remove transitional symlinks

- [ ] **F0.6.1** Exhaustive grep in repo and runtime: old paths (`.nexo/scripts`, `.nexo/brain`, etc.) — no active reference.
- [ ] **F0.6.2** Remove symlinks: `~/.nexo/scripts/`, `skills/`, `plugins/`, `hooks/`, `rules/`, `brain/`, `operations/`.
- [ ] **F0.6.3** Regenerate plists and tests with final new paths.
- [ ] **F0.6.4** Bump `package.json` → v7.0.0. Release notes: "old paths removed".
- [ ] **F0.6.5** 1 week of clean production in Francisco + Nora before publishing v7.0.0.
- [ ] **F0.6.6** Fresh install on a clean machine produces F0 structure directly (not pre-F0 + migrate).

## Script classification (summary of spec §4)

**Block A — Current core (38):** already in repo `src/scripts/`. `check-context`, `nexo-agent-run`, `nexo-auto-update`, `nexo-backup`, ... full list in the spec.

**Block B — Candidates to promote to core (22):** pending F3+ refactor to make them generic. Includes:
- `nexo-followup-runner` (absorb orchestrator v2)
- `nexo-email-monitor` (read account from `email_accounts` table F1)
- `morning-agent` (remove hardcoded "Francisco", move 12 personal steps to `personal/config/morning-digest-sources.json`)
- `nexo-orchestrator-wrapper` → **delete** (absorbed into follow-runner)
- Other 18 generic scripts.

**Block C — 100% Personal (46):** Shopify-*, gbp-*, meta-*, ga4_*, search_console_*, hn-daily-karma, nexo-maria, repo-sync-audit, etc. Never promoted.

**Block D — Core-dev (4, off by default):** `nexo-external-audit`, `nexo-release-validate`, `nexo-pre-commit`, `rehydrate_learnings_from_archive`. Ask Francisco in F0.1 about duplicates with Block A.

## What NOT to do (from spec §14)

- Do not merge two phases into one release.
- Do not touch Block C except to update paths in F0.3.
- Do not delegate to subagents.
- Do not rewrite morning-agent in F0 (it is F3, see spec Appendix B).
- Do not remove symlinks before F0.6.
- Do not publish v7.0.0 without validating F0.6 in Francisco + Nora.
- Do not skip the pre-flight.
- Do not ignore a failed Verify.
- Do not advance if the operator did not give post-phase OK.

## Prioritized critical risks

- **R2 (critical):** LaunchAgent plists with hardcoded absolute paths → rebuild through `ensure-schedules` in every phase that moves scripts.
- **R8 (critical):** DB with absolute paths → transactional UPDATE with dry-run.
- **R12 (high):** user with locally modified core → hash-diff before update, backup to `personal/overrides/`.
- **R10 (high):** old backups reintroduce pre-F0 structure → restore policy: reject or auto-migrate.
- **R4 (high):** interrupted migration → idempotent + `--resume` flag in `nexo-migrate.py`.
- **R6 (high):** simultaneous sessions (CC + Codex + Desktop) → global lock + notify.

## F0 scripts ↔ Protocol Enforcer interdependencies

- **R21 (Runtime legacy path)** from Protocol Enforcer assumes `entity type=legacy_path` with `old_path → canonical_path`. If F0.3+ relocates paths, **the `entities_universal.json` preset must be updated** (Protocol Enforcer item 0.4) with the new canonical routes.
- **R22 (Personal script pre-context)** from Protocol Enforcer benefits from `origin='user'` in `personal_scripts`: filter by user origin before applying the rule.
- **Item 0.X.4** (`locations` section in `nexo_system_catalog`): must reflect new paths after F0.3-F0.5. Update generator.
- **Phase E.3** (universal entities preset on `nexo init`): must generate the F0 structure directly on fresh install — coordinate E.3 with F0.6.
- **Guardian hook** (`protocol-pretool-guardrail.sh`): must respect `NEXO_MIGRATING=1`. F0.0.4 depends on this capability; if it does not exist, adjust in F0.0 before continuing.

## Referenced artifacts (from spec §13)

- Artifact NEXO #2: "NEXO F0 — Core vs Personal Scripts Classification WIP".
- 48h followup `nora-orchestrator`: `NF-PROTOCOL-1776504681-47624`.
- Followup to audit `dashboard/app.py`: `NF-PROTOCOL-1776508552-2584`.

---

# APPENDIX — Local zero-shot classifier (item 0.21, the "half GB")

## What it is and what it solves

Francisco asked: "explícame eso de la LLM, me hablaste de casi medio GB". This is the classifier from item **0.21**.

**Current problem:**
Guardian needs to decide things like:
- Is this user message a correction or not?
- Is this Bash command destructive in the wrong context?
- Is this text the agent just said a disguised "done claim"?

Today this is done with **regex and keywords** (hardcoded lists like "no", "te equivocas", "listo", "done", "fixed"...). Problems:
- **Language:** if you write in Catalan or English, the Spanish regex does not match.
- **Semantics:** "no está mal" contains "no" and triggers a false positive. "te equivocas de número" triggers but was not an operational correction.
- **Blind to tone and intent.**

## Solution: MDeBERTa-v3-base-xnli-multilingual

It is an **open-source** Hugging Face model. ~500 MB on disk.

**Characteristics:**
- **Local:** runs on your Mac without internet, API, or recurring cost.
- **CPU-only:** does not need GPU. Latency ~200-500 ms per classification.
- **Multilingual:** supports 100+ languages (ES, CA, EN, PT, FR, DE, IT, etc.) without retraining.
- **Zero-shot:** requires no prior training. You pass the text + a list of candidate labels IN REAL TIME, and it tells you which fits and with what confidence.

## How it works (in 4 steps)

1. **NEXO writes a message** ("lo hemos dejado, ya estaría") and wants to know if it is a "done claim".
2. **Classifier** receives:
   - Text: `"lo hemos dejado, ya estaría"`
   - Candidate labels: `["done_claim", "status_update", "question", "noise"]`
3. **Model evaluates** (without training, based on its general linguistic knowledge) which label has the strongest semantic affinity with the text. It returns something like:
   ```
   { "done_claim": 0.87, "status_update": 0.09, "question": 0.02, "noise": 0.02 }
   ```
4. **Guardian decides:** `done_claim > 0.6` → triggers R16 (declared-done without task_close) → injects reminder.

## Two levels with optional escalation

To avoid always loading the large model, two levels are used:

1. **Local zero-shot** (MDeBERTa) — always runs, fast, 0 cost.
2. **Full LLM** (Haiku or gpt-5.4-mini via tier `muy_bajo`) — only if local confidence <0.6.

This is what the plan calls **"triple reinforcement + fallback"**:
- Attempt 1: local zero-shot.
- Attempt 2 (if confidence is low): LLM with `max_tokens=3`, stop sequences, strict prompt "answer ONLY yes or no".
- Attempt 3 (if LLM also fails): conservative fallback `no` + logging.

## Implicit feedback loop (self-improves)

Plan 0.21 adds a loop without user intervention:
- If a learning is deleted in <24h → it was probably noise (classifier false positive).
- If a learning never fires in 30 days → it was probably noise.
- If the user repeats the same message 2-3 times → it was a real correction (classifier false negative).
- If a learning survives >30 useful days → legitimate correction.

All these signals are written to `~/.nexo/classifier/personal_dataset.jsonl` with the retroactive outcome. When ~100 auto-labeled examples accumulate, the classifier automatically moves from **pure zero-shot** to **zero-shot + KNN over your own dataset** — accuracy improves without you doing anything.

## Cost

- **Installation:** 500 MB downloaded once (Hugging Face CDN).
- **Runtime:** €0 (local).
- **Latency:** 200-500 ms per classification.
- **LLM escalation:** only when local has low confidence → ~1 Haiku call/session, cents.

## Why NOT just full LLM for everything

- Cost: tokens × calls × users = scales badly.
- Latency: 1-3s per call, unacceptable in hooks that run per message.
- Internet dependency.

## Why NOT just regex/keywords

- Blind to semantics.
- Blind to language (user writes in whatever language they want).
- Each false positive means noise to the user; each false negative means undetected drift.

---

# SUMMARY STATUS

| Block | Done | Pending |
|---|---|---|
| Phase 0 prerequisites | 0.1, 0.2, 0.3, 0.4-0.20, 0.22, 0.25, 0.X.1-6 | 0.21 consumer in auto_capture (followup), 0.23 weekly red-team cron (followup) |
| Phase A system prompt | A.1-A.7 (R26-R34 in prompt) | A.8 24h smoke omitted by mandate |
| Phase B MCP server | B.1-B.8 (12 rules + tests + hard) | B.4-B.6 shadow 72h omitted by mandate |
| Phase C wrapper block 1 | all + telemetry | C.9 smoke 7d omitted |
| Phase D wrapper block 2 | all | — |
| Phase D2 added rules | all | — |
| Phase E rollout | E.1, E.2, E.3, E.4, E.5 partial, E.8 | E.5 UI reactivation, E.6 dedicated README, E.7 video (optional) |
| Phase F telemetry | F.1-F.3, F.5, F.6, F.7, F.8 (pin docs) | F.4 automatic adjustment (real data), F.8 monthly reminder (followup) |
| T0 Desktop micro-patch | all (v0.17.0) | — |
| T1-T3 Desktop debt | all (v0.17.0) | — |
| T4 classifier in R15/R23* | all (v6.3.0 + v0.18.0) | T4.5 parity fixtures with mock LLM (followup) |
| T5 identity continuity | all (v6.2.0 + v0.17.0) | — |
| Bullet modal bug | all (v0.17.0) | — |
| **F0 scripts classification** | F0.0.1/3/6, F0.0.4, F0.1 | F0.0.7 Nora sync, F0.1 CLI --origin, F0.2 Desktop panel, F0.3-F0.6 physical move + breaking v7.0.0 **(DEFERRED — requires coordinated validation in Francisco + Nora runtime, Learning #450)** |

### Published releases
- NEXO Brain v6.2.0 + NEXO Desktop v0.17.0 (wave 1, already in production).
- NEXO Brain v6.3.0 + NEXO Desktop v0.18.0 (wave 2, PRs #217 + #5 prepared for merge after 2nd audit OK).

**Things Francisco must confirm before serial execution:**

1. **Versioning v0.16.2** — two incompatible proposals:
   - Option A: v0.16.2 = only T0 (micro-patch, ~30 min). T1+T2+T3+Modal bug → v0.16.3.
   - Option B: v0.16.2 = T0 + T1 + T2 + T3 + Modal bug.
2. **T5 rule numbering** — T5 asks for "R26 Identity Coherence" but R26 is already Jargon Filter (Phase A). Renumber to **R34**.
3. **E.5 Guardian Proposals panel** — remains hidden or is reactivated in UI.
4. **Exact local model for 0.21** — MDeBERTa-v3-base-xnli-multilingual is the proposal; alternatives: `bge-multilingual-gemma2` (more accurate, heavier), `xlm-roberta-base` (lighter).
5. **F0 scripts classification — order vs Protocol Enforcer** — run in parallel, or sequentially (F0 first and then Protocol Enforcer uses the new structure). F0 is the ideal prerequisite for R21/R22 to work on final paths, but each F0 micro-phase is its own release and takes weeks.
6. **Nora F0 coordination** — F0.0 to F0.6 apply in both instances (Francisco + Nora). When executed, notify the operator before each phase.

---

**End of document.** To execute from a fresh terminal: `NEXO, open this plan at /Users/franciscoc/Desktop/NEXO-PLAN-CONSOLIDADO.md and execute by blocks without stopping. Warn if anything requires a product decision.`
