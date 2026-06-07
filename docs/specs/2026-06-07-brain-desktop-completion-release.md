# Brain + Desktop Completion Release Spec

Fecha: 2026-06-07
Owner operativo: Nero
Alcance: NEXO Brain + NEXO Desktop
Release objetivo: beta y stable/normal, unsigned temporal permitido

## 1. Objetivo

Terminar los tres frentes abiertos y publicar una release coordinada de Brain
y Desktop en beta y stable/normal, sin firma/notarizacion, pero con evidencia
funcional suficiente para considerarla terminada:

1. Active Turn Input Router en Desktop.
2. Operational Closure Plane en Brain.
3. Managed Default MCPs en Brain + Desktop.

Firma, notarizacion Apple y Authenticode quedan fuera de este spec. La release
puede salir unsigned solo si todos los artefactos y manifests quedan marcados
con `signature_policy=unsigned-temporary` y la excepcion es explicita.

## 2. Regla principal

No publicar beta ni stable si queda algun P0/P1 conocido sin resolver.

No declarar terminado por tests mock si el criterio del spec exige prueba real
instalada. Los tests unitarios validan contrato; el release gate exige tambien
smoke instalado en macOS y Windows.

## 3. Estado de partida verificado

### 3.1 Active Turn Input Router

Ya existe:

- `nexo-desktop/lib/active-turn-input-router.js`.
- Wiring por `preload.js`, `main.js`, renderer y pending queue.
- Entrega mid-turn para Claude mediante proceso writable y JSONL a stdin.
- Tests verdes:
  - `node --test tests/active-turn-input-router.test.js tests/pending-queue-runtime.test.js`

Falta:

- Codex app-server real.
- `turn/steer` con `expectedTurnId`.
- `threadId` y `turnId` persistidos por conversacion.
- Manejo de `activeTurnNotSteerable`.
- Dedupe completo de replay/ack.
- Smoke real Codex mid-turn.

### 3.2 Operational Closure Plane

Ya existe:

- Migracion `closure_items`, `closure_item_sources`, `closure_item_events`,
  `closure_daily_snapshots`.
- Backfill read-only desde `protocol_tasks`, `followups`, `protocol_debt`,
  `outcomes`, `mcp_write_queue`.
- Ranking basico.
- Tools:
  - `nexo_closure_status`
  - `nexo_closure_next`
  - `nexo_closure_item_get`
  - `nexo_closure_verify`
  - `nexo_closure_close`
- Tests verdes:
  - `python3 -m pytest -q tests/test_closure_plane.py tests/test_migrations.py`

Falta:

- `nexo_closure_triage`.
- `nexo_closure_link`.
- `nexo_closure_snapshot`.
- Tabla real `closure_item_links`.
- Tabla real `closure_capability_readiness`.
- Integracion runner/Deep Sleep/outcomes/watchdog.
- Panel Desktop o superficie equivalente para operar Top 10.

### 3.3 Managed Default MCPs

Ya existe:

- `src/managed_mcp/catalog.json`.
- `src/managed_mcp/lock.json`.
- `bin/nexo-managed-mcp.js`.
- Merge en `src/client_sync.py` para Claude Code, Claude Desktop y Codex.
- `nexo_managed_mcp_status`.
- Release gate `scripts/verify_managed_mcp_lock.py`.
- Tests verdes:
  - `python3 -m pytest -q tests/test_managed_mcp.py tests/test_managed_mcp_release_gate.py`

Falta:

- Staging/instalacion real de providers en `runtime/managed-mcp/artifacts`.
- Healthchecks por provider.
- Integracion install/update real, no solo client sync.
- Estado de permisos en Desktop.
- Kill switch para Desktop session.
- Smoke real macOS + Windows de Chrome, desktop y power control.

## 4. Definicion de terminado

La release esta terminada cuando se cumplen todos estos puntos:

- Brain y Desktop tienen version nueva, changelog y contratos de release.
- No hay P0/P1 abiertos en los tres frentes.
- Todos los tests focales pasan.
- El smoke agregado Brain pasa.
- Desktop `npm run check` pasa.
- Build unsigned macOS y Windows pasa con verificacion `--allow-unsigned`.
- App instalada funciona en macOS y Windows.
- Beta publicada y verificada por manifests publicos.
- Stable/normal publicada solo despues de soak beta instalado.
- El release queda documentado como unsigned temporal.

## 5. Fase A - Preparacion de rama y baseline

### Tareas

1. Verificar worktrees:
   - `git -C /Users/franciscoc/Documents/_PhpstormProjects/nexo status --short`
   - `git -C /Users/franciscoc/Documents/_PhpstormProjects/nexo-desktop status --short`
2. Crear rama dedicada en cada repo:
   - Brain: `release/brain-desktop-completion-YYYYMMDD`
   - Desktop: `release/brain-desktop-completion-YYYYMMDD`
3. Registrar versiones actuales:
   - Brain `package.json`
   - Desktop `package.json`
   - Ultimo tag Brain
   - Ultimo tag Desktop
4. Ejecutar baseline:
   - Brain:
     - `python3 scripts/run_v7_30_21_smoke.py` o smoke equivalente de la nueva version.
     - `bash scripts/pre-release-verify.sh`
   - Desktop:
     - `npm run check`
     - `npm run verify:release:matrix`

### Gate

No empezar implementacion si baseline no permite distinguir regresion nueva de
deuda previa. Si hay deuda previa, documentarla con ID y decidir si bloquea.

## 6. Fase B - Completar Active Turn Input Router

### B1. Codex app-server client

Crear en Desktop una capa dedicada:

- `lib/codex-app-server-client.js`
- tests en `tests/codex-app-server-client.test.js`

Contrato minimo:

- Spawn: `codex app-server --listen stdio://`.
- JSON-RPC initialize.
- `thread/start` para conversacion nueva.
- `thread/resume` para conversacion existente si aplica.
- `turn/start`.
- `turn/steer` con `expectedTurnId`.
- `turn/interrupt` si el protocolo actual lo soporta.
- Timeout controlado.
- Fallback limpio a `codex exec` si app-server no inicializa.

### B2. Estado por conversacion

Persistir en la conversacion Desktop:

- `codexThreadId`.
- `codexActiveTurnId`.
- `activeTurnTransport`.
- `activeTurnSteerable`.
- `lastActiveTurnDeliveryId`.

No usar estos campos para Claude salvo que sea necesario para UI comun.

### B3. Adaptador Codex

Actualizar `renderer/app/04-input-events.js`:

- Dejar `supportsActiveTurnInput=false` solo si app-server no esta disponible.
- Si `NEXO_CODEX_APP_SERVER=1` y app-server esta sano, activar adaptador.
- Entregar texto mid-turn con `turn/steer`.
- Si Codex responde `activeTurnNotSteerable`, marcar `blocked_by_provider` o
  volver a `queued` sin perder el mensaje.

### B4. Dedupe y replay

Implementar dedupe por:

- `deliveryId`.
- `clientUserMessageId`.
- hash del texto normalizado.
- ventana temporal del turno activo.

No debe haber doble burbuja si el proveedor reemite el mensaje del usuario.

### B5. Tests

Obligatorios:

- `node --test tests/active-turn-input-router.test.js`
- `node --test tests/pending-queue-runtime.test.js`
- `node --test tests/codex-app-server-client.test.js`
- Fake app-server:
  - acepta `turn/steer`;
  - rechaza con `activeTurnNotSteerable`;
  - muere entre `canDeliver` y `deliver`;
  - timeout;
  - reemite user message.

### B6. Smokes reales

macOS instalado:

1. Abrir Desktop.
2. Usar Claude.
3. Lanzar tarea larga controlada.
4. Enviar follow-up mientras el turno esta activo.
5. Verificar que llega antes del final, sin duplicado.
6. Repetir con Codex app-server.

Windows instalado:

1. Mismo flujo Claude si esta disponible.
2. Mismo flujo Codex app-server.
3. Verificar Stop: lo no entregado queda pausado y no se envia tarde.

### Gate B

No pasar a release si Codex sigue `provider_unsupported` para input activo
cuando `NEXO_CODEX_APP_SERVER=1`.

## 7. Fase C - Completar Operational Closure Plane

### C1. Migracion de tablas restantes

Anadir migracion idempotente:

- `closure_item_links`
- `closure_capability_readiness`

Requisitos:

- Foreign keys a `closure_items`.
- Indices por source, capability y state.
- Migracion idempotente en `tests/test_migrations.py`.

### C2. Tools restantes

Exponer en `src/server.py`:

- `nexo_closure_triage(item_id, decision, note='', followup_date='')`
- `nexo_closure_link(item_id, target_type, target_id, relation='related')`
- `nexo_closure_snapshot()`

Actualizar:

- `tool-enforcement-map.json`
- tests de tool map
- docs/changelog si procede

### C3. Integraciones

Integrar sin acciones destructivas automaticas:

- followup-runner: lee `closure_next` y puede marcar triage seguro.
- Deep Sleep: incluye Top 10 closure y items verificados/cerrados.
- outcomes: si outcome falla, crea/actualiza closure item.
- watchdog/immune: FAIL persistente crea/actualiza closure item.
- mcp write queue: dead letters agrupadas por tipo/error.

### C4. Desktop surface

Crear superficie minima en Desktop:

- Panel o seccion "Cierres pendientes".
- Top 10 con estado, fuente, siguiente accion y evidencia requerida.
- Botones seguros:
  - verificar con evidencia;
  - cerrar si ya esta verificado;
  - marcar waiting external;
  - abrir fuente.

No mostrar IDs internos como texto principal; dejarlos como detalle tecnico.

### C5. Tests

Obligatorios:

- `python3 -m pytest -q tests/test_closure_plane.py tests/test_migrations.py`
- Nuevos tests:
  - triage cambia estado y registra evento;
  - link no duplica;
  - snapshot devuelve contadores/top items;
  - close sigue rechazando sin evidencia;
  - adapters no ejecutan acciones sensibles.

### Gate C

No pasar a release si las tools del spec (`triage`, `link`, `snapshot`) no
estan expuestas y cubiertas por tests.

## 8. Fase D - Completar Managed Default MCPs

### D1. Staging real de providers

Crear instalador/reconciler real:

- Descarga paquetes exactos desde `src/managed_mcp/lock.json`.
- Verifica integrity.
- Extrae en:
  - `~/.nexo/runtime/managed-mcp/artifacts/<provider>/`
- Crea bin wrappers.
- Es atomico: descarga a temp, luego rename.
- Puede reparar artifact corrupto.

### D2. Healthchecks

Implementar healthcheck por capability:

- `chrome_control`:
  - arranca provider;
  - lista tools;
  - abre/adjunta Chrome en modo seguro;
  - pagina simple o attach read-only.
- `desktop_control`:
  - verifica permisos OS;
  - screenshot/list windows harmless;
  - no mueve mouse ni escribe sin tarea activa.
- `power_control`:
  - comando seguro en temp dir;
  - prueba politica de path bloqueado;
  - no permite comandos destructivos sin confirmacion.

Resultado visible en `nexo_managed_mcp_status`.

### D3. Install/update wiring

Integrar reconciliacion en:

- `bin/nexo-brain.js` install/configure/update.
- `src/plugins/update.py` o flujo equivalente de update Brain.
- Desktop update flow: despues de instalar Brain, ejecutar status/reconcile y
  mostrar permisos/restart si hace falta.

### D4. Kill switch

Agregar kill switch:

- Env/config: `NEXO_MANAGED_MCP_DISABLE=1`.
- Desktop: control interno para desactivar MCPs gestionados en esta sesion.
- Debe preservar MCPs manuales del usuario.

### D5. Politica de seguridad

Ningun managed MCP de alto riesgo puede ejecutar acciones sensibles sin:

- tarea activa;
- contexto de usuario;
- path/host permitido;
- evidencia de permiso;
- confirmacion si es destructivo o irreversible.

### D6. Tests

Obligatorios:

- `python3 -m pytest -q tests/test_managed_mcp.py tests/test_managed_mcp_release_gate.py`
- Nuevos tests:
  - staging atomico;
  - integrity mismatch falla;
  - repair artifact corrupto;
  - healthcheck devuelve unhealthy sin romper startup;
  - kill switch elimina solo managed entries;
  - manual MCP sobrevive.

### D7. Smokes reales

macOS:

- `chrome_control`: abrir/listar pagina segura.
- `desktop_control`: screenshot/list window con permisos.
- `power_control`: temp dir command + bloqueo path sensible.

Windows:

- `chrome_control`: abrir/listar pagina segura.
- `desktop_control`: native-devtools u open-computer-use fallback.
- `power_control`: temp dir command + bloqueo path sensible.

### Gate D

No pasar a release si los managed MCPs solo existen en config pero no tienen
staging/health real y kill switch.

## 9. Fase E - Desktop UX minima

### E1. Input activo

La UI no debe mostrar `turn/steer`, JSON-RPC, stdin ni nombres internos.

Estados visibles:

- "Enviado al turno actual".
- "En cola".
- "Pausado por Stop".
- "Necesita respuesta/permiso".
- "Se enviara cuando termine este paso".

### E2. Managed MCPs

Mostrar solo estado operativo:

- Navegador disponible/no disponible.
- Control del ordenador disponible/no disponible.
- Acciones de sistema disponibles/no disponibles.
- Permisos que faltan.
- Boton de diagnostico.
- Boton de desactivar en esta sesion.

### E3. Closure Plane

Mostrar Top 10 operativo sin jerga:

- asunto;
- por que importa;
- que falta;
- accion recomendada;
- estado.

### E4. Tests UI

Obligatorios:

- `npm run build:react`
- tests renderer existentes
- nuevo test de texto: no aparecen `turn/steer`, `stdin`, `JSON-RPC`,
  `closure_item`, `managed_mcp` en UI normal.

## 10. Fase F - Versionado y changelog

### Brain

1. Bump patch version.
2. Actualizar:
   - `CHANGELOG.md`
   - `README.md` si cambia superficie publica.
   - `llms.txt` si cambia version publica.
   - `release-contracts/vX.Y.Z.json`
3. Crear smoke de release si cambia version:
   - `scripts/run_vX_Y_Z_smoke.py`
   - `release-contracts/smoke/vX.Y.Z.json`

### Desktop

1. Bump patch version.
2. Actualizar:
   - `CHANGELOG.md`
   - manifests/release contracts si aplica.
   - bundle Brain version.
3. Asegurar que el bundle Desktop incluye Brain nuevo.

### Gate F

No publicar Desktop si `BRAIN_VERSION` del bundle no coincide con Brain
publicado y verificado.

## 11. Fase G - Verification matrix

### Brain required commands

Ejecutar desde `/Users/franciscoc/Documents/_PhpstormProjects/nexo`:

```bash
python3 -m pytest -q tests/test_closure_plane.py tests/test_migrations.py
python3 -m pytest -q tests/test_managed_mcp.py tests/test_managed_mcp_release_gate.py
python3 scripts/verify_managed_mcp_lock.py
python3 scripts/verify_tool_map.py
bash scripts/pre-release-verify.sh
python3 scripts/run_vX_Y_Z_smoke.py
```

### Desktop required commands

Ejecutar desde `/Users/franciscoc/Documents/_PhpstormProjects/nexo-desktop`:

```bash
npm run check
npm run check:windows
node --test tests/active-turn-input-router.test.js tests/pending-queue-runtime.test.js
npm run verify:brain-bundle-source
npm run verify:release:matrix
```

### Desktop unsigned build commands

Unsigned public build debe ser explicito:

```bash
NEXO_ALLOW_UNSIGNED_PUBLIC_RELEASE=1 ALLOW_UNSIGNED=1 \
  node scripts/build-release.js --platform all --allow-unsigned true
```

Verificacion local unsigned:

```bash
node scripts/verify-release-artifacts.js --dist dist/release --allow-unsigned true
node scripts/verify-release-artifacts.js --platform win --dist dist/release-win/desktop --allow-unsigned true
```

### Installed-app smokes

macOS:

- instalar DMG generado;
- abrir app;
- login/runtime sano;
- primer chat;
- active input Claude;
- active input Codex;
- panel closure;
- managed MCP status;
- update check beta.

Windows:

- instalar EXE/NSIS generado;
- abrir app;
- login/runtime sano;
- primer chat;
- active input Codex;
- managed MCP status;
- desktop/power/chrome MCP smoke;
- update check beta.

## 12. Fase H - Publicacion beta

### Preconditions

- Todas las fases A-G verdes.
- Artefactos unsigned verificados.
- Evidence file de update smoke beta creado y validado por:
  - `CHANNEL=beta npm run verify:update-smoke-evidence`
- Release notes indican `unsigned-temporary`.

### Comandos

```bash
NEXO_ALLOW_UNSIGNED_PUBLIC_RELEASE=1 ALLOW_UNSIGNED=1 CHANNEL=beta \
  bash scripts/upload-release.sh
```

Despues:

```bash
npm run verify:release:matrix:public
curl -fsSL https://nexo-desktop.com/downloads/update-beta-mac.json
curl -fsSL https://nexo-desktop.com/downloads/update-beta-win.json
```

### Beta soak

Minimo:

- instalar desde canal beta en macOS;
- instalar desde canal beta en Windows;
- ejecutar smokes instalados de la fase G;
- comprobar logs sin P0/P1;
- comprobar que rollback a version previa sigue posible.

## 13. Fase I - Promocion stable/normal

Stable solo se publica si beta soak pasa.

### Preconditions

- Evidence file stable creado y validado por:
  - `npm run verify:stable-promotion-evidence`
- Manifests beta publicos verificados.
- Instalacion beta real en macOS y Windows sin P0/P1.
- El operador acepta que esta release es unsigned temporal.

### Comandos

```bash
NEXO_ALLOW_UNSIGNED_PUBLIC_RELEASE=1 ALLOW_UNSIGNED=1 CHANNEL=stable \
  bash scripts/upload-release.sh

npm run release:tag
```

O si `release:promote:stable` acepta correctamente las variables:

```bash
NEXO_ALLOW_UNSIGNED_PUBLIC_RELEASE=1 ALLOW_UNSIGNED=1 \
  npm run release:promote:stable
```

### Verificacion publica stable

```bash
npm run verify:release:matrix:public
curl -fsSL https://nexo-desktop.com/downloads/update-mac.json
curl -fsSL https://nexo-desktop.com/downloads/update-win.json
curl -fsSL https://nexo-desktop.com/downloads/latest.yml
```

Tambien verificar:

- GitHub Release Desktop existe con artefactos.
- Tags Brain y Desktop apuntan al commit correcto.
- NPM Brain publicado si hubo version Brain nueva.
- `npm view <brain-package>@<version>` devuelve version esperada.

## 14. Criterios de bloqueo

Bloquea beta:

- Codex app-server no entrega mid-turn en smoke real.
- Managed MCPs no tienen health real.
- Closure tools restantes no estan expuestas.
- Desktop `npm run check` falla.
- Brain pre-release falla.
- Build unsigned no se verifica con `--allow-unsigned`.
- App instalada no abre en macOS o Windows.

Bloquea stable/normal:

- Beta no instalada en macOS y Windows.
- Cualquier P0/P1 despues de beta.
- Update check publico falla.
- Manifests publicos no apuntan a los artefactos nuevos.
- Firma unsigned no esta marcada como temporal.

No bloquea:

- Falta de firma/notarizacion, siempre que este marcada como unsigned temporal.
- Mejoras cosmeticas no P0/P1.
- Documentacion externa comercial no necesaria para operar la release.

## 15. Evidencia final obligatoria

Antes de declarar release cerrada, adjuntar:

- Commit Brain.
- Tag Brain.
- `npm view` Brain.
- Commit Desktop.
- Tag Desktop.
- URLs publicas beta y stable.
- Checksums/size de DMG y EXE.
- Output resumido de tests Brain.
- Output resumido de tests Desktop.
- Resultado smoke instalado macOS.
- Resultado smoke instalado Windows.
- Confirmacion `signature_policy=unsigned-temporary`.
- Captura/log de update check beta.
- Captura/log de update check stable.

## 16. Rollback

Rollback beta:

1. Restaurar manifests beta anteriores.
2. Mantener artefactos nuevos en bucket pero no referenciados.
3. Registrar incidente con causa y version afectada.

Rollback stable:

1. Restaurar manifests stable anteriores.
2. Verificar update check vuelve a version previa.
3. No borrar artefactos nuevos hasta terminar postmortem.
4. Crear hotfix solo si el problema impide funcionamiento normal.

## 17. Orden de ejecucion recomendado

1. Implementar Active Turn Input Router Codex.
2. Completar Closure Plane tools/tablas.
3. Completar Managed MCP staging/health/kill switch.
4. Integrar superficies UI minimas Desktop.
5. Ejecutar tests focales por frente.
6. Ejecutar suites completas Brain + Desktop.
7. Bump Brain y publicar/verificar Brain si cambio runtime.
8. Bundle Brain en Desktop.
9. Build unsigned macOS + Windows.
10. Publicar beta.
11. Soak beta instalado.
12. Promocionar stable/normal.

## 18. Resultado esperado

Al terminar este spec, el usuario puede instalar NEXO Desktop unsigned desde
beta o stable/normal y comprobar que:

- puede escribir durante un turno activo en Claude y Codex sin perder mensajes;
- NEXO muestra y cierra trabajo pendiente con evidencia;
- los MCPs gestionados existen como capacidades operativas reales, no solo
  entradas de configuracion;
- la app actualiza desde el canal publico correspondiente;
- cualquier limitacion restante esta documentada y no afecta P0/P1.
