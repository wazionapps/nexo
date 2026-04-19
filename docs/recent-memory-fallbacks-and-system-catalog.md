# Recent Memory Fallbacks and the Live System Catalog

NEXO now has a clearer recent-memory ladder.

This matters because two different failures used to get mixed together:

- NEXO could have the fact in durable memory, but fail to load it into the current decision.
- The full conversation could exist in the transcript store, but there was no public MCP tool to reach it quickly.

This document explains the new ladder and the live self-map that sits next to it.

## The Retrieval Ladder

Use these layers in order:

1. `nexo_pre_action_context(...)`
2. `nexo_recent_context(...)`
3. `nexo_transcript_search(...)` / `nexo_transcript_read(...)`
4. `nexo_system_catalog(...)` / `nexo_tool_explain(...)` when the question is about NEXO itself

That means:

- recent operational memory stays the first stop
- raw transcripts are now the fallback when recent memory missed or was never captured cleanly
- the system catalog is the self-knowledge layer for tools, scripts, plugins, skills, crons, and projects

## What Each Layer Is For

### Hot Context

Use hot context for:

- what is alive in the last few hours
- what topic is still blocked / waiting / active
- what should be loaded before replying or acting

Tools:

- `nexo_pre_action_context`
- `nexo_recent_context`
- `nexo_recent_context_capture`
- `nexo_recent_context_resolve`
- `nexo_hot_context_list`

### Transcript Fallback

Use transcript fallback for:

- "I know we talked about this today but it did not land in memory"
- "Find the real conversation about DIGI / WiFi / DNS / etc."
- "Read the raw session when summaries and recall are not enough"

Tools:

- `nexo_transcript_recent`
- `nexo_transcript_search`
- `nexo_transcript_read`

Example:

```bash
nexo call nexo_transcript_search --input '{
  "query":"wifi digi",
  "hours":24,
  "client":"claude_code",
  "limit":5
}'
```

Then:

```bash
nexo call nexo_transcript_read --input '{
  "session_ref":"claude_code:session-wifi.jsonl",
  "max_messages":80
}'
```

The public transcript tools reuse the same parser that Deep Sleep uses overnight. There is no separate public parser with different behavior.

### Live System Catalog

Use the system catalog when the question is:

- what tools does NEXO have right now?
- what plugin exposes this capability?
- what scripts, crons, skills, projects, or artifacts exist?
- how is this runtime currently structured?

Tools:

- `nexo_system_catalog`
- `nexo_tool_explain`

Examples:

```bash
nexo call nexo_system_catalog --input '{"section":"core_tools","query":"transcript"}'
nexo call nexo_tool_explain --input '{"name":"nexo_pre_action_context"}'
```

## Why This Counts As A Live Ontology

The system catalog is not a hand-maintained markdown index.

It is generated on demand from canonical sources:

- core tools from `server.py`
- plugin tools from the plugin registry and live plugin modules
- skills from the skill registry
- scripts from the script registry
- crons from the cron manifest
- projects from `project-atlas.json`
- artifacts from the artifact registry

That means the map updates as the system changes. If a release adds new tools, skills, scripts, plugins, or cron entries, the catalog changes with it.

## Core vs Personal

What belongs in core:

- recent-memory substrate
- transcript fallback tools
- live system catalog generation
- canonical source parsing

What stays personal:

- your own routing rules
- ownership heuristics
- private scripts and private project semantics
- operator-specific escalation logic

The live system catalog can still show personal scripts or personal artifacts when they are part of the runtime. The ontology is shared; some entries are public-core and some are local-runtime.

## Guidance For Automation

If a script or agent has to make a decision with continuity risk, use this pattern:

1. `nexo_pre_action_context(...)`
2. `nexo_recent_context(...)` if you need a tighter read on the active topic
3. `nexo_transcript_search(...)` when you know the conversation happened but the recent-memory layer is thin or stale
4. `nexo_system_catalog(...)` / `nexo_tool_explain(...)` when the decision depends on how NEXO itself is structured

This is the preferred replacement for giant continuity prompts that try to encode everything in prose.
