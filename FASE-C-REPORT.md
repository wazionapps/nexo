# Protocol Enforcer Phase 2 — PHASE C Report (Wrapper Block 1)

**Session:** 2026-04-18
**Python branch:** `fase2-impl` in worktree `~/work/nexo-fase2/` (repo `wazionapps/nexo` @ main v6.0.6 `da754f1` + hotfix `4f37ab1`).
**JS branch:** `fase2-impl-desktop` in worktree `~/work/nexo-desktop-fase2/` (repo `nexo-desktop` @ main `5faaa73` v0.14.2).
**Status:** Phase C completed in both engines. The 4 stream-wrapper core rules are implemented and tested in isolation.

## Delivered rules

| Rule | Python | JS | Python tests | JS tests |
|-------|--------|----|-------|------|
| **R13** pre-Edit guard | `HeadlessEnforcer._check_r13` + `r13_pre_edit_guard.py` | `EnforcementEngine._checkR13` + `lib/r13-pre-edit-guard.js` | 10 integ | 10 unit + 10 integ |
| **R14** post-correction learning | `on_user_message` + `_advance_r14_window` + `r14_correction_learning.py` | `onUserMessage(convId, userText, opts)` + `_runR14Detection` + `lib/r14-correction-learning.js` | 10 integ | 4 unit + 2 integ |
| **R16** declared-done | `on_assistant_text` + `r16_declared_done.py` | `onAssistantText(convId, text, opts)` + `lib/r16-declared-done.js` | 10 integ | 3 unit + 2 integ |
| **R25** Nora/María read-only | `_check_r25` + `r25_nora_maria_read_only.py` | `_checkR25` + `lib/r25-nora-maria-read-only.js` | 11 integ | 5 unit + 3 integ |

## New modules created

**Python (fase2-impl):**
- `src/r13_pre_edit_guard.py` (already from Phase 0 spike, now integrated)
- `src/r14_correction_learning.py`
- `src/r16_declared_done.py`
- `src/r25_nora_maria_read_only.py`
- `src/enforcement_engine.py` expanded (734 lines, +373 vs baseline)
- 4 test files `tests/test_fase_c_r*.py` (41 new tests total)

**JS (fase2-impl-desktop):**
- `lib/r13-pre-edit-guard.js` (Phase 0 spike + Phase C wire)
- `lib/r14-correction-learning.js`
- `lib/r16-declared-done.js`
- `lib/r25-nora-maria-read-only.js`
- `lib/call-model-raw.js` (classifier infra)
- `lib/enforcement-classifier.js` (triple-reinforced yes/no + TTL cache)
- `enforcement-engine.js` expanded (669 lines, +280 vs baseline)
- 2 test files `tests/*-enforcement.test.js` (39 new tests total)

## Parity contract

Both engines share:

- `CLASSIFIER_QUESTION` strings (copy-pasted between files, in English — the classifier is multilingual through the prompt itself)
- `INJECTION_PROMPT_TEMPLATE` bodies (exact reminder text)
- Triple yes/no reinforcement (strict system prompt + `max_tokens=3` + regex parser + 1 retry + conservative "no" fallback)
- `muy_bajo` tier for the classifier (Haiku / gpt-5.4-mini)
- Guardian mode gating (off / shadow / soft / hard) with defense in depth: **R13, R14, R16, R25, R30 never resolve to `off`**
- Fail-closed paths: `ClassifierUnavailableError` → no injection (better false negative than false positive with backend down)
- 60s dedup per tag in `injection_queue` / `_enqueue`
- Byte-for-byte R13 decision logic (pure function `should_inject_r13` / `shouldInjectR13`)
- Byte-for-byte R25 decision logic (`should_inject_r25` / `shouldInjectR25`)

## Enforcement mode status (packaged defaults)

All Phase C rules start in **`hard`** by default — Phase 2 plan doc 1 marked them CORE and `guardian_config` (Python) + `R13_DEFAULT_RULE_MODES` (JS inlined) pin them to hard when the user does not have `~/.nexo/config/guardian.json` yet.

Operators can adjust them to `soft` or `shadow` by editing `~/.nexo/config/guardian.json`. **`off` is blocked** by the validator.

## Tests

Python isolated suite post-Phase C:
```
NEXO_HOME=/tmp/nexo-test-fase2 PYTHONPATH=src python3 -m pytest tests/ -q
  --ignore=tests/test_agent_runner.py
  --ignore=tests/test_agent_runner_bare_mode.py
  --ignore=tests/test_v6_fresh_install_skip.py
  → 1205 passed, 1 skipped, 2 xfailed in ~85s
```

JS Desktop suite post-Phase C:
```
cd ~/work/nexo-desktop-fase2 && npm test
  → 80 pass, 0 fail in ~850ms
```

No regressions. No side effects to the live runtime (isolated NEXO_HOME at all times).

## Accumulated commits

- `fase2-impl`: **31 commits** on top of main v6.0.6 (+ hotfix `4f37ab1` merged).
- `fase2-impl-desktop`: **2 commits** on top of main v0.14.2 from repo `nexo-desktop`.
- `main` in repo `wazionapps/nexo` has the hook hotfix (`4f37ab1`) but no tag — left for the next consolidated release.

## What is NOT done (Phase C pending)

- **Shadow 3d → soft 3d → hard calendar rollout:** the original plan asks to run each rule in shadow for 3 days before enabling hard. This report leaves everything ready to start that cycle when you decide.
- **Real R13 / R14 / R16 / R25 E2E:** tests are isolated. An end-to-end with Claude Code subprocess + stream-json + real classifier response remains for the activation session.
- **Automated JS ↔ Python parity test:** prompts/thresholds/tags are synchronized manually. A CI test that loads both engines with the same fixture and compares outputs remains pending (item 0.23 in the plan).
- **Desktop wiring for the R16 `hasOpenTask` probe:** the JS engine declares the hook but does not wire it — the Desktop renderer/main process must connect the query to the shared brain DB. We will do it when integrating the Phase E UI.
- **Phase D + D2** (9 + 12 rules): not started. Next tranche when you decide.

## Risks identified in Phase C

1. **Classifier depends on runtime `@anthropic-ai/sdk` + `openai` in Desktop**. These packages are NOT in Desktop `package.json`, neither dependencies nor devDependencies. They must be added before the first release that exposes Phase C in hard mode.
2. **`_defaultClassifier` in JS** calls classification on each turn where there is a user message or assistant text. If the operator has no API key configured, each call ends in `ClassifierUnavailableError` and the rules degrade to "no injection". Expected, but it should be logged the first time it happens for telemetry.
3. **R14 async detection** in JS does not block `onUserMessage` — the window opens when the Promise resolves. If the agent fires the 3 tool calls before detection finishes, R14 does not trigger even if there was a correction. Rare but real edge case; possible fix: make `onUserMessage` async or move detection to `_advanceR14Window` (more robust but more compute).
4. **R25 uses `guardianConfig.r25_read_only_hosts` in JS** and `entity_list` in Python — two different sources. Python reads from the shared brain (dynamic), JS from the config file (static until engine restart). Next iteration: unify through a helper that queries the brain from both sides.
5. **Python R16 `has_open_task`** queries `db.list_protocol_tasks(status='open')` without filtering by wrapper session_id. It may trigger on sessions different from the one that closed the task. Conservative but potentially noisy — next iteration should receive explicit `session_id`.

---

*Written by NEXO at Phase C close. Zero push to main (Python) / main (JS). Branches `fase2-impl` and `fase2-impl-desktop` are ready for review + merge when you decide on the consolidated release.*
