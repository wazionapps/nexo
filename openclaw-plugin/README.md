# @wazionapps/openclaw-memory-nexo-brain

Native OpenClaw memory plugin powered by [NEXO Brain](https://github.com/wazionapps/nexo) — a cognitive memory system modeled after human cognition.

## What It Does

Replaces OpenClaw's default memory with a full cognitive architecture:

- **Atkinson-Shiffrin Memory Model** — Sensory register → Short-term → Long-term, with natural decay
- **Semantic RAG** — Finds memories by meaning, not keywords (fastembed, 384-dim vectors)
- **Trust Scoring** — Calibrates verification rigor based on alignment history
- **Guard System** — Checks past errors before every code change
- **Cognitive Dissonance** — Detects conflicts between new instructions and established knowledge
- **Session Continuity** — Resumes from where the last session left off via session diaries

## Install

```bash
# 1. Install NEXO Brain cognitive engine
npx nexo-brain

# 2. Install the OpenClaw plugin
npm install @wazionapps/openclaw-memory-nexo-brain
```

## Configure

In `~/.openclaw/openclaw.json`:

```json
{
  "plugins": {
    "slots": {
      "memory": "memory-nexo-brain"
    },
    "memory-nexo-brain": {
      "nexoHome": "~/.nexo",
      "autoRecall": true,
      "autoCapture": true,
      "guardEnabled": true,
      "trustScoring": true
    }
  }
}
```

## Tools Provided

| Tool | Description |
|------|------------|
| `memory_recall` | Semantic search across all memories |
| `memory_store` | Store error patterns and lessons learned |
| `memory_guard` | Check past errors before editing code |
| `memory_trust` | Update trust score based on user feedback |
| `memory_dissonance` | Detect conflicts with established knowledge |
| `memory_sentiment` | Analyze user's emotional state |
| `memory_diary_write` | Write session summary for continuity |
| `memory_diary_read` | Read recent diaries to resume context |
| `memory_startup` | Register a new session |
| `memory_heartbeat` | Update session task, check inbox |

## CLI Commands

```bash
openclaw nexo-status    # Show cognitive memory statistics
openclaw nexo-recall "deployment issues"  # Semantic search
```

## Architecture

```
OpenClaw Agent
    ↓ (tool calls)
TypeScript Adapter (this plugin)
    ↓ (JSON-RPC over stdio)
Python MCP Server (NEXO Brain)
    ↓
SQLite (nexo.db + cognitive.db)
    ↓
fastembed vectors (BAAI/bge-small-en-v1.5, CPU)
```

## Requirements

- macOS (Linux planned)
- Python 3 with fastembed
- OpenClaw >= 2026.3.22
- Run `npx nexo-brain` first to install the cognitive engine

## License

MIT
