# NEXO Brain — Your AI Gets a Brain

[![npm v1.0.0](https://img.shields.io/npm/v/nexo-brain?label=npm&color=purple)](https://www.npmjs.com/package/nexo-brain)
[![F1 0.588 on LoCoMo](https://img.shields.io/badge/LoCoMo_F1-0.588-brightgreen)](https://github.com/wazionapps/nexo/blob/main/benchmarks/locomo/results/)
[![+55% vs GPT-4](https://img.shields.io/badge/vs_GPT--4-%2B55%25-blue)](https://github.com/snap-research/locomo/issues/33)
[![GitHub stars](https://img.shields.io/github/stars/wazionapps/nexo?style=social)](https://github.com/wazionapps/nexo/stargazers)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> **v1.0.0** — Cognitive Cortex, 30 Core Rules as DNA, Smart Startup, Context Packets, Auto-Prime. The first AI memory system with architectural inhibitory control — the agent reasons about whether to act before acting. Battle-tested from 6 months of production use, validated via multi-AI debate (Claude Opus + GPT-5.4 + Gemini 3.1 Pro).

**NEXO Brain transforms any MCP-compatible AI agent from a stateless assistant into a cognitive partner that remembers, learns, forgets, adapts, and builds a relationship with you over time.**

<p align="center">
  <a href="https://www.youtube.com/watch?v=J0hCWnYU4UY">
    <img src="assets/nexo-brain-infographic-v4.png" alt="NEXO Brain Architecture" width="700">
  </a>
</p>

[Watch the 1-minute overview on YouTube](https://www.youtube.com/watch?v=J0hCWnYU4UY) · [Watch the full deep-dive](https://www.youtube.com/watch?v=-uvhicUhGTY)

Every time you close a session, everything is lost. Your agent doesn't remember yesterday's decisions, repeats the same mistakes, and starts from zero. NEXO Brain fixes this with a cognitive architecture modeled after how human memory actually works.

## The Problem

AI coding agents are powerful but amnesic:
- **No memory** — closes a session, forgets everything
- **Repeats mistakes** — makes the same error you corrected yesterday
- **No context** — can't connect today's work with last week's decisions
- **Reactive** — waits for instructions instead of anticipating needs
- **No learning** — doesn't improve from experience
- **No safety** — stores anything it's told, including poisoned or redundant data

## The Solution: A Cognitive Architecture

NEXO Brain implements the **Atkinson-Shiffrin memory model** from cognitive psychology (1968) — the same model that explains how human memory works:

```
What you say and do
    |
    +---> Sensory Register (raw capture, 48h)
    |       |
    |       +---> Attention filter: "Is this worth remembering?"
    |               |
    |               v
    +---> Short-Term Memory (7-day half-life)
    |       |
    |       +---> Used often? --> Consolidate to Long-Term Memory
    |       +---> Not accessed? --> Gradually forgotten
    |
    +---> Long-Term Memory (60-day half-life)
            |
            +---> Active: instantly searchable by meaning
            +---> Dormant: faded but recoverable ("oh right, I remember now!")
            +---> Near-duplicates auto-merged to prevent clutter
```

This isn't a metaphor. NEXO Brain literally implements Ebbinghaus forgetting curves, rehearsal-based reinforcement, and memory consolidation during automated "sleep" processes.

## What Makes NEXO Brain Different

| Without NEXO Brain | With NEXO Brain |
|---------------------|-----------------|
| Memory gone after each session | Persistent across sessions with natural decay and reinforcement |
| Repeats the same mistakes | Checks "have I made this mistake before?" before every action |
| Keyword search only | Finds memories by **meaning**, not just words |
| Starts cold every time | Resumes from the mental state of the last session |
| Same behavior regardless of context | Adapts tone and approach based on your mood |
| No relationship | Trust score that evolves — makes fewer redundant checks as alignment grows |
| Stores everything blindly | Prediction error gating rejects redundant information at write time |
| Vulnerable to memory poisoning | 4-layer security pipeline scans every memory before storage |
| No proactive behavior | Context-triggered reminders fire when topics match, not just by date |

## How the Brain Works

### Memory That Forgets (And That's a Feature)

NEXO Brain uses **Ebbinghaus forgetting curves** — memories naturally fade over time unless reinforced by use. This isn't a bug, it's how useful memory works:

- A lesson learned yesterday is strong. If you never encounter it again, it fades — because it probably wasn't important.
- A lesson accessed 5 times in 2 weeks gets promoted to long-term memory — because repeated use proves it matters.
- A dormant memory can be reactivated if something similar comes up — the "oh wait, I remember this" moment.

### Semantic Search (Finding by Meaning)

NEXO Brain doesn't search by keywords. It searches by **meaning** using vector embeddings (fastembed, 768 dimensions).

Example: If you search for "deploy problems", NEXO Brain will find a memory about "SSH connection timeout on production server" — even though they share zero words. This is how human associative memory works.

### Metacognition (Thinking About Thinking)

Before every code change, NEXO Brain asks itself: **"Have I made a mistake like this before?"**

It searches its memory for related errors, warnings, and lessons learned. If it finds something relevant, it surfaces the warning BEFORE acting — not after you've already broken production.

### Cognitive Dissonance

When you give an instruction that contradicts established knowledge, NEXO Brain doesn't silently obey or silently resist. It **verbalizes the conflict**:

> "My memory says you prefer Tailwind over plain CSS, but you're asking me to write inline styles. Is this a permanent change or a one-time exception?"

You decide: **paradigm shift** (permanent change), **exception** (one-time), or **override** (old memory was wrong).

### Sibling Memories

Some memories look identical but apply to different contexts. "How to deploy" for Project A is different from Project B. NEXO Brain detects discriminating entities (different OS, platform, language) and links them as **siblings** instead of merging them:

> "Applying the Linux deploy procedure. Note: there's a sibling for macOS that uses a different port."

### Trust Score (0-100)

NEXO Brain tracks alignment with you through a trust score:

- **You say thanks** --> score goes up --> reduces redundant verification checks
- **Makes a mistake you already taught it** --> score drops --> becomes more careful, checks more thoroughly
- **The score doesn't control permissions** — you're always in control. It's a mirror that helps calibrate rigor.

### Sentiment Detection

NEXO Brain reads your tone (keywords, message length, urgency signals) and adapts:

- **Frustrated?** --> Ultra-concise mode. Zero explanations. Just solve the problem.
- **In flow?** --> Good moment to suggest that backlog item from last Tuesday.
- **Urgent?** --> Immediate action, no preamble.

### Sleep Cycle

Like a human brain, NEXO Brain has automated processes that run while you're not using it:

| Time | Process | Human Analogy |
|------|---------|---------------|
| 03:00 | Decay + memory consolidation + merge duplicates + dreaming | Deep sleep consolidation |
| 04:00 | Clean expired data, prune redundant memories | Synaptic pruning |
| 07:00 | Self-audit, health checks, metrics | Waking up + orientation |
| 23:30 | Process day's events, extract patterns | Pre-sleep reflection |
| Boot | Catch-up: run anything missed while computer was off | -- |

If your Mac was asleep during any scheduled process, NEXO Brain catches up in order when it wakes.

## Cognitive Cortex (v1.0.0)

The Cortex is a middleware cognitive layer that makes the agent **think before acting**. It implements architectural inhibitory control — the agent cannot bypass reasoning.

```
User message → Fast Path check → Simple chat? → Respond directly
                                → Action needed? → Cortex activates
                                                    ↓
                                              Generate cognitive state
                                              (goal, plan, unknowns, evidence)
                                                    ↓
                                              Middleware validates
                                              ├─ Unknowns? → ASK mode (tools blocked)
                                              ├─ No plan? → PROPOSE mode (read-only)
                                              └─ Plan + evidence → ACT mode (full access)
```

| Feature | What It Does |
|---------|-------------|
| **Inhibitory Control** | Physically restricts tools based on reasoning quality. Unknowns → can only ask. No plan → can only propose. Evidence + verification → can act. |
| **Event-Driven Activation** | Only activates on tool intent, ambiguity, destructive actions, or retries. Simple chat has zero overhead. |
| **Trust-Gated Escalation** | Low trust score → requires more evidence before allowing "act" mode. Trust builds through successful execution. |
| **Core Rules Injection** | Automatically surfaces relevant behavioral rules based on task type. |
| **Activation Metrics** | Tracks modes, inhibition rates, and task types for continuous improvement. |

The Cortex was designed through a 3-way AI debate (Claude Opus 4.6 + GPT-5.4 + Gemini 3.1 Pro) and validated against 6 months of real production failures.

## Cognitive Features

NEXO Brain provides 29 cognitive tools on top of the 76 base tools, totaling **113+ MCP tools**. These features implement cognitive science concepts that go beyond basic memory:

### Input Pipeline

| Feature | What It Does |
|---------|-------------|
| **Prediction Error Gating** | Only novel information is stored. Redundant content that matches existing memories is rejected at write time, keeping your memory clean without manual curation. |
| **Security Pipeline** | 4-layer defense against memory poisoning: injection detection, encoding analysis, behavioral anomaly scoring, and credential scanning. Every memory passes through all four layers before storage. |
| **Quarantine Queue** | New facts enter quarantine status and must pass a promotion policy before becoming trusted knowledge. Prevents unverified information from influencing decisions. Automated nightly processing promotes, rejects, or expires items. |
| **Secret Redaction** | Auto-detects and redacts API keys, tokens, passwords, and other sensitive data before storage. Secrets never reach the vector database. |

### Memory Management

| Feature | What It Does |
|---------|-------------|
| **Pin / Snooze / Archive** | Granular lifecycle states for memories. Pin = never decays (critical knowledge). Snooze = temporarily hidden (revisit later). Archive = cold storage (searchable but inactive). |
| **Intelligent Chunking** | Adaptive chunking that respects sentence and paragraph boundaries. Produces semantically coherent chunks instead of arbitrary token splits, reducing retrieval noise. |
| **Adaptive Decay** | Decay rate adapts per memory based on access patterns: frequently-accessed memories decay slower, rarely-accessed ones fade faster. Prevents permanent clutter while keeping active knowledge sharp. |
| **Auto-Migration** | Formal schema migration system (schema_migrations table) tracks all database changes. Safe, reversible schema evolution for production systems — upgrades never lose data. |
| **Auto-Merge Duplicates** | Batch cosine deduplication during the 03:00 sleep cycle. Respects sibling discrimination — similar memories about different contexts are kept separate. |
| **Memory Dreaming** | Discovers hidden connections between recent memories during the 03:00 sleep cycle. Surfaces non-obvious patterns like "these three bugs all relate to the same root cause." |

### Retrieval

| Feature | What It Does |
|---------|-------------|
| **HyDE Query Expansion** | Generates hypothetical answer embeddings for richer semantic search. Instead of searching for "deploy error", it imagines what a helpful memory about deploy errors would look like, then searches for that. |
| **Hybrid Search (FTS5+BM25+RRF)** | Combines dense vector search with BM25 keyword search via Reciprocal Rank Fusion. Outperforms pure semantic search on precise terminology and code identifiers. |
| **Cross-Encoder Reranking** | After initial vector retrieval, a cross-encoder model rescores candidates for precision. The top-k results are reordered by true semantic relevance before being returned to the agent. |
| **Multi-Query Decomposition** | Complex questions are automatically split into sub-queries. Each component is retrieved independently, then fused for a higher-quality answer — improves recall on multi-faceted prompts. |
| **Temporal Indexing** | Memories are indexed by time in addition to semantics. Time-sensitive queries ("what did we decide last Tuesday?") use temporal proximity scoring alongside semantic similarity. |
| **Spreading Activation** | Graph-based co-activation network. Memories retrieved together reinforce each other's connections, building an associative web that improves over time. |
| **Recall Explanations** | Transparent score breakdown for every retrieval result. Shows exactly why a memory was returned: semantic similarity, recency, access frequency, and co-activation bonuses. |

### Proactive

| Feature | What It Does |
|---------|-------------|
| **Prospective Memory** | Context-triggered reminders that fire when conversation topics match, not just by date. "Remind me about X when we discuss Y" works naturally. |
| **Hook Auto-capture** | Extracts decisions, corrections, and factual statements from conversations automatically. You don't need to explicitly say "remember this" — the system detects what's worth storing. |
| **Session Summaries** | Automatic end-of-session summarization that distills key decisions, errors, and follow-ups into a compact diary entry. The next session starts with full context — not a cold slate. |
| **Smart Startup** | Pre-loads relevant cognitive memories at session boot by composing a query from pending followups, due reminders, and last session's topics. Every session starts with the right context — not a cold search. |
| **Context Packets** | Bundles all area knowledge (learnings, recent changes, active followups, preferences, cognitive memories) into a single injectable packet for subagent delegation. Subagents never start blind again. |
| **Auto-Prime by Topic** | Heartbeat detects project/area keywords in conversation and automatically surfaces the most relevant learnings. No explicit memory query needed — context arrives proactively. |

## Benchmark: LoCoMo (ACL 2024)

NEXO Brain was evaluated on [LoCoMo](https://github.com/snap-research/locomo) (ACL 2024), a long-term conversation memory benchmark with 1,986 questions across 10 multi-session conversations.

| System | F1 | Adversarial | Hardware |
|---|---|---|---|
| **NEXO Brain v0.5.0** | **0.588** | **93.3%** | **CPU only** |
| GPT-4 (128K full context) | 0.379 | — | GPU cloud |
| Gemini Pro 1.0 | 0.313 | — | GPU cloud |
| LLaMA-3 70B | 0.295 | — | A100 GPU |
| GPT-3.5 + Contriever RAG | 0.283 | — | GPU |

**+55% vs GPT-4. Running entirely on CPU.**

**Key findings:**
- Outperforms GPT-4 (128K full context) by 55% on F1 score
- 93.3% adversarial rejection rate — reliably says "I don't know" when information isn't available
- 74.9% recall across 1,986 questions
- Open-domain F1: 0.637 | Multi-hop F1: 0.333 | Temporal F1: 0.326
- Runs on CPU with 768-dim embeddings (BAAI/bge-base-en-v1.5) — no GPU required
- First MCP memory server benchmarked on a peer-reviewed dataset

Full results in [`benchmarks/locomo/results/`](benchmarks/locomo/results/).

## Full Orchestration System (v0.7.0)

Memory alone doesn't make a co-operator. What makes the difference is the **behavioral loop** — the automated discipline that ensures every session starts informed, runs with guardrails, and ends with self-reflection.

### 5 Automated Hooks

These fire automatically at key moments in every Claude Code session:

| Hook | When | What It Does |
|------|------|-------------|
| **SessionStart** | Session opens | Generates a briefing from SQLite: overdue reminders, today's tasks, pending followups, active sessions |
| **Stop** | Session ends | Mandatory post-mortem: self-critique (5 questions), session buffer entry, followup creation, proactive seeds for next session |
| **PostToolUse** | After each tool call | Captures meaningful mutations to the Sensory Register |
| **PreCompact** | Before context compression | Saves checkpoint, reminds operator to write diary — prevents losing the thread |
| **Caffeinate** | Always (optional) | Keeps Mac awake for nocturnal cognitive processes |

### The Session Lifecycle

```
Session starts
    ↓
SessionStart hook generates briefing
    ↓
Operator reads diary, reminders, followups
    ↓
Heartbeat on every interaction (sentiment, context shifts)
    ↓
Guard check before every code edit
    ↓
PreCompact hook saves context if conversation is compressed
    ↓
Stop hook triggers mandatory post-mortem:
  - Self-critique: 5 questions about what could be better
  - Session buffer: structured entry for the reflection engine
  - Followups: anything promised gets scheduled
  - Proactive seeds: what can the next session do without being asked?
    ↓
Reflection engine processes buffer (after 3+ sessions)
    ↓
Nocturnal processes: decay, consolidation, self-audit, dreaming
```

### Reflection Engine

After 3+ sessions accumulate, the stop hook triggers `nexo-reflection.py`:
- Extracts recurring tasks, error patterns, mood trends
- Updates `user_model.json` with observed behavior
- No LLM required — runs as pure Python

### Auto-Migration

Existing users upgrading from v0.5.0:
```bash
npx nexo-brain  # detects v0.5.0, migrates automatically
```
- Updates hooks, core files, plugins, scripts
- **Never touches your data** (memories, learnings, preferences)
- Saves updated CLAUDE.md as reference (doesn't overwrite customizations)

## Knowledge Graph & Dashboard (v0.8)

### Knowledge Graph
A bi-temporal entity-relationship graph with 988 nodes and 896 edges. Entities and relationships carry both valid-time (when the fact was true) and system-time (when it was recorded), enabling temporal queries like "what did we know about X last Tuesday?". BFS traversal discovers multi-hop connections between concepts. Event-sourced edges with smart dedup (ADD/UPDATE/NOOP) prevent redundant writes while preserving full history.

4 new MCP tools: `nexo_kg_query` (SPARQL-like queries), `nexo_kg_path` (shortest path between entities), `nexo_kg_neighbors` (direct connections), `nexo_kg_stats` (graph metrics).

### Web Dashboard
A visual interface at `localhost:6174` with 6 pages: Overview (system health at a glance), Graph (interactive D3.js visualization of the knowledge graph), Memory (browse and search all memory stores), Somatic (pain map per file/area), Adaptive (personality signals and weights), and Sessions (active and historical sessions). Built with FastAPI backend and D3.js frontend.

### Cross-Platform Support
Full Linux support and Windows via WSL. The installer detects the platform and configures the appropriate process manager (LaunchAgents on macOS, catch-up on startup for Linux). PEP 668 compliance (venv on Ubuntu 24.04+). Session keepalive prevents phantom sessions during long tasks. Opportunistic maintenance runs cognitive processes when resources are available.

> **Windows users:** NEXO Brain requires [WSL (Windows Subsystem for Linux)](https://learn.microsoft.com/en-us/windows/wsl/install). Install WSL first, then run `npx nexo-brain` inside the Ubuntu/WSL terminal.

### Storage Router
A new abstraction layer routes storage operations through a unified interface, making the system multi-tenant ready. Each operator's data is isolated while sharing the same cognitive engine.

## Learned Weights & Somatic Markers (v0.7.0)

### Adaptive Learned Weights
Signal weights learn from real user feedback via Ridge regression. A 2-week shadow mode observes before activating. Weight momentum (85/15 blend) prevents personality whiplash. Automatic rollback if correction rate doubles.

### Somatic Markers (Pain Memory)
Files and areas that cause repeated errors accumulate a risk score (0.0–1.0). The guard system warns on HIGH RISK (>0.5) and CRITICAL RISK (>0.8), lowering thresholds for more paranoid checking. Clean guard checks reduce risk multiplicatively (×0.7). Nightly decay (×0.95) ensures old pain fades.

### Adaptive Personality v2
6 weighted signals: vibe, corrections, brevity, topic, tool errors, git diff. Emergency keywords bypass hysteresis. Severity-weighted decay. Manual override via `nexo_adaptive_override`.

## Quick Start

### Claude Code (Primary)

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
  Configuring MCP server...
  Setting up automated processes...
    5 automated processes configured.
  Caffeinate enabled.
  Generating operator instructions...

  +----------------------------------------------------------+
  |  Atlas is ready. Type 'atlas' to start.                  |
  +----------------------------------------------------------+
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
| MCP server | 109+ tools for memory, cognition, learning, guard | ~/.nexo/ |
| Plugins | Guard, episodic memory, cognitive memory, entities, preferences | ~/.nexo/plugins/ |
| Hooks (5) | SessionStart briefing, Stop post-mortem, PostToolUse capture, PreCompact checkpoint, Caffeinate | ~/.nexo/hooks/ |
| Reflection engine | Processes session buffer, extracts patterns, updates user model | ~/.nexo/scripts/ |
| CLAUDE.md | Complete operator instructions (Codex, hooks, guard, trust, memory) | ~/.claude/CLAUDE.md |
| LaunchAgents | Decay, sleep, audit, postmortem, catch-up | ~/Library/LaunchAgents/ |
| Auto-update | Checks for new versions at boot | Built into catch-up |
| Claude Code config | MCP server + 5 hooks registered | ~/.claude/settings.json |

### Requirements

- **macOS or Linux** (Windows via [WSL](https://learn.microsoft.com/en-us/windows/wsl/install))
- **Node.js 18+** (for the installer)
- **Claude Opus (latest version) strongly recommended.** NEXO Brain provides 109+ MCP tools across 19 categories. This cognitive load requires a top-tier model with large context window. Smaller models (Haiku, Sonnet) may struggle with tool selection and produce inconsistent results. Opus handles all 109+ tools without hesitation.
- Python 3, Homebrew, and Claude Code are installed automatically if missing.

## Architecture

### 109+ MCP Tools across 19 Categories

| Category | Count | Tools | Purpose |
|----------|-------|-------|---------|
| Cognitive | 8 | retrieve, stats, inspect, metrics, dissonance, resolve, sentiment, trust | The brain — memory, RAG, trust, mood |
| Cognitive Input | 5 | prediction_gate, security_scan, quarantine, promote, redact | Input pipeline — gating, security, quarantine |
| Cognitive Advanced | 8 | hyde_search, spread_activate, explain_recall, dream, prospect, hook_capture, pin, archive | Advanced retrieval, proactive, lifecycle |
| Guard | 3 | check, stats, log_repetition | Metacognitive error prevention |
| Episodic | 10 | change_log/search/commit, decision_log/outcome/search, review_queue, diary_write/read, recall | What happened and why |
| Sessions | 4 | startup, heartbeat, stop, status | Session lifecycle + context shift detection |
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
| Adaptive & Somatic | 4 | adaptive_weights, adaptive_override, somatic_check, somatic_stats | Learned signal weights + pain memory per file |
| Knowledge Graph | 4 | kg_query, kg_path, kg_neighbors, kg_stats | Bi-temporal entity-relationship graph |

### Plugin System

NEXO Brain supports hot-loadable plugins. Drop a `.py` file in `~/.nexo/plugins/`:

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
- **Secret redaction.** API keys and tokens are stripped before they ever reach memory storage.

## The Psychology Behind NEXO Brain

NEXO Brain isn't just engineering — it's applied cognitive psychology:

| Psychological Concept | How NEXO Brain Implements It |
|----------------------|----------------------|
| Atkinson-Shiffrin (1968) | Three memory stores: sensory register --> STM --> LTM |
| Ebbinghaus Forgetting Curve (1885) | Exponential decay: `strength = strength * e^(-lambda * time)` |
| Rehearsal Effect | Accessing a memory resets its strength to 1.0 |
| Memory Consolidation | Nightly process promotes frequently-used STM to LTM |
| Prediction Error | Only surprising (novel) information gets stored — redundant input is gated |
| Spreading Activation (Collins & Loftus, 1975) | Retrieving a memory co-activates related memories through an associative graph |
| HyDE (Gao et al., 2022) | Hypothetical document embeddings improve semantic recall |
| Prospective Memory (Einstein & McDaniel, 1990) | Context-triggered intentions fire when cue conditions match |
| Metacognition | Guard system checks past errors before acting |
| Cognitive Dissonance (Festinger, 1957) | Detects and verbalizes conflicts between old and new knowledge |
| Theory of Mind | Models user behavior, preferences, and mood |
| Synaptic Pruning | Automated cleanup of weak, unused memories |
| Associative Memory | Semantic search finds related concepts, not just matching words |
| Memory Reconsolidation | Dreaming process discovers hidden connections during sleep |

## Integrations

### Claude Code (Primary)

NEXO Brain is designed as an MCP server. Claude Code is the primary supported client:

```bash
npx nexo-brain
```

All 109+ tools are available immediately after installation. The installer configures Claude Code's `~/.claude/settings.json` automatically.

### OpenClaw

NEXO Brain also works as a cognitive memory backend for [OpenClaw](https://github.com/openclaw/openclaw):

#### MCP Bridge (Zero Code)

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

#### ClawHub Skill

```bash
npx clawhub@latest install nexo-brain
```

#### Native Memory Plugin

```bash
npm install @wazionapps/openclaw-memory-nexo-brain
```

```json
{
  "plugins": {
    "slots": {
      "memory": "memory-nexo-brain"
    }
  }
}
```

This replaces OpenClaw's default memory system with NEXO Brain's full cognitive architecture.

### Any MCP Client

NEXO Brain works with any application that supports the MCP protocol. Configure it as an MCP server pointing to `~/.nexo/src/server.py`.

## Listed On

| Directory | Type | Link |
|-----------|------|------|
| npm | Package | [nexo-brain](https://www.npmjs.com/package/nexo-brain) |
| Glama | MCP Directory | [glama.ai](https://glama.ai/mcp/servers/@wazionapps/nexo) |
| mcp.so | MCP Directory | [mcp.so](https://mcp.so/server/nexo/wazionapps) |
| mcpservers.org | MCP Directory | [mcpservers.org](https://mcpservers.org) |
| OpenClaw | Native Plugin | [openclaw.com](https://openclaw.ai) |
| dev.to | Technical Article | [How I Applied Cognitive Psychology to AI Agents](https://dev.to/wazionapps/how-i-applied-cognitive-psychology-to-give-ai-agents-real-memory-2oce) |
| nexo-brain.com | Official Website | [nexo-brain.com](https://nexo-brain.com) |

## Inspired By

NEXO Brain builds on ideas from several open-source projects. We're grateful for the research and implementations that inspired specific features:

| Project | Inspired Features |
|---------|------------------|
| [Vestige](https://github.com/pchaganti/gx-vestige) | HyDE query expansion, spreading activation, prediction error gating, memory dreaming, prospective memory |
| [ShieldCortex](https://github.com/PShieldCortex/ShieldCortex) | Security pipeline (4-layer memory poisoning defense) |
| [Bicameral](https://github.com/nicobailey/Bicameral) | Quarantine queue (trust promotion policy for new facts) |
| [claude-mem](https://github.com/nicobailey/claude-mem) | Hook auto-capture (extracting decisions and facts from conversations) |
| [ClawMem](https://github.com/nicobailey/ClawMem) | Co-activation reinforcement (memories retrieved together strengthen connections) |

## Support the Project

If NEXO Brain is useful to you, consider:

- **Star this repo** — it helps others discover the project and motivates continued development
- **[Sponsor on GitHub](https://github.com/sponsors/wazionapps)** — support ongoing development directly
- **Share your experience** — tell others how you're using cognitive memory in your AI workflows
- **Contribute** — see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. Issues and PRs welcome

[![Star History Chart](https://api.star-history.com/svg?repos=wazionapps/nexo&type=Date)](https://star-history.com/#wazionapps/nexo&Date)

## License

MIT -- see [LICENSE](LICENSE)

---

Created by **Francisco Cerdà Puigserver** & **NEXO** (Claude Opus) · Built by [WAzion](https://www.wazion.com)
