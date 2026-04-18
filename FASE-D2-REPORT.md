# Protocol Enforcer Fase 2 — FASE D2 Report (Wrapper Bloque 3)

**Sesión:** 2026-04-18
**Branch Python:** `fase2-impl` en worktree `~/work/nexo-fase2/` (repo `wazionapps/nexo` @ main v6.0.6 `da754f1` + hotfix `4f37ab1`).
**Branch JS:** `fase2-impl-desktop` en worktree `~/work/nexo-desktop-fase2/` (repo `nexo-desktop` @ main rebased sobre v0.14.3 `c6989cd`).
**Estado:** Fase D2 cerrada en ambos engines. 12 reglas incident-driven (R23b–R23m) implementadas y testeadas. Parity byte-for-byte con fix retroactivo compartido (R23f WHERE regex).

## Reglas entregadas

### Tranche 1 — Hard bloqueantes (4)

| Regla | Función | Python | JS | Tests Py | Tests JS |
|-------|---------|--------|----|----|----|
| **R23b** deploy_vhost | scp/rsync docroot vs user-cited domain cross-check | `_check_r23b` + `r23b_deploy_vhost.py` | `_checkR23b` + `lib/r23b-deploy-vhost.js` | 3 integ | 1 integ |
| **R23e** force_push_main | git push --force/-f on main/master/production/release-* | `_check_r23e` + `r23e_force_push_main.py` | `_checkR23e` + `lib/r23e-force-push-main.js` | 6 integ | 3 unit + 2 integ |
| **R23f** db_no_where | DELETE/UPDATE sin WHERE contra DB client | `_check_r23f` + `r23f_db_no_where.py` | `_checkR23f` + `lib/r23f-db-no-where.js` | 5 integ | 2 unit + 1 integ |
| **R23l** resource_collision | create de recurso cloud con nombre ya registrado | `_check_r23l` + `r23l_resource_collision.py` | `_checkR23l` + `lib/r23l-resource-collision.js` | 3 integ | 1 integ |

### Tranche 2 — Soft (6)

| Regla | Función | Python | JS | Tests Py | Tests JS |
|-------|---------|--------|----|----|----|
| **R23c** cwd_mismatch | destructive bash cwd != project.local_path | `_check_r23c` + `r23c_cwd_mismatch.py` | `_checkR23c` + `lib/r23c-cwd-mismatch.js` | 3 integ | 1 integ |
| **R23d** chown_chmod_recursive | -R chown/chmod root-ish sin ls previo | `_check_r23d` + `r23d_chown_chmod_recursive.py` | `_checkR23d` + `lib/r23d-chown-chmod-recursive.js` | 3 integ | 2 unit + 1 integ |
| **R23g** secrets_in_output | env dump, echo secret, cat key files, bearer tokens | `_check_r23g` + `r23g_secrets_in_output.py` | `_checkR23g` + `lib/r23g-secrets-in-output.js` | 4 integ | 3 unit + 1 integ |
| **R23i** auto_deploy_ignored | Edit/Write en auto_deploy repo tras push reciente | `_check_r23i` + `r23i_auto_deploy_ignored.py` | `_checkR23i` + `lib/r23i-auto-deploy-ignored.js` | 2 integ | 1 integ |
| **R23k** script_duplicates_skill | nexo_personal_script_create con skill_match ≥ 0.75 | `_check_r23k` + `r23k_script_duplicates_skill.py` | `_checkR23k` + `lib/r23k-script-duplicates-skill.js` | 0 (silent probe) | 0 (silent probe) |
| **R23m** message_duplicate | jaccard 90% vs 15min mismo hilo | `_check_r23m` + `r23m_message_duplicate.py` | `_checkR23m` + `lib/r23m-message-duplicate.js` | 3 integ | 1 unit + 1 integ |

### Tranche 3 — Shadow (2)

| Regla | Función | Python | JS | Tests Py | Tests JS |
|-------|---------|--------|----|----|----|
| **R23h** shebang_mismatch | `#!` vs `which` resolve con misma familia interp | `_check_r23h` + `r23h_shebang_mismatch.py` | `_checkR23h` + `lib/r23h-shebang-mismatch.js` | 3 integ | 0 (shadow-only, cubre Python) |
| **R23j** global_install | npm -g / pip --user / brew install sin permit | `_check_r23j` + `r23j_global_install.py` | `_checkR23j` + `lib/r23j-global-install.js` | 6 integ | 3 integ |

## Entidades nuevas

**D2.0 — vhost_mapping:** 8 entidades seed (`entities_universal.json`):

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

- Mismas `CLASSIFIER_QUESTION` / `INJECTION_PROMPT_TEMPLATE` texto byte-for-byte donde aplica.
- Mismos thresholds: R23m 90% jaccard + 15min, R23k 0.75 skill similarity, R23d whitelist root-ish (`/`, `/home`, `/var`, `/etc`, `/opt`, `/usr`, `/srv`).
- Mismo permit markers R23j: `install globally`, `si instala global`, `yes install globally`, `global install ok`, etc.
- R23i state shape idéntico: `recentPush` flag + Edit/Write evaluation clears it once.
- R23m ring buffer máx 16 entradas, mismo schema {thread, body, ts}.
- Core defence-in-depth sin cambios: R13/R14/R16/R25/R30 siguen bloqueados a no-`off`.
- Fail-closed paths: classifier caído / preset unreadable / entidad ausente → no inyección.
- **Fix compartido retroactivo:** R23f regex en ambos lados chequeaba WHERE solo en `tail` (grupo post-SET); para `UPDATE x SET y WHERE z` el grupo SET greedy consumía WHERE y producía falso positivo. Fix: chequear `WHERE_RE.search(match.group(0))` — full match, no tail.

## Estado de enforcement modes (defaults packaged)

`guardian_default.json` v1.3.3:

| Regla | Default mode |
|-------|--------------|
| R23b, R23e, R23f, R23l | `hard` |
| R23c, R23d, R23g, R23i, R23k, R23m | `soft` |
| R23h, R23j | `shadow` |

Operadores ajustan vía `~/.nexo/config/guardian.json`. Para R21 y R24 (Fase D) hard mode sigue requiriendo override; para D2 core-bloqueantes no se bloquea explícitamente en el validator (no son R13/R14/R16/R25/R30 — son incident-driven). Considerar endurecer en Fase F si telemetría lo pide.

**Cleanup:** removidas 9 claves placeholder del plan original que mis implementaciones superseden (`R23b_deploy_path_mismatch`, `R23c_cwd_destructive`, `R23d_chown_recursive`, `R23e_git_push_force_main`, `R23f_db_prod_no_where`, `R23g_secrets_in_logs`, `R23h_interpreter_mismatch`, `R23l_resource_name_collision`, `R23m_duplicate_email`). Final R23 rule count en preset: 13.

## Tests

**Python isolated suite post-Fase D2:**
```
NEXO_HOME=/tmp/nexo-test-fase2 python3 -m pytest tests/test_fase_d2_*.py -q
41 passed
```

**Python combined Fase C+D+D2:**
```
NEXO_HOME=/tmp/nexo-test-fase2 python3 -m pytest tests/test_fase_c_*.py tests/test_fase_d_*.py tests/test_fase_d2_*.py -q
127 passed
```

**JS (desde worktree Desktop):**
```
node --test tests/fase-d-enforcement.test.js tests/fase-d2-enforcement.test.js tests/r13-enforcement.test.js tests/r14-r16-r25-enforcement.test.js
92 pass, 0 fail
```

## Protocol Enforcer: scope fase por fase

| Fase | Scope | Estado |
|------|-------|--------|
| Fase 0 | Preflight, snapshots, dry-run infra, MCP bridge | cerrada |
| Fase A | Capa 1 server-side (R01–R12 tools.*) | cerrada |
| Fase B | `tool-enforcement-map.json` v2.1.0 + bridge delivery | cerrada |
| Fase C | Wrapper Bloque 1 core: R13 + R14 + R16 + R25 | cerrada |
| Fase D | Wrapper Bloque 2: R15 + R17–R24 | cerrada |
| **Fase D2** | **Wrapper Bloque 3: R23b–R23m (12 reglas incident-driven)** | **cerrada** |
| Fase E | Installer + preset distribuido + Desktop UI quarantine | pendiente |
| Fase F | Telemetría + red-team + loops E2E | pendiente |

## Commits de esta fase

**Python (`fase2-impl`):**
- `dbb6bb9` — Fase D2 tranche 1 hard (R23b/R23e/R23f/R23l)
- `32eef51` — Fase D2 tranche 2 soft (R23c/R23d/R23g/R23i/R23k/R23m)
- `ba0887a` — Fase D2 tranche 3 shadow (R23h/R23j) + cleanup duplicados
- `e83c5f5` — parity fix R23f WHERE regex

**JS (`fase2-impl-desktop`):**
- `31aa202` — Fase D2 JS twins (12 modulos + engine + tests)

## Próximos pasos

1. **Fase E** — `scripts/install_guardian.py`, Desktop UI para cuarentena de reminders (reusa componentes TodoWrite + approval modal ya existentes), preset distribuido empaquetado.
2. **Fase F** — Telemetría (`guardian_telemetry.jsonl`), red-team corpus, loops E2E Claude Code ↔ Desktop.
3. **Auditoría pre-release (gate obligatorio)** — Francisco ordenó: antes de cualquier tag se hace auditoría 100% capa-por-capa de todos los commits usando las mejores skills, y cualquier hallazgo se arregla en el mismo flujo (no se posterga).
4. **Release consolidado** — tag único `v6.1.0` core + `v0.15.0` Desktop cuando Fase F esté cerrada y la auditoría esté pasada.

## Pending técnicos (heredados + nuevos)

- `nexo-desktop/package.json`: añadir `@anthropic-ai/sdk` + `openai` como deps runtime antes del tag.
- Desktop: wire `hasOpenTask` probe al `onUserMessage` desde el main process.
- CI parity test JS↔Python: item 0.23 pendiente — ahora cubre también los D2 templates.
- R23d Python: `_is_root_ish` acepta `/opt/app` y `/usr/...`, confirmar con Francisco si hay falso positivo para paths que estén dentro de un workspace real. Subpath matching ya mitiga (requiere ls previo). Telemetría Fase F lo dirá.
- R23i: persistir `recentPush` cross-session no está soportado. Si el operador hace push y luego cierra la terminal y abre otra, el flag no llega. Documentado como limitación.
- R23k: silent skill_match sólo funciona cuando el caller alimenta `__skillMatches` en `toolInput` (Desktop) o el plugin `skill_registry` está disponible (Python). Si ninguno, fail-closed.

## Archivos clave para handoff

**Python:**
- `src/enforcement_engine.py:1-1693` (engine ampliado +693 vs post-Fase D)
- `src/r23b_deploy_vhost.py`, `src/r23c_cwd_mismatch.py`, `src/r23d_chown_chmod_recursive.py`, `src/r23e_force_push_main.py`, `src/r23f_db_no_where.py`, `src/r23g_secrets_in_output.py`, `src/r23h_shebang_mismatch.py`, `src/r23i_auto_deploy_ignored.py`, `src/r23j_global_install.py`, `src/r23k_script_duplicates_skill.py`, `src/r23l_resource_collision.py`, `src/r23m_message_duplicate.py`
- `src/presets/entities_universal.json` (+8 vhost_mapping)
- `src/presets/guardian_default.json` v1.3.3

**JS:**
- `enforcement-engine.js:1-1090` (ampliado +256 vs post-Fase D)
- `lib/r23b-deploy-vhost.js` … `lib/r23m-message-duplicate.js` (12 modulos)
- `tests/fase-d2-enforcement.test.js`

**Reports:** `FASE0-REPORT.md`, `FASE-C-REPORT.md`, `FASE-D-REPORT.md`, `FASE-D2-REPORT.md` (este).
