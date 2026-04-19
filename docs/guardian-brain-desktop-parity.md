# Guardian Brain/Desktop Parity

Scope: runtime Guardian rules that Desktop enforces or consumes directly. Core
MCP-only rules R01-R12 stay on the Brain side and are not part of this matrix.

## Runtime Rule Matrix

| Rule | Brain exists | Desktop wiring | Source of truth | Evidence |
| --- | --- | --- | --- | --- |
| R13 pre-edit guard | `src/r13_pre_edit_guard.py` + `src/enforcement_engine.py` | `lib/r13-pre-edit-guard.js` + `enforcement-engine.js::onBeforeToolCall/onToolCall` | live tool input | `tests/test_t4_classifier_wrap_python.py`, `nexo-desktop/tests/r13-enforcement.test.js` |
| R14 correction learning | `src/r14_correction_learning.py` + `src/enforcement_engine.py` | `lib/r14-correction-learning.js` + `main.js -> claude-stream-router -> onAssistantText` | assistant text + subsequent tool calls | `tests/adversarial/test_guardian_redteam.py`, `nexo-desktop/tests/r14-r16-r25-enforcement.test.js`, `nexo-desktop/tests/assistant-side-runtime-rules.test.js` |
| R15 project context | `src/r15_project_context.py` + `src/enforcement_engine.py` | `lib/r15-project-context.js` + `enforcement-engine.js::onUserMessage` | `guardian-runtime-surfaces.json.projects` fallback preset | `tests/test_guardian_runtime_surfaces.py`, `nexo-desktop/tests/fase-d-enforcement.test.js`, `nexo-desktop/tests/guardian-runtime-surfaces-parity.test.js` |
| R16 declared done | `src/r16_declared_done.py` + `src/enforcement_engine.py` | `lib/r16-declared-done.js` + `main.js -> claude-stream-router -> onAssistantText` | assistant text + task-open probe | `tests/adversarial/test_guardian_redteam.py`, `nexo-desktop/tests/r14-r16-r25-enforcement.test.js`, `nexo-desktop/tests/assistant-side-runtime-rules.test.js` |
| R17 promise debt | `src/r17_promise_debt.py` + `src/enforcement_engine.py` | `lib/r17-promise-debt.js` + `main.js -> claude-stream-router -> onAssistantText` | assistant text + later tool-call window | `tests/test_fase_d_r17_r20_integration.py`, `nexo-desktop/tests/fase-d-enforcement.test.js`, `nexo-desktop/tests/assistant-side-runtime-rules.test.js` |
| R18 followup autocomplete | `src/r18_followup_autocomplete.py` + `src/enforcement_engine.py` | `lib/r18-followup-autocomplete.js` + after-tool path | followup matcher helper | `tests/test_followup_match.py`, `nexo-desktop/tests/fase-d-enforcement.test.js` |
| R19 project grep | `src/r19_project_grep.py` + `src/enforcement_engine.py` | `lib/r19-project-grep.js` + on-tool path | `guardian-runtime-surfaces.json.projects` fallback preset | `tests/test_fase_d_r17_r20_integration.py`, `nexo-desktop/tests/fase-d-enforcement.test.js`, `nexo-desktop/tests/guardian-runtime-surfaces-parity.test.js` |
| R20 constant grep | `src/r20_constant_change.py` + `src/enforcement_engine.py` | `lib/r20-constant-change.js` + on-tool path | tool payload + classifier | `tests/test_fase_d_r17_r20_integration.py`, `nexo-desktop/tests/fase-d-enforcement.test.js` |
| R21 legacy path | `src/r21_legacy_path.py` + `src/enforcement_engine.py` | `lib/r21-legacy-path.js` + on-tool path | `guardian-runtime-surfaces.json.legacy_mappings` fallback preset | `tests/test_guardian_runtime_surfaces.py`, `nexo-desktop/tests/fase-d-enforcement.test.js`, `nexo-desktop/tests/guardian-runtime-surfaces-parity.test.js` |
| R22 personal script probe | `src/r22_personal_script.py` + `src/enforcement_engine.py` | `lib/r22-personal-script.js` + on-tool path | recent runtime records | `tests/test_personal_scripts_enabled.py`, `nexo-desktop/tests/fase-d-enforcement.test.js` |
| R23 ssh without atlas | `src/r23_ssh_without_atlas.py` + `src/enforcement_engine.py` | `lib/r23-ssh-without-atlas.js` + on-tool path | `guardian-runtime-surfaces.json.known_hosts` fallback preset | `tests/test_guardian_runtime_surfaces.py`, `nexo-desktop/tests/fase-d-enforcement.test.js`, `nexo-desktop/tests/guardian-runtime-surfaces-parity.test.js` |
| R23b deploy vhost | `src/r23b_deploy_vhost.py` + `src/enforcement_engine.py` | `lib/r23b-deploy-vhost.js` + D2 dispatch | `guardian-runtime-surfaces.json.vhost_mappings` fallback preset | `tests/test_guardian_runtime_surfaces.py`, `nexo-desktop/tests/fase-d2-enforcement.test.js`, `nexo-desktop/tests/guardian-runtime-surfaces-parity.test.js` |
| R23c cwd mismatch | `src/r23c_cwd_mismatch.py` + `src/enforcement_engine.py` | `lib/r23c-cwd-mismatch.js` + D2 dispatch | project local paths + cwd | `tests/test_guardian_runtime_surfaces.py`, `nexo-desktop/tests/fase-d2-enforcement.test.js` |
| R23d chown/chmod recursive | `src/r23d_chown_chmod_recursive.py` + `src/enforcement_engine.py` | `lib/r23d-chown-chmod-recursive.js` + D2 dispatch | tool payload | `tests/adversarial/test_guardian_redteam.py`, `nexo-desktop/tests/fase-d2-enforcement.test.js` |
| R23e force push main | `src/r23e_force_push_main.py` + `src/enforcement_engine.py` | `lib/r23e-force-push-main.js` + D2 dispatch | tool payload | `tests/adversarial/test_guardian_redteam.py`, `nexo-desktop/tests/fase-d2-enforcement.test.js` |
| R23f db no where | `src/r23f_db_no_where.py` + `src/enforcement_engine.py` | `lib/r23f-db-no-where.js` + D2 dispatch | `guardian-runtime-surfaces.json.db_production_markers` fallback preset | `tests/test_guardian_runtime_surfaces.py`, `nexo-desktop/tests/fase-d2-enforcement.test.js`, `nexo-desktop/tests/guardian-runtime-surfaces-parity.test.js` |
| R23g secrets in output | `src/r23g_secrets_in_output.py` + `src/enforcement_engine.py` | `lib/r23g-secrets-in-output.js` + D2 dispatch | tool payload | `tests/adversarial/test_guardian_redteam.py`, `nexo-desktop/tests/fase-d2-enforcement.test.js` |
| R23h shebang mismatch | `src/r23h_shebang_mismatch.py` + `src/enforcement_engine.py` | `lib/r23h-shebang-mismatch.js` + D2 dispatch | file content + path | `tests/test_fase_2_audit_gaps.py`, `nexo-desktop/tests/fase-d2-enforcement.test.js` |
| R23i auto deploy ignored | `src/r23i_auto_deploy_ignored.py` + `src/enforcement_engine.py` | `lib/r23i-auto-deploy-ignored.js` + D2 dispatch | project deploy metadata | `tests/test_guardian_runtime_surfaces.py`, `nexo-desktop/tests/fase-d2-enforcement.test.js` |
| R23j global install | `src/r23j_global_install.py` + `src/enforcement_engine.py` | `lib/r23j-global-install.js` + D2 dispatch | tool payload + operator text | `tests/adversarial/test_guardian_redteam.py`, `nexo-desktop/tests/fase-d2-enforcement.test.js` |
| R23k script duplicates skill | `src/r23k_script_duplicates_skill.py` + `src/enforcement_engine.py` | `lib/r23k-script-duplicates-skill.js` + D2 dispatch | runtime catalog + tool payload | `tests/test_fase_2_audit_gaps.py`, `nexo-desktop/tests/fase-d2-enforcement.test.js` |
| R23l resource collision | `src/r23l_resource_collision.py` + `src/enforcement_engine.py` | `lib/r23l-resource-collision.js` + D2 dispatch | entity registry snapshot | `tests/test_guardian_runtime_surfaces.py`, `nexo-desktop/tests/fase-d2-enforcement.test.js`, `nexo-desktop/tests/guardian-runtime-surfaces-parity.test.js` |
| R23m message duplicate | `src/r23m_message_duplicate.py` + `src/enforcement_engine.py` | `lib/r23m-message-duplicate.js` + D2 dispatch | recent message ring buffer | `tests/test_fase_2_audit_gaps.py`, `nexo-desktop/tests/fase-d2-enforcement.test.js` |
| R24 stale memory | `src/r24_stale_memory.py` + `src/enforcement_engine.py` | `lib/r24-stale-memory.js` + check window | reminder/recall window state | `tests/test_fase_2_audit_gaps.py`, `nexo-desktop/tests/fase-d-enforcement.test.js` |
| R25 read-only hosts | `src/r25_nora_maria_read_only.py` + `src/enforcement_engine.py` | `lib/r25-nora-maria-read-only.js` + on-tool path | `guardian-runtime-surfaces.json.read_only_hosts` + `destructive_patterns` fallback preset | `tests/test_guardian_runtime_surfaces.py`, `nexo-desktop/tests/r14-r16-r25-enforcement.test.js`, `nexo-desktop/tests/guardian-runtime-surfaces-parity.test.js` |
| R_CATALOG before artifact create | `src/r_catalog.py` + `src/enforcement_engine.py` | `lib/r-catalog.js` + before-tool path | live catalog file | `tests/test_install_guardian.py`, `nexo-desktop/tests/guardian-runtime-overrides.test.js` |
| R34 identity coherence | `src/r34_identity_coherence.py` + `src/enforcement_engine.py` | `lib/r34-identity-coherence.js` + `main.js -> claude-stream-router -> onAssistantMessage` | assistant text + recent tool names | `tests/test_r34_identity_coherence.py`, `nexo-desktop/tests/r34-identity-coherence.test.js`, `nexo-desktop/tests/assistant-side-runtime-rules.test.js` |

## Config And Dataset Parity

| Surface | Brain | Desktop | Evidence |
| --- | --- | --- | --- |
| Default rule modes | `src/presets/guardian_default.json` | `enforcement-engine.js::PACKAGED_GUARDIAN_RULE_MODES` | `nexo-desktop/tests/guardian-defaults-parity.test.js` |
| Runtime overrides TTL | `src/guardian_config.py` | `enforcement-engine.js::guardianRuleMode()` | `tests/test_tools_guardian_override.py`, `nexo-desktop/tests/guardian-runtime-overrides.test.js` |
| Runtime datasets snapshot | `src/guardian_runtime_surfaces.py` + `client_sync.sync_all_clients()` | `enforcement-engine.js::_loadGuardianRuntimeSurfaces()` and dataset helpers | `tests/test_guardian_runtime_surfaces.py`, `tests/test_client_sync.py::test_sync_all_clients_writes_guardian_runtime_surfaces_snapshot`, `nexo-desktop/tests/guardian-runtime-surfaces-parity.test.js` |
| Assistant-side stream wiring | `src/enforcement_engine.py` on assistant message/text | `main.js` + `lib/claude-stream-router.js` | `nexo-desktop/tests/claude-stream-router.test.js`, `nexo-desktop/tests/assistant-side-runtime-rules.test.js` |
| Local telemetry file | `src/guardian_telemetry.py` | `lib/guardian-telemetry.js` | `tests/test_guardian_telemetry.py`, `nexo-desktop/tests/guardian-telemetry.test.js` |

## Interpretation

- Brain remains the canonical source of truth for entities and Guardian policy.
- Desktop consumes Brain-shaped runtime data instead of growing its own parallel
  manual lists.
- Remaining work after this document is product-facing, not parity-baseline:
  multi-language label hardening, release packaging, fresh-install cleanup, and
  end-to-end verification on the final coordinated release.
