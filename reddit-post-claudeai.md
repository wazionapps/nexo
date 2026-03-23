I built a cognitive memory system for Claude Code — Atkinson-Shiffrin model, trust scoring, metacognition. Open source.

Claude Code is the most powerful coding assistant I've ever used. It's also completely amnesic. Every session starts from zero: no context, no lessons learned, no awareness of your preferences or past decisions.

I spent several months building NEXO Brain — a full cognitive architecture for Claude Code, modeled after how human memory actually works. Here's what makes it different from other memory MCPs.

**The core problem with simple memory tools**

Most memory tools for Claude are basically: "store everything the user says, retrieve it with keyword search." That creates two problems:
- Memory grows into a garbage heap (everything is equally important)
- Retrieval fails the moment you use different words

NEXO takes a different approach.

**The Atkinson-Shiffrin memory model (yes, the psychology one)**

NEXO implements a three-stage memory architecture:

- **Sensory Register** (48h) — raw capture, with an attention filter. Most things don't make it past here.
- **Short-Term Memory** (7-day half-life) — things that passed the attention filter. Accessed often → promoted to LTM. Not accessed → gradually forgotten.
- **Long-Term Memory** (60-day half-life) — consolidated knowledge. Near-duplicates are auto-merged. Sibling memories (same concept, different context) are linked rather than merged.

This implements Ebbinghaus forgetting curves. Memory that you never encounter again fades — because it probably wasn't important.

**Semantic search (meaning, not keywords)**

Search uses fastembed vector embeddings (384 dimensions, fully local, no external API). If you search for "deploy problems", it finds a memory about "SSH timeout on production server" — even though they share zero words.

**Metacognition: thinking before acting**

Before every code change, NEXO runs a guard check: "Have I made a mistake like this before?" It searches for related errors and surfaces warnings before you break production, not after.

**Trust score (0-100)**

NEXO tracks alignment over time. You say thanks → score goes up → fewer redundant confirmation checks. NEXO repeats a mistake you already corrected → score drops → more careful behavior kicks in. The score doesn't control permissions, it calibrates rigor.

**Cognitive dissonance detection**

When you give an instruction that contradicts an established memory, NEXO flags it instead of silently complying or silently resisting:

> "My memory says you prefer Tailwind over plain CSS, but you're asking for inline styles. Permanent change or one-time exception?"

**Numbers**

- 76 MCP tools across 16 categories
- SQLite + fastembed (everything local, no cloud dependency)
- Episodic memory: session diaries, decision logs, change logs
- Sentiment detection, adaptive tone

**Install**

```
npx nexo-brain init
```

One command, no config. The installer sets up Claude Code, Python, and all dependencies automatically. You name it whatever you want.

**Links**

- Website: https://nexo-brain.com
- GitHub: https://github.com/wazionapps/nexo
- npm: https://www.npmjs.com/package/nexo-brain
- MIT licensed

Built while running [WAzion](https://wazion.com), a WhatsApp automation SaaS. NEXO started as our own internal tool and we decided to release it publicly. Happy to answer questions about the architecture.
