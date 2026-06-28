# @wazionapps/openclaw-memory-nexo-brain

Native OpenClaw memory plugin powered by the [NEXO Desktop runtime core](https://nexo-desktop.com).

## What It Does

Replaces OpenClaw's default memory with NEXO Desktop runtime services:

- **Local Memory** - semantic recall across working and long-term memory
- **Deep Sleep** - overnight consolidation, cleanup, and memory maintenance
- **Trust Scoring** - calibrates verification rigor based on alignment history
- **Guard System** - checks past errors before code changes
- **Conflict Detection** - detects conflicts between new instructions and established knowledge
- **Session Continuity** - resumes from where the last session left off via session diaries

## Install

```bash
# 1. Install the NEXO Desktop runtime core compatibility package
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
| `memory_recall` | Semantic search across all memories (STM + LTM) |
| `memory_store` | Store error patterns and lessons learned |
| `memory_forget` | Archive or delete outdated memories (GDPR-compliant) |
| `memory_pin` | Pin a memory so it never decays |
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
openclaw nexo status     # Show cognitive memory statistics
openclaw nexo recall "deployment issues"  # Semantic search
openclaw nexo guard --area shopify        # Check past errors
openclaw nexo trust                       # Show trust score
```

## Architecture

```
OpenClaw Agent
    ↓ (tool calls)
TypeScript Adapter (this plugin)
    ↓ (JSON-RPC over stdio)
Python MCP Server (NEXO runtime core)
    ↓
SQLite (nexo.db + cognitive.db)
    ↓
fastembed vectors (BAAI/bge-small-en-v1.5, CPU)
```

## Requirements

- macOS (Linux planned)
- Python 3 with fastembed
- OpenClaw >= 2026.3.0
- Run `npx nexo-brain` first to install the runtime core

## License

AGPL-3.0 -- see [LICENSE](../LICENSE)
