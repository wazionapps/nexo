# Hot Context Memory

NEXO now has a dedicated `hot context` layer for recent operational continuity.

This is not the same thing as:

- long-term memory (`remember`, `recall`, STM/LTM, consolidation)
- reminder/followup history
- session diary summaries

`Hot context` is the shared 24-hour memory that should keep an active topic fresh across:

- new chats
- different clients
- cron ticks
- email processing
- protocol tasks

## Mental model

NEXO memory is now split into two useful layers:

- `durable memory`: learnings, reminders, followups, diary, STM/LTM, decisions
- `recent operational memory`: `recent_events` + `hot_context`

Use hot context when the question is:

- "What were we actively doing in the last few hours?"
- "Did we already talk about this today?"
- "What is the current live state of this topic?"

Use durable memory when the question is:

- "What rule did we learn?"
- "What should we remember in general?"
- "What happened weeks ago?"

## Core data model

Two new SQLite tables power this layer:

- `hot_context`
- `recent_events`

### `hot_context`

Represents the live topic or thread:

- `context_key`
- `title`
- `summary`
- `context_type`
- `state`
- `owner`
- `source_type`
- `source_id`
- `session_id`
- `first_seen_at`
- `last_event_at`
- `expires_at`

Typical states:

- `active`
- `waiting_user`
- `waiting_third_party`
- `blocked`
- `resolved`
- `abandoned`

### `recent_events`

Represents the recent timeline attached to a topic:

- event type
- title/summary/body
- actor
- source
- session
- timestamps
- TTL

The default freshness window is `24h`, configurable per call up to `168h`.

## Core tools

### `nexo_pre_action_context`

Use this before acting when continuity matters.

Example:

```bash
nexo call nexo_pre_action_context --input '{
  "query":"francisco email dns recambiosbmw",
  "hours":24,
  "limit":8
}'
```

This returns a bundle with:

- matching hot contexts
- recent events
- related reminders
- related followups

### `nexo_recent_context_capture`

Capture or refresh an active topic.

Example:

```bash
nexo call nexo_recent_context_capture --input '{
  "title":"SES issue for holidays2thecanaries",
  "summary":"Maria owns the decision. Waiting third party confirmation.",
  "topic":"holidays2thecanaries ses",
  "state":"waiting_third_party",
  "owner":"maria",
  "source_type":"email",
  "source_id":"thread-123"
}'
```

### `nexo_recent_context`

Read recent continuity by query or exact `context_key`.

### `nexo_recent_context_resolve`

Resolve a live topic when it is clearly closed.

### `nexo_hot_context_list`

List currently active hot contexts.

## When hot context is not enough

Hot context is the first recent-memory layer, not the last one.

If the runtime knows a conversation happened recently but the hot-context layer does not have enough detail, use the transcript fallback tools:

- `nexo_transcript_recent(...)`
- `nexo_transcript_search(...)`
- `nexo_transcript_read(...)`

Those tools read the same Claude Code / Codex transcript families that Deep Sleep uses overnight, so the public fallback path and the overnight analysis path stay aligned.

If the question is about NEXO itself rather than the operator's recent work, use the live system catalog:

- `nexo_system_catalog(...)`
- `nexo_tool_explain(...)`

See [Recent Memory Fallbacks and the Live System Catalog](./recent-memory-fallbacks-and-system-catalog.md).

## Automatic feeders

The core already feeds hot context from:

- `heartbeat`
- `task_open`
- `task_close`
- reminder lifecycle
- followup lifecycle

This means recent operational continuity improves even if a client only uses the protocol path and normal reminder/followup tools.

## Dashboard

The dashboard exposes this via:

- `/api/recent-context`
- `Memory` page -> `Hot Context 24h`

That surface is for observability and debugging, not just for the UI.

## Reminder and followup history

Reminder/followup history is still important, but it is a different layer.

History answers:

- what happened to this specific item
- who changed it
- why it was completed/deleted/restored

Hot context answers:

- what is currently alive in the last 24 hours
- what topic is still active across channels
- what should be loaded before deciding or replying

Do not confuse them.

## Script guidance

Personal or core automation scripts should use this pattern:

1. `nexo_pre_action_context(...)` before taking action
2. `nexo_recent_context_capture(...)` when a topic becomes active, blocked, or waiting
3. `nexo_recent_context_resolve(...)` when the topic is clearly done
4. `nexo_transcript_search(...)` / `nexo_transcript_read(...)` if the recent-memory layer is too thin but the transcript should still exist
5. `nexo_system_catalog(...)` / `nexo_tool_explain(...)` when the automation needs a live map of NEXO itself

This is the preferred replacement for giant prompt-only continuity rules.

## Design boundaries

What belongs in core:

- the 24h shared recent-memory substrate
- context states
- pre-action bundle
- reusable MCP tools

What stays personal:

- routing rules like "this belongs to Maria"
- operator-specific ownership rules
- script-specific query composition
- local workflow heuristics

## Expected outcome

If a user talks about topic `X` now, NEXO should be able to rehydrate the recent operational context of `X` hours later, even if:

- the client changed
- the session changed
- a cron resumed the work
- the next action happens through email/orchestrator/runtime automation
