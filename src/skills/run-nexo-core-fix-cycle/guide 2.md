# Run NEXO Core Fix Cycle

Usa esta skill cuando haya que implementar y verificar un grupo pequeño de fixes del core de NEXO sin improvisar el plano ni repetir siempre la misma fase de descubrimiento y test.

## Steps
1. Fija primero el plano: repo público `nexo`, runtime instalado `~/.nexo` y claims/documentación pública. No mezcles esos tres mundos.
2. Abre `nexo_task_open(...)` y `nexo_workflow_open(...)` antes de tocar código. Si el fix es de acción, pasa también por `nexo_cortex_decide(...)`.
3. Ejecuta la skill con `areas` ajustadas al fix. El helper te devuelve el mapa de archivos y corre la batería de tests focalizada para `protocol`, `plane`, `guard`, `cortex` y/o `release`.
4. Implementa el cambio mínimo defendible solo en la superficie correcta. Si el problema es producto, se arregla en el repo; no en `~/.nexo`.
5. Reejecuta la skill para revalidar el clúster exacto de tests tocado por el fix.
6. Cierra con `nexo_task_close(...)` y evidencia real. Si hubo edición real, deja `change_log` y captura learning si cambió una regla canónica.

## Gotchas
- No uses diary, workflow text o intuición como sustituto de git/tests/runtime reales.
- Si el fix toca doctor o claims públicos, fija el `plane` explícito antes de ejecutar diagnósticos.
- Si el fix toca release o runtime update, usa la vía oficial (`nexo update`, doctor, skill de release final) y no scripts laterales.
- Si el helper no encuentra un área, añade la superficie nueva de forma explícita en la skill en vez de seguir repitiendo grep manual disperso.
