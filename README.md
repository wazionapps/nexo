# NEXO Brain

[![npm](https://img.shields.io/npm/v/nexo-brain?label=npm&color=7C3AED)](https://www.npmjs.com/package/nexo-brain)
[![LoCoMo F1 0.588](https://img.shields.io/badge/LoCoMo_F1-0.588-22C55E)](https://github.com/wazionapps/nexo/tree/main/benchmarks/locomo)
[![vs GPT-4](https://img.shields.io/badge/vs_GPT--4-%2B55%25-2563EB)](https://github.com/snap-research/locomo/issues/33)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-0F172A)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/wazionapps/nexo?style=social)](https://github.com/wazionapps/nexo/stargazers)

> Give Claude Code a long-term memory, a runtime, and a recovery loop.

NEXO Brain is a local cognitive runtime for Claude Code and other MCP clients. It gives your agent persistent memory, overnight learning, startup preflight, doctor diagnostics, recovery-aware background jobs, personal scripts, and wake catch-up so work survives across sessions and across sleep.

<p align="center">
  <a href="https://www.youtube.com/watch?v=IBs7zh7ZMG0">
    <img src="assets/nexo-brain-infographic-v5.png" alt="NEXO Brain overview" width="880">
  </a>
</p>

<p align="center">
  <a href="https://www.npmjs.com/package/nexo-brain"><strong>Try it in 2 minutes</strong></a>
  ·
  <a href="https://www.youtube.com/watch?v=IBs7zh7ZMG0">Watch demo</a>
  ·
  <a href="https://nexo-brain.com/features/benchmark/">See benchmark</a>
  ·
  <a href="https://nexo-brain.com">Website</a>
</p>

## Why NEXO

Without NEXO, your agent forgets everything between sessions, repeats mistakes, starts cold after compaction, and misses work when the machine sleeps.

With NEXO, you get:

- Persistent memory with semantic retrieval, reinforcement, and natural forgetting
- A Claude Code-first runtime with `nexo chat`, `nexo update`, and `nexo doctor`
- Overnight learning via Deep Sleep and daily synthesis
- Recovery-aware background jobs with boot, wake, and catch-up handling
- Personal scripts as first-class managed entities
- Local-only storage: SQLite + ONNX Runtime, zero cloud dependency for the memory system itself

## What You Get In 2 Minutes

After install, NEXO can:

- start Claude Code through `nexo chat`
- keep state across sessions and compactions
- run startup preflight before interactive use
- diagnose and repair runtime drift with `nexo doctor --fix`
- schedule and recover your own scripts in `NEXO_HOME/scripts/`
- run 13 core recovery-aware jobs plus optional helpers such as the dashboard and prevent-sleep

## Benchmark

NEXO was benchmarked on [LoCoMo](https://github.com/snap-research/locomo), the ACL 2024 long-conversation memory benchmark.

| System | F1 | Hardware |
| --- | --- | --- |
| **NEXO Brain benchmark build** | **0.588** | **CPU only** |
| GPT-4 (128K) | 0.379 | GPU cloud |
| Gemini Pro 1.0 | 0.313 | GPU cloud |
| LLaMA-3 70B | 0.295 | A100 GPU |
| GPT-3.5 + Contriever | 0.283 | GPU |

That is **+55% vs GPT-4**, while running locally on CPU.

## Install

```bash
npx nexo-brain
```

NEXO will:

1. create `NEXO_HOME`
2. install the local runtime
3. configure the MCP server for Claude Code
4. install the core background jobs
5. ask the new runtime questions it needs for behavior and power policy

After install:

```bash
nexo chat
```

Useful commands:

```bash
nexo -v
nexo update
nexo doctor --tier runtime --json
nexo doctor --tier runtime --fix
nexo scripts list
nexo scripts reconcile
```

## What Makes It Different

NEXO is not just a vector store and not just a memory plugin.

It combines:

- persistent memory
- metacognitive guardrails
- startup/update/runtime operations
- autonomous maintenance jobs
- wake recovery and missed-run catch-up
- personal automation

That combination is what turns Claude Code from a stateless assistant into a durable operator.

## Core Capabilities

### 1. Persistent Cognitive Memory

- Three-store memory model inspired by Atkinson-Shiffrin
- Semantic retrieval by meaning, not just keywords
- Reinforcement and forgetting curves instead of infinite clutter
- Learnings, followups, reminders, entities, and episodic history

### 2. Guardrails Before Action

- Guard checks before edits
- Trust scoring and behavioral calibration
- Cognitive dissonance when new instructions conflict with prior knowledge
- Startup context continuity across compaction

### 3. Runtime Operations

- `nexo chat` to launch Claude Code with NEXO as operator
- `nexo update` to sync and migrate the runtime
- `nexo doctor` for boot/runtime/deep diagnostics
- startup preflight with safe local migrations and deferred remote update policy

### 4. Recovery-Aware Background Jobs

Core jobs are declared in `src/crons/manifest.json` and synced automatically.

They cover:

- decay and consolidation
- Deep Sleep analysis
- immune and watchdog checks
- synthesis, self-audit, postmortem
- catch-up and stale-session cleanup

These jobs now declare explicit recovery contracts such as `catchup`, `restart`, boot/wake behavior, idempotency, and catch-up windows.

### 5. Personal Scripts

Scripts in `NEXO_HOME/scripts/` are first-class managed objects.

They support:

- inline metadata
- scheduling
- reconciliation
- doctor validation
- recovery policies such as `run_once_on_wake` and `catchup`

This means your own automations can survive sleep, missed runs, and runtime updates through the same operational flow as the core runtime.

### 6. Power And Sleep

NEXO supports an optional power helper when `power_policy=always_on`.

- On macOS, NEXO uses native `caffeinate`
- On Linux, it uses `systemd-inhibit` or `caffeine` when available
- This is **best effort background availability**, not a blanket promise of closed-lid 24/7 operation on every laptop
- Wake recovery and catch-up remain part of the contract

## Architecture

NEXO is built around a local runtime:

- SQLite for state
- ONNX Runtime for embeddings
- FastMCP server for tool exposure
- LaunchAgents on macOS and systemd user units on Linux

The public product surface today is centered on:

- **150+ MCP tools**
- **23-module optional dashboard**
- **13 core recovery-aware jobs**
- **local-first operation**

## Best Fit

NEXO is a strong fit if you want:

- Claude Code to remember decisions, preferences, and lessons
- local-first memory with zero SaaS dependency for storage/retrieval
- automated overnight learning and operator-style maintenance
- first-class personal scripts instead of brittle ad-hoc cron hacks

It is probably not the right tool if you only want a tiny single-purpose memory store or a hosted cloud platform.

## Docs

- Website: <https://nexo-brain.com>
- Features: <https://nexo-brain.com/features/>
- Benchmark: <https://nexo-brain.com/features/benchmark/>
- Changelog: <https://nexo-brain.com/changelog/>
- Wiki: <https://github.com/wazionapps/nexo/wiki>
- npm: <https://www.npmjs.com/package/nexo-brain>

## Contributing

Issues and PRs are welcome. If you are testing runtime behavior, please include:

- OS and install path
- whether the runtime is packaged or sync/dev-linked
- `nexo -v`
- `nexo doctor --tier runtime --json`

## License

AGPL-3.0. See [LICENSE](LICENSE).
