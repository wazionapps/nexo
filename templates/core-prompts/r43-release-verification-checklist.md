Antes de cerrar una tarea de release/paridad, la evidencia debe incluir checks objetivos o una excepcion explicita por cada gate. Faltan: [[missing]].

Ejecuta o cita: `gh pr view` (MERGED), `gh release view vX.Y.Z`, `gh run view ... --json conclusion`, `curl` del manifest publico y `git tag -l vX.Y.Z`. Si es release de NEXO Desktop, incluye tambien auditoria de promesas abiertas: grep del transcript, busqueda en `dist/release`/bundle empaquetado y followups NF para lo no implementado.
