# Protocol Enforcer Fase 2 — FASE D Report (Wrapper Bloque 2)

**Sesión:** 2026-04-18
**Branch Python:** `fase2-impl` en worktree `~/work/nexo-fase2/` (repo `wazionapps/nexo` @ main v6.0.6 `da754f1` + hotfix `4f37ab1`).
**Branch JS:** `fase2-impl-desktop` en worktree `~/work/nexo-desktop-fase2/` (repo `nexo-desktop` @ main `5faaa73` v0.14.2 → rebased sobre v0.14.3 `c6989cd`).
**Estado:** Fase D completada en ambos engines. 9 reglas nuevas del wrapper de stream (R15 + R17–R24) implementadas y testeadas en isolation en ambos lados.

## Reglas entregadas

| Regla | Función | Python | JS | Tests Py | Tests JS |
|-------|---------|--------|----|----|----|
| **R15** project_context | Detectar proyecto mencionado en user msg y activar contexto | `HeadlessEnforcer._check_r15` + `r15_project_context.py` | `_checkR15` + `lib/r15-project-context.js` | 5 integ | 3 integ |
| **R17** promise_debt | Classifier de promesa + sliding window N tool_calls → recordatorio | `on_assistant_text` + `_advance_r17_window` + `r17_promise_debt.py` | `_runR17Detection` + `_advanceR17Window` + `lib/r17-promise-debt.js` | 6 integ | 4 integ |
| **R18** followup_autocomplete | Sugerencia retroactiva de cierre de followup tras evidencia | `_check_r18` + `r18_followup_autocomplete.py` | `_checkR18` + `lib/r18-followup-autocomplete.js` | 5 integ | 3 integ |
| **R19** project_grep | Exigir grep previo antes de Write en proyecto con `require_grep` | `_check_r19` + `r19_project_grep.py` | `_checkR19` + `lib/r19-project-grep.js` | 4 integ | 3 integ |
| **R20** constant_change | Classifier + grep-symbol antes de tocar constante/config | `_check_r20` + `r20_constant_change.py` | `_checkR20` + `lib/r20-constant-change.js` | 5 integ | 3 integ |
| **R21** legacy_path | Redirect si el path edit está en `legacy_paths` | `_check_r21` + `r21_legacy_path.py` | `_checkR21` + `lib/r21-legacy-path.js` | 5 integ | 3 integ |
| **R22** personal_script | Probes de contexto antes de Write de personal script | `_check_r22` + `r22_personal_script.py` | `_checkR22` + `lib/r22-personal-script.js` | 4 integ | 3 integ |
| **R23** ssh_without_atlas | Reminder si ssh target no está en project-atlas | `_check_r23` + `r23_ssh_without_atlas.py` | `_checkR23` + `lib/r23-ssh-without-atlas.js` | 6 integ | 3 integ |
| **R24** stale_memory | Window-based stale memory cited sin verificación | `notify_stale_memory_cited` + `_advance_r24_window` + `r24_stale_memory.py` | `notifyStaleMemoryCited` + `_advanceR24Window` + `lib/r24-stale-memory.js` | 5 integ | 3 integ |

## Nuevos módulos creados

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
- `src/enforcement_engine.py` ampliado a **1204 líneas** (+470 vs post-Fase C)
- 4 test tranches `tests/test_fase_d_*.py` (45 tests nuevos total)

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
- `enforcement-engine.js` ampliado a **828 líneas** (+159 vs post-Fase C)
- 1 test file `tests/fase-d-enforcement.test.js` (28 tests nuevos)

## Parity contract

Ambos engines mantienen:

- `CLASSIFIER_QUESTION` strings idénticos (R17, R20, R22) copy-paste byte-for-byte entre archivos.
- `INJECTION_PROMPT_TEMPLATE` bodies idénticos (texto del reminder).
- Mismos thresholds por ventana: R17 = 3 tool_calls, R24 = 3 turns sin verificación.
- Triple refuerzo yes/no sigue siendo vía `call_model_raw` / `callModelRaw` compartido (tier `muy_bajo`).
- Guardian modes gating (off / shadow / soft / hard) para todas las Fase D excepto cuando la regla cae bajo core defence-in-depth (R13/R14/R16/R25/R30 siguen bloqueadas a no-`off`).
- Entidades consultadas en `entities_universal.json` (`legacy_paths`, `destructive_commands`, `project_atlas` hosts) comparten schema.
- Fail-closed paths: classifier caído → no inyección; project_atlas unreadable → ignora sin crash.
- Dedup 60s por tag en `injection_queue` / `_enqueue`.
- R15, R18, R19, R21, R23 son lógica pura (sin classifier) y tienen decision functions `should_inject_*` / `shouldInject*` — byte-for-byte.
- R17 ordering: `onAssistantText` / `on_assistant_text` corre **R17 detection primero**, antes de cualquier early-exit de R16 — crítico para que la ventana avance siempre.

## Estado de enforcement modes (defaults packaged)

Defaults de Fase D en `guardian_default.json` (Python) y `R13_DEFAULT_RULE_MODES` (JS inline):

| Regla | Default mode | Razón |
|-------|--------------|-------|
| R15 | `shadow` | Informativa, activa contexto sin bloquear |
| R17 | `soft` | Promise debt — reminder es útil pero no crítico |
| R18 | `shadow` | Sugerencia retroactiva, no bloquea |
| R19 | `soft` | Grep-antes-de-Write puede generar falsos positivos |
| R20 | `soft` | Constant change — probe útil, no mandatorio |
| R21 | `hard` | Legacy path — redirect es seguridad |
| R22 | `soft` | Personal script probe |
| R23 | `shadow` | SSH atlas — informativa primeras iteraciones |
| R24 | `shadow` | Stale memory — rollout gradual, telemetría primero |

Operadores ajustan vía `~/.nexo/config/guardian.json`. Validator sigue aceptando cualquier mode para Fase D (no son core).

## Tests

Python isolated suite post-Fase D:

```
NEXO_HOME=/tmp/nexo-test-fase2 python3 -m pytest tests/test_fase_d_*.py -q
45 passed
```

Full suite `tests/` (1250+ tests):
```
NEXO_HOME=/tmp/nexo-test-fase2 python3 -m pytest -q
1250 passed (post-Fase C 1205 + 45 Fase D)
```

JS isolated suite post-Fase D:
```
cd ~/work/nexo-desktop-fase2 && npx jest
114 tests passing, 0 failures
  (86 baseline Fase 0–C + 28 Fase D)
```

## Coordinación con otra terminal Desktop

Durante Fase D, otra terminal trabajó i18n EN/ES del Desktop. Entregó `v0.14.3` (`c6989cd`: footer Listo mid-stream + Parar reset enforcerInjecting). Worktree `fase2-impl-desktop` rebase limpio sobre v0.14.3 sin conflictos — los cambios de esa terminal (renderer, IPC, traducciones) no tocan `enforcement-engine.js` ni `lib/`.

## Protocol Enforcer: scope fase por fase

| Fase | Reglas | Estado |
|------|--------|--------|
| Fase 0 | Preflight, snapshots, dry-run infra, MCP bridge | cerrada |
| Fase A | Capa 1 server-side (R01–R12 tools.* validations) | cerrada |
| Fase B | `tool-enforcement-map.json` v2.1.0 + bridge delivery | cerrada |
| Fase C | Wrapper Bloque 1 core: R13 + R14 + R16 + R25 | cerrada |
| **Fase D** | **Wrapper Bloque 2: R15 + R17–R24** | **cerrada** |
| Fase D2 | R23b–R23m (12 reglas incident-driven) | pendiente |
| Fase E | Installer + Desktop UI quarantine + preset distribuido | pendiente |
| Fase F | Telemetría + red-team + loops | pendiente |

## Próximos pasos

1. **Fase D2** — 12 reglas `R23b..R23m` derivadas de incidentes reales (registro en brain).
2. **Fase E** — `scripts/install_guardian.py`, UI Desktop para cuarentena de reminders, distribución de `guardian_default.json` empaquetada.
3. **Fase F** — Telemetría (`guardian_telemetry.jsonl`), red-team corpus, loops E2E entre Claude Code ↔ Desktop.
4. **Release consolidado** (decisión usuario Option B) — tag único `v6.1.0` core + `v0.15.0` Desktop cuando F esté cerrada. Nada de tags intermedios.

## Pending técnicos anotados

- `nexo-desktop/package.json`: añadir `@anthropic-ai/sdk` + `openai` como deps runtime antes del tag (ahora sólo devDeps).
- Desktop: wire `hasOpenTask` probe al `enforcement-engine.onUserMessage` desde el main process (hoy el engine acepta `opts.hasOpenTask` pero el caller no lo alimenta).
- CI parity test JS↔Python: script que compara byte-for-byte los `CLASSIFIER_QUESTION` y `INJECTION_PROMPT_TEMPLATE` de ambos lados. Item 0.23 del plan — pendiente.

## Archivos clave para handoff

**Python:** `src/enforcement_engine.py:1-1204`, `src/r15_project_context.py`, `src/r17_promise_debt.py` … `src/r24_stale_memory.py`, `src/presets/entities_universal.json`, `src/presets/guardian_default.json`.

**JS:** `enforcement-engine.js:1-828`, `lib/r15-project-context.js` … `lib/r24-stale-memory.js`, `lib/call-model-raw.js`, `lib/enforcement-classifier.js`.

**Tests:** `tests/test_fase_d_tranche{1,2,3,4}.py` (Python), `tests/fase-d-enforcement.test.js` (JS).

**Reports:** `FASE0-REPORT.md`, `FASE-C-REPORT.md`, `FASE-D-REPORT.md` (este).
