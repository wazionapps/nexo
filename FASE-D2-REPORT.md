# Protocol Enforcer Phase 2 — PHASE D2 Report (Wrapper Block 3)

**Session:** 2026-04-18
**Python branch:** `fase2-impl` in worktree `~/work/nexo-fase2/` (repo `wazionapps/nexo` @ main v6.0.6 `da754f1` + hotfix `4f37ab1`).
**JS branch:** `fase2-impl-desktop` in worktree `~/work/nexo-desktop-fase2/` (repo `nexo-desktop` @ main rebased onto v0.14.3 `c6989cd`).
**Status:** Phase D2 closed in both engines. 12 incident-driven rules (R23b–R23m) implemented and tested. Byte-for-byte parity with a shared retroactive fix (R23f WHERE regex).

## Delivered rules

### Tranche 1 — Hard blockers (4)

| Rule | Function | Python | JS | Py tests | JS tests |
|-------|---------|--------|----|----|----|
| **R23b** deploy_vhost | scp/rsync docroot vs user-cited domain cross-check | `_check_r23b` + `r23b_deploy_vhost.py` | `_checkR23b` + `lib/r23b-deploy-vhost.js` | 3 integ | 1 integ |
| **R23e** force_push_main | git push --force/-f on main/master/production/release-* | `_check_r23e` + `r23e_force_push_main.py` | `_checkR23e` + `lib/r23e-force-push-main.js` | 6 integ | 3 unit + 2 integ |
| **R23f** db_no_where | DELETE/UPDATE without WHERE against client DB | `_check_r23f` + `r23f_db_no_where.py` | `_checkR23f` + `lib/r23f-db-no-where.js` | 5 integ | 2 unit + 1 integ |
| **R23l** resource_collision | creating a cloud resource with an already registered name | `_check_r23l` + `r23l_resource_collision.py` | `_checkR23l` + `lib/r23l-resource-collision.js` | 3 integ | 1 integ |

### Tranche 2 — Soft (6)

| Rule | Function | Python | JS | Py tests | JS tests |
|-------|---------|--------|----|----|----|
| **R23c** cwd_mismatch | destructive bash cwd != project.local_path | `_check_r23c` + `r23c_cwd_mismatch.py` | `_checkR23c` + `lib/r23c-cwd-mismatch.js` | 3 integ | 1 integ |
| **R23d** chown_chmod_recursive | -R chown/chmod on root-ish path without prior ls | `_check_r23d` + `r23d_chown_chmod_recursive.py` | `_checkR23d` + `lib/r23d-chown-chmod-recursive.js` | 3 integ | 2 unit + 1 integ |
| **R23g** secrets_in_output | env dump, echo secret, cat key files, bearer tokens | `_check_r23g` + `r23g_secrets_in_output.py` | `_checkR23g` + `lib/r23g-secrets-in-output.js` | 4 integ | 3 unit + 1 integ |
| **R23i** auto_deploy_ignored | Edit/Write in auto_deploy repo after recent push | `_check_r23i` + `r23i_auto_deploy_ignored.py` | `_checkR23i` + `lib/r23i-auto-deploy-ignored.js` | 2 integ | 1 integ |
| **R23k** script_duplicates_skill | nexo_personal_script_create with skill_match ≥ 0.75 | `_check_r23k` + `r23k_script_duplicates_skill.py` | `_checkR23k` + `lib/r23k-script-duplicates-skill.js` | 0 (silent probe) | 0 (silent probe) |
| **R23m** message_duplicate | 90% jaccard vs same thread within 15min | `_check_r23m` + `r23m_message_duplicate.py` | `_checkR23m` + `lib/r23m-message-duplicate.js` | 3 integ | 1 unit + 1 integ |

### Tranche 3 — Shadow (2)

| Rule | Function | Python | JS | Py tests | JS tests |
|-------|---------|--------|----|----|----|
| **R23h** shebang_mismatch | `#!` vs `which` resolve within same interpreter family | `_check_r23h` + `r23h_shebang_mismatch.py` | `_checkR23h` + `lib/r23h-shebang-mismatch.js` | 3 integ | 0 (shadow-only, covered by Python) |
| **R23j** global_install | npm -g / pip --user / brew install without permit | `_check_r23j` + `r23j_global_install.py` | `_checkR23j` + `lib/r23j-global-install.js` | 6 integ | 3 integ |

## New entities

**D2.0 — vhost_mapping:** 8 seed entities (`entities_universal.json`):

| name | domain | host | docroot |
|------|--------|------|---------|
| systeam_es | systeam.es | vicshop | /home/vicshopsysteam/public_html |
| wazion_com | wazion.com | wazion-gcp | /var/www/wazion.com/public_html |
| recambios_bmw | recambios-bmw.es | mundiserver | /home/vicshop/public_html |
| allinoneapp | allinoneapp.com | mundiserver | /home/vicshop/allinoneapp |
| bulksend | bulksend.app | mundiserver | /home/vicshop/bulksend |
| nexo_brain | nexo-brain.com | github-pages | public/ |
| canarirural | canarirural.com | mundiserver | /home/canariru/public_html |
| vic_shop | vicshop.com | vicshop | /home/vicshopsysteam/vicshop |

## Parity contract

- Same `CLASSIFIER_QUESTION` / `INJECTION_PROMPT_TEMPLATE` text byte-for-byte where applicable.
- Same thresholds: R23m 90% jaccard + 15min, R23k 0.75 skill similarity, R23d root-ish whitelist (`/`, `/home`, `/var`, `/etc`, `/opt`, `/usr`, `/srv`).
- Same R23j permit markers: `install globally`, `si instala global`, `yes install globally`, `global install ok`, etc.
- Identical R23i state shape: `recentPush` flag + Edit/Write evaluation clears it once.
- R23m ring buffer max 16 entries, same schema {thread, body, ts}.
- Core defense in depth unchanged: R13/R14/R16/R25/R30 remain blocked from non-`off`.
- Fail-closed paths: classifier down / preset unreadable / entity absent → no injection.
- **Shared retroactive fix:** R23f regex on both sides checked WHERE only in `tail` (post-SET group); for `UPDATE x SET y WHERE z`, the greedy SET group consumed WHERE and produced a false positive. Fix: check `WHERE_RE.search(match.group(0))` — full match, not tail.

## Enforcement mode status (packaged defaults)

`guardian_default.json` v1.3.3:

| Rule | Default mode |
|-------|--------------|
| R23b, R23e, R23f, R23l | `hard` |
| R23c, R23d, R23g, R23i, R23k, R23m | `soft` |
| R23h, R23j | `shadow` |

Operators adjust via `~/.nexo/config/guardian.json`. For R21 and R24 (Phase D), hard mode still requires override; for D2 core blockers, the validator does not explicitly block them (they are not R13/R14/R16/R25/R30 — they are incident-driven). Consider hardening in Phase F if telemetry asks for it.

**Cleanup:** removed 9 placeholder keys from the original plan that my implementations supersede (`R23b_deploy_path_mismatch`, `R23c_cwd_destructive`, `R23d_chown_recursive`, `R23e_git_push_force_main`, `R23f_db_prod_no_where`, `R23g_secrets_in_logs`, `R23h_interpreter_mismatch`, `R23l_resource_name_collision`, `R23m_duplicate_email`). Final R23 rule count in preset: 13.

## Tests

**Python isolated suite post-Phase D2:**
```
NEXO_HOME=/tmp/nexo-test-fase2 python3 -m pytest tests/test_fase_d2_*.py -q
41 passed
```

**Python combined Phase C+D+D2:**
```
NEXO_HOME=/tmp/nexo-test-fase2 python3 -m pytest tests/test_fase_c_*.py tests/test_fase_d_*.py tests/test_fase_d2_*.py -q
127 passed
```

**JS (from Desktop worktree):**
```
node --test tests/fase-d-enforcement.test.js tests/fase-d2-enforcement.test.js tests/r13-enforcement.test.js tests/r14-r16-r25-enforcement.test.js
92 pass, 0 fail
```

## Protocol Enforcer: phase-by-phase scope

| Phase | Scope | Status |
|------|-------|--------|
| Phase 0 | Preflight, snapshots, dry-run infra, MCP bridge | closed |
| Phase A | Layer 1 server-side (R01–R12 tools.*) | closed |
| Phase B | `tool-enforcement-map.json` v2.1.0 + bridge delivery | closed |
| Phase C | Wrapper Block 1 core: R13 + R14 + R16 + R25 | closed |
| Phase D | Wrapper Block 2: R15 + R17–R24 | closed |
| **Phase D2** | **Wrapper Block 3: R23b–R23m (12 incident-driven rules)** | **closed** |
| Phase E | Installer + distributed preset + Desktop UI quarantine | pending |
| Phase F | Telemetry + red-team + E2E loops | pending |

## Commits from this phase

**Python (`fase2-impl`):**
- `dbb6bb9` — Phase D2 tranche 1 hard (R23b/R23e/R23f/R23l)
- `32eef51` — Phase D2 tranche 2 soft (R23c/R23d/R23g/R23i/R23k/R23m)
- `ba0887a` — Phase D2 tranche 3 shadow (R23h/R23j) + duplicate cleanup
- `e83c5f5` — parity fix R23f WHERE regex

**JS (`fase2-impl-desktop`):**
- `31aa202` — Phase D2 JS twins (12 modules + engine + tests)

## Next steps

1. **Phase E** — `scripts/install_guardian.py`, Desktop UI for reminder quarantine (reuse existing TodoWrite components + approval modal), distributed packaged preset.
2. **Phase F** — Telemetry (`guardian_telemetry.jsonl`), red-team corpus, Claude Code ↔ Desktop E2E loops.
3. **Pre-release audit (mandatory gate)** — Francisco ordered: before any tag, perform a 100% layer-by-layer audit of all commits using the best skills, and fix any finding in the same flow (do not postpone).
4. **Consolidated release** — single tag `v6.1.0` core + `v0.15.0` Desktop when Phase F is closed and the audit passes.

## Technical pending items (inherited + new)

- `nexo-desktop/package.json`: add `@anthropic-ai/sdk` + `openai` as runtime deps before the tag.
- Desktop: wire `hasOpenTask` probe to `onUserMessage` from the main process.
- JS↔Python CI parity test: item 0.23 pending — now also covers the D2 templates.
- R23d Python: `_is_root_ish` accepts `/opt/app` and `/usr/...`; confirm with Francisco whether there is a false positive for paths inside a real workspace. Subpath matching already mitigates it (requires prior ls). Phase F telemetry will tell.
- R23i: persisting `recentPush` cross-session is not supported. If the operator pushes and then closes the terminal and opens another, the flag does not carry over. Documented as a limitation.
- R23k: silent skill_match only works when the caller feeds `__skillMatches` in `toolInput` (Desktop) or the `skill_registry` plugin is available (Python). If neither exists, fail-closed.

## Key handoff files

**Python:**
- `src/enforcement_engine.py:1-1693` (engine expanded +693 vs post-Phase D)
- `src/r23b_deploy_vhost.py`, `src/r23c_cwd_mismatch.py`, `src/r23d_chown_chmod_recursive.py`, `src/r23e_force_push_main.py`, `src/r23f_db_no_where.py`, `src/r23g_secrets_in_output.py`, `src/r23h_shebang_mismatch.py`, `src/r23i_auto_deploy_ignored.py`, `src/r23j_global_install.py`, `src/r23k_script_duplicates_skill.py`, `src/r23l_resource_collision.py`, `src/r23m_message_duplicate.py`
- `src/presets/entities_universal.json` (+8 vhost_mapping)
- `src/presets/guardian_default.json` v1.3.3

**JS:**
- `enforcement-engine.js:1-1090` (expanded +256 vs post-Phase D)
- `lib/r23b-deploy-vhost.js` … `lib/r23m-message-duplicate.js` (12 modules)
- `tests/fase-d2-enforcement.test.js`

**Reports:** `FASE0-REPORT.md`, `FASE-C-REPORT.md`, `FASE-D-REPORT.md`, `FASE-D2-REPORT.md` (this one).
