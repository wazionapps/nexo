# Reddit Post — r/LocalLLaMA v2

**Title:** Built a local cognitive memory layer for MCP-compatible agents — SQLite + fastembed, no cloud, Atkinson-Shiffrin model, +55% on LoCoMo vs GPT-4

---

Memory for LLM agents is mostly unsolved. Naive RAG dumps everything into a vector store and hopes for the best. The result: growing garbage heaps of equally-weighted context, retrieval that collapses when phrasing differs, and zero forgetting.

I spent 6 months building a different approach — running it in production on a real business before open-sourcing it.

**NEXO Brain.** LoCoMo F1 0.588, +55% over GPT-4.

---

**Architecture**

Three-stage memory modeled on Atkinson-Shiffrin (the actual psychology model):

| Stage | TTL | Behavior |
|-------|-----|----------|
| Sensory Register | 48h | Raw capture + attention filter. Most things don't pass. |
| Short-Term Memory | 7-day half-life | Survived filtering. Accessed again → promoted to LTM. |
| Long-Term Memory | 60-day half-life | Consolidated. Near-duplicates auto-merged. Siblings linked. |

Forgetting is Ebbinghaus decay, not deletion. Memory that was never re-accessed fades — because it probably wasn't important. This keeps the store lean without manual pruning.

**Embeddings:** fastembed (BAAI/bge-base-en-v1.5, 768-dim), fully local, no external API. HNSW index. Semantic search that finds "SSH timeout on production" when you query "deploy problems."

**Knowledge graph:** entities and relationships stored separately from vector memory. Queried via `nexo_kg_query`. Supports path-finding between concepts.

**Claim graph:** contradictory beliefs surface as dissonance events rather than silent overrides. The agent flags them: *"My memory says X, you're now saying Y — permanent change or exception?"*

---

**What's local, what's external**

Everything is local:
- SQLite (operational data, episodic memory, credentials, learnings)
- cognitive.db (separate SQLite for vector store + metadata)
- fastembed inference runs on CPU — no GPU required, no API call

Nothing leaves your machine. No Pinecone, no OpenAI embeddings, no cloud sync.

---

**Works with any MCP-compatible agent**

NEXO exposes 100+ MCP tools. Any agent that speaks MCP can use them — it's not Claude-specific. The memory layer, guard system, trust scoring, and episodic diary are all MCP tools.

If your agent supports MCP, it gets cognitive memory.

---

**Other subsystems worth knowing about**

- **Metacognitive guard:** Before code changes, searches error history and surfaces known failure patterns. Calibrated by trust score (0–100), so it gets out of the way when things are going well.
- **Deep Sleep:** Nightly process that reads session transcripts, extracts patterns, and writes new learnings autonomously. Memory improves while you sleep.
- **Session diaries + decision logs:** Episodic memory that captures *why* a decision was made, not just what was done. Survives context window resets.
- **Adaptive decay:** Weights automatically tune based on actual retrieval performance. The model learns which memories are useful.

---

**Install**

```
npx nexo-brain init
```

Sets up SQLite, Python environment, fastembed, and MCP config in one command. No Docker, no API keys.

AGPL-3.0. GitHub: https://github.com/wazionapps/nexo

---

Built while running a €300K+/year ecommerce operation — NEXO started as an internal tool we couldn't live without. Happy to go deep on any part of the architecture.

**What approach are you using for agent memory today?** Curious how others are handling the forgetting/consolidation problem specifically.
