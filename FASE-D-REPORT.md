# Protocol Enforcer Phase 2 — PHASE D Report (Wrapper Block 2)

**Session:** 2026-04-18
**Python branch:** `fase2-impl` in worktree `~/work/nexo-fase2/` (repo `wazionapps/nexo` @ main v6.0.6 `da754f1` + hotfix `4f37ab1`).
**JS branch:** `fase2-impl-desktop` in worktree `~/work/nexo-desktop-fase2/` (repo `nexo-desktop` @ main `5faaa73` v0.14.2 → rebased onto v0.14.3 `c6989cd`).
**Status:** Phase D completed in both engines. 9 new stream-wrapper rules (R15 + R17–R24) implemented and tested in isolation on both sides.

## Delivered rules

| Rule | Function | Python | JS | Py tests | JS tests |
|-------|---------|--------|----|----|----|
| **R15** project_context | Detect project mentioned in user msg and activate context | `HeadlessEnforcer._check_r15` + `r15_project_context.py` | `_checkR15` + `lib/r15-project-context.js` | 5 integ | 3 integ |
| **R17** promise_debt | Promise classifier + sliding window of N tool_calls → reminder | `on_assistant_text` + `_advance_r17_window` + `r17_promise_debt.py` | `_runR17Detection` + `_advanceR17Window` + `lib/r17-promise-debt.js` | 6 integ | 4 integ |
| **R18** followup_autocomplete | Retroactive followup close suggestion after evidence | `_check_r18` + `r18_followup_autocomplete.py` | `_checkR18` + `lib/r18-followup-autocomplete.js` | 5 integ | 3 integ |
| **R19** project_grep | Require prior grep before Write in project with `require_grep` | `_check_r19` + `r19_project_grep.py` | `_checkR19` + `lib/r19-project-grep.js` | 4 integ | 3 integ |
| **R20** constant_change | Classifier + grep-symbol before touching constant/config | `_check_r20` + `r20_constant_change.py` | `_checkR20` + `lib/r20-constant-change.js` | 5 integ | 3 integ |
| **R21** legacy_path | Redirect if edit path is in `legacy_paths` | `_check_r21` + `r21_legacy_path.py` | `_checkR21` + `lib/r21-legacy-path.js` | 5 integ | 3 integ |
| **R22** personal_script | Context probes before writing a personal script | `_check_r22` + `r22_personal_script.py` | `_checkR22` + `lib/r22-personal-script.js` | 4 integ | 3 integ |
| **R23** ssh_without_atlas | Reminder if ssh target is not in project-atlas | `_check_r23` + `r23_ssh_without_atlas.py` | `_checkR23` + `lib/r23-ssh-without-atlas.js` | 6 integ | 3 integ |
| **R24** stale_memory | Window-based stale memory cited without verification | `notify_stale_memory_cited` + `_advance_r24_window` + `r24_stale_memory.py` | `notifyStaleMemoryCited` + `_advanceR24Window` + `lib/r24-stale-memory.js` | 5 integ | 3 integ |

## New modules created

**Python (fase2-impl):**
- `src/r15_project_context.py`
- `src/r17_promise_debt.py`
- `src/r18_followup_autocomplete.py`
- `src/r19_project_grep.py`
- `src/r20_constant_change.py`
- `src/r21_legacy_path.py`
- `src/r22_personal_script.py`
- `src/r23_ssh_without_atlas.py`
- `src/r24_stale_memory.py`
- `src/enforcement_engine.py` expanded to **1204 lines** (+470 vs post-Phase C)
- 4 test tranches `tests/test_fase_d_*.py` (45 new tests total)

**JS (fase2-impl-desktop):**
- `lib/r15-project-context.js`
- `lib/r17-promise-debt.js`
- `lib/r18-followup-autocomplete.js`
- `lib/r19-project-grep.js`
- `lib/r20-constant-change.js`
- `lib/r21-legacy-path.js`
- `lib/r22-personal-script.js`
- `lib/r23-ssh-without-atlas.js`
- `lib/r24-stale-memory.js`
- `enforcement-engine.js` expanded to **828 lines** (+159 vs post-Phase C)
- 1 test file `tests/fase-d-enforcement.test.js` (28 new tests)

## Parity contract

Both engines preserve:

- Identical `CLASSIFIER_QUESTION` strings (R17, R20, R22), byte-for-byte copy-paste between files.
- Identical `INJECTION_PROMPT_TEMPLATE` bodies (reminder text).
- Same window thresholds: R17 = 3 tool_calls, R24 = 3 turns without verification.
- Triple yes/no reinforcement still through shared `call_model_raw` / `callModelRaw` (tier `muy_bajo`).
- Guardian mode gating (off / shadow / soft / hard) for all Phase D rules except where the rule falls under core defense in depth (R13/R14/R16/R25/R30 remain blocked from `off`).
- Entities queried in `entities_universal.json` (`legacy_paths`, `destructive_commands`, `project_atlas` hosts) share schema.
- Fail-closed paths: classifier down → no injection; project_atlas unreadable → ignore without crash.
- 60s dedup per tag in `injection_queue` / `_enqueue`.
- R15, R18, R19, R21, R23 are pure logic (no classifier) and have decision functions `should_inject_*` / `shouldInject*` — byte-for-byte.
- R17 ordering: `onAssistantText` / `on_assistant_text` runs **R17 detection first**, before any R16 early-exit — critical so the window always advances.

## Enforcement mode status (packaged defaults)

Phase D defaults in `guardian_default.json` (Python) and `R13_DEFAULT_RULE_MODES` (JS inline):

| Rule | Default mode | Reason |
|-------|--------------|--------|
| R15 | `shadow` | Informational, activates context without blocking |
| R17 | `soft` | Promise debt — reminder is useful but not critical |
| R18 | `shadow` | Retroactive suggestion, does not block |
| R19 | `soft` | Grep-before-Write may generate false positives |
| R20 | `soft` | Constant change — useful probe, not mandatory |
| R21 | `hard` | Legacy path — redirect is safety |
| R22 | `soft` | Personal script probe |
| R23 | `shadow` | SSH atlas — informational during first iterations |
| R24 | `shadow` | Stale memory — gradual rollout, telemetry first |

Operators adjust via `~/.nexo/config/guardian.json`. Validator still accepts any mode for Phase D rules (they are not core).

## Tests

Python isolated suite post-Phase D:

```
NEXO_HOME=/tmp/nexo-test-fase2 python3 -m pytest tests/test_fase_d_*.py -q
45 passed
```

Full `tests/` suite (1250+ tests):
```
NEXO_HOME=/tmp/nexo-test-fase2 python3 -m pytest -q
1250 passed (post-Phase C 1205 + 45 Phase D)
```

JS isolated suite post-Phase D:
```
cd ~/work/nexo-desktop-fase2 && npx jest
114 tests passing, 0 failures
  (86 baseline Phase 0–C + 28 Phase D)
```

## Coordination with another Desktop terminal

During Phase D, another terminal worked on Desktop EN/ES i18n. It delivered `v0.14.3` (`c6989cd`: Ready footer mid-stream + Stop resets enforcerInjecting). Worktree `fase2-impl-desktop` rebased cleanly onto v0.14.3 without conflicts — that terminal's changes (renderer, IPC, translations) do not touch `enforcement-engine.js` or `lib/`.

## Protocol Enforcer: phase-by-phase scope

| Phase | Rules | Status |
|------|--------|--------|
| Phase 0 | Preflight, snapshots, dry-run infra, MCP bridge | closed |
| Phase A | Layer 1 server-side (R01–R12 tools.* validations) | closed |
| Phase B | `tool-enforcement-map.json` v2.1.0 + bridge delivery | closed |
| Phase C | Wrapper Block 1 core: R13 + R14 + R16 + R25 | closed |
| **Phase D** | **Wrapper Block 2: R15 + R17–R24** | **closed** |
| Phase D2 | R23b–R23m (12 incident-driven rules) | pending |
| Phase E | Installer + Desktop UI quarantine + distributed preset | pending |
| Phase F | Telemetry + red-team + loops | pending |

## Next steps

1. **Phase D2** — 12 `R23b..R23m` rules derived from real incidents (registered in brain).
2. **Phase E** — `scripts/install_guardian.py`, Desktop UI for reminder quarantine, packaged `guardian_default.json` distribution.
3. **Phase F** — Telemetry (`guardian_telemetry.jsonl`), red-team corpus, E2E loops between Claude Code ↔ Desktop.
4. **Consolidated release** (user decision Option B) — single tag `v6.1.0` core + `v0.15.0` Desktop when F is closed. No intermediate tags.

## Noted technical pending items

- `nexo-desktop/package.json`: add `@anthropic-ai/sdk` + `openai` as runtime deps before the tag (currently only devDeps).
- Desktop: wire the `hasOpenTask` probe into `enforcement-engine.onUserMessage` from the main process (today the engine accepts `opts.hasOpenTask`, but the caller does not feed it).
- JS↔Python CI parity test: script that compares the `CLASSIFIER_QUESTION` and `INJECTION_PROMPT_TEMPLATE` from both sides byte-for-byte. Item 0.23 in the plan — pending.

## Key handoff files

**Python:** `src/enforcement_engine.py:1-1204`, `src/r15_project_context.py`, `src/r17_promise_debt.py` … `src/r24_stale_memory.py`, `src/presets/entities_universal.json`, `src/presets/guardian_default.json`.

**JS:** `enforcement-engine.js:1-828`, `lib/r15-project-context.js` … `lib/r24-stale-memory.js`, `lib/call-model-raw.js`, `lib/enforcement-classifier.js`.

**Tests:** `tests/test_fase_d_tranche{1,2,3,4}.py` (Python), `tests/fase-d-enforcement.test.js` (JS).

**Reports:** `FASE0-REPORT.md`, `FASE-C-REPORT.md`, `FASE-D-REPORT.md` (this one).
