Protocol reminder: keep `nexo_heartbeat(...)` current and do not close optimistically; if there are real changes, record `nexo_change_log(...)` or close with `nexo_task_close(...)` plus evidence.
