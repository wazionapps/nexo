# NEXO Brain — Your AI Gets a Brain

[![npm](https://img.shields.io/npm/v/nexo-brain?label=npm&color=purple)](https://www.npmjs.com/package/nexo-brain)
[![F1 0.588 on LoCoMo](https://img.shields.io/badge/LoCoMo_F1-0.588-brightgreen)](https://github.com/wazionapps/nexo/blob/main/benchmarks/locomo/results/)
[![+55% vs GPT-4](https://img.shields.io/badge/vs_GPT--4-%2B55%25-blue)](https://github.com/snap-research/locomo/issues/33)
[![GitHub stars](https://img.shields.io/github/stars/wazionapps/nexo?style=social)](https://github.com/wazionapps/nexo/stargazers)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

> The first AI memory system with architectural inhibitory control — the agent reasons about whether to act before acting. Cognitive Cortex, Context Continuity via auto-compaction hooks, Smart Startup, Context Packets, Auto-Prime, 30 Core Rules as DNA, auto-update, auto-diary, and CLAUDE.md version tracker. Battle-tested from 6 months of production use, validated via multi-AI debate.

**NEXO Brain transforms any MCP-compatible AI agent from a stateless assistant into a cognitive partner that remembers, learns, forgets, adapts, and builds a relationship with you over time.**

<p align="center">
  <a href="https://www.youtube.com/watch?v=IBs7zh7ZMG0">
    <img src="assets/nexo-brain-infographic-v5.png" alt="NEXO Brain Architecture" width="700">
  </a>
</p>

[Watch the overview on YouTube](https://www.youtube.com/watch?v=IBs7zh7ZMG0) · [Watch the full deep-dive](https://www.youtube.com/watch?v=bKAfowyyy5M)

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

## Cognitive Cortex

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

## Context Continuity (Auto-Compaction)

NEXO Brain automatically preserves session context when Claude Code compacts conversations. Using PreCompact and PostCompact hooks:

- **PreCompact**: Saves a complete session checkpoint to SQLite (task, files, decisions, errors, reasoning thread, next step)
- **PostCompact**: Re-injects a structured Core Memory Block into the conversation, so the session continues seamlessly

This means long sessions (8+ hours) feel like one continuous conversation instead of restarting after each compaction.

**How it works:**
1. Configure the hooks in your Claude Code `settings.json`
2. NEXO Brain's heartbeat automatically maintains the checkpoint
3. When compaction happens, the PreCompact hook reads the checkpoint and injects a recovery block
4. The session continues from exactly where it left off

**Setup:**
```json
{
  "hooks": {
    "PreCompact": [{
      "matcher": "*",
      "hooks": [{"type": "command", "command": "bash path/to/nexo/src/hooks/pre-compact.sh", "timeout": 10}]
    }],
    "PostCompact": [{
      "matcher": "*",
      "hooks": [{"type": "command", "command": "bash path/to/nexo/src/hooks/post-compact.sh", "timeout": 10}]
    }]
  }
}
```

2 new MCP tools: `nexo_checkpoint_save` (manual or hook-triggered checkpoint), `nexo_checkpoint_read` (retrieves the latest checkpoint for context injection).

## Cognitive Features

NEXO Brain provides **147+ MCP tools** across 20+ categories. These features implement cognitive science concepts that go beyond basic memory:

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

## Nervous System (v2.0.0)

NEXO Brain doesn't just respond — it runs 15 autonomous processes in the background, like a biological nervous system. They handle maintenance, health monitoring, and self-improvement without any user interaction:

| Script | Schedule | What It Does |
|--------|----------|-------------|
| **cognitive-decay** | 03:00 daily | Ebbinghaus decay + memory consolidation + duplicate merging + dreaming |
| **sleep** | 04:00 daily | Synaptic pruning, expired data cleanup |
| **deep-sleep** | 04:30 daily | 4-phase overnight pipeline: Collect→Extract→Synthesize→Apply. Analyzes all sessions, detects emotional patterns, abandoned projects, productivity issues, and auto-creates learnings |
| **self-audit** | 07:00 daily | Health checks, guard stats, trust score review, metrics |
| **postmortem** | 23:30 daily | Session consolidation, extract patterns from day's events |
| **catchup** | On boot | Runs any missed scheduled processes (Mac was off/asleep) |
| **tcc-approve** | On boot (macOS) | Auto-approve macOS permissions for Claude Code updates |
| **prevent-sleep** | Always (daemon) | Keeps machine awake for nocturnal processes (caffeinate/systemd-inhibit) |
| **evolution** | Weekly (Sun) | Self-improvement proposals — NEXO suggests and applies enhancements |
| **followup-hygiene** | Weekly (Sun) | Normalizes statuses, flags stale followups, cleans orphans |
| **learning-housekeep** | 03:15 daily | Dedup learnings, adjust weights by usage, process overdue reviews, reconcile decision outcomes |
| **immune** | Every 30 min | Quarantine processing, memory promotion/rejection, synaptic pruning |
| **synthesis** | 06:00 daily | Memory synthesis — discovers cross-memory patterns |
| **watchdog** | Every 30 min | Monitors services, LaunchAgents, and infrastructure health |
| **auto-close-sessions** | Every 5 min | Cleans stale sessions |

Core processes are defined in `src/crons/manifest.json` and auto-synced to your system by `nexo_update`. On macOS they run via LaunchAgents; on Linux via systemd user timers. `tcc-approve`, `prevent-sleep`, and `backup` are platform/personal helpers — not in the manifest but listed above for completeness. Personal crons (your own scripts) are never touched by the sync. If your Mac was asleep during a scheduled process, the catch-up script re-runs everything in order when it wakes.

## Deep Sleep v2 — Overnight Learning (v2.1.0)

Deep Sleep is a 4-phase pipeline that runs at 4:30 AM and makes NEXO smarter while you sleep:

```
Phase 1: COLLECT (Python)
├── Reads all session transcripts from the day
├── Splits each session into individual .txt files
└── Gathers DB state (followups, learnings, trust)

Phase 2: EXTRACT (Opus, one call per session)
├── 8 types of findings per session:
│   ├── Uncaptured corrections (user corrected agent, no learning saved)
│   ├── Self-corrected errors (knowledge gaps to fix)
│   ├── Unformalised ideas (mentioned but never tracked)
│   ├── Missed commitments (promised but no followup)
│   ├── Protocol violations (guard_check, heartbeat, change_log)
│   ├── Emotional signals (frustration, flow, satisfaction)
│   ├── Abandoned projects (started but not finished)
│   └── Productivity patterns (corrections, proactivity, tool efficiency)
└── Outputs per-session JSON with findings + emotional timeline

Phase 3: SYNTHESIZE (Opus, one call)
├── Cross-session patterns (same error in 5 sessions = systemic)
├── Daily mood arc with score (0.0 = terrible day, 1.0 = great day)
├── Recurring triggers (what causes frustration vs flow)
├── Productivity analysis (corrections, tool efficiency)
├── Abandoned project detection
├── Morning agenda (prioritized)
└── Calibration recommendations

Phase 4: APPLY (Python)
├── Auto-creates learnings from high-confidence findings
├── Creates followups for unfinished work
├── Updates mood_history in calibration.json (30-day rolling)
├── Generates session-tone.json (emotional guidance for next session)
└── Writes morning-briefing.md
```

### Session Tone — Emotional Intelligence

Deep Sleep generates a `session-tone.json` that tells NEXO how to behave next morning:

- **Agent made many mistakes yesterday** → Acknowledge them, show what was learned, demonstrate improvement
- **User had a bad day (mood < 40%)** → Supportive approach, lighter start, avoid known frustration triggers
- **User had a great day (mood > 70%)** → Reinforce momentum, reference wins, push ambitious goals
- **Agent was too reactive** → Be proactive today, don't wait for instructions

This is read by `nexo_smart_startup` and injected into every session's context. NEXO adapts its personality based on real behavioral data, not just configuration.

## Cron Manifest (v2.1.0)

All core crons are defined in `src/crons/manifest.json`. When you run `nexo_update`, the sync script:
- **Installs** new crons from the manifest
- **Updates** changed schedules/intervals
- **Removes** crons no longer in the manifest (only core ones)
- **Never touches** personal crons you created yourself

Run `python3 src/crons/sync.py --dry-run` to preview changes without applying.

## Dashboard (v1.6.0)

A web interface at `localhost:6174` with 6 interactive pages for visual insight into your brain's state:

| Page | What It Shows |
|------|-------------|
| **Overview** | System health at a glance — memory counts, trust score, active sessions, recent changes |
| **Graph** | Interactive D3.js visualization of the knowledge graph (nodes, edges, clusters) |
| **Memory** | Browse and search all memory stores (STM, LTM, sensory, archived) |
| **Somatic** | Pain map per file/area — see which parts of your codebase cause the most errors |
| **Adaptive** | Personality signals, learned weights, and current mode |
| **Sessions** | Active and historical sessions with timeline and diary entries |

Built with FastAPI backend and D3.js frontend. Dashboard files are installed to `NEXO_HOME/dashboard/` but must be started manually:

```bash
python3 ~/.nexo/dashboard/app.py
```

This opens `localhost:6174` in your browser. Add `--port 8080` to change the port or `--no-browser` to skip auto-opening.

## Full Orchestration System

Memory alone doesn't make a co-operator. What makes the difference is the **behavioral loop** — the automated discipline that ensures every session starts informed, runs with guardrails, and ends with self-reflection.

### Automated Hooks

7 hooks fire automatically at key moments in every Claude Code session:

| Hook | When | What It Does |
|------|------|-------------|
| **SessionStart (timestamp)** | Session opens | Writes session timestamp for staleness detection |
| **SessionStart (briefing)** | Session opens | Generates briefing from SQLite: overdue reminders, today's tasks, pending followups, active sessions. Cleans up post-mortem flags. |
| **Stop** | Session ends | Mandatory post-mortem: self-critique (5 questions), session buffer entry, followup creation, proactive seeds for next session |
| **PostToolUse (capture)** | After each tool call | Captures meaningful mutations to the Sensory Register + auto-diary every 10 tool calls |
| **PostToolUse (inbox)** | After each tool call | Inter-terminal inbox delivery between parallel sessions |
| **PreCompact** | Before context compression | Saves full session checkpoint to SQLite — task, files, decisions, errors, reasoning thread + emergency diary |
| **PostCompact** | After context compression | Re-injects Core Memory Block so the session continues seamlessly from where it left off |

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
PreCompact hook saves full checkpoint if conversation is compressed
    ↓
PostCompact hook re-injects Core Memory Block → session continues seamlessly
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

Existing users upgrading from any previous version:
```bash
npx nexo-brain  # detects current version, migrates automatically
```
- Updates hooks, core files, plugins, scripts, and LaunchAgent templates
- Runs database schema migrations automatically
- **Never touches your data** (memories, learnings, preferences)
- Saves updated CLAUDE.md as reference (doesn't overwrite customizations)

## Knowledge Graph (v0.8)

A bi-temporal entity-relationship graph with 988 nodes and 896 edges. Entities and relationships carry both valid-time (when the fact was true) and system-time (when it was recorded), enabling temporal queries like "what did we know about X last Tuesday?". BFS traversal discovers multi-hop connections between concepts. Event-sourced edges with smart dedup (ADD/UPDATE/NOOP) prevent redundant writes while preserving full history.

4 MCP tools: `nexo_kg_query` (SPARQL-like queries), `nexo_kg_path` (shortest path between entities), `nexo_kg_neighbors` (direct connections), `nexo_kg_stats` (graph metrics).

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
  Setting up nervous system...
    15 autonomous processes configured.
    Dashboard configured at localhost:6174.
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

Under the hood, the alias runs:
```bash
claude --dangerously-skip-permissions "."
```
`--dangerously-skip-permissions` launches Claude Code with tool-use permissions pre-approved so the operator can act autonomously. The `"."` triggers the operator to start immediately. Operator behavior (startup, context, greeting) is defined in `~/.claude/CLAUDE.md`.

That's it. No need to run `claude` manually. Your operator will greet you immediately — adapted to the time of day, resuming from where you left off if there's a previous session. No cold starts, no waiting for your input.

### What Gets Installed

| Component | What | Where |
|-----------|------|-------|
| Cognitive engine | Python: fastembed, numpy, vector search | pip packages |
| MCP server | 147+ tools for memory, cognition, learning, guard | NEXO_HOME/ |
| Plugins | Guard, episodic memory, cognitive memory, entities, preferences, update, etc. | Code: src/plugins/, Personal: NEXO_HOME/plugins/ |
| Hooks (7) | SessionStart, Stop, PostToolUse, PreCompact, PostCompact | NEXO_HOME/hooks/ |
| Nervous system | 15 autonomous processes (decay, sleep, audit, evolution, watchdog, etc.) | NEXO_HOME/scripts/ |
| Dashboard | Web UI at localhost:6174 (6 pages) | NEXO_HOME/dashboard/ |
| CLAUDE.md | Complete operator instructions (Codex, hooks, guard, trust, memory) | ~/.claude/CLAUDE.md |
| Schedule config | schedule.json with customizable process times and timezone | NEXO_HOME/config/ |
| Auto-update | Non-blocking startup check (5s max), opt-out via schedule.json | Built into server startup |
| CLAUDE.md tracker | Version-tracked core sections with safe updates preserving customizations | Built into auto-update |
| Auto-diary | 3-layer system: PostToolUse every 10 calls, PreCompact emergency, heartbeat DIARY_OVERDUE | Built into hooks |
| Claude Code config | MCP server + 7 hooks + 15 processes registered | ~/.claude/settings.json |

### Requirements

- **macOS or Linux** (Windows via [WSL](https://learn.microsoft.com/en-us/windows/wsl/install))
- **Node.js 18+** (for the installer)
- **Claude Opus (latest version) strongly recommended.** NEXO Brain provides 147+ MCP tools across 20+ categories. This cognitive load requires a top-tier model with large context window. Smaller models (Haiku, Sonnet) may struggle with tool selection and produce inconsistent results. Opus handles all 147+ tools without hesitation.
- Python 3, Homebrew, and Claude Code are installed automatically if missing.

## Architecture

### Unified Code/Data Separation (v2.0.0)

NEXO Brain separates **code** (immutable, in the repo or npm package) from **data** (personal, in `NEXO_HOME`):

| Path | Contents |
|------|----------|
| `src/` (or npm package) | Server, plugins, hooks, scripts — never modified at runtime |
| `NEXO_HOME/` (default `~/.nexo/`) | Database, config, personal plugins, schedule, backups |
| `NEXO_HOME/config/schedule.json` | Customizable process schedules, timezone, auto_update flag |
| `NEXO_HOME/plugins/` | Personal plugins that override or extend repo plugins |
| `NEXO_HOME/data/` | SQLite databases (nexo.db, cognitive.db), migration state |

The plugin loader scans `src/plugins/` first (base), then `NEXO_HOME/plugins/` (personal override by filename). This dual-directory approach lets you extend NEXO without forking the repo.

### 147+ MCP Tools across 20+ Categories

| Category | Count | Tools | Purpose |
|----------|-------|-------|---------|
| Cognitive | 8 | retrieve, stats, inspect, metrics, dissonance, resolve, sentiment, trust | The brain — memory, RAG, trust, mood |
| Cognitive Input | 5 | prediction_gate, security_scan, quarantine, promote, redact | Input pipeline — gating, security, quarantine |
| Cognitive Advanced | 8 | hyde_search, spread_activate, explain_recall, dream, prospect, hook_capture, pin, archive | Advanced retrieval, proactive, lifecycle |
| Guard | 3 | check, stats, log_repetition | Metacognitive error prevention |
| Episodic | 10 | change_log/search/commit, decision_log/outcome/search, review_queue, diary_write/read, recall | What happened and why |
| Sessions | 4 | startup, heartbeat, stop, status | Session lifecycle + context shift detection + inter-terminal auto-inbox |
| Coordination | 7 | track, untrack, files, send, ask, answer, check_answer | Multi-session file coordination + messaging |
| Reminders | 5 | list, create, update, complete, delete | User's tasks and deadlines |
| Followups | 4 | create, update, complete, delete | System's autonomous verification tasks |
| Learnings | 5 | add, search, update, delete, list | Error patterns and prevention rules |
| Credentials | 5 | create, get, update, delete, list | Local credential storage (plaintext SQLite — protect with filesystem permissions) |
| Task History | 3 | log, list, frequency | Execution tracking and overdue alerts |
| Menu | 1 | menu | Operations center with box-drawing UI |
| Entities | 5 | search, create, update, delete, list | People, services, URLs |
| Preferences | 4 | get, set, list, delete | Observed user preferences |
| Agents | 5 | get, create, update, delete, list | Agent delegation registry |
| Backup | 3 | now, list, restore | SQLite data safety |
| Evolution | 5 | propose, approve, reject, status, history | Self-improvement proposals |
| Adaptive & Somatic | 4 | adaptive_weights, adaptive_override, somatic_check, somatic_stats | Learned signal weights + pain memory per file |
| Knowledge Graph | 4 | kg_query, kg_path, kg_neighbors, kg_stats | Bi-temporal entity-relationship graph |
| Context Continuity | 2 | checkpoint_save, checkpoint_read | Auto-compaction session preservation |
| Update | 1 | update | Pull latest code, backup, migrate, verify (with rollback) |

### Plugin System

NEXO Brain supports hot-loadable plugins with a dual-directory loader. Base plugins live in `src/plugins/` (repo). Personal plugins go in `NEXO_HOME/plugins/` and can override base plugins by filename. Drop a `.py` file in `NEXO_HOME/plugins/`:

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
- **Auto-update is resilient.** NEXO checks for updates on startup. If an update fails, it continues with the current version and notifies you. Local migrations (database schema, configuration) always run. Network updates (git pull) can be disabled by setting `auto_update: false` in `NEXO_HOME/config/schedule.json`.
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

All 147+ tools are available immediately after installation. The installer configures Claude Code's `~/.claude/settings.json` automatically.

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
        "args": ["~/.nexo/server.py"],
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
openclaw mcp set nexo-brain '{"command":"python3","args":["~/.nexo/server.py"],"env":{"NEXO_HOME":"~/.nexo"}}'
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

NEXO Brain works with any application that supports the MCP protocol. Configure it as an MCP server pointing to `server.py` inside `NEXO_HOME` (default `~/.nexo/server.py`), with the `NEXO_HOME` env var set to the same directory.

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
| Vestige | HyDE query expansion, spreading activation, prediction error gating, memory dreaming, prospective memory |
| ShieldCortex | Security pipeline (4-layer memory poisoning defense) |
| Bicameral | Quarantine queue (trust promotion policy for new facts) |
| claude-mem | Hook auto-capture (extracting decisions and facts from conversations) |
| ClawMem | Co-activation reinforcement (memories retrieved together strengthen connections) |

## Support the Project

If NEXO Brain is useful to you, consider:

- **Star this repo** — it helps others discover the project and motivates continued development
- **[Sponsor on GitHub](https://github.com/sponsors/wazionapps)** — support ongoing development directly
- **Share your experience** — tell others how you're using cognitive memory in your AI workflows
- **Contribute** — see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. Issues and PRs welcome

[![Star History Chart](https://api.star-history.com/svg?repos=wazionapps/nexo&type=Date)](https://star-history.com/#wazionapps/nexo&Date)

## Known Issues (v2.0.0)

| Priority | Issue | Planned |
|----------|-------|---------|
| P0 | Credentials stored in plaintext SQLite — protect with filesystem permissions | v2.1.0 |
| P0 | Shell hooks use SQL string interpolation (injection risk) | v2.1.0 |
| P0 | Dashboard has no authentication (localhost only) | v2.1.0 |
| P0 | Database migrations are fail-open (errors logged but not blocking) | v2.1.0 |

## Changelog

### v2.0.0 — Unified Architecture (2026-03-31)
- **Code/data separation**: Code in repo (`src/`), personal data in `NEXO_HOME` (default `~/.nexo/`). `NEXO_HOME` env var required.
- **Plugin loader dual-directory**: Scans `src/plugins/` (base) then `NEXO_HOME/plugins/` (personal override by filename).
- **Auto-update on startup**: Non-blocking (5s max), resilient, opt-out via `schedule.json`. Separate from manual `nexo_update` tool.
- **Auto-diary**: 3-layer system — PostToolUse every 10 calls, PreCompact emergency save, heartbeat DIARY_OVERDUE signal.
- **CLAUDE.md version tracker**: Section markers enable safe core updates without losing user customizations.
- **schedule.json**: Customizable process schedules with timezone support and `auto_update` flag.
- **15 autonomous processes**: Added auto-close-sessions, synthesis, backup, tcc-approve, prevent-sleep (cross-platform).
- **7 hooks**: SessionStart (timestamp + briefing), Stop, PostToolUse (capture + inbox), PreCompact, PostCompact.
- **147+ MCP tools**: Added `nexo_update` tool for manual updates with rollback.
- **Lambda fix**: Decay values were 24x too aggressive (STM: 7h to 7d, LTM: 2.4d to 60d).
- **Guard scoping**: Was returning 35+ irrelevant blocking rules; now scoped to area and gated to high/critical.
- **12 rounds of external audit**: ~60 findings resolved.

### v1.7.0 — Full Internationalization + Linux Support (2026-03-31)
- **Full i18n**: All UI strings, error messages, DB status values in English. NLP detection patterns retain bilingual keywords (Spanish + English) for multilingual user support.
- **Linux support**: systemd user timers (preferred) or crontab fallback for all automated cognitive processes.
- **Auto-resolve followups**: Change log entries automatically cross-reference and complete matching open followups.
- **Free-form learning categories**: No more hardcoded category validation — use any category name.
- **CLAUDE.md template rewrite**: 494 to 127 lines, compact procedural format with full heartbeat signal reactions.
- **Complete sanitization**: All hardcoded paths use `NEXO_HOME` env var. No credentials or personal data in the distributed package. Migration scripts and maintainer tooling use configurable paths.

### v1.6.0 — Nervous System + Dashboard v2 (2026-03-30)
- **Nervous System**: 11 autonomous scripts (decay, deep sleep, self-audit, catchup, evolution, followup hygiene, immune, watchdog, github monitor, learning validator)
- **Dashboard v2**: 6 interactive pages at localhost:6174 (Overview, Graph, Memory, Somatic, Adaptive, Sessions)
- **LaunchAgent Templates**: macOS automation templates included in the package for scheduling the nervous system
- **Hooks**: 7 total — SessionStart, Stop, PostToolUse, PreCompact, PostCompact
- **Installer**: Now configures dashboard LaunchAgent, nervous system scripts, and all templates automatically

### v1.5.2 — Deep Sleep (2026-03-29)
- **Deep Sleep**: Reads full session transcripts (not just diary) — finds uncaptured corrections, protocol violations, missed commitments
- Uses Claude CLI in `--bare` mode (no hooks, no CLAUDE.md interference)
- Catch-up system re-runs yesterday if the Mac was off

### v1.5.0 — Modular Core + Knowledge Graph Search (2026-03-29)
- **Architecture**: `db.py` refactored into `db/` package (11 modules); `cognitive.py` into `cognitive/` package (6 modules)
- **KG Boost**: Knowledge Graph connection count influences search result ranking
- **HNSW Vector Index**: Optional approximate nearest neighbor acceleration (auto-activates above 10,000 memories)
- **Claim Graph**: Decomposes blob memories into atomic verifiable facts with provenance and contradiction detection
- **Inter-terminal Auto-inbox (D+)**: `nexo_startup` accepts `claude_session_id` for automatic inbox delivery between parallel terminals
- **Tests**: 24 pytest tests across 3 suites (cognitive, knowledge graph, migrations)

### v1.4.1 — Multi-AI Code Review (2026-03-29)
- **Fix**: 3 bugs found by GPT-5.4 (Codex CLI) + Gemini 2.5 (Gemini CLI) reviewing full codebase
- **Security**: Memory sanitization prevents prompt injection via stored content
- **Migration #13**: Normalizes legacy status values on upgrade

### v1.4.0 — The Brain Dreams (2026-03-29)
- **Major**: All 9 nightly scripts migrated from Python word-overlap to CLI wrapper pattern
- **Stop Hook v8**: Session-scoped tool counting, buffer fallback removed
- **Guard**: Behavioral rules section surfaces most-violated rules at session start

### v1.3.0 — Evolution System (2026-03-28)
- **New**: Self-improvement cycle — NEXO proposes and applies improvements weekly
- Dual-mode: auto (low-risk) and review (owner approval required)
- Circuit breaker, snapshot/rollback, immutable file protection

### v1.2.3 — AGPL-3.0 License (2026-03-27)
- License changed from MIT to AGPL-3.0

### v1.2.1 — Stop Hook Hotfix (2026-03-27)
- **Fix**: v1.2.0 deleted the flag on approve, causing infinite block loops if session didn't close immediately
- **Fix**: Removed TTL on flag — it persists until SessionStart cleans it up next session
- **New**: Trivial sessions (<5 meaningful tool calls) skip post-mortem entirely and approve immediately
- SessionStart hook now cleans up `.postmortem-complete` flag on session start

### v1.2.0 — Blocking Stop Hook (2026-03-27)
- **Fix**: Stop hook now uses `"decision": "block"` instead of `"approve"` to enforce post-mortem execution
- Previous behavior: hook injected `systemMessage` but AI had already responded — instructions were never processed
- New behavior: session close is blocked until AI completes self-critique, session diary, buffer entry, and followups
- Flag-based mechanism (`.postmortem-complete`) allows second close attempt to succeed
- Works for all NEXO users, not just specific setups

### v1.1.1 — Multi-terminal fix (2026-03-27)
- **Fix**: PostCompact now reads the correct session's checkpoint in multi-terminal setups
- Changelog section added to README

### v1.1.0 — Context Continuity (2026-03-27)
- **Context Continuity**: PreCompact/PostCompact hooks preserve session state across compaction events
- New `session_checkpoints` SQLite table + migration #12
- New tools: `nexo_checkpoint_save`, `nexo_checkpoint_read`
- Heartbeat automatically maintains checkpoint every interaction
- Core Memory Block re-injected post-compaction with task, files, decisions, reasoning thread
- 115+ total tools at the time, 20 categories

### v1.0.0 — Cognitive Cortex + Stable Release (2026-03-26)
- **Cognitive Cortex**: architectural inhibitory control (ASK/PROPOSE/ACT modes)
- 30 Core Rules as immutable DNA in SQLite
- Designed via 3-way AI debate (Claude Opus + GPT-5.4 + Gemini 3.1 Pro)
- Artifact Registry for operational facts
- Full benchmark suite (LoCoMo F1: 0.588)

### v0.10.0 — Smart Context (2026-03-22)
- Smart Startup: pre-loads memories from pending followups + diary
- Context Packet: structured injection for subagents
- Auto-Prime: keyword-triggered area learnings in heartbeat
- Diary Archive: permanent subconscious memory (180d+ auto-archived)

### v0.9.0 — Cognitive Memory (2026-03-15)
- Atkinson-Shiffrin memory model (STM → LTM promotion)
- Semantic RAG with fastembed (BAAI/bge-base-en-v1.5, 768 dims)
- Trust scoring, sentiment detection, adaptive personality modes
- Ebbinghaus decay, sister detection, quarantine system

## License

AGPL-3.0 -- see [LICENSE](LICENSE)

---

Created by **Francisco Cerdà Puigserver** & **NEXO** (Claude Opus) · Built by [WAzion](https://www.wazion.com)
