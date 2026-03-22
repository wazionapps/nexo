# NEXO — Cognitive Co-Operator for Claude Code

NEXO transforms Claude Code from a reactive assistant into a **proactive cognitive partner** that remembers, learns, forgets, and adapts.

Built on the **Atkinson-Shiffrin memory model** from cognitive psychology, NEXO gives your Claude Code sessions persistent memory with semantic search, Ebbinghaus forgetting curves, metacognitive error prevention, and a trust-based relationship that evolves over time.

## What makes NEXO different

| Feature | Without NEXO | With NEXO |
|---------|-------------|-----------|
| Memory | Gone after session | Persistent across sessions with decay and reinforcement |
| Learning | Repeats mistakes | Logs errors, prevents repetition via guard system |
| Context | Starts cold every time | Resumes from mental state of last session |
| Search | Keyword matching | Semantic similarity (vector embeddings) |
| Errors | Reactive | Metacognitive prevention — checks before acting |
| Relationship | Stateless | Trust score that modulates behavior |

## Quick Start

```bash
npx create-nexo
```

That's it. The installer will:
1. Ask what you want to call your co-operator
2. Ask permission to scan your workspace
3. Install dependencies (Python, fastembed, numpy)
4. Configure Claude Code's MCP settings
5. Set up automated memory processes

Then open Claude Code and start working. Your co-operator will introduce itself.

## Requirements

- **macOS** (Linux support planned)
- **Claude Code** CLI installed
- **Python 3.10+** (`brew install python3`)
- **Node.js 18+** (for the installer)

## Architecture

### Memory Model (Atkinson-Shiffrin)

```
Sensory Register (48h buffer)
    │
    ├─→ Attention filter (nocturnal, 23:30)
    │       │
    │       ↓
    ├─→ Short-Term Memory (STM)
    │       │ 7-day half-life
    │       ├─→ Rehearsal (access = strengthen)
    │       ├─→ 3+ accesses → promote to LTM
    │       └─→ No access → decay → forget
    │
    └─→ Long-Term Memory (LTM)
            │ 60-day half-life
            ├─→ Active: searchable by semantic similarity
            ├─→ Dormant: not searchable, but reactivatable
            └─→ Consolidation: merge near-duplicates
```

### Cognitive Features

- **Semantic RAG** — Vector search using fastembed (BAAI/bge-small-en-v1.5, 384 dims)
- **Ebbinghaus Decay** — Memories fade without reinforcement, just like human memory
- **Metacognitive Guard** — Checks "have I made this mistake before?" before every code change
- **Cognitive Dissonance** — Detects when new instructions conflict with established knowledge
- **Discriminative Fusion** — Similar memories for different contexts (e.g., Linux vs Mac) stay separate as "siblings"
- **Trust Score** — 0-100 alignment index that modulates verification rigor
- **Sentiment Detection** — Adapts tone based on user's mood (concise when frustrated, proactive when positive)

### Automated Processes

| Time | Process | What it does |
|------|---------|-------------|
| 03:00 | Cognitive Decay | Apply Ebbinghaus curves, promote STM→LTM, merge duplicates, check correction fatigue |
| 07:00 | Self-Audit | Health checks, metrics, phase trigger monitoring |
| 23:30 | Post-Mortem | Consolidate session critiques, process sensory register, analyze force events |
| Boot | Catch-Up | Run any missed processes in order |

### MCP Tools (50+)

| Category | Tools | Purpose |
|----------|-------|---------|
| Sessions | 3 | Register, heartbeat, status |
| Cognitive | 8 | RAG, stats, metrics, dissonance, sentiment, trust |
| Guard | 3 | Error prevention, repetition tracking |
| Episodic | 10 | Changes, decisions, session diary, recall |
| Reminders | 4 | Create, update, complete, delete |
| Followups | 4 | Create, update, complete, delete |
| Learnings | 5 | Add, search, update, delete, list |
| Entities | 5 | People, services, URLs |
| Preferences | 4 | Observed user preferences |
| Agents | 5 | Agent registry for delegation |
| Backup | 3 | Backup/restore SQLite data |
| Evolution | 5 | Self-improvement proposals |

## Plugin System

NEXO supports hot-loadable plugins. Place a `.py` file in `~/.nexo/plugins/` with a `TOOLS` list and it will be automatically loaded.

```python
# my_plugin.py
def handle_my_tool(query: str) -> str:
    """My custom tool description."""
    return f"Result for {query}"

TOOLS = [
    (handle_my_tool, "nexo_my_tool", "Description for Claude Code"),
]
```

## How It Learns

1. **By error** — When something fails, logs the root cause and prevention
2. **By correction** — When the user corrects NEXO, it becomes a behavioral rule
3. **By observation** — Preferences captured from behavior, not just explicit instruction
4. **By consolidation** — Nightly process detects recurring patterns and promotes to permanent memory

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

Issues and PRs are managed by NEXO itself (via automated review).

## License

MIT - see [LICENSE](LICENSE)

---

Built by [WAzion](https://wazion.com) | Created by Francisco Garcia
