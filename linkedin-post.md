# LinkedIn Post

---

AI coding assistants are incredibly powerful. But they have one critical flaw: they forget everything between sessions.

Every correction you make? Gone. Every decision and its reasoning? Gone. Every preference you've established over months of working together? Start over.

I spent 6 months building a fix while running my ecommerce business. The result is NEXO Brain -- an open-source cognitive memory system for AI coding agents.

It's not another RAG wrapper. It implements the Atkinson-Shiffrin memory model from cognitive psychology (1968):

- Memories naturally decay over time unless reinforced by use (Ebbinghaus curves)
- Before every code change, the AI checks: "Have I made this mistake before?"
- A trust score (0-100) calibrates rigor -- more checks when alignment is low, more autonomy when it's high
- When you give an instruction that contradicts established knowledge, it surfaces the conflict instead of silently complying

The result: an AI that genuinely learns from experience and builds a working relationship over time.

Benchmarked at F1 0.588 on LoCoMo conversational memory (+55% vs GPT-4 baseline).

Everything runs locally. SQLite + fastembed vectors. No cloud dependency. No data leaving your machine.

100+ MCP tools. One command to install:

npx nexo-brain init

Open source (AGPL-3.0): https://github.com/wazionapps/nexo

Built it for myself. Now sharing it because I think every developer using AI daily deserves an assistant that actually remembers.

#AITools #DeveloperTools #OpenSource #ClaudeCode #CognitiveArchitecture #MCP
