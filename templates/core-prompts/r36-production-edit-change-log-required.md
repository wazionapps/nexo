Has ejecutado una mutacion de produccion. Antes de cerrar la tarea, registra el cambio con `nexo_change_log(...)` o cierra con `nexo_task_close(...)` incluyendo archivos/artefacto, motivo, riesgo y verificacion real.

Ruta productiva detectada: [[files]]. Si ya registraste el cambio en los ultimos 5 turnos, cita el `change_log` en el cierre; si no, llama `nexo_change_log(...)` antes del siguiente `nexo_task_close(...)`.
