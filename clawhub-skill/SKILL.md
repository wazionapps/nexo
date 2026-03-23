---
name: nexo-brain
description: Cognitive memory system for AI agents — Atkinson-Shiffrin memory model, semantic RAG, trust scoring, and metacognitive error prevention. Gives your agent persistent memory that learns, forgets, and adapts.
version: 1.0.0
metadata:
  openclaw:
    requires:
      bins:
        - python3
    emoji: "\U0001F9E0"
    homepage: https://github.com/wazionapps/nexo
    os: ["macos"]
---

# NEXO Brain — Cognitive Memory for Your Agent

NEXO Brain gives your agent persistent memory modeled after human cognition. It remembers across sessions, learns from mistakes, naturally forgets what's irrelevant, and builds a trust-based relationship with you.

## Setup

First, install the cognitive engine:

```bash
npx nexo-brain
```

Then add the MCP server to your OpenClaw config (`~/.openclaw/openclaw.json`):

```json
{
  "mcp": {
    "servers": {
      "nexo-brain": {
        "command": "python3",
        "args": ["~/.nexo/src/server.py"],
        "env": {
          "NEXO_HOME": "~/.nexo"
        }
      }
    }
  }
}
```

Restart the gateway: `openclaw gateway restart`

## What You Get

76 MCP tools across 16 categories:

- **Cognitive Memory** — RAG-powered semantic search, trust scoring, sentiment detection, cognitive dissonance resolution
- **Guard System** — Checks "have I made this mistake before?" before every code change
- **Episodic Memory** — Change logs, decision logs with reasoning, session diaries for continuity
- **Learnings** — Error patterns and prevention rules, searchable by category
- **Session Management** — Startup, heartbeat, multi-session coordination
- **Reminders & Followups** — Track user tasks and system verification tasks separately
- **Entities & Preferences** — Remember people, services, URLs, and observed user preferences
- **Backup & Evolution** — SQLite backup with retention, self-improvement proposals

## How Memory Works

NEXO implements the Atkinson-Shiffrin memory model (1968):

1. **Sensory Register** — Raw capture, 48h retention
2. **Short-Term Memory** — 7-day half-life, promoted if used frequently
3. **Long-Term Memory** — 60-day half-life, semantic search by meaning

Memories naturally decay via Ebbinghaus forgetting curves. Accessing a memory reinforces it. Automated "sleep cycles" consolidate, prune, and merge memories.

## Key Tools

| Tool | When to Use |
|------|------------|
| `nexo_startup` | Once at session start — registers session, returns active sessions |
| `nexo_heartbeat` | Every interaction — updates task, checks inbox |
| `nexo_cognitive_retrieve` | Semantic search across all memories |
| `nexo_guard_check` | Before editing code — checks for past errors |
| `nexo_learning_add` | After resolving an error — prevents recurrence |
| `nexo_session_diary_write` | Before closing session — enables continuity |
| `nexo_cognitive_trust` | After user feedback — calibrates rigor level |

## Privacy

Everything stays local. Two SQLite databases in `~/.nexo/`. No telemetry, no cloud APIs. Vector search runs on CPU via fastembed.

## More Info

- [GitHub](https://github.com/wazionapps/nexo)
- [npm](https://www.npmjs.com/package/nexo-brain)
