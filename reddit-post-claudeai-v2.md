# Reddit Post — r/ClaudeAI v2

**Title:** Claude Code forgot everything I taught it — again. So I built it a real memory system (open source, all local)

---

You correct Claude on something. It says "got it." Next session? Blank slate. It makes the same mistake. You correct it again.

That loop kills flow. After the hundredth time, I stopped accepting it and built a fix.

**NEXO Brain** is a cognitive memory architecture for Claude Code — modeled after how human memory actually works, not a glorified text file. It's been running in production for 6+ months on a real €300K/year ecommerce business.

**Benchmark: LoCoMo F1 0.588 — +55% over GPT-4 on long-conversation memory.**

---

**What it actually does (before/after)**

*Before NEXO:*
> Me: "Don't use inline styles, always use utility classes."
> Claude: "Got it!"
> *Next session*
> Claude: `style="padding: 20px"`

*With NEXO:*
> Claude: "My memory says you prefer utility classes over inline styles, but you're asking for inline styles here. Permanent change or one-time exception?"

That's cognitive dissonance detection — Claude surfacing a conflict instead of silently ignoring it.

---

**The architecture (not a RAG wrapper)**

Three-stage memory modeled on Atkinson-Shiffrin psychology:

- **Sensory Register** (48h) — raw capture with attention filter. Most things don't survive.
- **Short-Term Memory** (7-day half-life) — things that mattered. Accessed often → promoted. Ignored → fades.
- **Long-Term Memory** (60-day half-life) — consolidated knowledge. Near-duplicates auto-merge. Related concepts link as siblings.

Ebbinghaus forgetting curves mean memory that was never relevant again quietly disappears. No garbage heap accumulation.

Search uses fastembed vector embeddings — local, 768-dim, no API call. "Deploy problems" finds "SSH timeout on production server" because it understands meaning, not keywords.

**Other things it tracks:**
- **Trust score (0–100):** You say thanks → fewer redundant checks. Claude repeats a corrected mistake → more careful behavior kicks in.
- **Metacognitive guard:** Before every code change, checks "have I made this mistake before?" Surfaces warnings before you break production.
- **Episodic memory:** Session diaries, decision logs, change logs. Next session, Claude knows *why* a decision was made, not just *what* it was.

---

**Install**

```
npx nexo-brain init
```

One command. Sets up everything — Claude Code config, Python, fastembed, SQLite. All local, nothing leaves your machine.

100+ MCP tools. AGPL-3.0.

GitHub: https://github.com/wazionapps/nexo

---

**What do you find most frustrating about Claude's memory between sessions?** I'm curious whether the pattern I built around (repeated corrections, lost context, no decision history) matches what others hit most.
