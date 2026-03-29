# Product Hunt Launch — NEXO Brain

## Tagline (60 chars max)
Give your AI coding agent a brain that remembers and learns

## Description
NEXO Brain transforms AI coding assistants from stateless tools into cognitive partners. Built on the Atkinson-Shiffrin memory model from cognitive psychology, it gives agents persistent memory that naturally decays, reinforces through use, and searches by meaning — not keywords.

Before every code change, it checks "have I made this mistake before?" A trust score calibrates rigor based on your interaction history. When new instructions contradict established knowledge, it flags the conflict instead of silently complying.

100+ MCP tools. SQLite + fastembed. Everything local, nothing leaves your machine.

Benchmarked: LoCoMo F1 0.588 (+55% vs GPT-4). Battle-tested for 6 months running a real ecommerce business.

One command: `npx nexo-brain init`

## Maker Comment (first comment, <800 chars)
I built NEXO Brain because I was tired of correcting Claude on the same things every session. After 6 months of running it on my own business (ecommerce, ads, server ops), I open-sourced it.

The key insight: AI memory shouldn't store everything forever. It should forget what's irrelevant and strengthen what matters — exactly like human memory works.

The most impactful feature isn't the vector search or the knowledge graph. It's the metacognitive guard: before editing any code, Claude checks its own mistake history. That single feature has prevented more production incidents than any test suite.

Would love feedback on the architecture. What would you want persistent AI memory to do that it currently doesn't?

## Categories
- AI Tools
- Developer Tools
- Open Source
- Productivity

## Topics/Tags
claude-code, mcp, memory, ai-agent, open-source, developer-tools

## Links
- Website: https://nexo-brain.com
- GitHub: https://github.com/wazionapps/nexo
- npm: https://www.npmjs.com/package/nexo-brain
- YouTube (1-min): https://www.youtube.com/watch?v=J0hCWnYU4UY

## Gallery Images Needed (5)
1. Hero: NEXO Brain logo + tagline + "npx nexo-brain init"
2. Architecture diagram (use nexo-brain-infographic-v4.png)
3. Before/After comparison (stateless vs cognitive)
4. Terminal screenshot showing installation
5. LoCoMo benchmark results chart
