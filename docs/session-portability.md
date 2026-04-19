# Session Portability

NEXO already shares one brain across Claude Code, Codex, and Claude Desktop. Session portability adds an explicit handoff layer on top of that shared runtime.

## Why

Sometimes shared memory is not enough by itself.

You may want to move active work between clients with a portable packet that includes:

- current task
- latest checkpoint
- latest diary or draft
- open protocol tasks
- open workflow goals
- open workflow runs

## Tools

### `nexo_session_portable_context`

Returns a human-readable handoff packet for another client/runtime.

Use it when you want another client to continue the same work with explicit context instead of reconstructing it from scratch.

### `nexo_session_export_bundle`

Writes a machine-readable JSON bundle to disk for archival or cross-client handoff.

The export includes:

- session metadata
- checkpoint
- latest diary
- draft summary if no final diary exists
- open protocol tasks
- open workflow goals
- open workflow runs

## Typical Flow

1. Client A works normally with `startup`, `heartbeat`, workflow/protocol tools, and checkpoints.
2. Before handing off, call `nexo_session_portable_context` for a readable summary.
3. If you want a durable artifact, call `nexo_session_export_bundle`.
4. Client B starts with that packet and the same shared brain instead of starting blind.
