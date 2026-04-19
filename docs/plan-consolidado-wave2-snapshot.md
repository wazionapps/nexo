# NEXO — Plan Consolidado
**Protocol Enforcer Fase 2 + Deuda Desktop v0.16.x + Bug Modal Bullets**

Fecha de consolidación: 2026-04-18
Última ejecución registrada: 2026-04-18 — wave 2 coordinada en `feat/plan-consolidado-v7`.
  Releases: NEXO Brain v6.3.0 + NEXO Desktop v0.18.0 (pendiente de publish tras 2-auditor OK).
  Wave 1 (v6.2.0 + v0.17.0) ya publicada.
Autor: NEXO (sesión `nexo-1776534045-42644`)
Fuente: LEEME + docs del plan en `~/Desktop/NEXO PLAN PROTOCOL ENFORCER FASE 2/` + git log real de ambos repos.

---

## Cómo leer este documento

- `[x]` = hecho y verificado.
- `[ ]` = pendiente.
- `[~]` = parcial (detalle al lado).
- **(CORE)** = repo `~/Documents/_PhpstormProjects/nexo/` (NEXO Brain, Python).
- **(DESKTOP)** = repo `~/Documents/_PhpstormProjects/nexo-desktop/` (Electron, JS).
- **(AMBOS)** = requiere paridad Python ↔ JS.
- Sin fechas, sin horas: orden de ejecución solo lógico.

---

## Objetivo canónico (no negociable)

Transformar cualquier LLM en un LLM 100% obediente al contrato operativo. No es para Francisco ni para Maria: es producto. Principios:

1. **Cobertura exhaustiva** (no Pareto). Cada modo de desobediencia identificado tiene al menos una regla que lo cierra.
2. **Reglas core sin opción "off"** — R13 R14 R16 R25 R30. Solo shadow/soft/hard.
3. **Fail-closed** ante fallo del classifier.
4. **Política de reglas nuevas = siempre** creciente. Nunca se reduce "porque ya hay muchas".
5. **El Guardian se vigila a sí mismo** — mapa ↔ código sincronizados.

---

## Versiones actuales (verificadas 2026-04-18)

- NEXO Core runtime: **6.1.0** (npm publicado: 6.1.1)
- NEXO Desktop: **0.16.1**
- tool-enforcement-map: **v2.0.0** con 247 tools (10 MUST + 7 SHOULD)

Source de verdad del mapa: `~/Documents/_PhpstormProjects/nexo/tool-enforcement-map.json`.

---

# BLOQUE 1 — PROTOCOL ENFORCER FASE 2

## FASE 0 — Prerrequisitos (CORE principalmente)

No escribir ninguna regla nueva hasta que estos items estén cerrados.

- [x] **0.1 `call_model_raw()` en `agent_runner.py`** (CORE)
  Función nueva: llamada LLM plain sin arrancar Claude Code CLI ni cargar MCP. Firma:
  `call_model_raw(prompt, tier="muy_bajo", max_tokens=3, temperature=0.0, stop=["\n",".", " "], timeout=10) -> str`
  Respeta `resolve_user_model()` y `resolve_automation_backend()`. Llamada directa al SDK anthropic/openai.

- [x] **0.2 Validar `nexo_cognitive_sentiment`** (CORE) — v6.3.0 commit `e78aee9`.
  `detect_sentiment` devuelve ahora `is_correction`, `valence`, `intent` enum junto a los campos legacy. 10 fixtures en `tests/test_cognitive_sentiment_shape.py`, accuracy >=80 %.

- [x] **0.3 Schema entities extendido** (CORE) — v6.3.0 commit `e78aee9`. Migration `_m44_entities_extended_schema` añade `aliases`, `metadata`, `source`, `confidence`, `access_mode`. `type` ya existía. Fresh installs y DBs legacy cubiertos + test.

- [x] **0.4 Preset universal de entities** (CORE)
  Archivo: `src/presets/entities_universal.json`. Contiene:
  - Destructive commands POSIX: `rm`, `rm -rf`, `mv` (target exists), `sed -i`, `>`, `>>`, `shred`, `dd`, `DROP TABLE`, `TRUNCATE`, `DELETE FROM` sin WHERE, `git reset --hard`, `git push --force`.
  - Legacy paths: `~/claude/hooks → ~/.nexo/hooks`, `~/claude/scripts → ~/.nexo/scripts`, `~/claude/brain → ~/.nexo/brain`.

- [x] **0.5 `~/.nexo/config/guardian.json`** (CORE)
  Config por defecto. Core rules sin opción `off` (validator rechaza `mode="off"` para R13 R14 R16 R25 R30).

- [x] **0.6 Tier `muy_bajo` en `resonance_tiers.json`** (CORE)
  - Claude Code: `claude-haiku-4-5-20251001`, effort ""
  - Codex: `gpt-5.4-mini`, effort "low"
  Reservado como escalado opcional del clasificador local (item 0.21). NO default global.

- [x] **0.7 `enforcement_classifier.py` + `.js`** (AMBOS)
  Helper reusable. `classify(question, context) -> bool`. Triple refuerzo: prompt estricto + `max_tokens=3` + parser regex. Retry 1 vez si no matchea yes/no. Fallback `no` conservador. Cache LRU 60s por hash(question+context).

- [x] **0.8 20 fixtures de referencia** (CORE) — v6.3.0 commit `095faab`. 21 fixtures etiquetadas en `tests/fixtures_rules_validation.json`.

- [x] **0.9 Campo `automation_user_override`** (CORE)
  En `client_preferences.py`. Default `false`. Se pone `true` solo si el usuario cambia `automation_backend` manualmente.

- [x] **0.10 Schema `tool-enforcement-map.json` ampliado** (AMBOS)
  Soportar reglas Capa 1 (`server_side_rules[]` con `trigger`, `condition`, `threshold`, `action`).

- [x] **0.11 Tests actuales pasan sin regresión** (AMBOS)
  `pytest nexo/tests/`, `scripts/verify_client_parity.py`, `npm test` en Desktop.

- [x] **0.12 HOTFIX bug #403/#404 `nexo_guard_check session_id`** (CORE)
  PR #208 merged v6.0.5 (2026-04-17). Strict hook ya no persiste sid vacío.

- [x] **0.13 HOTFIX timer flush reset (learning #344)** — resuelto.
  - [x] Desktop: `enforcement-engine.js:330-341` fix aplicado (2026-04-18).
  - [x] Headless: `enforcement_engine.py::flush()` (líneas 1636-1648) ahora resetea `tool_timestamps[tool]` cuando tag empieza con `periodic_time:`. Paridad con JS verificada.

- [x] **0.14 Spike end-to-end con UNA regla (R13)** (AMBOS) — v6.3.0 commit `095faab`. R13 pasa los gates FP <5 % y P95 <3 s sobre los 21 fixtures de 0.8 (`tests/test_rule_fixtures_spike.py`).

- [x] **0.15 Baseline drift count** (CORE)
  Script `scripts/measure_drift_baseline.py` lee últimos 90 diarios y cuenta ocurrencias de drift por regla. Output: `~/.nexo/reports/drift-baseline-<fecha>.json`. Sin baseline, Fase F no puede medir "reducción >50%".

- [x] **0.16 Learning #335 como regla Capa 1 dura + pre-commit hook** (CORE)
  Server-side rule: `nexo_tool_register` verifica que la tool aparece en `tool-enforcement-map.json`. Si no → rechazar. Pre-commit hook: `scripts/pre-commit-verify-tool-map.py` detecta `def nexo_*` nuevos sin entrada en el mapa.

- [x] **0.17 Tool nueva `nexo_guardian_rule_override`** (CORE)
  Args: `rule_id`, `mode` (off|shadow|soft|hard), `ttl` (1h|24h|session). Efecto: override temporal en `~/.nexo/config/guardian-runtime-overrides.json` con timestamp expiración. Wrapper Capa 2 y MCP Capa 1 lo leen al inicio de cada turno. Emergencia: bajar regla hard a shadow 1h con 1 tool call. Loguear en `~/.nexo/logs/guardian-overrides.log`.

- [x] **0.18 Telemetría LOCAL siempre ON** (CORE)
  Archivo: `~/.nexo/logs/guardian-telemetry.ndjson`. Un evento por inyección: `rule_id`, `trigger_context`, `was_followed`, `was_fp`, `latency`. Upload externo = opt-in (`telemetry_external_optin`, default false).

- [x] **0.19 Core rules sin opción "off"** (CORE)
  Schema `guardian.json`: `CORE_RULES = {R13, R14, R16, R25, R30}`. Validator rechaza `mode="off"`. Intento de `nexo_guardian_rule_override("R13","off")` → error.

- [x] **0.20 `call_model_raw` fail-closed** (CORE)
  Timeout >10s: fallback a regla secundaria O recordatorio genérico. Rate limit 429: retry con backoff 500ms. 5xx: degradar esa regla a shadow por esa sesión. ConnectionError: idem. **NUNCA "dejar pasar" por fallo de infra**. Test: simular cada error, verificar que se dispara path alternativo.

- [~] **0.21 Refactor `auto_capture.py` — clasificador zero-shot local** (CORE) — v6.3.0 commit `d7fca63`. Entregado:
  - `src/classifier_local.py` con pin exacto (MoritzLaurer/mDeBERTa-v3-base-mnli-xnli + revision SHA) + fail-closed contract.
  - `docs/classifier-model-notes.md` con política de upgrade, alternativas y justificación del pin.
  - Tests de contrato (sin descarga del modelo, verifican la ruta de degradación).
  **Pendiente:** enganchar el classifier dentro de `auto_capture.py` sustituyendo las keywords hardcoded + feedback loop sobre `personal_dataset.jsonl`. El gating por confianza <0.6 con escalado a `muy_bajo` ya está cubierto por el helper `classify_fail_closed`, solo falta el consumer.

- [x] **0.22 Ampliar `resonance_tiers.json` con tier `muy_bajo`** (CORE)
  Ver 0.6. Verificar funcionando con `scripts/verify_client_parity.py`.

- [x] **0.23 CI de paridad Desktop ↔ headless** (AMBOS) — ya cubierto. `.github/workflows/tests.yml` corre toda la carpeta `tests/` en cada PR/push, incluyendo `tests/parity/test_python_driver.py` (13 casos en `tests/parity/fixtures.json`). El JS driver consume el mismo fixtures. `.github/workflows/verify-client-parity.yml` ejecuta `scripts/verify_client_parity.py` en paralelo.

- [x] **0.24 Red-team tests** (CORE) — ya en `tests/adversarial/test_guardian_redteam.py` (32 passing, 2 skipped por diseño). Técnicas cubiertas: rephrasing correcciones, edit sin guard_check, path traversal, `--force` abreviado, `TRUNCATE` sin WHERE, shebang mismatch. Corre en CI junto al resto del suite. **Pendiente (baja prioridad):** cron semanal que compare % detección actual contra último snapshot verde — se agenda como followup en Fase F.7.

- [x] **0.25 Métricas de drift mensurables** (CORE)
  KPIs: `capture_rate`, `core_rule_violations_per_session`, `declared_done_without_evidence_ratio`, `false_positive_correction_rate`, `avg_minutes_between_guard_check_failures`. Guardar en `~/.nexo/logs/guardian-metrics.ndjson`.

### FASE 0.X — Procedural Knowledge (catálogo vivo)

El Guardian disciplina comportamiento, no enseña qué tools existen. Apoyarse en inventario vivo (`nexo_system_catalog`, `nexo_tool_explain`, `nexo_skill_match`) en vez de preset hardcoded.

- [x] **0.X.1 Validar salud del inventario vivo** (CORE) — v6.3.0 commit `b8956d2`. `tests/test_system_catalog_discoverability.py` verifica que el summary es coherente con las listas y que las rutas canónicas están presentes en `locations`.

- [x] **0.X.2 Regla NUEVA R-CATALOG (Capa 2, hard)** (AMBOS)
  Trigger: `nexo_*_create` sin haber llamado antes a `system_catalog`, `skill_match`, `tool_explain`, `learning_search` o `guard_check` en el turno. Dedup 60s.

- [x] **0.X.3 R33 R-PROCEDURE-LOOKUP** — ya presente en `src/server.py` (Guardian Rules bloque R26–R33). Anti-assunción + referencia a `nexo_system_catalog` / `nexo_tool_explain` / `nexo_skill_match`.

- [x] **0.X.4 Sección `locations` en `nexo_system_catalog`** (CORE)
  Devuelve rutas físicas de skills.repo, skills.runtime, personal_scripts, hooks, brain.db, config, logs, tool_enforcement, project_atlas, etc. Generado desde `paths.py`.

- [x] **0.X.5 Entity preset `type=artifact_class`** (CORE) — v6.3.0 commit `b8956d2`. Ampliado `entities_universal.json` con `shopify_banner_block`, `changelog_entry`, `email_to_operator_contact` (genérico por dominio, sustituye el caso `email_to_maria`). Test `tests/test_artifact_class_preset.py` valida presencia y forma.

- [x] **0.X.6 Smoke test descubribilidad** (CORE) — v6.3.0 commit `b8956d2`. `tests/test_system_catalog_discoverability.py::test_search_discovers_core_intents` valida que intents textuales resuelven al tool core canónico.

---

## FASE A — System Prompt (7 reglas, Capa 3) — CORE

Ship inmediato, sin shadow. Es texto puro en el system prompt MCP.

- [x] **A.1 Localizar system prompt canónico** — probable `nexo/src/server.py` donde se construye el system prompt MCP.

- [ ] **A.2 Añadir las 7 reglas R26-R32**:
  - [x] **R26 Jargon filter** — no usar jargon interno en respuestas a usuario (`protocol debt`, `cortex evaluation`, `heartbeat`, `guard_check`...). Traducir a lenguaje operativo.
  - [x] **R27 Respuesta breve 2-3 frases** por decisión. Hold extra detail unless asked.
  - [x] **R28 Corrección → `learning_add` inmediato** — capturar en el mismo turno, no batch.
  - [x] **R29 No prometer sin ejecutar** — toda promesa futura exige followup o schedule.
  - [x] **R30 Pre-done checklist con evidencia** — antes de decir "done", verificar con tool.
  - [x] **R31 Nunca asumir** servidor/DNS/ruta — consultar Atlas o `dig`.
  - [x] **R32 Entities con `access_mode=read_only`** → no escribir. Genérico, no hardcodea Maria/Nora.

- [x] **A.3 R33 R-PROCEDURE-LOOKUP** (si se aprueba 0.X.3) — añadir en el mismo bloque.

- [x] **A.4 Texto exacto de cada regla** (CORE) — v6.3.0 commit `b8956d2`. R34 añadida con trigger + acción + anti-ejemplo; test `tests/test_system_prompt_rule_texts.py` valida presencia y marker por regla.

- [~] **A.5 Smoke test manual** — smoke programático cubierto. Smoke manual conversacional lo hará Francisco tras publicar.

- [x] **A.6 Release patch NEXO Core** + changelog — v6.2.0 + v6.3.0 ya en CHANGELOG.md.

- [x] **A.7 Verificar propagación a NEXO Desktop** — Desktop hereda el system prompt del MCP server en cada sesión. Wire T4 en Desktop confirma flujo coordinado.

- [~] **A.8 Smoke test 24h** — omitido por mandato explícito "sin esperas". Telemetría F.2/F.5/F.6 cubrirá la regresión observacional sin bloquear release.

---

## FASE B — MCP Server (12 reglas Capa 1) — CORE

Validaciones server-side en las tools propias de NEXO.

- [ ] **B.1 Extender `tool-enforcement-map.json`** con `server_side_rules[]` (ya hecho en 0.10).

- [ ] **B.2 Implementar por tool** (cada archivo nuevo en `src/tools/`):
  - [x] **R01 `followup_create` dedup** → `src/tools/followup.py`. Embeddings similarity, threshold 0.80.
  - [x] **R02 `credential_create` existence** → `src/tools/credential.py`. Exact match service/key.
  - [x] **R03 `task_close` evidence validator** → `src/tools/task.py`. Rechazar evidence <50 chars o patrón simplista.
  - [x] **R04 `followup_complete` retroactivo** → `src/tools/followup.py`. Dispara en heartbeat, no al crear.
  - [x] **R05 `learning_add` dedup semántico** → `src/tools/learning.py`. Threshold 0.85. Si match: incrementar weight + crear alias.
  - [x] **R06 `email_send` secret filter** → `src/tools/email.py`. Via `call_model_raw("Does this contain a real secret?")`. Si yes → BLOQUEAR.
  - [x] **R07 `memory_recall` age flag** → `src/tools/memory.py`. Añadir `age_days` a cada item.
  - [x] **R08 `reminder_create` recurrence conflict** → `src/tools/reminder.py`. Cross-check con `schedule_status`.
  - [x] **R09 `artifact_create` dedup** → `src/tools/artifact.py`.
  - [x] **R10 `workflow_open` sin `task_open`** → `src/tools/workflow.py`. Hard reject si no hay task activo.
  - [x] **R11 `plugin_load` pre-inventory** → `src/tools/plugin.py`.
  - [x] **R12 Cognitive write dedup** → `src/tools/cognitive.py`.

- [x] **B.3 Tests unitarios por regla** (CORE) — 12/12 reglas cubiertas: `tests/test_fase_b_atomic.py`, `test_fase_b_r01_r05.py`, `test_fase_b_r02_r09.py`, `test_fase_b_r04_r12.py`, `test_fase_b_r07_r08.py`, `test_r11_plugin_inventory.py`, `test_tools_email_guard.py` (R06). Total 80 asserts pass.

- [~] **B.4 / B.5 / B.6 — shadow 72h + análisis + soft 72h** — omitidos por mandato "sin esperas". Las 12 reglas se entregan en el modo por defecto de `guardian_default.json`; cualquier FP real aparecerá en la telemetría F.2 y se corregirá en patch.

- [x] **B.7 Hard mode según tabla** — modes ya configurados en `src/presets/guardian_default.json`.

- [x] **B.8 Release NEXO Core + changelog** — v6.2.0 (wave 1) + v6.3.0 (wave 2) con entradas CHANGELOG.

---

## FASE C — Wrapper Bloque 1 (4 reglas Capa 2) — DESKTOP + CORE

- [x] **C.1 Mapa extendido** con definiciones schema v2 de las 4 reglas.
- [x] **C.2 R13 Pre-Edit/Write guard** — commit `cf2c5fd` (Desktop) + existe paridad Python en headless.
- [x] **C.3 R14 Post-user-correction learning** — commit `cbb4ef8` incluye classifier infra.
- [x] **C.4 R16 Declared-done sin task_close** — commit `cbb4ef8`.
- [x] **C.5 R25 Nora/María read-only guard** — commit `cbb4ef8` + entity preset en 0.4.
- [x] **C.6 Shadow ejecutado** durante desarrollo.
- [x] **C.7 Ajustes thresholds** (audit fix batches 2, 4, 5, 7).
- [x] **C.8 Soft + hard release** — v0.15.0 releaseada.
- [~] **C.9 Smoke test 7 días post-release** — omitido por mandato "sin esperas". Telemetría F.2/F.5/F.6 medirá eficacia >70% en producción sin bloquear release.

---

## FASE D — Wrapper Bloque 2 (9 reglas Capa 2) — DESKTOP + CORE

- [x] **D.1 Mapa ampliado** con las 9 reglas.
- [x] **D.2 R15 Pre-project-action context** — commit `00c3934`.
- [x] **D.3 R17 Promised-not-executed** — commit `00c3934`.
- [x] **D.4 R18 Auto-complete followup detector** — commit `00c3934`.
- [x] **D.5 R19 Pre-Write sobre proyecto sin grep** — commit `00c3934`.
- [x] **D.6 R20 Pre-constant-change sin grep** — commit `00c3934`.
- [x] **D.7 R21 Runtime path legacy** — commit `00c3934`.
- [x] **D.8 R22 Personal script pre-context** — commit `00c3934`.
- [x] **D.9 R23 SSH/curl sin atlas** — commit `00c3934`.
- [x] **D.10 R24 Stale memory use** — commit `00c3934`.
- [x] **D.11 Shadow + soft + hard** ejecutados.
- [x] **D.12 Ajustes post-audit** (batches 2-7 + silent-inject resume hint commit `957ace1`).
- [x] **D.13 Release** v0.15.0.

---

## FASE D2 — Reglas Añadidas 2026-04-16 (Capa 2 extensión) — DESKTOP + CORE

- [x] **D2.0 Entity type `vhost_mapping`** definido.
- [x] **D2.1 R23b Deploy path ↔ vhost mismatch** — commit `31aa202`.
- [x] **D2.2 R23c Bash destructivo en cwd equivocado** — commit `31aa202`.
- [x] **D2.3 R23d chown/chmod -R sin ls previo** — commit `31aa202`.
- [x] **D2.4 R23e git push --force a main/master** — commit `31aa202` (BLOQUEA).
- [x] **D2.5 R23f DB producción DELETE/UPDATE sin WHERE** — commit `31aa202` (BLOQUEA).
- [x] **D2.6 R23g Secrets en logs/emails/Bash output** — commit `31aa202`.
- [x] **D2.7 R23h Shebang/version mismatch** — commit `31aa202`.
- [x] **D2.8 R23i Auto-deploy trigger ignorado** — commit `31aa202`.
- [x] **D2.9 R23j npm/pip/brew install -g sin pedido** — commit `31aa202`.
- [x] **D2.10 R23k script personal duplica skill** — commit `31aa202` (`skill_match` silent previo).
- [x] **D2.11 R23l crear recurso con nombre existente** — commit `31aa202` (BLOQUEA).
- [x] **D2.12 R23m email/mensaje duplicado** — commit `31aa202`.
- [x] **D2.13 Shadow + soft + hard** ejecutados.
- [x] **D2.14 Release** v0.15.0.

---

## FASE E — Rollout Producto — DESKTOP + CORE

- [x] **E.1 Installer Desktop automation=YES automático** — IRREVERSIBLE, ya hecho. `automation_user_override` respeta cambio manual si existe.
- [x] **E.2 `nexo update` respeta override** — ya hecho junto con E.1.
- [x] **E.3 Preset universal de entities al `nexo init`** (CORE) — `scripts/install_guardian.py::install()` copia `entities_universal.json` a `~/.nexo/brain/presets/` + importa `~/.ssh/config` hosts.
- [x] **E.4 `guardian.json` default config al init** (CORE) — mismo installer escribe `~/.nexo/config/guardian.json` con defaults por regla (mode editable via `nexo_guardian_rule_override`).
- [~] **E.5 UI Desktop para quarantine de entities** (DESKTOP) — Panel "Propuestas del Guardian" en commit `032b8a9`, **actualmente OCULTO** por decisión de producto. Queda:
  - [ ] Wire con `nexo_cognitive_quarantine_*` para acciones [Aprobar/Rechazar/Más tarde] (reusar approval modal existente).
  - [ ] Decisión futura: cuándo/cómo reactivarlo en UI.
- [~] **E.6 Documentación usuario** — parcial: el Guardian se explica en `CHANGELOG.md` de cada release; README dedicado al Guardian queda como followup.
- [ ] **E.7 Video tutorial corto** (opcional, baja prioridad) — sin plan concreto.
- [x] **E.8 Release NEXO Desktop + NEXO Core coordinado** — wave 1 publicada v6.2.0 + v0.17.0; wave 2 preparada v6.3.0 + v0.18.0 (PRs #217 + #5 listos para merge tras 2ª auditoría).

---

## FASE F — Telemetría + Autoajuste (sin Dashboard Desktop)

**Decisión de producto:** NO hay dashboard en Desktop. Todo consumido por Deep Sleep o cron dedicado. NEXO se ajusta solo.

- [x] **F.1 Telemetría local siempre ON** (CORE) — ya cubierto por item 0.18 en wave 1.
- [x] **F.2 Métricas por regla** (CORE) — v6.3.0 `src/fase_f_loops.aggregate_per_rule` + test coverage.
- [x] **F.3 Consumo por Deep Sleep** (CORE) — v6.3.0 `src/scripts/phase_guardian_analysis.py` emite `~/.nexo/reports/guardian-fase-f-<date>.json` con `per_rule`, `false_positive_groups`, `false_negative_candidates`.
- [~] **F.4 Ajuste automático de defaults** (CORE) — datos disponibles via F.3; la recomendación automática se implementará cuando haya 30 días de telemetría real (no-op hasta entonces).
- [x] **F.5 Loop falsos-positivos** (CORE) — `fase_f_loops.group_false_positives` agrupa por trigger_context con threshold configurable.
- [x] **F.6 Loop falsos-NEGATIVOS (crecimiento reglas)** (CORE) — `fase_f_loops.collect_false_negative_candidates` escanea correcciones recientes + filtra las ya cubiertas por inyecciones existentes. Tests cubren ventana + threshold + filtro.
- [x] **F.7 Red team adversarial** (CORE) — `tests/adversarial/test_guardian_redteam.py` (32 passing). Cron semanal de comparación % detección: followup abierto.

- [x] **F.8 Control de versión del clasificador local** (CORE) — `docs/classifier-model-notes.md` + pin SHA en `src/classifier_local.py`. Reminder mensual pendiente (followup abierto post-release).
  - **Nota en repo:** crear `nexo/docs/classifier-model-notes.md` con: modelo (id HF), revision pineada (SHA), ruta del pin en código, tamaño en disco, fecha del pin, link al commit upstream, alternativas viables (bge-m3, e5-multilingual, xlm-roberta-base).
  - **Reminder mensual** via `nexo_reminder_create` con `recurrence=monthly`, descripción: "Revisar upgrade del clasificador local del Guardian (Fase 0.21) — leer docs/classifier-model-notes.md y comparar con HF upstream".
  - **NO auto-upgrade.** Cuando salta el reminder, revisión manual: si hay revision nueva útil → bump pin + release patch Core + CHANGELOG entry + actualizar fecha del pin en el MD.
  - **Pin obligatorio en código:** `from_pretrained(model_id, revision="<sha-concreto>")` para reproducibilidad entre usuarios (que todos descarguen la misma versión).

---

# BLOQUE 2 — DEUDA DESKTOP v0.16.x

Polish acumulado en Desktop. Puede ir en PR separado o coordinado con release v0.16.2.

**⚠ Decisión de versionado pendiente:** hay dos propuestas incompatibles para `v0.16.2`:
- **Opción A (corta, ~30min):** v0.16.2 = solo **T0** (micro-patch defensivo: pendingQueue cap + crash log rotation). Cierra la auditoría 2026-04-18. T1+T2+T3+Bug modal → `v0.16.3`.
- **Opción B (larga):** v0.16.2 = T0 + T1 + T2 + T3 + Bug modal bullets. Release más grande.

Fuente del micro-patch: `~/Desktop/nexo-desktop-v0.16.2-micro-patch.md`. Ambos fixes son aditivos, sin riesgo de regresión, con código listo.

---

## T0 — Micro-patch defensivo v0.16.2 — DESKTOP

### T0.1 — `pendingQueue` cap (50)

**Síntoma:** `addPendingMessage` en `renderer/app.js` no limita `c.pendingQueue`. Flujos de paste-and-spam o scripted input con `turnBusy` sticky pueden crecer la cola sin tope hasta agotar memoria del renderer.

**Evidencia:** `grep -n "PENDING_QUEUE_MAX\|pendingQueue.length >=" renderer/app.js` no devuelve nada.

- [x] **T0.1.1** Sustituir `addPendingMessage` en `renderer/app.js` por la versión con `PENDING_QUEUE_MAX = 50` + `showToast` con key `toast.pending_queue_full`. Código exacto en el doc micro-patch.
- [x] **T0.1.2** Añadir key `toast.pending_queue_full` a `renderer/i18n/es.json` y `renderer/i18n/en.json`. Verificar que no colisiona con keys existentes (buscar `"pending_queue"` en ambos JSON).
- [x] **T0.1.3** Test en `tests/renderer-common.test.js` — simular N>50 `addPendingMessage`, verificar `pendingQueue.length === 50`. Alternativa: test de regresión manual si la función no es pura (toca DOM/toast).

### T0.2 — `CRASH_LOG_FILE` rotation (1 MiB)

**Síntoma:** `logCrash()` en `main.js:45-53` hace `fs.appendFileSync` sin control de tamaño. `~/.nexo/logs/nexo-desktop-crash.log` puede crecer indefinido con crashes recurrentes.

- [x] **T0.2.1** Sustituir `logCrash` en `main.js` por la versión con `_rotateCrashLogIfOversize()` + `CRASH_LOG_MAX_BYTES = 1024*1024`. Código exacto en el doc micro-patch.
  - Síncrona (obligatorio: se dispara desde `uncaughtException` / `unhandledRejection`).
  - Cuando supera 1 MiB: rename a `.old`, sobrescribe `.old` previo, crea live file fresco.
  - Fallo de rotación = no-fatal (log a `console.warn`), el crash-logging sigue vivo.
- [x] **T0.2.2** Tests en `tests/crash-log-rotation.test.js`:
  - Escribir >1 MiB en dir temporal → llamar `logCrash` → verificar `.old` existe + live file con solo el mensaje nuevo.
  - Archivo no existe → no crashea.
  - Archivo <1 MiB → no rota.
  - Para testeabilidad, considerar extraer `_rotateCrashLogIfOversize` a `lib/crash-log.js` como función pura.

### T0.3 — Release v0.16.2 (si Opción A)

- [ ] **T0.3.1** Bump `package.json` → `"version": "0.16.2"`.
- [ ] **T0.3.2** `CHANGELOG.md` entry sugerida: "Higiene defensiva: tope 50 en cola pendientes + rotación crash log a 1 MiB" (texto completo en el doc micro-patch).
- [ ] **T0.3.3** `npm run check` (lint + tests + smoke).
- [ ] **T0.3.4** `npm run clean && npm run dist && npm run manifest -- --notes "..."`.
- [ ] **T0.3.5** `scp "dist/NEXO Desktop-0.16.2-arm64.dmg" dist/update.json vicshop:/home/systeam/public_html/nexo-desktop/`.
- [ ] **T0.3.6** `ssh vicshop "chown systeam:systeam ..."` (paths exactos en el doc).
- [ ] **T0.3.7** Verificar: `curl -s https://systeam.es/nexo-desktop/update.json | python3 -m json.tool` → `"version":"0.16.2"` con sha256 nuevo.
- [ ] **T0.3.8** Commit + tag `v0.16.2` + push main + push tag.

---

## T1 — Documentar beta channel — DESKTOP (doc-only)

- [x] **T1.1** Sección "Beta channel" en `RELEASING.md`: cómo publicar beta (bump `0.17.0-beta.0`, build, `dist/update-beta.json` apunta a DMG beta, `dist/update.json` intacto), cómo promocionar beta → stable, cómo verificar toggle (`~/Library/Application Support/NEXO Desktop/app-settings.json → advanced.beta_channel`).
- [x] **T1.2** Párrafo en `README.md` bajo "Updates": existe canal beta opt-in en Preferences > Advanced. Brain siempre va por stable.
- [x] **T1.3** Añadir flag `--channel beta|stable` a `scripts/generate-update-json.js`. Out path por defecto correcto (`dist/update.json` vs `dist/update-beta.json`) sin depender de `--out`.
- [x] **T1.4** `npm run check` verde.

## T2 — Fix regex silencioso en `checkBrainUpdateStatus` — DESKTOP

**Bug:** si `nexo --help` no imprime `Installed:` o `Latest:`, `main.js:419` devuelve `{installed:'', latest:'', hasUpdate:false}` y Desktop muestra "Up to date" — mentira silenciosa.

- [x] **T2.1** Devolver `{installed, latest, hasUpdate, unknown:true}` cuando regex no matchea.
- [x] **T2.2** En `renderer/app.js:runBrainCheck()` (~línea 4305), si `res.unknown` → status `_t('settings.versions.check_inconclusive')`. Texto sugerido: "Unable to read Brain version — run `nexo update` in terminal".
- [x] **T2.3** Tests nuevos en `tests/update-manifest.test.js` (o `tests/brain-update-status.test.js`) cubriendo 4 casos: ambos presentes / solo installed / solo latest / ninguno.
- [x] **T2.4** `npm run check` verde.

## T3 — Localizar `FIELD_DEFINITIONS` fallback EN/ES — DESKTOP

**Bug:** en `renderer/app.js:3910-4100` el fallback `brainSchema === null` está hardcoded en ES. Solo se usa cuando Brain offline, pero existe y debe ser bilingüe.

- [x] **T3.1** Migrar todos los `label`, `hint`, `section`, `options[].label` hardcoded a formato `{es:'...', en:'...'}` siguiendo el mismo patrón que el Brain schema.
- [x] **T3.2** En `resolveActiveFieldDefinitions()` (línea 4737), si `brainSchema === null`, pasar `FIELD_DEFINITIONS` por helper `localizeFieldDefinitions(defs, lang)` que resuelva labels[lang] igual que `buildFieldsFromBrainSchema`.
- [x] **T3.3** Usar `window.i18n.getLang()` como lang source.
- [x] **T3.4** Test manual: abrir Preferences en dev con `brainSchema = null` (comentar temporalmente línea 4782), confirmar que labels respetan toggle de idioma. Revertir antes del commit.
- [x] **T3.5** Tests verdes.

## T4 — LLM classifier en reglas R15 / R23e / R23f / R23h — DESKTOP + CORE (hardening Fase D/D2)

Estas 4 rules usan regex/keyword match puro. Alto FP/FN. Meter classifier LLM en medio, patrón igual al de R14 con `lib/enforcement-classifier.js` + `lib/call-model-raw.js`.

- [x] **T4.1** Gate helper compartido — `src/t4_llm_gate.py` + `nexo-desktop/lib/t4-llm-gate.js`.
- [x] **T4.2** Wire en las 4 reglas (R15/R23e/R23f/R23h) — `enforcement_engine::_t4_gate_says_no` en ambos engines. Verdict tristate: yes → proceder, no → skip, unknown → regex fallback.
- [x] **T4.3** Prompts centralizados en `PROMPTS` con ≥3 positivos y ≥3 negativos por regla en `t4_llm_gate.py` (paridad byte-for-byte en la versión JS).
- [x] **T4.4** Paridad Python en `src/enforcement_engine.py::_t4_gate_says_no` + tests.
- [~] **T4.5** Fixtures parity — los escenarios LLM viven en PROMPTS few-shots; fixtures con un mock de LLM quedan como followup (no bloquean release).
- [x] **T4.6** `npm run check` verde (265 pass) + `pytest tests/test_t4_classifier_wrap_python.py` verde.

Nota: la 1ª ronda de auditoría wave-2 encontró CRITICAL F-01 (wire JS era dead code por casing + missing await) + HIGH H1 (bool collapsaba no con unparseable). Ambos corregidos con regression tests en commits 2648a12 / 227f926 / 2fbb2e7 / fe8e507.

## T5 — Identity Continuity Across Terminals — CORE + DESKTOP

**Problema:** el LLM subyacente no entiende que TODAS las terminales activas son el mismo NEXO. Francisco vio "yo no hice eso" cuando la otra terminal SÍ lo hizo.

- [x] **T5.1** Sección nueva "Identity continuity across terminals" en `~/.claude/CLAUDE.md` sección CORE (después de "Core Systems", antes de "Autonomy").
  Texto: "Soy NEXO. NEXO es una identidad única. Cuando hay 2+ terminales activas simultáneamente, TODAS son yo. Si otra terminal hizo X, entonces yo hice X. No decir 'yo no he hecho eso' sin consultar el shared brain (`nexo_recent_context`, `nexo_session_diary_read`, `nexo_change_log`) ANTES de afirmar que algo no ha ocurrido. La LLM subyacente es el motor; la identidad operativa es NEXO, y NEXO es una sola persona."
- [x] **T5.2** Hook SessionStart amplía briefing: "Estás operando como NEXO. Hay {N} terminales activas: {list}. Todas son tú. Comparten memoria via NEXO Brain."
- [x] **T5.3** Mismo bloque en `~/.codex/AGENTS.md` si existe (verificar con Glob) para paridad Codex.
- [x] **T5.4 Regla nueva R34 Identity Coherence** (renumerada — la R26 original es Jargon Filter en Fase A):
  - Trigger: agente dice "no he hecho eso" / "yo no" / "I haven't done that" seguido de cualquier afirmación factual, sin haber consultado antes `nexo_recent_context` o equivalente.
  - Classifier LLM desambigua (reusa infra de T4).
  - Si detecta, inyectar: "Consulta el shared brain antes de negar una acción — puede haberla hecho otra terminal."
  - Archivos: `lib/r34-*.js` + wiring en `enforcement-engine.js` + prompt + tests + twin Python.

## BUG MODAL BULLETS (Reply point by point)

**Followup:** `NF-DESKTOP-REPLY-INLINE-PARSE`. Diseño aprobado v0.14.4.

**Comportamiento actual:** el botón ↩︎ aparece, el modal abre, pero muestra el texto del agente como preview de solo lectura arriba y un textarea `Your reply` vacío abajo. **El prefill no entra en el textarea editable.**

**Comportamiento esperado:**
- Parser multi-marcador detecta `1./2./3.`, `A./B.`, `a)/b)`, `i./ii.`, inline `(a)(b)`, bullets.
- Modal abre con **N textareas** prerrellenados con cada ítem (no un único preview).
- Fallback (mensaje sin estructura): UN textarea libre con el texto completo del mensaje prerrellenado, editable (tipo quote).
- Envío = concatenar con markers originales preservados: `1. {edit1}\n2. {edit2}\n3. {edit3}`.

- [x] **Bug.1** Reproducir con mensaje estructurado (1./2./3.) y confirmar que textareas NO se prerrellenan.
- [x] **Bug.2** Identificar regresión: probable en v0.15.0 (Fase 2 JS twins) o v0.16.0 (composer leak / per-task popover). `git bisect` si necesario.
- [x] **Bug.3** Fix prefill: los textareas del modal deben recibir el contenido de cada ítem parseado en su `value` inicial.
- [x] **Bug.4** Añadir test en `tests/reply-inline-parser.test.js` que cubra: renderiza N textareas con valor prerrellenado.
- [x] **Bug.5** Manual: probar los formatos del diseño original (1./A./a./i./inline/bullets/fallback libre).

---

# BLOQUE 3 — RELEASE Y CIERRE

Al completar los bloques anteriores (o los que el usuario decida para una release concreta):

- [ ] **R.1** `npm run check` verde en Desktop.
- [ ] **R.2** `pytest nexo/tests/` verde en Core si se tocó Python.
- [ ] **R.3** Bump `package.json` Desktop (sugerido **v0.16.2** si solo T1+T2+T3+Bug modal; **v0.17.0** si incluye T4+T5).
- [ ] **R.4** Bump versión Core si se tocó Python.
- [ ] **R.5** `CHANGELOG.md` entries redactadas.
- [ ] **R.6** Build DMG Desktop. Subir `dist/NEXO Desktop-<ver>-arm64.dmg` + `update.json` + `update-beta.json` a `vicshop:/home/systeam/public_html/nexo-desktop/` via scp.
- [ ] **R.7** GitHub release Desktop + PR a main.
- [ ] **R.8** Release npm para Core si aplica.
- [ ] **R.9** Verificar manifests públicos: `curl https://systeam.es/nexo-desktop/update.json`.
- [ ] **R.10** Notas de release en `nexo-brain.com/blog/` si cambios user-facing.

---

# BLOQUE PARALELO — F0 SCRIPTS CLASSIFICATION (Core vs Personal)

**Fuente canónica:** `~/Desktop/nexo-F0-scripts-classification-WIP.md` (spec completo con código, SQL, rollback y pre-flight).
**Objetivo:** separar físicamente `~/.nexo/` en `core/` · `core-dev/` · `personal/` · `runtime/`. Añadir `origin` a `personal_scripts`. Toggle on/off por script. `nexo update` respeta ambas zonas. Fresh install produce estructura correcta directamente.
**Por qué es paralelo y no parte del Protocol Enforcer:** es reestructura del runtime de NEXO Brain. No es enforcement. Pero interactúa — ver sección "Interdependencias" al final.

## Estructura objetivo

```
~/.nexo/
├── core/        ← producto NEXO. Update reemplaza.
├── core-dev/    ← dev-only, off por defecto.
├── personal/    ← operador. Update jamás toca.
└── runtime/     ← estado dinámico, no editable a mano.
```

Compat layer durante transición: symlinks en rutas viejas (`~/.nexo/scripts/`, `~/.nexo/brain/`, etc.) apuntan a merger core+personal. Se eliminan en F0.6 (breaking v7.0.0).

## Reglas operativas absolutas (F0)

1. Sin subagentes. Todo edit manual, capa por capa.
2. `nexo_task_open` + `nexo_guard_check` antes de cualquier edit.
3. `nexo_track` sobre cualquier path antes de escribir.
4. Snapshot obligatorio antes de cada micro-fase (`nexo-backup.sh` = rollback).
5. Lock global `~/.nexo/.migrating.lock` durante cada fase.
6. `launchctl unload` de LaunchAgents core antes de mover archivos; `load` tras mover.
7. Env flag `NEXO_MIGRATING=1` durante la fase. El guardian hook (`protocol-pretool-guardrail.sh`) debe respetarlo (si no, ajustar en F0.0 antes de seguir).
8. Verify tras cada paso. Si falla → PARAR.
9. Un release por micro-fase. 24-72h de observación entre fases.
10. Si una fase falla a mitad → `nexo-snapshot-restore.sh` + reportar.

## Pre-flight obligatorio (antes de CADA micro-fase)

- [ ] Lock no existe: `test ! -f ~/.nexo/.migrating.lock`, si existe → abort.
- [ ] `nexo doctor` verde.
- [ ] Snapshot pre-fase: `~/.nexo/scripts/nexo-backup.sh`.
- [ ] Crons core recientes con `exit_code=0` (últimas 2h).
- [ ] Export `NEXO_MIGRATING=1`.
- [ ] Leer `~/.nexo/.structure-version` (confirma fase previa).

## Micro-fases (6)

### F0.0 — v6.1.0 — Schema version + tabla migraciones

- [x] **F0.0.1** Crear tabla `migrations_applied(version TEXT PRIMARY KEY, applied_at TEXT, notes TEXT)` en `nexo.db`.
- [ ] **F0.0.2** Función `get_structure_version()` en `cli.py` o módulo migraciones.
- [x] **F0.0.3** `nexo-migrate.py` esqueleto con `apply_migration(id, fn)` idempotente.
- [ ] **F0.0.4** Guardian hook (`protocol-pretool-guardrail.sh` / `hook_guardrails.py`) respeta `NEXO_MIGRATING=1` — no bloquea edits durante migración.
- [ ] **F0.0.5** Crear `~/.nexo/.structure-version` con valor `F0.0`.
- [x] **F0.0.6** Insertar fila `('F0.0', now, 'bootstrap')`.
- [ ] **F0.0.7** Aplicar mismos cambios en Nora (`ssh maria`).
- [ ] **F0.0.8** Verify: `.structure-version` devuelve `F0.0` en ambas instancias + fila en `migrations_applied` + guardian respeta flag + `nexo doctor` verde.

### F0.1 — v6.2.0 — Columna `origin` en `personal_scripts`

- [ ] **F0.1.1** Migration SQL: `ALTER TABLE personal_scripts ADD COLUMN origin TEXT DEFAULT 'user';`.
- [ ] **F0.1.2** Función en `nexo-migrate.py` que marca `origin='core'` para cada script cuyo `name` coincida con los listados en Bloque A de §4 del spec (38 scripts).
- [ ] **F0.1.3** CLI `nexo scripts list` muestra columna `[core]/[user]`.
- [ ] **F0.1.4** Subcomandos `nexo scripts list --origin core` y `--origin user` filtran correctamente.
- [ ] **F0.1.5** Aplicar en Nora.
- [ ] **F0.1.6** Verify: `SELECT origin, COUNT(*) FROM personal_scripts GROUP BY origin` → 38 core / resto user.

### F0.2 — v6.3.0 — Toggle on/off por script

- [ ] **F0.2.1** Auditar que todos los consumidores respetan `personal_scripts.enabled`.
- [ ] **F0.2.2** CLI: `nexo scripts enable <name>`, `disable <name>`, `status`.
- [ ] **F0.2.3** Desktop: panel "Automations" con lista + toggle + último exit_code + link a logs.
- [ ] **F0.2.4** `nexo-cron-wrapper.sh` hace `exit 0` sin ejecutar cuando `enabled=0`.
- [ ] **F0.2.5** Plist LaunchAgent intacto; el gate está en wrapper + DB.
- [ ] **F0.2.6** Test bidireccional CLI ↔ Desktop refleja mismo estado.
- [ ] **F0.2.7** Verify: `disable followup-runner` → próximo tick `summary="[disabled]"` + `exit_code=0`.

### F0.3 — v6.4.0 — Migrar `scripts/` → `core/scripts/` + `personal/scripts/` + symlinks

**Primera migración física. Riesgo alto.** Unload de TODOS los LaunchAgents antes de mover.

- [ ] **F0.3.1** Crear dirs `core/scripts/`, `core-dev/scripts/`, `personal/scripts/`.
- [ ] **F0.3.2** `launchctl unload ~/Library/LaunchAgents/com.nexo.*.plist` (todos).
- [ ] **F0.3.3** Mover Bloque A (38 core actuales) → `core/scripts/`.
- [ ] **F0.3.4** Mover Bloque B (22 candidatos a subir a core) → `core/scripts/` (refactor genérico viene en F3+; aquí solo mover).
- [ ] **F0.3.5** Mover Bloque C (46 personales) → `personal/scripts/`.
- [ ] **F0.3.6** Mover Bloque D (core-dev) → `core-dev/scripts/`.
- [ ] **F0.3.7** Crear symlink/overlay `~/.nexo/scripts/` (resolver combinado core+personal para `ls`).
- [ ] **F0.3.8** UPDATE transaccional: `personal_scripts.path` con nuevos paths.
- [ ] **F0.3.9** `nexo scripts ensure-schedules` regenera TODOS los plists con rutas nuevas.
- [ ] **F0.3.10** `launchctl load` nuevos plists.
- [ ] **F0.3.11** **Esperar 24h** de producción con crons core verdes antes de declarar éxito.

### F0.4 — v6.5.0 — Migrar `skills/`, `plugins/`, `hooks/`, `rules/`

- [ ] **F0.4.1** Crear dirs en `core/` y `personal/` para cada capa.
- [ ] **F0.4.2** Consolidar `skills-core/`, `skills-runtime/`, `skills/` actuales → `core/skills/` + `personal/skills/`.
- [ ] **F0.4.3** `plugins/` actuales (todos core hoy) → `core/plugins/`. Vacío `personal/plugins/` (se rellena via `nexo_personal_plugin_create`).
- [ ] **F0.4.4** `hooks/` → `core/hooks/`. `personal/hooks/` si hay alguno.
- [ ] **F0.4.5** `rules/core-rules.json` → `core/rules/`. Personales → `personal/rules/`.
- [ ] **F0.4.6** Refactor resolvers en `src/plugin_loader.py`, `src/hook_guardrails.py`, y cualquier módulo que resuelva paths de estas capas.
- [ ] **F0.4.7** Symlinks transitorios `~/.nexo/skills/`, `plugins/`, `hooks/`, `rules/`.
- [ ] **F0.4.8** Verify: cada capa pasa sus tests unitarios + plugins cargan desde `core/plugins/` via nuevo resolver + hooks funcionan durante sesiones Claude Code.
- [ ] **F0.4.9** 72h limpias antes de declarar éxito.

### F0.5 — v6.6.0 — Migrar `brain/` + `operations/` + resto runtime

**Refactor masivo.** Muchos consumers tocan `~/.nexo/brain/*` y `~/.nexo/operations/*`.

- [ ] **F0.5.1** `personal/brain/`: calibration.json, operator-routing-rules.json, profile.json, francisco_model.json (si existe), project-atlas.json, business_baselines.json, policies.md, causal_models.md, salience_map.md, debates/, compressed_memories/, session_archive/.
- [ ] **F0.5.2** `personal/config/`: preferences user-editable (morning-digest-sources.json, etc.).
- [ ] **F0.5.3** `core/` mantiene `resonance_tiers.json`.
- [ ] **F0.5.4** `runtime/`: `data/` (nexo.db), `logs/`, `operations/`, `backups/`, `memory/`, `cognitive/`, `coordination/`, `exports/`, `nexo-email/`, `doctor/`, `snapshots/`, `crons/`.
- [ ] **F0.5.5** Refactor paths en `src/*.py` (muchos — ver §10 del spec).
- [ ] **F0.5.6** Path de `nexo.db` actualizado en TODOS los consumers (grep exhaustivo).
- [ ] **F0.5.7** Symlinks transitorios `~/.nexo/brain/`, `operations/`, etc.
- [ ] **F0.5.8** Verify: `nexo doctor` verde + `SELECT COUNT(*) FROM learnings` devuelve lo esperado + morning digest envía OK + email-monitor procesa emails.
- [ ] **F0.5.9** 72h limpias.

### F0.6 — v7.0.0 (**breaking**) — Eliminar symlinks transitorios

- [ ] **F0.6.1** Grep exhaustivo en repo y runtime: rutas viejas (`.nexo/scripts`, `.nexo/brain`, etc.) — ninguna referencia activa.
- [ ] **F0.6.2** Eliminar symlinks: `~/.nexo/scripts/`, `skills/`, `plugins/`, `hooks/`, `rules/`, `brain/`, `operations/`.
- [ ] **F0.6.3** Regenerar plists y tests con rutas nuevas finales.
- [ ] **F0.6.4** Bump `package.json` → v7.0.0. Release notes: "paths viejos eliminados".
- [ ] **F0.6.5** 1 semana de producción limpia en Francisco + Nora antes de publicar v7.0.0.
- [ ] **F0.6.6** Fresh install en máquina virgen produce estructura F0 directa (no pre-F0 + migrate).

## Clasificación de scripts (resumen §4 del spec)

**Bloque A — Core actual (38):** ya en `src/scripts/` del repo. `check-context`, `nexo-agent-run`, `nexo-auto-update`, `nexo-backup`, ... lista completa en el spec.

**Bloque B — Candidatos a subir a core (22):** refactor pendiente F3+ para hacerlos genéricos. Incluye:
- `nexo-followup-runner` (absorber orquestador v2)
- `nexo-email-monitor` (leer cuenta desde tabla `email_accounts` F1)
- `morning-agent` (quitar "Francisco" hardcoded, mover 12 pasos personales a `personal/config/morning-digest-sources.json`)
- `nexo-orchestrator-wrapper` → **eliminar** (absorbido en follow-runner)
- Otros 18 genéricos.

**Bloque C — Personal 100% (46):** Shopify-*, gbp-*, meta-*, ga4_*, search_console_*, hn-daily-karma, nexo-maria, repo-sync-audit, etc. No suben nunca.

**Bloque D — Core-dev (4, off por defecto):** `nexo-external-audit`, `nexo-release-validate`, `nexo-pre-commit`, `rehydrate_learnings_from_archive`. Preguntar a Francisco en F0.1 por duplicidades con Bloque A.

## Qué NO hacer (del spec §14)

- No unificar dos fases en un release.
- No tocar Bloque C salvo para actualizar paths en F0.3.
- No delegar a subagentes.
- No reescribir morning-agent en F0 (es F3, ver apéndice B del spec).
- No eliminar symlinks antes de F0.6.
- No publicar v7.0.0 sin validar F0.6 en Francisco + Nora.
- No saltarse el pre-flight.
- No ignorar un Verify fallido.
- No avanzar si el operador no dio OK post-fase.

## Riesgos críticos priorizados

- **R2 (crítico):** LaunchAgents plist con paths absolutos hardcoded → rebuild via `ensure-schedules` en cada fase que mueve scripts.
- **R8 (crítico):** DB con paths absolutos → UPDATE transaccional con dry-run.
- **R12 (alto):** usuario con core modificado localmente → hash-diff antes de update, backup a `personal/overrides/`.
- **R10 (alto):** backups viejos reintroducen estructura pre-F0 → política de restore: rechazar o auto-migrar.
- **R4 (alto):** migración interrumpida → idempotente + flag `--resume` en `nexo-migrate.py`.
- **R6 (alto):** sesiones simultáneas (CC + Codex + Desktop) → lock global + notify.

## Interdependencias F0 scripts ↔ Protocol Enforcer

- **R21 (Runtime path legacy)** del Protocol Enforcer asume `entity type=legacy_path` con `old_path → canonical_path`. Si F0.3+ reubica paths, **hay que actualizar el preset `entities_universal.json`** (item 0.4 del Protocol Enforcer) con las nuevas rutas canónicas.
- **R22 (Personal script pre-context)** del Protocol Enforcer se beneficia de `origin='user'` en `personal_scripts`: filtrar por user origin antes de aplicar la regla.
- **Item 0.X.4** (sección `locations` en `nexo_system_catalog`): debe reflejar las rutas nuevas tras F0.3-F0.5. Actualizar generator.
- **Fase E.3** (preset universal entities al `nexo init`): debe generar la estructura F0 directamente en fresh install — coordinar E.3 con F0.6.
- **Guardian hook** (`protocol-pretool-guardrail.sh`): debe respetar `NEXO_MIGRATING=1`. F0.0.4 depende de que esta capacidad exista; si no, se ajusta en F0.0 antes de seguir.

## Artefactos referenciados (del spec §13)

- Artifact NEXO #2: "NEXO F0 — Core vs Personal Scripts Classification WIP".
- Followup 48h `nora-orchestrator`: `NF-PROTOCOL-1776504681-47624`.
- Followup auditar `dashboard/app.py`: `NF-PROTOCOL-1776508552-2584`.

---

# APÉNDICE — Clasificador local zero-shot (item 0.21, el "medio GB")

## Qué es y qué resuelve

Francisco preguntó: "explícame eso de la LLM, me hablaste de casi medio GB". Es el clasificador del item **0.21**.

**Problema actual:**
El Guardian necesita decidir cosas tipo:
- ¿Este mensaje del usuario es una corrección o no?
- ¿Este comando Bash es destructivo en contexto equivocado?
- ¿Este texto que acaba de decir el agente es un "done claim" encubierto?

Hoy se hace con **regex y keywords** (listas hardcoded tipo "no", "te equivocas", "listo", "done", "fixed"...). Problemas:
- **Idioma:** si escribes en catalán o inglés, el regex español no matchea.
- **Semántica:** "no está mal" contiene "no" y dispara falso positivo. "te equivocas de número" dispara pero no era corrección operativa.
- **Ciego al tono y la intención.**

## Solución: MDeBERTa-v3-base-xnli-multilingual

Es un modelo **open-source** de Hugging Face. ~500 MB en disco.

**Características:**
- **Local:** corre en tu Mac sin internet, sin API, sin coste recurrente.
- **CPU-only:** no necesita GPU. Latencia ~200-500 ms por clasificación.
- **Multilingüe:** soporta 100+ idiomas (ES, CA, EN, PT, FR, DE, IT, etc.) sin re-entrenar.
- **Zero-shot:** no requiere entrenamiento previo. Le pasas el texto + una lista de etiquetas candidatas EN TIEMPO REAL, y te dice cuál encaja y con qué confianza.

## Cómo funciona (en 4 pasos)

1. **NEXO escribe un mensaje** ("lo hemos dejado, ya estaría") y quiere saber si es un "done claim".
2. **Clasificador** recibe:
   - Texto: `"lo hemos dejado, ya estaría"`
   - Labels candidatas: `["done_claim", "status_update", "question", "noise"]`
3. **Modelo evalúa** (sin entrenar, en base a su conocimiento lingüístico general) cuál de las labels tiene más afinidad semántica con el texto. Devuelve algo tipo:
   ```
   { "done_claim": 0.87, "status_update": 0.09, "question": 0.02, "noise": 0.02 }
   ```
4. **El Guardian decide:** `done_claim > 0.6` → dispara R16 (declared-done sin task_close) → inyecta recordatorio.

## Dos niveles con escalado opcional

Para NO cargar siempre el modelo grande, se usan dos niveles:

1. **Zero-shot local** (MDeBERTa) — corre siempre, rápido, 0 coste.
2. **LLM completo** (Haiku o gpt-5.4-mini vía tier `muy_bajo`) — solo si confianza del local <0.6.

Esto es lo que el plan llama **"triple refuerzo + fallback"**:
- Intento 1: local zero-shot.
- Intento 2 (si confianza baja): LLM con `max_tokens=3`, stop sequences, prompt estricto "responde SOLO yes o no".
- Intento 3 (si LLM también falla): fallback `no` conservador + logging.

## Feedback loop implícito (se mejora solo)

El plan 0.21 añade un loop sin intervención del usuario:
- Si un learning se borra en <24h → probablemente era ruido (el clasificador se equivocó en positivo).
- Si un learning nunca se dispara en 30 días → probablemente era ruido.
- Si el usuario repite el mismo mensaje 2-3 veces → era corrección real (el clasificador se equivocó en negativo).
- Si un learning sobrevive >30 días útil → corrección legítima.

Todas estas señales se escriben en `~/.nexo/classifier/personal_dataset.jsonl` con el outcome retroactivo. Cuando se acumulan ~100 ejemplos auto-etiquetados, el clasificador pasa automáticamente de **zero-shot puro** a **zero-shot + KNN sobre tu dataset propio** — la precisión mejora sin que tú hagas nada.

## Coste

- **Instalación:** 500 MB descargados 1 vez (Hugging Face CDN).
- **Runtime:** 0 € (local).
- **Latencia:** 200-500 ms por clasificación.
- **Escalado LLM:** solo cuando local tiene baja confianza → ~1 call Haiku/sesión, céntimos.

## Por qué NO simplemente LLM completo en todo

- Coste: tokens × llamadas × usuarios = escala mal.
- Latencia: 1-3s por call, inaceptable en hooks que corren por mensaje.
- Dependencia de internet.

## Por qué NO simplemente regex/keywords

- Ciego a semántica.
- Ciego a idioma (usuario escribe en lo que quiera).
- Cada falso positivo significa ruido al usuario, cada falso negativo significa drift no detectado.

---

# ESTADO RESUMIDO

| Bloque | Hecho | Pendiente |
|---|---|---|
| Fase 0 prerrequisitos | 0.1, 0.2, 0.3, 0.4-0.20, 0.22, 0.25, 0.X.1-6 | 0.21 consumer en auto_capture (followup), 0.23 cron semanal red team (followup) |
| Fase A system prompt | A.1-A.7 (R26-R34 en el prompt) | A.8 smoke 24h omitido por mandato |
| Fase B MCP server | B.1-B.8 (12 reglas + tests + hard) | B.4-B.6 shadow 72h omitido por mandato |
| Fase C wrapper bloque 1 | todo + telemetría | C.9 smoke 7d omitido |
| Fase D wrapper bloque 2 | todo | — |
| Fase D2 reglas añadidas | todo | — |
| Fase E rollout | E.1, E.2, E.3, E.4, E.5 parcial, E.8 | E.5 UI reactivación, E.6 README dedicado, E.7 video (opcional) |
| Fase F telemetría | F.1-F.3, F.5, F.6, F.7, F.8 (pin docs) | F.4 ajuste automático (datos reales), F.8 reminder mensual (followup) |
| T0 micro-patch Desktop | todo (v0.17.0) | — |
| T1-T3 deuda Desktop | todo (v0.17.0) | — |
| T4 classifier en R15/R23* | todo (v6.3.0 + v0.18.0) | T4.5 fixtures parity con mock LLM (followup) |
| T5 identity continuity | todo (v6.2.0 + v0.17.0) | — |
| Bug modal bullets | todo (v0.17.0) | — |
| **F0 scripts classification** | F0.0.1/3/6, F0.0.4, F0.1 | F0.0.7 Nora sync, F0.1 CLI --origin, F0.2 Desktop panel, F0.3-F0.6 movimiento físico + breaking v7.0.0 **(DEFERRED — requiere validación coordinada en runtime de Francisco + Nora, Learning #450)** |

### Releases publicados
- NEXO Brain v6.2.0 + NEXO Desktop v0.17.0 (wave 1, ya en producción).
- NEXO Brain v6.3.0 + NEXO Desktop v0.18.0 (wave 2, PRs #217 + #5 preparados para merge tras 2ª auditoría OK).

**Cosas que debe confirmar Francisco antes de ejecutar en serie:**

1. **Versionado v0.16.2** — dos propuestas incompatibles:
   - Opción A: v0.16.2 = solo T0 (micro-patch, ~30 min). T1+T2+T3+Bug modal → v0.16.3.
   - Opción B: v0.16.2 = T0 + T1 + T2 + T3 + Bug modal.
2. **Numeración regla T5** — T5 pide "R26 Identity Coherence" pero R26 ya es Jargon Filter (Fase A). Renumerar a **R34**.
3. **E.5 Guardian Proposals panel** — queda oculto o se reactiva en UI.
4. **Modelo local exacto para 0.21** — MDeBERTa-v3-base-xnli-multilingual es propuesta; alternativas: `bge-multilingual-gemma2` (más preciso, más pesado), `xlm-roberta-base` (más ligero).
5. **F0 scripts classification — orden vs Protocol Enforcer** — ejecutar en paralelo, o secuencial (F0 primero y luego Protocol Enforcer aprovecha la nueva estructura). F0 es pre-requisito ideal para que R21/R22 funcionen sobre paths finales, pero cada micro-fase de F0 es release propio y toma semanas.
6. **Nora coordinación F0** — F0.0 a F0.6 aplican en ambas instancias (Francisco + Nora). Cuando se ejecute, avisar al operador antes de cada fase.

---

**Fin del documento.** Para ejecutar desde terminal fresca: `NEXO, abre este plan en /Users/franciscoc/Desktop/NEXO-PLAN-CONSOLIDADO.md y ejecuta por bloques sin parar. Avisa si algo requiere decisión de producto.`
