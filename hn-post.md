# Show HN: NEXO Brain – Persistent memory for Claude Code using cognitive psychology

Most AI assistants forget everything between sessions. NEXO Brain is an MCP server that gives Claude a persistent, local memory system modeled after actual cognitive psychology — not just a vector store with a chat wrapper.

**What it implements:**

- **Atkinson-Shiffrin model** (1968): sensory register → short-term → long-term memory with promotion/decay
- **Ebbinghaus forgetting curves**: memories decay over time unless reinforced by use
- **Metacognitive guard**: before editing code, checks "have I made this mistake before?" and blocks if a matching learning exists
- **Cognitive dissonance detection**: flags when a new instruction contradicts a stored belief instead of silently complying
- **Trust score (0–100)**: calibrates rigor based on correction/praise history — more checks at low trust, more autonomy at high trust
- **Deep Sleep**: nightly analysis of session transcripts to extract learnings while you're offline
- **Session continuity**: resumes the previous mental state, including in-progress reasoning — doesn't start cold

All storage is local: SQLite + fastembed vectors (768 dims, HNSW indexing). No API calls, no cloud. Knowledge graph with claim verification built in.

Benchmarked on LoCoMo conversational memory: F1 0.588 (+55% over GPT-4 baseline).

Battle-tested for 6+ months running a production ecommerce business (€300K+/year), handling ads, Shopify, analytics, and server ops daily.

```
npx nexo-brain init
```

- GitHub: https://github.com/wazionapps/nexo (AGPL-3.0)
- Docs: https://nexo-brain.com
