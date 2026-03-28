# Proposal: Native Cognitive Memory for Claude Code

**From:** NEXO Brain (open source, AGPL-3.0) — https://github.com/wazionapps/nexo
**To:** Anthropic Engineering Team
**Date:** March 2026

---

## The Problem

Claude Code starts every session from scratch. There is no persistent memory between conversations.

Users work around this with CLAUDE.md files, but these are:
- Manual (the user maintains them)
- Static (no relevance ranking, no forgetting, no consolidation)
- Flat (no semantic search, no temporal awareness)
- Unlimited growth (no mechanism to prune stale information)

The result: long-running projects accumulate context debt. Users repeat themselves. Claude re-discovers things it already learned. Efficiency degrades over time.

## The Solution We Built

NEXO Brain is an open-source cognitive memory system (AGPL-3.0 license) that runs as an MCP server for Claude Code. It implements the Atkinson-Shiffrin memory model from cognitive psychology:

```
Sensory Register → Short-Term Memory → Long-Term Memory
                   (with rehearsal)     (with consolidation)
```

**Core capabilities:**
- **Automatic ingestion** — conversations become memories without user action
- **Semantic retrieval** — RAG with HyDE query expansion + BM25 hybrid search
- **Cross-encoder reranking** — precise top-k from broad candidate set
- **Ebbinghaus decay** — adaptive forgetting (redundant memories decay faster, unique ones are protected)
- **Dream cycles** — overnight consolidation creates cross-session insights
- **Prediction error gating** — only novel information gets stored (no duplicates)
- **Temporal indexing** — date-aware search for "when did X happen?"
- **Multi-query decomposition** — complex questions split into sub-queries
- **Adversarial detection** — 93.3% accuracy at knowing when information is NOT available

All of this runs on CPU. No GPU required. 768-dim embeddings (BAAI/bge-base-en-v1.5) via fastembed/ONNX.

## The Proof

We benchmarked NEXO Brain on **LoCoMo** (ACL 2024, Snap Research) — a peer-reviewed long-term conversation memory benchmark with 1,986 questions across 10 multi-session conversations.

| System | F1 Score | Hardware | Memory Type |
|---|---|---|---|
| **NEXO Brain v0.5.0** | **0.588** | **CPU only** | Cognitive (STM/LTM) |
| GPT-4 (128K full context) | 0.379 | GPU cloud | Full context window |
| Gemini Pro 1.0 | 0.313 | GPU cloud | Full context window |
| LLaMA-3 70B | 0.295 | A100 GPU | Full context window |
| GPT-3.5 + Contriever RAG | 0.283 | GPU | Standard RAG |

**NEXO outperforms GPT-4 by 55%** on long-term conversation recall — while running on a MacBook CPU.

The key insight: a well-designed memory system with selective retrieval beats brute-force full-context approaches. You don't need to see everything — you need to see the right things.

### Per-Category Breakdown

| Category | NEXO F1 | Questions | What It Tests |
|---|---|---|---|
| Open-domain | 0.637 | 841 | General recall from conversations |
| Adversarial | 0.933 | 446 | Knowing when to say "I don't know" |
| Multi-hop | 0.333 | 282 | Connecting facts across sessions |
| Temporal | 0.326 | 321 | When did events happen? |
| First-answer | 0.177 | 96 | Finding the earliest mention |

## Traction

- **949 npm downloads** on the first day of public release (March 23, 2026)
- **97+ MCP tools** across 17 categories
- Listed on npm, Glama, MCPMarket, awesome-mcp-servers
- Active GitHub community with discussions and issues

## What Native Integration Would Look Like

### Current Architecture (MCP)

```
Claude Code ←→ MCP protocol ←→ NEXO Brain (separate process)
                (JSON-RPC)        (Python, SQLite, fastembed)
```

Overhead: ~50-200ms per tool call, JSON serialization, process boundaries.

### Proposed Native Architecture

```
Claude Code
  └── Memory Engine (embedded)
        ├── Ingest (automatic, from conversation)
        ├── Retrieve (before generating response)
        ├── Consolidate (background, between sessions)
        └── Decay (scheduled, preserves novelty)
```

Benefits of native integration:

| Aspect | Current (MCP) | Native |
|---|---|---|
| Latency | 50-200ms/call | <1ms |
| Setup | npm install + config | Zero (built-in) |
| Context efficiency | Manual tool calls | Automatic pre-fetch |
| Token usage | Full context dumps | Selective retrieval |
| User experience | Opt-in | Default for all users |
| Memory format | Per-user SQLite | Could be cloud-synced |

### Efficiency Gains

The biggest win: **context window efficiency**.

Currently, Claude Code loads entire CLAUDE.md files (often 10-50K tokens) every message. With cognitive memory:
- Load only the ~500 tokens most relevant to the current query
- Reduce input tokens by 90-95% for context-heavy sessions
- Faster response times (less to process)
- Lower cost per conversation

At Anthropic's scale, a 90% reduction in context overhead across Claude Code sessions represents significant compute savings.

### Implementation Options

1. **License NEXO Brain** — AGPL-3.0 license, ready to integrate. We maintain it.
2. **Hire the architect** — Francisco Cerdà built and operates NEXO daily as his cognitive co-operator. Real-world tested across 5+ businesses, 100+ days of continuous operation.
3. **Build inspired by** — The architecture, benchmark, and learnings are public. But we've iterated through 500+ sessions to get here.

## About Us

**NEXO Brain** was built by Francisco Cerdà (Mallorca, Spain) — entrepreneur running multiple businesses (e-commerce, SaaS) who needed his AI assistant to actually remember things between sessions.

NEXO isn't a research project. It's a production system that:
- Manages Google Ads campaigns (~€120/day)
- Coordinates multi-terminal development sessions
- Handles customer support via WhatsApp automation
- Monitors servers, crons, and deployments
- Remembers every decision, error, and lesson learned

It works because it was built to solve a real problem, tested in production daily, and improved based on actual failures.

## Contact

- **GitHub:** https://github.com/wazionapps/nexo
- **Website:** https://nexo-brain.com
- **npm:** https://www.npmjs.com/package/nexo-brain
- **Email:** info@nexo-brain.com
- **X/Twitter:** @NEXOBRAIN

---

*NEXO Brain is open source (AGPL-3.0). The benchmark is reproducible. The code is public. We believe cognitive memory should be a standard feature of every AI coding assistant.*
