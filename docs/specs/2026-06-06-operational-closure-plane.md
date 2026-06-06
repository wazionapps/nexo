# NEXO Operational Closure Plane - Spec de implementacion

Fecha: 2026-06-06
Estado: borrador completo para implementacion posterior
Scope inicial: runtime personal de NEXO Brain
Scope posterior: Desktop/UI, producto publico y automatizaciones delegadas

## 1. Resumen ejecutivo

NEXO ya tiene muchas piezas potentes: followups, workflows, goals, outcomes,
Deep Sleep, Watchdog, Immune, lifecycle, cola MCP, memoria, aprendizajes y
catalogo de sistema. La brecha real no es "crear otro runner"; la brecha es
cerrar el circuito completo entre deteccion, priorizacion, accion, evidencia y
cierre verificable.

Esta especificacion propone una capa nueva: Operational Closure Plane, en
espanol "Capa de Cierre Operativo". Su funcion es convertir senales abiertas
del sistema en trabajo priorizado, con siguiente accion clara, responsable,
condicion de bloqueo, prueba exigida y cierre verificable.

Objetivo cuantificado: mejorar NEXO al menos un 20% en capacidad operativa
medida por reduccion de backlog abierto, menor tiempo hasta cierre, menor deuda
sin clasificar y mayor porcentaje de acciones cerradas con evidencia.

## 2. Decision de diseno

No se debe sustituir lo que ya existe. La capa nueva debe sentarse encima de
las piezas actuales y actuar como plano de convergencia.

Regla central:

> Todo asunto abierto importante debe tener exactamente una ficha canonica de
> cierre, aunque venga de followups, protocol_tasks, outcomes, workflows,
> cron, lifecycle, MCP write queue, Deep Sleep o soporte.

La ficha canonica no ejecuta todo por si sola. Primero normaliza, prioriza y
decide el siguiente paso seguro. Solo delega ejecucion cuando hay capacidad,
permiso y criterio de cierre.

## 3. Inventario de lo que ya existe

### 3.1 Followups

Ya existe:

- Runner de followups.
- Limite operativo por ciclo.
- Capacidad de lanzar agentes.
- Cierre, reprogramacion y recordatorios de bloqueo.

Mantener:

- Semantica de followup como promesa o accion pendiente.
- Ejecucion conservadora.
- Registro de bloqueos.

Brecha:

- Hay demasiados followups pendientes, vencidos o sin fecha.
- No todos tienen owner, siguiente accion, criterio de cierre o evidencia.
- El runner ejecuta por cola, pero no decide impacto global entre fuentes.

### 3.2 Protocol tasks y deuda operativa

Ya existe:

- Apertura/cierre de tareas.
- Evidencia de cierre.
- Reglas de seguridad antes de editar.
- Registro de deuda cuando falta disciplina de cierre.

Mantener:

- Tarea como contenedor de trabajo real.
- Cierre con evidencia antes de afirmar resultado.

Brecha:

- Existen tareas abiertas, parciales, bloqueadas y deuda abierta acumulada.
- Falta una vista canonica que diga que hacer primero y que puede cerrarse
  automaticamente por estar duplicado, resuelto o obsoleto.

### 3.3 Workflows y goals

Ya existe:

- Workflows durables.
- Goals activos, bloqueados, completados y abandonados.
- Capacidad de continuar trabajo entre sesiones.

Mantener:

- Workflow como unidad duradera para trabajos largos.
- Goal como objetivo de alto nivel.

Brecha:

- Goals activos pueden quedar sin siguiente accion verificable.
- Workflows abiertos o running pueden no estar conectados a followups,
  outcomes o deuda.
- Falta reconciliacion entre "objetivo activo" y "trabajo realmente movido".

### 3.4 Outcomes

Ya existe:

- Registro de resultados esperados.
- Checker diario.
- Estados como pending y missed.

Mantener:

- Outcome como verificacion diferida de una accion medible.

Brecha:

- Hay pocos outcomes respecto al volumen real de compromisos.
- Los outcomes missed no siempre se transforman en plan de recuperacion.
- El checker evalua vencimientos, pero no reordena prioridades globales.

### 3.5 Deep Sleep y Evolution

Ya existe:

- Analisis nocturno.
- Aplicacion de findings como aprendizajes, followups, skills, briefing items
  y code_change staging.
- Ciclo semanal de mejora.

Mantener:

- Deep Sleep como analizador pesado fuera de la conversacion.
- Evolution como mejora periodica.

Brecha:

- Deep Sleep no es una cola unica de cierre operativo.
- Findings pueden crear nuevas piezas sin garantizar convergencia posterior.
- Falta retroalimentacion cuantitativa entre cierre real y propuestas de
  evolucion.

### 3.6 Watchdog e Immune

Ya existe:

- Chequeos de salud.
- Smoke tests.
- Estado OK/WARN/FAIL.
- Cuarentena o protecciones cuando algo rompe.

Mantener:

- Watchdog como sensor tecnico.
- Immune como defensa de seguridad y salud.

Brecha:

- Un WARN/FAIL no siempre se traduce en item priorizado con owner, plan,
  permiso requerido y criterio de cierre.
- Cuando todo esta OK, eso no significa que los ciclos operativos esten
  convergiendo.

### 3.7 Cola MCP y lifecycle

Ya existe:

- Write queue con estados.
- Dead letters.
- Lifecycle con canonical_done, canonical_pending, retryable_error y errores
  de vinculacion.

Mantener:

- Modelo conservador de escritura.
- Trazabilidad de errores.

Brecha:

- Dead letters y retryable errors pueden acumularse sin entrar a una rutina de
  recuperacion priorizada.
- Los errores de vinculacion necesitan triage canonico, no solo logs.

### 3.8 Automation reconciler

Ya existe:

- Reconciliador conservador.
- Dry run.
- Acciones seguras limitadas.
- Revision manual para acciones peligrosas.

Mantener:

- No tocar LaunchAgents automaticamente.
- No borrar spool automaticamente.
- No hacer acciones destructivas sin aprobacion.

Brecha:

- Las acciones manuales detectadas quedan como informe, no necesariamente como
  cola de cierre con prioridad y seguimiento.

### 3.9 Memoria y contexto local

Ya existe:

- Pipeline de memoria procesado.
- FTS operativo.
- Migracion de embeddings.
- Local context como fuente de contexto amplio.

Mantener:

- Memoria como fuente de continuidad y evidencia.
- Politica de memoria separada de ejecucion.

Brecha:

- El historial ayuda a recordar, pero no decide por si solo que debe cerrarse.
- Crecimientos anormales o riesgos de almacenamiento deben entrar a cierre
  operativo con umbral y plan.

## 4. Problema exacto que resuelve

NEXO puede detectar, recordar, ejecutar y proteger, pero todavia puede dejar
demasiadas cosas en estado abierto. El sintoma no es ausencia de inteligencia;
es falta de convergencia operacional entre muchos sistemas ya existentes.

Evidencia local usada como snapshot:

- protocol_tasks con cientos de items open, partial y blocked.
- protocol_debt abierto cerca de 900 items.
- followups pendientes en cientos, muchos vencidos o sin fecha.
- workflow_goals activos en volumen alto comparado con completados.
- outcomes con missed y pending, pero bajo volumen total.
- mcp_write_queue con dead_letter acumulado aunque la cola activa este limpia.
- cron con errores recientes.
- lifecycle con retryable_error y session-not-linked-to-nexo.
- local-context.db grande, con historial previo de crecimiento extremo.

La capa debe responder siempre a estas preguntas:

1. Que es lo mas importante que sigue abierto?
2. Por que esta abierto?
3. Se puede ejecutar ya?
4. Que falta para ejecutarlo?
5. Que prueba demuestra que se cerro?
6. Que sistema debe recibir el cierre final?

## 5. No objetivos

Esta capa no debe:

- Reemplazar followups, workflows, outcomes o Deep Sleep.
- Ejecutar acciones sensibles sin permiso actual del operador.
- Tocar infraestructura de terceros sin autorizacion explicita.
- Borrar datos, spool, LaunchAgents, credenciales o colas por heuristica.
- Crear otra memoria paralela generalista.
- Saltarse reglas de seguridad antes de escribir codigo.
- Marcar cerrado algo sin prueba fisica o verificable.
- Convertir todos los problemas en tickets humanos.

## 6. Concepto central

Crear una entidad canonica llamada `closure_item`.

Un `closure_item` representa cualquier asunto que necesita converger hacia un
cierre verificable. Puede nacer desde un followup, task, workflow, outcome,
log de cron, dead letter, finding de Deep Sleep, alerta de Watchdog, deuda,
aprendizaje contradictorio o peticion directa del operador.

Cada item debe tener:

- Fuente original.
- Tipo de problema.
- Impacto estimado.
- Riesgo.
- Estado.
- Siguiente accion.
- Bloqueador actual, si existe.
- Capacidad necesaria.
- Permiso necesario.
- Owner logico.
- Fecha de deteccion.
- Fecha objetivo.
- Prueba requerida.
- Enlaces a sistemas existentes.
- Evento de cierre.

## 7. Estados

Estados propuestos:

- `detected`: detectado por un adaptador, sin triage.
- `triaged`: clasificado y deduplicado.
- `planned`: tiene plan y criterio de cierre.
- `ready`: ejecutable ahora con capacidad y permiso suficientes.
- `running`: hay una accion en curso.
- `waiting_user`: falta una decision o permiso del operador.
- `waiting_external`: depende de sistema externo, cliente, API, DNS, proveedor.
- `blocked`: no se puede avanzar con los datos actuales.
- `verifying`: se ejecuto accion y se esta comprobando resultado.
- `verified`: la prueba confirma el resultado.
- `closed`: cierre propagado a sistemas fuente.
- `rejected`: no requiere accion tras revision.
- `stale`: ya no aplica por antiguedad o cambio de contexto.

Transiciones validas principales:

```text
detected -> triaged
triaged -> planned
triaged -> rejected
planned -> ready
planned -> waiting_user
planned -> waiting_external
planned -> blocked
ready -> running
running -> verifying
running -> waiting_external
running -> blocked
verifying -> verified
verifying -> running
verifying -> blocked
verified -> closed
blocked -> planned
waiting_user -> planned
waiting_external -> planned
stale -> closed
```

Regla: `closed` solo es valido si existe evidencia suficiente o si el item fue
rechazado/stale con razon explicita.

## 8. Modelo de datos

### 8.1 Tabla `closure_items`

```sql
CREATE TABLE closure_items (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  summary TEXT,
  kind TEXT NOT NULL,
  state TEXT NOT NULL,
  source_primary TEXT NOT NULL,
  source_key TEXT NOT NULL,
  dedupe_key TEXT NOT NULL,
  impact_score REAL NOT NULL DEFAULT 0,
  urgency_score REAL NOT NULL DEFAULT 0,
  risk_score REAL NOT NULL DEFAULT 0,
  confidence_score REAL NOT NULL DEFAULT 0,
  priority_score REAL NOT NULL DEFAULT 0,
  safety_class TEXT NOT NULL DEFAULT 'normal',
  capability_required TEXT,
  capability_status TEXT NOT NULL DEFAULT 'unknown',
  owner TEXT NOT NULL DEFAULT 'nero',
  next_action TEXT,
  blocker_reason TEXT,
  evidence_required TEXT,
  evidence_observed TEXT,
  deadline_at TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  last_progress_at TEXT,
  closed_at TEXT,
  close_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

### 8.2 Tabla `closure_item_sources`

```sql
CREATE TABLE closure_item_sources (
  id TEXT PRIMARY KEY,
  closure_item_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_status TEXT,
  source_payload_json TEXT,
  observed_at TEXT NOT NULL,
  FOREIGN KEY (closure_item_id) REFERENCES closure_items(id)
);
```

### 8.3 Tabla `closure_item_links`

```sql
CREATE TABLE closure_item_links (
  id TEXT PRIMARY KEY,
  closure_item_id TEXT NOT NULL,
  link_type TEXT NOT NULL,
  link_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (closure_item_id) REFERENCES closure_items(id)
);
```

Ejemplos de `link_type`:

- `protocol_task`
- `followup`
- `workflow_run`
- `workflow_goal`
- `outcome`
- `learning`
- `cron_run`
- `mcp_write_queue`
- `deep_sleep_finding`
- `support_case`

### 8.4 Tabla `closure_item_events`

```sql
CREATE TABLE closure_item_events (
  id TEXT PRIMARY KEY,
  closure_item_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT,
  note TEXT,
  evidence TEXT,
  actor TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (closure_item_id) REFERENCES closure_items(id)
);
```

### 8.5 Tabla `closure_capability_readiness`

```sql
CREATE TABLE closure_capability_readiness (
  id TEXT PRIMARY KEY,
  capability TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT,
  verified_at TEXT NOT NULL,
  verification_evidence TEXT,
  expires_at TEXT
);
```

Estados de capacidad:

- `available`
- `missing_tool`
- `missing_credential`
- `needs_user_permission`
- `unsafe`
- `external_blocker`
- `unknown`

### 8.6 Tabla `closure_daily_snapshots`

```sql
CREATE TABLE closure_daily_snapshots (
  id TEXT PRIMARY KEY,
  snapshot_date TEXT NOT NULL,
  open_count INTEGER NOT NULL,
  ready_count INTEGER NOT NULL,
  blocked_count INTEGER NOT NULL,
  waiting_user_count INTEGER NOT NULL,
  closed_24h_count INTEGER NOT NULL,
  stale_count INTEGER NOT NULL,
  avg_age_days REAL,
  p95_age_days REAL,
  top_blockers_json TEXT,
  created_at TEXT NOT NULL
);
```

## 9. Deduplicacion

Cada adaptador debe producir un `dedupe_key` estable.

Formato recomendado:

```text
<kind>:<normalized_subject>:<source_family>
```

Ejemplos:

- `dead_letter:mcp_write_queue:session-not-linked-to-nexo`
- `followup:operator-map-update:nexo`
- `cron_error:gbp-reviews-watch:ssl-certificate-verify`
- `workflow_goal:nexo-operational-closure:active`

Reglas:

- Si dos fuentes describen el mismo bloqueo, se fusionan en un item.
- Si una fuente nueva aumenta impacto o urgencia, actualiza el item.
- Si la fuente original se cierra pero quedan fuentes hermanas abiertas, el
  item no se cierra todavia.
- Un item cerrado puede reabrirse si aparece la misma clave con evidencia
  nueva y estado actual no resuelto.

## 10. Ranking de prioridad

Formula inicial:

```text
priority_score =
  impact_score
  + urgency_score
  + age_score
  + unblock_score
  + evidence_strength_score
  - risk_score
  - waiting_penalty
  - uncertainty_penalty
```

### 10.1 Impacto

Escala 0 a 100:

- 90-100: impide operar NEXO o afecta datos/seguridad.
- 70-89: bloquea compromisos con el operador o ejecucion recurrente.
- 50-69: acumula deuda relevante o afecta calidad visible.
- 20-49: mejora local o limpieza.
- 0-19: ruido o accion opcional.

### 10.2 Urgencia

Escala 0 a 100:

- 100: vencido y con consecuencia actual.
- 80: vence en 24 horas.
- 60: vence en 7 dias.
- 30: sin fecha pero envejecido.
- 10: mejora sin fecha.

### 10.3 Riesgo

Escala 0 a 100:

- 100: accion destructiva, legal, financiera, publica o privada sensible.
- 80: cambia infraestructura, credenciales o datos de terceros.
- 60: cambia runtime/core.
- 40: cambia repo con tests.
- 20: documentacion o lectura.
- 0: clasificacion sin efectos.

### 10.4 Unblock score

Sube prioridad cuando cerrar un item desbloquea otros.

Ejemplos:

- Cerrar `session-not-linked-to-nexo` puede desbloquear lifecycle.
- Clasificar dead letters puede limpiar cola MCP.
- Reducir followups undated mejora runner y briefing.

## 11. Adaptadores de entrada

### 11.1 Followup adapter

Lee:

- Followups pending.
- Due or past.
- Undated.
- Blocked.
- Reprogramados muchas veces.

Crea items:

- `followup_due`
- `followup_undated`
- `followup_blocked`
- `followup_repeatedly_deferred`

Accion tipica:

- Vincular a workflow existente.
- Pedir permiso si la accion es sensible.
- Convertir en outcome si es medible.
- Marcar stale si ya no aplica y hay evidencia.

### 11.2 Protocol task adapter

Lee:

- Tasks open.
- Tasks partial.
- Tasks blocked.
- Tasks viejas sin progreso.

Crea items:

- `task_open_stale`
- `task_partial_needs_close`
- `task_blocked_needs_decision`

Accion tipica:

- Recuperar evidencia.
- Continuar tarea.
- Cerrar con resultado real.
- Crear followup si necesita espera externa.

### 11.3 Protocol debt adapter

Lee:

- Deuda abierta.
- Deuda repetida por tipo.
- Deuda relacionada con cierre sin evidencia.

Crea items:

- `protocol_debt_cluster`
- `missing_evidence_debt`
- `promise_debt`

Accion tipica:

- Consolidar clusters.
- Convertir patrones repetidos en learning o test.
- Cerrar deuda que ya no aplica con evidencia.

### 11.4 Workflow adapter

Lee:

- Workflow runs open/running/blocked.
- Goals active/blocked/abandoned.
- Workflows sin update reciente.

Crea items:

- `workflow_stalled`
- `goal_without_next_action`
- `goal_abandoned_review`

Accion tipica:

- Actualizar checkpoint.
- Cerrar workflow completado.
- Crear followup de continuacion.
- Rechazar goal obsoleto con razon.

### 11.5 Outcome adapter

Lee:

- Outcomes pending vencidos.
- Outcomes missed.
- Acciones medibles sin outcome.

Crea items:

- `outcome_missed_recovery`
- `outcome_pending_verification`
- `missing_outcome_for_commitment`

Accion tipica:

- Verificar resultado.
- Abrir plan de recuperacion.
- Crear outcome futuro.
- Vincular outcome a task/followup.

### 11.6 Cron adapter

Lee:

- Cron runs error.
- Cron runs killed.
- Jobs con errores repetidos.
- Jobs sin ejecucion esperada.

Crea items:

- `cron_repeated_error`
- `cron_killed`
- `cron_missing_run`

Accion tipica:

- Capturar log minimo.
- Clasificar si es externo, credencial, SSL, bug o timeout.
- Abrir tarea tecnica si es bug.
- Crear followup si depende de proveedor.

### 11.7 MCP write queue adapter

Lee:

- failed.
- dead_letter.
- retrying envejecido.
- processing atascado.

Crea items:

- `mcp_dead_letter_cluster`
- `mcp_retry_stalled`
- `mcp_write_failed`

Accion tipica:

- Agrupar por error.
- Reintentar solo si es idempotente y permitido.
- Convertir en tarea si hay bug de vinculacion.
- Cerrar si el efecto ya fue aplicado.

### 11.8 Lifecycle adapter

Lee:

- canonical_pending.
- retryable_error.
- session-not-linked-to-nexo.
- inconsistencias de estado.

Crea items:

- `lifecycle_retryable_error`
- `lifecycle_linking_error`
- `lifecycle_pending_stale`

Accion tipica:

- Reparar enlaces si la evidencia es determinista.
- Pedir decision si hay ambiguedad.
- Abrir bug si el patron se repite.

### 11.9 Deep Sleep adapter

Lee:

- Findings no aplicados.
- Findings aplicados que crean trabajo.
- Recomendaciones repetidas.

Crea items:

- `deep_sleep_unapplied_finding`
- `deep_sleep_generated_work`
- `deep_sleep_repeated_pattern`

Accion tipica:

- Convertir finding en closure_item.
- Vincular learning/followup/skill creado.
- Medir si la recomendacion genero cierre real.

### 11.10 Watchdog/Immune adapter

Lee:

- WARN.
- FAIL.
- Quarantines.
- Smoke failures.

Crea items:

- `watchdog_warn`
- `watchdog_fail`
- `immune_quarantine_review`

Accion tipica:

- Verificar salud.
- Abrir tarea tecnica.
- Mantener blocked si requiere decision.
- Cerrar cuando el smoke vuelve a OK y hay evidencia.

### 11.11 Local context adapter

Lee:

- Tamano de DB.
- Crecimiento anomalo.
- Tablas anormales.
- Errores de ingestion o FTS.

Crea items:

- `local_context_growth_risk`
- `local_context_ingest_error`
- `local_context_health_warning`

Accion tipica:

- Medir tamano.
- Ejecutar diagnostico read-only.
- Proponer compactacion solo con plan y backup.

## 12. Politica de ejecucion

### Fase 0: read-only

Permitido:

- Leer estados.
- Crear snapshot.
- Crear closure_items.
- Calcular prioridad.
- Generar dashboard textual.
- Proponer top 10 acciones.

No permitido:

- Cerrar fuentes.
- Reintentar colas.
- Editar runtime.
- Borrar nada.
- Pedir credenciales nuevas salvo que una accion concreta lo requiera.

### Fase 1: cierres seguros

Permitido:

- Marcar `rejected` cuando la fuente ya no existe.
- Marcar `stale` con razon explicita.
- Cerrar duplicados si todas las fuentes apuntan al mismo cierre.
- Crear followups de espera si falta decision externa.
- Crear outcomes para acciones medibles ya acordadas.

Requiere evidencia:

- `ls`, `wc`, `rg`, consulta DB, status API, test, log o prueba equivalente.

### Fase 2: ejecucion delegada

Permitido:

- Abrir protocol_task.
- Abrir workflow.
- Delegar a followup-runner o agente si esta listo.
- Actualizar item a running/verifying.

Requiere:

- Capacidad disponible.
- Riesgo aceptable.
- Owner definido.
- Criterio de cierre.

### Fase 3: acciones controladas

Permitido solo con aprobacion actual cuando aplique:

- Cambios en runtime/core.
- Acciones publicas.
- Emails/mensajes.
- Pagos.
- Cambios de infraestructura.
- Datos privados de terceros.
- Reintentos que puedan duplicar efectos.

## 13. Herramientas propuestas

### 13.1 `nexo_closure_status`

Devuelve resumen:

- Abiertos por estado.
- Cerrados ultimas 24h.
- Top blockers.
- Aging.
- Fuentes con mas deuda.
- Capacidad faltante.

### 13.2 `nexo_closure_next`

Devuelve el siguiente item recomendado.

Parametros:

- `limit`
- `kind`
- `max_risk`
- `include_waiting`
- `source`
- `area`

Respuesta:

- Item.
- Por que es prioritario.
- Que accion sigue.
- Que permiso falta, si aplica.
- Que evidencia cerraria el item.

### 13.3 `nexo_closure_item_get`

Devuelve detalle completo de un item:

- Fuentes.
- Links.
- Eventos.
- Score.
- Historial de decisiones.
- Evidencia.

### 13.4 `nexo_closure_triage`

Clasifica uno o varios items.

Acciones:

- Cambiar kind.
- Cambiar estado.
- Anadir blocker.
- Anadir next_action.
- Anadir evidence_required.
- Fusionar duplicados.

### 13.5 `nexo_closure_verify`

Ejecuta o registra una verificacion.

Tipos:

- `file_exists`
- `db_query`
- `command_exit_zero`
- `http_status`
- `mcp_status`
- `test_output`
- `manual_evidence`

### 13.6 `nexo_closure_close`

Cierra item y propaga cierre a fuentes cuando sea seguro.

Requisitos:

- Estado `verified`, `rejected` o `stale`.
- Evidencia presente.
- Razon de cierre.
- Propagacion idempotente.

### 13.7 `nexo_closure_snapshot`

Crea snapshot diario y guarda metricas para Evolution.

### 13.8 `nexo_closure_link`

Vincula item con task, workflow, followup, outcome o learning.

## 14. UI propuesta en NEXO Desktop

Nombre de vista: `Control`

Objetivo: que el operador vea en 10 segundos si NEXO esta convergiendo.

### 14.1 Panel superior

Metricas:

- Open.
- Ready.
- Waiting user.
- Blocked.
- Closed 24h.
- Avg age.
- Debt delta.

### 14.2 Top 10

Cada fila:

- Titulo.
- Fuente.
- Estado.
- Prioridad.
- Riesgo.
- Siguiente accion.
- Bloqueador.
- Boton de detalle.
- Boton de aprobar cuando aplique.

### 14.3 Vista detalle

Debe mostrar:

- Por que existe.
- Que sistemas lo reportaron.
- Que se hara.
- Que falta.
- Que prueba cerrara.
- Historial de eventos.
- Links a task/followup/workflow/outcome.

### 14.4 Filtros

- Ready now.
- Waiting for me.
- Blocked.
- High impact.
- Old.
- By source.
- By area.
- By risk.

### 14.5 UX critica

La UI no debe ser un dashboard bonito pero pasivo. Debe responder:

> Que puedo autorizar o resolver ahora para desbloquear mas sistema?

## 15. Integracion con Deep Sleep

Deep Sleep debe emitir candidatos de cierre, no solo notas o followups.

Nuevo output recomendado:

```json
{
  "type": "closure_candidate",
  "title": "Dead letters MCP acumuladas",
  "kind": "mcp_dead_letter_cluster",
  "source_key": "mcp_write_queue:dead_letter",
  "impact": 70,
  "risk": 40,
  "evidence_required": "Consulta DB muestra dead_letter=0 o todos triaged",
  "next_action": "Agrupar errores por causa y crear plan de recuperacion"
}
```

Reglas:

- Deep Sleep no cierra automaticamente acciones sensibles.
- Deep Sleep puede cerrar items stale solo si la evidencia es determinista.
- Cada finding repetido debe aumentar prioridad o fusionarse.

## 16. Integracion con followup-runner

El runner no debe escanear todo sin prioridad global. Debe poder pedir:

```text
nexo_closure_next(limit=5, max_risk=40, state=ready)
```

Luego:

- Ejecuta los items listos.
- Si falta decision, marca waiting_user.
- Si falta credencial, marca missing_credential.
- Si hay bloqueo externo, marca waiting_external.
- Si se cierra, registra evidencia y propaga a followup/task/outcome.

## 17. Integracion con outcomes

Regla:

> Toda accion con resultado medible debe tener outcome o closure evidence.

Ejemplos:

- "Reducir dead letters de 89 a 0 o triaged" debe crear outcome.
- "Cerrar followups vencidos" debe crear snapshot antes/despues.
- "Corregir SSL en job GBP" debe tener test o log posterior.

Cuando un outcome pasa a `missed`, se crea item:

```text
kind = outcome_missed_recovery
state = detected
impact = max(original_impact, 60)
next_action = "decidir recuperacion o cierre justificado"
```

## 18. Integracion con Watchdog e Immune

Watchdog sigue siendo sensor. Closure Plane decide convergencia.

Flujo:

```text
watchdog FAIL -> closure_item detected
closure_item triaged -> task/workflow si necesita accion
test OK -> closure_item verifying
evidence captured -> closure_item closed
```

Immune/quarantine:

- Nunca se levanta cuarentena sin prueba.
- Nunca se ignora cuarentena por prioridad.
- Cada cuarentena debe tener item con owner y decision.

## 19. Integracion con lifecycle

Para errores como `session-not-linked-to-nexo`:

1. Agrupar por firma de error.
2. Medir cantidad y edad.
3. Detectar si hay patron de source/session.
4. Si reparacion es determinista, proponer fix.
5. Si no, abrir bug/workflow.
6. Cerrar solo cuando canonical state queda limpio o triaged.

## 20. Seguridad

Safety classes:

- `read_only`
- `documentation`
- `repo_edit`
- `runtime_edit`
- `external_api`
- `private_data`
- `public_action`
- `financial`
- `destructive`

Politica:

- `read_only` y `documentation`: auto permitido.
- `repo_edit`: permitido con control de escritura y verificacion.
- `runtime_edit`: requiere plan, tests y preferiblemente release path.
- `external_api`: requiere verificar credencial, idempotencia y permiso.
- `private_data`: minimo necesario.
- `public_action`: aprobacion actual.
- `financial`: aprobacion actual.
- `destructive`: aprobacion explicita y backup cuando aplique.

## 21. Contratos de cierre

Cada kind debe definir `evidence_required`.

Ejemplos:

| Kind | Evidencia minima |
| --- | --- |
| `doc_created` | `ls -lh`, `wc -l`, `rg` secciones clave |
| `code_fix` | test relevante pasa y diff revisado |
| `dead_letter_cluster` | DB muestra 0 pendientes o items triaged |
| `followup_due` | followup cerrado/reprogramado con razon |
| `workflow_stalled` | checkpoint actualizado o workflow cerrado |
| `outcome_missed_recovery` | outcome actualizado y nuevo plan |
| `cron_repeated_error` | log posterior OK o bug abierto |
| `local_context_growth_risk` | tamano medido y decision registrada |

Regla general:

> Sin evidencia no hay cierre; sin cierre no se afirma resultado.

## 22. Metricas de exito

MVP debe demostrar una mejora minima del 20% en al menos tres de estas
metricas durante 14 dias:

- Reduccion de `protocol_debt` abierto.
- Reduccion de followups vencidos.
- Reduccion de followups sin fecha.
- Reduccion de tasks open/partial viejas.
- Reduccion de dead letters no triaged.
- Reduccion de workflow_goals activos sin next_action.
- Aumento de outcomes creados para acciones medibles.
- Aumento de cierres con evidencia.
- Menor edad media de items abiertos.
- Menor numero de errores repetidos sin item asociado.

Objetivo preferente:

```text
top_10_items_actionable_ratio >= 0.90
```

Es decir: 9 de los 10 asuntos mas importantes deben tener accion inmediata,
permiso requerido o bloqueador claro.

## 23. Plan de implementacion

### Fase A: migracion y modelos

Crear:

- Migracion DB para tablas `closure_*`.
- Modelos Python.
- Indices por state, priority, source, dedupe_key, updated_at.
- Tests de migracion.

Aceptacion:

- Migracion aplica en DB de test.
- Migracion es idempotente.
- Rollback documentado si el sistema lo permite.

### Fase B: adaptadores read-only

Crear adaptadores:

- followups.
- protocol_tasks.
- protocol_debt.
- workflows/goals.
- outcomes.
- cron.
- mcp_write_queue.
- lifecycle.
- Deep Sleep.
- Watchdog/Immune.
- local context.

Aceptacion:

- Cada adaptador genera candidatos sin modificar fuentes.
- Dedupe estable.
- Snapshot reproducible.

### Fase C: ranking y triage

Crear:

- Calculadora de scores.
- Normalizador de risk.
- Motor de dedupe.
- Transiciones de estado.

Aceptacion:

- Tests por formula.
- Tests por fusion de duplicados.
- Tests por aging.
- Tests por bloqueo.

### Fase D: herramientas MCP

Crear herramientas:

- `nexo_closure_status`
- `nexo_closure_next`
- `nexo_closure_item_get`
- `nexo_closure_triage`
- `nexo_closure_link`
- `nexo_closure_verify`
- `nexo_closure_close`
- `nexo_closure_snapshot`

Aceptacion:

- Schemas claros.
- Errores explicitos.
- Sin efectos no solicitados en status/next/get.
- close exige evidencia.

### Fase E: integracion con runners

Actualizar:

- followup-runner para consumir items ready.
- outcome-checker para crear recovery items.
- Deep Sleep apply_findings para emitir closure candidates.
- automation_reconciler para guardar manual actions como closure_items.

Aceptacion:

- Runner puede operar en modo dry-run.
- No aumenta acciones sensibles automaticas.
- Cada accion deja evento.

### Fase F: UI Desktop

Crear vista:

- Control.
- Top 10.
- Filtros.
- Detalle.
- Acciones de aprobar/rechazar.

Aceptacion:

- Vista carga con DB real.
- Top 10 coincide con `nexo_closure_next`.
- Las acciones sensibles muestran permiso requerido.
- No hay cierre sin evidencia.

### Fase G: Evolution feedback

Conectar snapshots a Evolution:

- Patrones repetidos.
- Cuellos de botella.
- Capacidades faltantes.
- Skills candidatas.
- Mejoras de producto.

Aceptacion:

- Weekly Evolution recibe metricas.
- Las propuestas citan evidencia de closure_items.
- Se mide si las propuestas reducen backlog.

## 24. Tests necesarios

### 24.1 Unit tests

- State machine.
- Score.
- Dedupe.
- Safety class.
- Evidence requirements.
- Capability readiness.

### 24.2 Integration tests

- DB migration.
- Adapter followups.
- Adapter protocol_tasks.
- Adapter outcomes.
- Adapter cron.
- Adapter MCP queue.
- Adapter lifecycle.

### 24.3 Safety tests

- No closure without evidence.
- No destructive action from read-only mode.
- No public/private/financial action without permission.
- No runtime edit through direct live install path.
- No auto-clear quarantine without proof.

### 24.4 Regression tests

- Duplicated candidates do not create duplicate closure_items.
- Closed item can re-open only with new evidence.
- Stale item records reason.
- Missing credential moves to `waiting_user` or `blocked`, not `ready`.

### 24.5 End-to-end tests

Caso 1:

```text
dead_letter cluster detected -> triaged -> planned -> waiting_user -> ready
-> running -> verifying -> closed
```

Caso 2:

```text
followup vencido -> linked to workflow -> executed -> outcome registered
-> evidence verified -> closed
```

Caso 3:

```text
cron SSL error -> grouped -> external blocker -> followup created
-> provider fixed -> verification OK -> closed
```

## 25. Backfill inicial

Primer backfill read-only:

1. protocol_tasks open/partial/blocked.
2. protocol_debt open.
3. followups pending/due/undated.
4. workflows open/running/blocked.
5. workflow_goals active/blocked/abandoned.
6. outcomes pending/missed.
7. MCP dead letters.
8. lifecycle retryable/canonical_pending.
9. cron errors ultimos 7 dias.
10. Watchdog/Immune WARN/FAIL.
11. local context health warnings.

Salida esperada:

- Snapshot inicial.
- Top 25 items.
- Top blockers.
- Top missing capabilities.
- Items rechazados/stale candidatos, sin aplicar todavia.

## 26. Politica de propagacion de cierre

Cuando `closure_item` llega a `closed`, debe propagar cierre a fuentes si
aplica:

- followup: completar, reprogramar o anotar.
- protocol_task: cerrar con evidencia.
- outcome: actualizar estado.
- workflow: actualizar checkpoint o cerrar.
- debt: resolver si la regla ya se cumplio.
- cron/lifecycle/MCP: registrar evento de recuperacion o triage.

La propagacion debe ser idempotente.

Ejemplo:

```text
closure_item closed twice -> no duplicate followup completion
```

## 27. Observabilidad

Logs minimos:

- adapter run id.
- candidates scanned.
- items created.
- items updated.
- duplicates merged.
- transitions.
- close attempts rejected.
- close attempts accepted.

Metricas:

- closure_items_total by state.
- closure_items_created_24h.
- closure_items_closed_24h.
- closure_items_waiting_user.
- closure_items_blocked.
- closure_age_p50/p95.
- closure_ready_count.
- closure_close_without_evidence_rejected.

## 28. Riesgos y mitigaciones

### Riesgo: crear otra cola mas

Mitigacion:

- Closure Plane no sustituye fuentes.
- Cada item debe enlazar fuentes.
- Cada cierre se propaga.

### Riesgo: automatizar demasiado

Mitigacion:

- Safety classes.
- Fases read-only primero.
- Permiso explicito para acciones sensibles.

### Riesgo: ruido por demasiados candidatos

Mitigacion:

- Dedupe agresivo.
- Top 10 accionable.
- Agrupacion por causa.
- Stale/rejected con razon.

### Riesgo: scoring incorrecto

Mitigacion:

- Score explicable.
- Override manual.
- Snapshots diarios.
- Revision semanal por Evolution.

### Riesgo: cierre falso

Mitigacion:

- Evidencia requerida por kind.
- close rechaza sin evidencia.
- Eventos auditables.

## 29. Criterios de aceptacion del MVP

MVP completo cuando:

- Existe migracion `closure_*`.
- Existe backfill read-only.
- `nexo_closure_status` funciona.
- `nexo_closure_next` devuelve top items explicados.
- `nexo_closure_item_get` muestra fuentes y eventos.
- `nexo_closure_verify` registra evidencia.
- `nexo_closure_close` rechaza cierre sin evidencia.
- Al menos cinco adaptadores reales estan activos.
- Snapshot diario se guarda.
- Top 10 tiene next_action, blocker o permission_needed.
- No se ejecutan acciones sensibles automaticamente.
- Tests criticos pasan.

## 30. Primera version recomendada

Implementar primero solo esto:

1. Tablas `closure_items`, `closure_item_sources`, `closure_item_events`.
2. Adaptadores read-only para:
   - protocol_tasks.
   - followups.
   - protocol_debt.
   - outcomes.
   - mcp_write_queue.
3. Ranking basico.
4. Herramientas:
   - `nexo_closure_status`.
   - `nexo_closure_next`.
   - `nexo_closure_item_get`.
5. Snapshot inicial.

Esto ya daria valor sin tocar ejecucion automatica.

## 31. Mejora de "20% minimo"

Hipotesis:

> Si NEXO siempre sabe sus 10 asuntos abiertos mas importantes, por que siguen
> abiertos y que prueba los cerraria, el rendimiento operativo mejora al menos
> un 20%.

Medicion:

Antes:

- Contar deuda abierta.
- Contar followups vencidos.
- Contar items sin fecha.
- Contar workflows/goals sin next_action.
- Contar dead letters no triaged.

Despues de 14 dias:

- Mismas metricas.
- Comparar reduccion.
- Contar cierres con evidencia.
- Contar decisiones del operador resueltas desde UI/CLI.

Exito:

- 20% o mas de mejora en tres metricas.
- 90% o mas de top 10 con accion/bloqueador/evidencia.
- 0 cierres aceptados sin evidencia.

## 32. Preguntas abiertas antes de implementar

1. El primer UI debe vivir en Desktop o basta CLI/MCP durante MVP?
2. El backfill inicial debe importar todo el historico o solo abiertos actuales?
3. Que umbral de antiguedad marca `stale` por defecto?
4. Que items puede cerrar el sistema sin confirmacion del operador?
5. Que metricas exactas quiere usar Francisco como "20% mejor"?
6. Debe mostrarse como "Control", "Cierre", "Operacion" o "Convergencia"?

## 33. Checklist de implementacion

- [ ] Crear migracion DB.
- [ ] Crear modelos y helpers.
- [ ] Crear state machine.
- [ ] Crear score calculator.
- [ ] Crear dedupe engine.
- [ ] Crear adapter protocol_tasks.
- [ ] Crear adapter followups.
- [ ] Crear adapter protocol_debt.
- [ ] Crear adapter outcomes.
- [ ] Crear adapter mcp_write_queue.
- [ ] Crear adapter cron.
- [ ] Crear adapter lifecycle.
- [ ] Crear adapter workflows/goals.
- [ ] Crear adapter Deep Sleep.
- [ ] Crear adapter Watchdog/Immune.
- [ ] Crear `nexo_closure_status`.
- [ ] Crear `nexo_closure_next`.
- [ ] Crear `nexo_closure_item_get`.
- [ ] Crear `nexo_closure_triage`.
- [ ] Crear `nexo_closure_link`.
- [ ] Crear `nexo_closure_verify`.
- [ ] Crear `nexo_closure_close`.
- [ ] Crear snapshot diario.
- [ ] Integrar outcome-checker.
- [ ] Integrar followup-runner.
- [ ] Integrar Deep Sleep.
- [ ] Crear UI Desktop Control.
- [ ] Crear tests unitarios.
- [ ] Crear tests de integracion.
- [ ] Crear tests de seguridad.
- [ ] Correr backfill read-only.
- [ ] Medir baseline.
- [ ] Activar Fase 1.
- [ ] Medir mejora a 14 dias.

## 34. Referencias operativas del snapshot

Fecha del snapshot usado para este spec: 2026-06-06.

Hechos considerados:

- followup-runner existe y ejecuta de forma limitada.
- workflows/goals existen.
- outcomes existen, pero el volumen registrado es bajo frente a compromisos.
- Deep Sleep aplica findings, pero no es una cola global de cierre.
- automation_reconciler es conservador y deja acciones manuales.
- Watchdog/Immune estaban sanos en el snapshot revisado.
- memoria/FTS/embeddings estaban operativos.
- habia deuda, followups, tasks, goals y dead letters acumulados.
- habia errores cron recientes y errores lifecycle por vinculacion de sesion.

Conclusion:

La mejora no es "anadir memoria" ni "otro agente mas". La mejora fuerte es
dar a NEXO una capa de cierre operacional que convierta todo lo abierto en un
ranking verificable de siguiente accion hasta que el sistema realmente cierre.
