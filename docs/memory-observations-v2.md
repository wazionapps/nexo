# Memory Observations v2

Memory Observations v2 is Brain's evidence-first operational memory layer.
It does not replace cognitive STM/LTM; it records what happened, derives
searchable observations, and keeps evidence refs attached so agents can answer
memory questions without inventing.

## Install And Update Behavior

- Fresh installs create `memory_events`, `memory_observations`,
  `memory_observation_queue`, and the observation search index through normal
  `init_db()` migrations.
- Existing installs migrate through schema v62 and keep prior data untouched.
- Startup runs bounded maintenance and backfill so older `protocol_tasks`,
  `change_log`, `session_diary`, and `recent_events` gradually become
  observations without blocking MCP startup.
- Backfill is idempotent. Re-running it skips rows already imported and pages
  past them, so large histories converge over repeated runs.

## Runtime Surfaces

- `nexo_memory_event_list` / `nexo_memory_event_stats`
- `nexo_memory_observation_list` / `nexo_memory_observation_stats`
- `nexo_memory_observation_process`
- `nexo_memory_search`, `nexo_memory_answer`, `nexo_memory_timeline`
- `nexo_memory_backfill`, `nexo_memory_health`, `nexo_memory_maintenance`
- Dashboard: `/memory` shows observations, raw events, queue state, search,
  and manual backfill controls.

## Safety Rules

- `nexo_memory_answer` only answers from candidates with `evidence_refs`.
- Tool input/output is hashed, not stored verbatim.
- Metadata, summaries, facts, entities, and refs are redacted before storage.
- FTS degradation is visible through `nexo_memory_health`; search still falls
  back to regular observation listing when FTS is unavailable.
- Producers surface memory-capture failures in their return payloads while
  preserving the primary change-log/task-close operation.

## Verification

```bash
python3 -m pytest tests/test_memory_v2.py tests/test_migrations.py tests/test_server_protocol_exports.py tests/test_dashboard_app.py -q
python3 -m ruff check src/db/_memory_v2.py src/memory_retrieval.py src/tools_memory_v2.py src/server.py src/tools_sessions.py src/dashboard/app.py
```
