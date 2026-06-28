---
name: nexo-brain
description: Runtime core for NEXO Desktop. Provides local memory, Deep Sleep, Evolution support-ticket mode, skills, watchdog, and MCP tools for the desktop product.
version: 7.38.7
metadata:
  openclaw:
    requires:
      bins:
        - python3
    emoji: "N"
    homepage: https://nexo-desktop.com
    os:
      - darwin
      - linux
    install:
      - id: npm
        kind: node
        package: nexo-brain
        bins:
          - nexo
          - nexo-brain
        label: Install NEXO Desktop runtime core (npm)
---

# NEXO Desktop Runtime Core

This skill installs the runtime core used by NEXO Desktop. It provides local memory, Deep Sleep, Evolution support-ticket mode, skills, watchdog, followups, and MCP tools through the existing compatibility package.

## Setup

If your OpenClaw client shows an install action for this skill, use that first. It installs the compatibility package used by the NEXO Desktop runtime via your configured Node package manager.

If you are setting it up manually, install the runtime core:

```bash
npx nexo-brain
```

After the runtime is installed, add the MCP server to your OpenClaw config (`~/.openclaw/openclaw.json`):

```json
{
  "mcp": {
    "servers": {
      "nexo-brain": {
        "command": "python3",
        "args": ["~/.nexo/server.py"],
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

Key runtime capabilities include:

- **Local Memory** - semantic recall, trust scoring, sentiment detection, and continuity
- **Guard System** - checks past mistakes before code changes
- **Deep Sleep** - overnight consolidation, cleanup, and memory maintenance
- **Learnings** - error patterns and prevention rules, searchable by category
- **Session Management** - startup, heartbeat, and multi-session coordination
- **Reminders & Followups** - tracks user tasks and system verification tasks separately
- **Entities & Preferences** - remembers people, services, URLs, and observed user preferences
- **Watchdog** - local reliability checks and recovery signals

## How Memory Works

NEXO keeps local working memory and long-term memory on device:

1. **Recent context** - short-lived session and task state
2. **Working memory** - active preferences, entities, and decisions
3. **Long-term memory** - semantic search by meaning with retention controls

Deep Sleep consolidates, prunes, and merges memory so the runtime stays useful without sending private examples to public services.

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

Everything stays local by default in `~/.nexo/`. Support and improvement tickets are anonymized: personal names, client data, examples, and private content are not sent.

## More Info

- [GitHub](https://github.com/wazionapps/nexo)
- [npm](https://www.npmjs.com/package/nexo-brain)
