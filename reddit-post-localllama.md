Open source cognitive architecture for Claude Code — implements Atkinson-Shiffrin memory model with fastembed RAG, Ebbinghaus decay, and metacognitive error guard

I want to share something I've been building: a full cognitive architecture for Claude Code, implemented as an MCP server. More technically interesting than "memory persistence" — let me explain the design.

**Why not just store everything?**

Most memory solutions for LLM agents treat memory as a flat append-only log with keyword retrieval. This doesn't scale and produces noisy recall. The human memory system solved this problem with multi-stage consolidation and decay. I ported that.

**Architecture: Atkinson-Shiffrin (1968) + Ebbinghaus**

Three memory stages, each with different retention characteristics:

- **Sensory Register** (48h) — raw capture, with an attention filter. Most things don't make it past here.
- **Short-Term Memory** (7-day half-life) — things that passed the attention filter. Accessed often → promoted to LTM. Not accessed → gradually forgotten.
- **Long-Term Memory** (60-day half-life) — consolidated knowledge. Near-duplicates are auto-merged. Sibling memories (same concept, different context) are linked rather than merged.

This implements Ebbinghaus forgetting curves. Memory that you never encounter again fades — because it probably wasn't important.

**Semantic search (meaning, not keywords)**

Search uses fastembed vector embeddings (384 dimensions, fully local, no external API). If you search for "deploy problems", it finds a memory about "SSH timeout on production server" — even though they share zero words.

**Near-duplicate detection and sibling linking**

When a new memory is added, NEXO checks for near-duplicates above a similarity threshold. If found:
- Same context → merge (update strength, preserve most recent phrasing)
- Different context (different project, OS, language) → link as **siblings**

Sibling linking prevents the "which deploy procedure applies here?" problem by preserving context-specific variants while surfacing them together.

**Metacognitive guard**

Before any code edit, NEXO runs a guard check against the memory store: "Have I made a mistake in this area before?" It returns relevant learnings, blocking rules, and schema facts to inject into the agent's context before it writes code. This is the most operationally valuable piece — it prevents repeating documented mistakes.

**Trust score (0-100)**

An alignment index that adjusts based on explicit and implicit events (thanks, corrections, repeated errors, delegation). At low scores, guard thresholds tighten. At high scores, redundant verification checks are reduced. Not a permissions system — purely a rigor calibrator.

**Episodic memory layer**

On top of the semantic memory, there's a separate episodic layer:
- Change log (file-level, with `why`, `risks`, `verify` fields)
- Decision log (alternatives considered, reasoning, outcomes)
- Session diary (mental state continuity — the next session reads this and "resumes" rather than starting cold)

**Numbers**

- 76 MCP tools across 16 categories
- SQLite + fastembed (everything local, no cloud dependency)
- Works with Claude Code (tested), likely portable to any MCP-compatible agent

**Install**

```
npx nexo-brain init
```

Or clone: https://github.com/wazionapps/nexo

- Website: https://nexo-brain.com
- npm: https://www.npmjs.com/package/nexo-brain
- AGPL-3.0 licensed

Built while running WAzion (https://wazion.com), a WhatsApp automation SaaS. NEXO started as our own internal tool and we decided to release it publicly. Happy to discuss the memory architecture or the forgetting curve implementation.
