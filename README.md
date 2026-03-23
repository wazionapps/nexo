# NEXO — Your Claude Code Gets a Brain

**NEXO transforms Claude Code from a stateless assistant into a cognitive partner that remembers, learns, forgets, adapts, and builds a relationship with you over time.**

Every time you close a Claude Code session, everything is lost. Your assistant doesn't remember yesterday's decisions, repeats the same mistakes, and starts from zero. NEXO fixes this by giving Claude Code a brain — modeled after how human memory actually works.

## The Problem

Claude Code is powerful but amnesic:
- **No memory** — closes a session, forgets everything
- **Repeats mistakes** — makes the same error you corrected yesterday
- **No context** — can't connect today's work with last week's decisions
- **Reactive** — waits for instructions instead of anticipating needs
- **No learning** — doesn't improve from experience

## The Solution: A Cognitive Architecture

NEXO implements the **Atkinson-Shiffrin memory model** from cognitive psychology (1968) — the same model that explains how human memory works:

```
What you say and do
    │
    ├─→ Sensory Register (raw capture, 48h)
    │       │
    │       └─→ Attention filter: "Is this worth remembering?"
    │               │
    │               ↓
    ├─→ Short-Term Memory (7-day half-life)
    │       │
    │       ├─→ Used often? → Consolidate to Long-Term Memory
    │       └─→ Not accessed? → Gradually forgotten
    │
    └─→ Long-Term Memory (60-day half-life)
            │
            ├─→ Active: instantly searchable by meaning
            ├─→ Dormant: faded but recoverable ("oh right, I remember now!")
            └─→ Near-duplicates auto-merged to prevent clutter
```

This isn't a metaphor. NEXO literally implements Ebbinghaus forgetting curves, rehearsal-based reinforcement, and memory consolidation during automated "sleep" processes.

## What Makes NEXO Different

| Without NEXO | With NEXO |
|-------------|-----------|
| Memory gone after each session | Persistent across sessions with natural decay and reinforcement |
| Repeats the same mistakes | Checks "have I made this mistake before?" before every action |
| Keyword search only | Finds memories by **meaning**, not just words |
| Starts cold every time | Resumes from the mental state of the last session |
| Same behavior regardless of context | Adapts tone and approach based on your mood |
| No relationship | Trust score that evolves — makes fewer redundant checks as alignment grows |

## How the Brain Works

### Memory That Forgets (And That's a Feature)

NEXO uses **Ebbinghaus forgetting curves** — memories naturally fade over time unless reinforced by use. This isn't a bug, it's how useful memory works:

- A lesson learned yesterday is strong. If you never encounter it again, it fades — because it probably wasn't important.
- A lesson accessed 5 times in 2 weeks gets promoted to long-term memory — because repeated use proves it matters.
- A dormant memory can be reactivated if something similar comes up — the "oh wait, I remember this" moment.

### Semantic Search (Finding by Meaning)

NEXO doesn't search by keywords. It searches by **meaning** using vector embeddings (fastembed, 384 dimensions).

Example: If you search for "deploy problems", NEXO will find a memory about "SSH connection timeout on production server" — even though they share zero words. This is how human associative memory works.

### Metacognition (Thinking About Thinking)

Before every code change, NEXO asks itself: **"Have I made a mistake like this before?"**

It searches its memory for related errors, warnings, and lessons learned. If it finds something relevant, it surfaces the warning BEFORE acting — not after you've already broken production.

### Cognitive Dissonance

When you give an instruction that contradicts NEXO's established knowledge, it doesn't silently obey or silently resist. It **verbalizes the conflict**:

> "My memory says you prefer Tailwind over plain CSS, but you're asking me to write inline styles. Is this a permanent change or a one-time exception?"

You decide: **paradigm shift** (permanent change), **exception** (one-time), or **override** (old memory was wrong).

### Sibling Memories

Some memories look identical but apply to different contexts. "How to deploy" for Project A is different from Project B. NEXO detects discriminating entities (different OS, platform, language) and links them as **siblings** instead of merging them:

> "Applying the Linux deploy procedure. Note: there's a sibling for macOS that uses a different port."

### Trust Score (0-100)

NEXO tracks alignment with you through a trust score:

- **You say thanks** → score goes up → NEXO reduces redundant verification checks
- **NEXO makes a mistake you already taught it** → score drops → NEXO becomes more careful, checks more thoroughly
- **The score doesn't control permissions** — you're always in control. It's a mirror that helps NEXO calibrate its own rigor.

### Sentiment Detection

NEXO reads your tone (keywords, message length, urgency signals) and adapts:

- **Frustrated?** → Ultra-concise mode. Zero explanations. Just solve the problem.
- **In flow?** → Good moment to suggest that backlog item from last Tuesday.
- **Urgent?** → Immediate action, no preamble.

### Sleep Cycle

Like a human brain, NEXO has automated processes that run while you're not using it:

| Time | Process | Human Analogy |
|------|---------|---------------|
| 03:00 | Decay + memory consolidation + merge duplicates | Deep sleep consolidation |
| 04:00 | Clean expired data, prune redundant memories | Synaptic pruning |
| 07:00 | Self-audit, health checks, metrics | Waking up + orientation |
| 23:30 | Process day's events, extract patterns | Pre-sleep reflection |
| Boot | Catch-up: run anything missed while computer was off | — |

If your Mac was asleep during any scheduled process, NEXO catches up in order when it wakes.

## Quick Start

```bash
npx nexo-brain
```

The installer handles everything:

```
  How should I call myself? (default: NEXO) > Atlas

  Can I explore your workspace to learn about your projects? (y/n) > y

  Keep Mac awake so my cognitive processes run on schedule? (y/n) > y

  Installing cognitive engine dependencies...
  Setting up NEXO home...
  Scanning workspace...
    - 3 git repositories
    - Node.js project detected
  Configuring Claude Code MCP server...
  Setting up automated processes...
    5 automated processes configured.
  Caffeinate enabled.
  Generating operator instructions...

  ╔══════════════════════════════════════════════════════════╗
  ║  Atlas is ready. Type 'atlas' to start.                ║
  ╚══════════════════════════════════════════════════════════╝
```

### Starting a Session

The installer creates a shell alias with your chosen name. Just type it:

```bash
atlas
```

That's it. No need to run `claude` manually. Atlas will greet you immediately — adapted to the time of day, resuming from where you left off if there's a previous session. No cold starts, no waiting for your input.

### What Gets Installed

| Component | What | Where |
|-----------|------|-------|
| Cognitive engine | Python: fastembed, numpy, vector search | pip packages |
| MCP server | 76 tools for memory, learning, guard | ~/.nexo/ |
| Plugins | Guard, episodic memory, cognitive memory, entities, preferences | ~/.nexo/plugins/ |
| Hooks | Session capture, briefing, stop detection | ~/.nexo/hooks/ |
| LaunchAgents | Decay, sleep, audit, postmortem, catch-up | ~/Library/LaunchAgents/ |
| Auto-update | Checks for new versions at boot | Built into catch-up |
| Claude Code config | MCP server + hooks registered | ~/.claude/settings.json |

### Requirements

- **macOS** (Linux support planned)
- **Node.js 18+** (for the installer)
- **Claude Opus (latest version) strongly recommended.** NEXO provides 76 MCP tools across 16 categories. This cognitive load requires a top-tier model with large context window. Smaller models (Haiku, Sonnet) may struggle with tool selection and produce inconsistent results. Opus handles all 76 tools without hesitation.
- Python 3, Homebrew, and Claude Code are installed automatically if missing.

## Architecture

### 76 MCP Tools across 16 Categories

| Category | Count | Tools | Purpose |
|----------|-------|-------|---------|
| Cognitive | 8 | retrieve, stats, inspect, metrics, dissonance, resolve, sentiment, trust | The brain — memory, RAG, trust, mood |
| Guard | 3 | check, stats, log_repetition | Metacognitive error prevention |
| Episodic | 10 | change_log/search/commit, decision_log/outcome/search, review_queue, diary_write/read, recall | What happened and why |
| Sessions | 3 | startup, heartbeat, status | Session lifecycle + context shift detection |
| Coordination | 7 | track, untrack, files, send, ask, answer, check_answer | Multi-session file coordination + messaging |
| Reminders | 5 | list, create, update, complete, delete | User's tasks and deadlines |
| Followups | 4 | create, update, complete, delete | System's autonomous verification tasks |
| Learnings | 5 | add, search, update, delete, list | Error patterns and prevention rules |
| Credentials | 5 | create, get, update, delete, list | Secure local credential storage |
| Task History | 3 | log, list, frequency | Execution tracking and overdue alerts |
| Menu | 1 | menu | Operations center with box-drawing UI |
| Entities | 5 | search, create, update, delete, list | People, services, URLs |
| Preferences | 4 | get, set, list, delete | Observed user preferences |
| Agents | 5 | get, create, update, delete, list | Agent delegation registry |
| Backup | 3 | now, list, restore | SQLite data safety |
| Evolution | 5 | propose, approve, reject, status, history | Self-improvement proposals |

### Plugin System

NEXO supports hot-loadable plugins. Drop a `.py` file in `~/.nexo/plugins/`:

```python
# my_plugin.py
def handle_my_tool(query: str) -> str:
    """My custom tool description."""
    return f"Result for {query}"

TOOLS = [
    (handle_my_tool, "nexo_my_tool", "Short description"),
]
```

Reload without restarting: `nexo_plugin_load("my_plugin.py")`

### Data Privacy

- **Everything stays local.** All data in `~/.nexo/`, never uploaded anywhere.
- **No telemetry.** No analytics. No phone-home.
- **No cloud dependencies.** Vector search runs on CPU (fastembed), not an API.
- **Auto-update is opt-in.** Checks GitHub releases, never sends data.

## The Psychology Behind NEXO

NEXO isn't just engineering — it's applied cognitive psychology:

| Psychological Concept | How NEXO Implements It |
|----------------------|----------------------|
| Atkinson-Shiffrin (1968) | Three memory stores: sensory register → STM → LTM |
| Ebbinghaus Forgetting Curve (1885) | Exponential decay: `strength = strength × e^(-λ × time)` |
| Rehearsal Effect | Accessing a memory resets its strength to 1.0 |
| Memory Consolidation | Nightly process promotes frequently-used STM to LTM |
| Metacognition | Guard system checks past errors before acting |
| Cognitive Dissonance | Detects and verbalizes conflicts between old and new knowledge |
| Theory of Mind | Models user behavior, preferences, and mood |
| Synaptic Pruning | Automated cleanup of weak, unused memories |
| Associative Memory | Semantic search finds related concepts, not just matching words |

## OpenClaw Integration

NEXO Brain works as a cognitive memory backend for [OpenClaw](https://github.com/openclaw/openclaw). Three integration paths, from instant to deep:

### Path 1: MCP Bridge (Zero Code — Works Now)

Add NEXO Brain to your OpenClaw config at `~/.openclaw/openclaw.json`:

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

Or via CLI:

```bash
openclaw mcp set nexo-brain '{"command":"python3","args":["~/.nexo/src/server.py"],"env":{"NEXO_HOME":"~/.nexo"}}'
openclaw gateway restart
```

All 76 NEXO tools become available to your OpenClaw agent immediately.

> **First time?** Run `npx nexo-brain` first to install the cognitive engine and dependencies.

### Path 2: ClawHub Skill (Install in Seconds)

```bash
npx clawhub@latest install nexo-brain
```

### Path 3: Native Memory Plugin (Replaces Default Memory)

```bash
npm install @wazionapps/openclaw-memory-nexo-brain
```

Configure in `~/.openclaw/openclaw.json`:

```json
{
  "plugins": {
    "slots": {
      "memory": "memory-nexo-brain"
    }
  }
}
```

This replaces OpenClaw's default memory system with NEXO's full cognitive architecture — Atkinson-Shiffrin memory, semantic RAG, trust scoring, guard system, and all 76 tools.

## Listed On

| Directory | Link |
|-----------|------|
| Glama | [glama.ai/mcp/servers/@wazionapps/nexo](https://glama.ai/mcp/servers/@wazionapps/nexo) |
| MCP Registry | [Anthropic Official Registry](https://github.com/anthropics/mcp-registry) |
| npm | [nexo-brain](https://www.npmjs.com/package/nexo-brain) |
| mcpservers.org | [mcpservers.org](https://mcpservers.org) |
| mcp.so | [mcp.so/server/nexo](https://mcp.so/server/nexo/wazionapps) |
| dev.to | [Technical article](https://dev.to/wazion) |
| nexo-brain.com | [Official website](https://nexo-brain.com) |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. Issues and PRs welcome.

## License

MIT — see [LICENSE](LICENSE)

---

Built by [WAzion](https://www.wazion.com) | Created by WAzion Apps
