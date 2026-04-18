# Run NEXO Audit Phase

Usa esta skill cuando haya que ejecutar una fase de auditoria de NEXO y el cuello de botella sea decidir el alcance de `evolution_apply` y arrancar una tanda de items con disciplina empirica.

## Pasos
1. Abre `goal + workflow + task` y fija el terreno real antes de interpretar el informe:
   - repo/runtime activo
   - DB real
   - mecanismo de update
   - tests y estado git
2. Fija la regla de autonomia antes de empezar:
   - el operador no quiere checkpoints uno-a-uno para trabajo mecanico
   - NEXO hace branches, PRs, merge y reporta despues con evidencia
   - solo un blast radius arquitectonico enorme merece checkpoint
3. Trata `evolution_apply` como una decision tecnica de implementacion, no como permiso humano:
   - el camino de apply ya existe via `evolution_log` + `_apply_accepted_proposals`
   - el sandbox/snapshot/rollback protege la materializacion del cambio aceptado
   - no dupliques ese mecanismo en deep sleep ni en el runner de auditoria
4. Lanza la verificacion empirica de todos los items en paralelo:
   - `grep + read` del codigo
   - SQL/schema real
   - AST/tests/imports/logs cuando aplique
   - asume FP hasta que la evidencia lo contradiga
5. Clasifica cada item:
   - `real_gap`
   - `casi_fp`
   - `fp`
6. Ordena solo los `real_gap` por riesgo/blast radius y ejecutalos con worktree aislado si tocan core.
7. Por cada `real_gap`:
   - `guard_check`
   - `track`
   - branch propia
   - implementacion minima
   - tests adyacentes
   - PR + auto-merge squash
   - seguir al siguiente sin esperar CI salvo bloqueo real
8. Para `fp` o `casi_fp`, captura learning/patron reusable en vez de reimplementar.
9. Cierra la fase con evidencia real: PRs, tests, merge status y resultados de verificacion.

## Gotchas
- Learning #198: no confundas "como trabaja NEXO" con "que puede aplicar evolution_apply". Lo primero ya esta resuelto: autonomia total.
- `apply_findings.py` ya stagea `code_change` en `evolution_log`; `nexo-evolution-run.py` ya consume `accepted` con sandbox/snapshot/rollback. Si el item pide eso, primero verifica si ya existe.
- En Fase 1+2 la auditoria automatica sobreestimo ~70% de gaps. Si no hay evidencia dura, no abras codigo.
