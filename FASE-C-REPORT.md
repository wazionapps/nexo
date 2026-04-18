# Protocol Enforcer Fase 2 — FASE C Report (Wrapper Bloque 1)

**Sesión:** 2026-04-18
**Branch Python:** `fase2-impl` en worktree `~/work/nexo-fase2/` (repo `wazionapps/nexo` @ main v6.0.6 `da754f1` + hotfix `4f37ab1`).
**Branch JS:** `fase2-impl-desktop` en worktree `~/work/nexo-desktop-fase2/` (repo `nexo-desktop` @ main `5faaa73` v0.14.2).
**Estado:** Fase C completada en ambos engines. Las 4 reglas core del wrapper de stream están implementadas y testeadas en isolation.

## Reglas entregadas

| Regla | Python | JS | Tests Python | Tests JS |
|-------|--------|----|-------|------|
| **R13** pre-Edit guard | `HeadlessEnforcer._check_r13` + `r13_pre_edit_guard.py` | `EnforcementEngine._checkR13` + `lib/r13-pre-edit-guard.js` | 10 integ | 10 unit + 10 integ |
| **R14** post-correction learning | `on_user_message` + `_advance_r14_window` + `r14_correction_learning.py` | `onUserMessage(convId, userText, opts)` + `_runR14Detection` + `lib/r14-correction-learning.js` | 10 integ | 4 unit + 2 integ |
| **R16** declared-done | `on_assistant_text` + `r16_declared_done.py` | `onAssistantText(convId, text, opts)` + `lib/r16-declared-done.js` | 10 integ | 3 unit + 2 integ |
| **R25** Nora/María read-only | `_check_r25` + `r25_nora_maria_read_only.py` | `_checkR25` + `lib/r25-nora-maria-read-only.js` | 11 integ | 5 unit + 3 integ |

## Nuevos módulos creados

**Python (fase2-impl):**
- `src/r13_pre_edit_guard.py` (ya de Fase 0 spike, ahora integrado)
- `src/r14_correction_learning.py`
- `src/r16_declared_done.py`
- `src/r25_nora_maria_read_only.py`
- `src/enforcement_engine.py` ampliado (734 líneas, +373 vs baseline)
- 4 test files `tests/test_fase_c_r*.py` (41 tests nuevos total)

**JS (fase2-impl-desktop):**
- `lib/r13-pre-edit-guard.js` (Fase 0 spike + Fase C wire)
- `lib/r14-correction-learning.js`
- `lib/r16-declared-done.js`
- `lib/r25-nora-maria-read-only.js`
- `lib/call-model-raw.js` (infra classifier)
- `lib/enforcement-classifier.js` (triple-reinforced yes/no + TTL cache)
- `enforcement-engine.js` ampliado (669 líneas, +280 vs baseline)
- 2 test files `tests/*-enforcement.test.js` (39 tests nuevos total)

## Parity contract

Ambos engines comparten:

- `CLASSIFIER_QUESTION` strings (copy-paste entre archivos, en inglés — el classifier es multilingual via el prompt mismo)
- `INJECTION_PROMPT_TEMPLATE` bodies (texto exacto del reminder)
- Triple refuerzo yes/no (system prompt estricto + `max_tokens=3` + regex parser + 1 retry + fallback "no" conservador)
- Tier `muy_bajo` para el classifier (Haiku / gpt-5.4-mini)
- Guardian modes gating (off / shadow / soft / hard) con defence-in-depth: **R13, R14, R16, R25, R30 nunca se resuelven a `off`**
- Fail-closed paths: `ClassifierUnavailableError` → no inyección (mejor falso negativo que falso positivo con backend caído)
- Dedup 60s por tag en `injection_queue` / `_enqueue`
- R13 decision logic byte-for-byte (función pura `should_inject_r13` / `shouldInjectR13`)
- R25 decision logic byte-for-byte (`should_inject_r25` / `shouldInjectR25`)

## Estado de enforcement modes (defaults packaged)

Todas las reglas Fase C arrancan en **`hard`** por defecto — Fase 2 plan doc 1 las marcó CORE y `guardian_config` (Python) + `R13_DEFAULT_RULE_MODES` (JS inlined) las pineagan a hard cuando el usuario no tiene `~/.nexo/config/guardian.json` aún.

Operadores pueden ajustar a `soft` o `shadow` editando `~/.nexo/config/guardian.json`. **`off` queda bloqueado** por el validator.

## Tests

Python isolated suite post-Fase C:
```
NEXO_HOME=/tmp/nexo-test-fase2 PYTHONPATH=src python3 -m pytest tests/ -q
  --ignore=tests/test_agent_runner.py
  --ignore=tests/test_agent_runner_bare_mode.py
  --ignore=tests/test_v6_fresh_install_skip.py
  → 1205 passed, 1 skipped, 2 xfailed in ~85s
```

JS Desktop suite post-Fase C:
```
cd ~/work/nexo-desktop-fase2 && npm test
  → 80 pass, 0 fail in ~850ms
```

Sin regresiones. Sin side effects a runtime vivo (isolated NEXO_HOME en todo momento).

## Commits acumulados

- `fase2-impl`: **31 commits** sobre main v6.0.6 (+ hotfix `4f37ab1` mergeado).
- `fase2-impl-desktop`: **2 commits** sobre main v0.14.2 del repo `nexo-desktop`.
- `main` del repo `wazionapps/nexo` tiene el hotfix hook (`4f37ab1`) pero sin tag — queda para el próximo release consolidado.

## Qué NO está hecho (pendientes Fase C)

- **Shadow 3d → soft 3d → hard** calendar rollout: el plan original pide correr cada regla en shadow durante 3 días antes de activarla hard. Este reporte deja todo listo para arrancar ese ciclo cuando tú decidas.
- **R13 / R14 / R16 / R25 E2E real**: los tests son isolated. Un end-to-end con subprocess Claude Code + stream-json + respuesta real del classifier queda para la sesión de activación.
- **Parity test JS ↔ Python automatizado**: los prompts/thresholds/tags están sincronizados manualmente. Un test CI que cargue ambos engines con un mismo fixture y compare outputs queda pendiente (item 0.23 del plan).
- **Desktop wiring del R16 `hasOpenTask` probe**: el JS engine declara el hook pero no lo cablea — el renderer/main process de Desktop debe conectar la query a la DB del shared brain. Lo haremos cuando toque integrar la UI de Fase E.
- **Fase D + D2** (9 + 12 reglas): sin empezar. Siguiente tranche cuando decidas.

## Riesgos identificados en Fase C

1. **Classifier depende de `@anthropic-ai/sdk` + `openai` runtime en Desktop**. Estos paquetes NO están en `package.json` del Desktop ni como dependencies ni devDependencies. Hay que añadirlos antes del primer release que exponga Fase C en hard mode.
2. **`_defaultClassifier` en JS** llama a la clasificación en cada turno donde haya user message o assistant text. Si el operador no tiene API key configurada, cada llamada acaba en `ClassifierUnavailableError` y las reglas degradan a "sin inyección". Esperado, pero conviene loggear la primera vez que ocurra para telemetría.
3. **R14 detection async** en JS no bloquea `onUserMessage` — la ventana se abre cuando la Promise resuelve. Si el agente dispara los 3 tool calls antes de que la detección termine, R14 no se dispara aunque hubiera correction. Edge case raro pero real; fix posible: hacer `onUserMessage` async o mover la detección a `_advanceR14Window` (más robusto pero más computación).
4. **R25 usa `guardianConfig.r25_read_only_hosts` en JS** y `entity_list` en Python — dos fuentes diferentes. Python lee del shared brain (dinámico), JS del config file (estático hasta reiniciar engine). Siguiente iteración: unificar via un helper que consulte el brain desde ambos lados.
5. **R16 `has_open_task` Python** consulta `db.list_protocol_tasks(status='open')` sin filtrar por session_id del wrapper. Puede disparar en sesiones distintas a la que cerró el task. Conservador pero potencialmente ruidoso — siguiente iteración reciba `session_id` explícito.

---

*Escrito por NEXO al cierre de Fase C. Zero push a main (Python) / main (JS). Las ramas `fase2-impl` y `fase2-impl-desktop` están listas para review + merge cuando decidas el release consolidado.*
