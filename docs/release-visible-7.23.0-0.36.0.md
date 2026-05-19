# Cambios visibles - Brain 7.23.0 y Desktop 0.36.0

Fecha de trabajo: 2026-05-19.

## Brain 7.23.0

- Antes de responder, Brain tiene un router pre-respuesta para preguntarse si ya hay contexto util: trabajo previo, autoria, archivos tocados, decisiones, evidencias, localizacion de artefactos o diagnostico runtime.
- El router ya no depende de una lista fija en Desktop: Desktop envia `intent=auto` y Brain decide que mirar.
- Hay un ledger virtual de evidencia que unifica tareas, workflows, cambios, diarios, snapshots de continuidad, lifecycle events, local-context queries y evidencias registradas; esto hace mas facil contestar "esto ya lo hice?", "donde esta?", "con que prueba?".
- Las Memory Observations tienen procesador convergente con CLI/MCP para backfill, repair y SLA; reduce informacion guardada que luego no se consume.
- Hay auditorias read-only nuevas para saved-not-used, automatizaciones, MCP live/catalogo, transcripts y localizacion de artefactos; sirven para encontrar huecos reales sin tocar datos.
- El guard de privacidad de release usa `rg` cuando existe, evitando cuelgues de `grep -R` en la verificacion final.
- La suite de tests queda mas robusta frente a contaminacion de conexiones DB entre tests, especialmente en el ledger de evidencia.
- Evolution, seguridad y firma/certificados quedan fuera por decision de Francisco y no forman parte de esta release.

## Desktop 0.36.0

- Cada turno visible consulta Brain antes de escribir a Claude, despues de bootstrap/protocolo/continuidad y con timeout/fallo abierto para no bloquear el chat.
- El payload del router viaja por stdin (`--payload-stdin`), no por argv, para evitar exponer texto del usuario, rutas o secretos en argumentos de proceso.
- El chat conserva payloads grandes de lifecycle mediante `--payload-file`, evitando degradaciones por limites de argv.
- Soporte dentro de Preferencias queda mas legible: skeleton/loading, refresco con icono girando, chips de estado, preview, fecha de actualizacion y respuestas cliente/soporte diferenciadas.
- Errores recuperables de chat como timeout/red/proceso interrumpido tienen auto-retry acotado con backoff, sin duplicar turnos si la conversacion ya esta ocupada o archivada.
- El bloqueo fantasma `human-input-pending` deja de secuestrar el composer: si Claude ya siguio autonomamente, Desktop limpia el bloqueo; si realmente queda una pregunta pendiente, muestra aviso y lleva al usuario a la pregunta sin enviar ni crear error rojo.
- El inventario de residuos React queda actualizado para que el crecimiento temporal de `ComposerControls.jsx` y `SupportTab.jsx` sea visible y controlado, no deuda oculta.

## Validacion obtenida hasta ahora

- Desktop `0.36.0`: `npm run check` completo verde tras reconstruir dependencias locales con `npm ci`.
- Desktop incluye: build React, lint, unitarios, product contracts, syntax smoke y hashes bundled verificados.
- Brain `7.23.0`: privacy guard, tool-map sync y release-readiness verdes; wrapper final sigue ejecutando full pytest en este momento.
