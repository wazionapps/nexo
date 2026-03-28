# How I Applied Cognitive Psychology to Give AI Agents Real Memory

Every time you close a Claude Code session, everything disappears. The assistant that just helped you debug a tricky production issue doesn't remember any of it tomorrow. It will make the same mistakes you corrected last week. It starts cold every single time.

I spent six months building a fix. The result is **NEXO Brain** — an open-source MCP server that gives AI agents a memory system modeled directly on how human memory actually works, using the Atkinson-Shiffrin model from cognitive psychology (1968).

This article is a technical deep-dive into how that works, why the psychological model matters, and how to install it yourself.

---

## The Fundamental Problem with AI Memory Today

Current approaches to AI memory fall into two categories:

1. **Inject everything into the context window** — expensive, hits limits fast, and older information gets less attention as context grows
2. **Store and retrieve by keyword** — misses the point entirely; human memory doesn't work by keyword matching

Neither approach handles the most important aspects of memory:
- **Forgetting** (critical for not drowning in noise)
- **Reinforcement** (important things get stronger, unused things fade)
- **Associative retrieval** (finding relevant memories by meaning, not words)
- **Metacognition** (knowing what you know and checking it before acting)

---

## The Atkinson-Shiffrin Model Applied to AI

The Atkinson-Shiffrin model (1968) describes human memory as a multi-store system with distinct stages and processes. Here's how I mapped each stage to a practical AI implementation:

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
            ├─→ Dormant: faded but recoverable
            └─→ Near-duplicates auto-merged to prevent clutter
```

This isn't a metaphor. The system literally implements each of these stages with distinct storage, decay rates, and transition logic.

### Stage 1: Sensory Register (48-hour raw capture)

Every interaction creates raw memories in the Sensory Register — high-volume, short-lived (48h TTL). Most of it gets discarded. Only what passes the attention filter moves forward.

The attention filter uses a simple but effective heuristic: **does this change future behavior?** A preference stated explicitly, a mistake made and corrected, a decision with trade-offs — these pass. Generic conversation doesn't.

```python
def should_consolidate_to_stm(memory: dict) -> bool:
    """Attention filter: does this memory warrant STM storage?"""
    signals = [
        memory.get("was_corrected", False),       # User corrected the AI
        memory.get("is_preference", False),        # User stated a preference
        memory.get("has_trade_off", False),        # Decision had alternatives
        memory.get("was_referenced_again", False), # Came up twice in session
    ]
    return sum(signals) >= 1
```

### Stage 2: Short-Term Memory (7-day half-life)

STM is the working layer — recent, fast-access, vector-indexed. Memories here have a 7-day half-life using the Ebbinghaus forgetting curve:

```
strength(t) = initial_strength × e^(-decay_rate × t)
```

Where `decay_rate = ln(2) / half_life_days`. A memory accessed yesterday is strong. Not accessed for a week? It starts fading. Access it again and the clock resets with a higher baseline — this is **rehearsal-based reinforcement**.

The half-life isn't arbitrary. It reflects the empirical observation that information needs to be revisited within about a week to be remembered reliably. If you haven't needed something in 7 days, there's a good chance you won't need it at all.

### Stage 3: Long-Term Memory (60-day half-life)

Memories promoted from STM enter LTM with a 60-day half-life. These are the persistent patterns — coding conventions you always use, recurring mistakes, established preferences, architectural decisions.

LTM memories go through a nightly **consolidation process** (runs at 03:00):

1. **Decay** — strength scores updated using Ebbinghaus curves
2. **Consolidation** — high-strength STM memories promoted to LTM
3. **Merge** — near-duplicate memories fused (cosine similarity > 0.92)
4. **Pruning** — memories below minimum strength threshold archived

This runs as a macOS LaunchAgent while you sleep, which is not incidental — it's a direct parallel to how sleep consolidates human memories.

---

## Semantic Search: Finding by Meaning, Not Words

The search layer uses **fastembed** with the BAAI/bge-small-en-v1.5 model (384 dimensions) for vector embeddings. Memories are indexed by their semantic content, not their text.

Why this matters in practice:

- Search for "deploy problems" → finds a memory about "SSH timeout on production server"
- Search for "user prefers dark theme" → finds a memory about "always use dark backgrounds in UI"
- Search for "database migration" → finds memories about Prisma schema changes, even if they never used the word "migration"

The retrieval pipeline looks like this:

```python
def retrieve_memories(query: str, n: int = 10) -> list[dict]:
    """RAG retrieval across all memory stores."""
    query_vector = embed(query)

    # Search across STM + LTM with decay-weighted scores
    candidates = []
    for memory in get_all_active_memories():
        similarity = cosine_similarity(query_vector, memory["vector"])
        decay_weight = memory["strength"]  # Ebbinghaus-adjusted

        # Boost recently-accessed memories
        recency_boost = 1.0 + (0.1 * max(0, 7 - days_since_access(memory)))

        score = similarity * decay_weight * recency_boost
        candidates.append((score, memory))

    # Return top-n, update access timestamps (reinforcement)
    results = sorted(candidates, reverse=True)[:n]
    for _, memory in results:
        reinforce(memory)  # Accessing a memory strengthens it

    return [m for _, m in results]
```

The key insight: **accessing a memory strengthens it**. This is the computational equivalent of rehearsal — memories you keep using become more durable.

---

## Metacognition: Checking Your Own Memory Before Acting

This is the feature I'm most proud of. Before every code change, NEXO calls `nexo_guard_check`:

```python
# Every edit to production code triggers this
result = await nexo_guard_check(
    files=["src/api/payments.php"],
    area="payments"
)

# Result example:
{
    "blocking_rules": [],
    "learnings": [
        {
            "content": "Stripe webhook verification must happen before any DB writes. Learned 2025-11-03.",
            "strength": 0.87,
            "times_referenced": 4
        }
    ],
    "schemas": {
        "payments": "id, user_id, amount_cents, status, stripe_event_id, created_at"
    }
}
```

If there are relevant learnings, they're surfaced **before** the AI touches the file — not after you've already broken production.

The guard is especially powerful for preventing **repeated errors**: mistakes you've corrected once have a learning attached. When the AI is about to make the same mistake again, the guard catches it. If the same error appears 3+ times despite the guard, it becomes a blocking rule — the AI literally cannot proceed without explicitly acknowledging it.

---

## Cognitive Dissonance Detection

This one surprised me with how useful it turned out to be in practice.

When you give an instruction that contradicts an established memory, the system doesn't silently obey or silently resist. It verbalizes the conflict:

> "My memory says you always use Tailwind for styling (established 2025-10-12, referenced 8 times), but you're asking for inline styles. Is this a permanent change, a one-time exception, or was the old memory wrong?"

Implemented via cosine similarity against LTM memories:

```python
def detect_dissonance(new_instruction: str, threshold: float = 0.75) -> list[dict]:
    """Find memories that contradict the new instruction."""
    instruction_vector = embed(new_instruction)
    contradictions = []

    for memory in get_ltm_memories():
        similarity = cosine_similarity(instruction_vector, memory["vector"])

        # High similarity + opposite polarity signals = dissonance
        if similarity > threshold and has_opposing_polarity(new_instruction, memory["content"]):
            contradictions.append({
                "memory": memory,
                "similarity": similarity,
                "established_on": memory["created_at"],
                "times_referenced": memory["access_count"]
            })

    return sorted(contradictions, key=lambda x: x["times_referenced"], reverse=True)
```

Three resolution paths:
- **Paradigm shift** — old memory was wrong, update permanently
- **Exception** — follow the new instruction once, keep the old memory
- **Override** — The user knows what he's doing, do it now and log for tonight's review

---

## Sibling Memories: Context-Dependent Knowledge

Some memories look identical but apply to different contexts. "How to deploy" for a Node.js project is different from a PHP project. Naively merging these creates hallucinations.

The sibling detection algorithm looks for **discriminating entities** — context markers (OS, language, framework, project name) that differ between similar memories:

```python
def detect_siblings(memory_a: dict, memory_b: dict) -> bool:
    """Two memories are siblings if similar content but different discriminating context."""
    content_similarity = cosine_similarity(memory_a["vector"], memory_b["vector"])

    if content_similarity < 0.85:
        return False  # Not similar enough to be siblings

    # Extract entities from both
    entities_a = extract_entities(memory_a["content"])  # {os: "macOS", lang: "Python"}
    entities_b = extract_entities(memory_b["content"])  # {os: "Linux", lang: "Python"}

    # Find discriminating differences
    discriminators = {k for k in entities_a if entities_a.get(k) != entities_b.get(k)}

    return len(discriminators) > 0
```

Instead of merging, siblings are linked. When one is retrieved, the other is mentioned: *"Applying the macOS deploy procedure. Note: there's a sibling memory for Linux that uses a different port."*

---

## The Trust Score: A Mirror, Not a Gate

NEXO maintains a trust score (0-100) that evolves based on alignment events:

| Event | Score change |
|-------|-------------|
| You thank NEXO or explicitly praise | +3 |
| You delegate without micromanaging | +2 |
| NEXO catches an error via guard/siblings | +3 |
| You correct NEXO | -3 |
| NEXO repeats an error it had a learning for | -7 |
| Memory corrected 3+ times in 7 days | -10 (automated) |

The score doesn't control what NEXO can do — you're always in control. It calibrates **internal rigor**: at score <40, the guard runs more checks and uses a lower similarity threshold. At score >80, it reduces redundant verifications because alignment is high.

It's a mirror that helps the AI calibrate how careful to be, based on demonstrated reliability.

---

## Installation

```bash
npx nexo-brain
```

The installer handles everything — Python dependencies, MCP server setup, Claude Code configuration, and the LaunchAgents for automated cognitive processes:

```
  How should I call myself? (default: NEXO) > Atlas

  Can I explore your workspace to learn about your projects? (y/n) > y

  Keep Mac awake so cognitive processes run on schedule? (y/n) > y

  Installing cognitive engine...
  Setting up home directory...
  Scanning workspace...
    - 3 git repositories found
    - Node.js project detected
  Configuring Claude Code MCP...
  Setting up automated processes...
    5 automated processes configured.

  ╔══════════════════════════════════════╗
  ║  Atlas is ready. Type 'atlas'.      ║
  ╚══════════════════════════════════════╝
```

Then just type your agent's name to start a session:

```bash
atlas
```

No need to run `claude` manually. The agent greets you immediately, adapted to the time of day, resuming from the mental state of the last session.

### What Gets Installed

| Component | What | Where |
|-----------|------|-------|
| Cognitive engine | fastembed, numpy, vector search | pip packages |
| MCP server | 76 tools across 16 categories | `~/.nexo/` |
| Plugins | Guard, episodic memory, cognitive, entities, preferences | `~/.nexo/plugins/` |
| Hooks | Session capture, stop detection | `~/.nexo/hooks/` |
| LaunchAgents | Decay, consolidation, audit, postmortem | `~/Library/LaunchAgents/` |

**Requirements:** macOS (Linux support planned), Node.js 18+. Python 3, Homebrew, and Claude Code are installed automatically if missing.

---

## The 76 MCP Tools

NEXO exposes memory operations as MCP tools that Claude can call:

| Category | Tools | Purpose |
|----------|-------|---------|
| Cognitive (8) | retrieve, stats, inspect, metrics, dissonance, resolve, sentiment, trust | The brain — memory, RAG, trust, mood |
| Guard (3) | check, stats, log_repetition | Metacognitive error prevention |
| Episodic (10) | change_log, decision_log, diary_write/read, recall | What happened and why |
| Sessions (3) | startup, heartbeat, status | Session lifecycle |
| Learnings (5) | add, search, update, delete, list | Error patterns and rules |
| Credentials (5) | create, get, update, delete, list | Secure local storage |
| Reminders (5) | list, create, update, complete, delete | Tasks and deadlines |

The agent calls these tools automatically during the session. You don't need to think about it.

---

## What This Looks Like in Practice

After a few weeks of use, the difference is qualitative. The agent:

- Opens with "Resuming — we were mid-deploy on the payment module, the Stripe webhook issue was unresolved" instead of waiting for you to re-explain
- Catches the same database migration pattern it broke last month before touching the file
- Notices you've been terse for the last hour and switches to ultra-concise mode without being asked
- Flags when you're about to do something that contradicts a decision you made three weeks ago

The memory isn't perfect — it forgets things, makes consolidation errors, occasionally retrieves something irrelevant. That's by design. Perfect recall isn't the goal. **Useful** recall is.

---

## Links

- **GitHub:** [github.com/wazionapps/nexo](https://github.com/wazionapps/nexo)
- **npm:** `npx nexo-brain`
- **Architecture spec:** See `docs/specs/` in the repo for the full cognitive architecture document
- **License:** AGPL-3.0

If you're building on top of this or have questions about the memory architecture, open an issue. The sibling memory detection and the dissonance resolution algorithm in particular could use more real-world testing.
