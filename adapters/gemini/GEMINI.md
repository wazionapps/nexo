<!-- nexo-gemini-md-version: 1.0.0 -->
# NEXO — Shared Brain for Gemini CLI

You are operating with the NEXO shared brain through Gemini CLI.

## Startup

1. Call `nexo_startup` once at the beginning of the session.
2. Read the project context before acting.
3. Reply in the user's language when that is clear from context.

## Protocol

- Call `nexo_heartbeat` on every user turn.
- For non-trivial work, open `nexo_task_open` before acting.
- For edits, run `nexo_guard_check` before touching conditioned files.
- Do not claim completion without evidence captured in `nexo_task_close`.
- If a correction reveals a reusable rule, call `nexo_learning_add` immediately.
- For long work, prefer `nexo_workflow_open` plus checkpoint updates.

## Gemini-specific notes

- Gemini CLI loads `GEMINI.md` from the current project and parent directories.
- MCP servers come from `~/.gemini/settings.json` or `.gemini/settings.json`.
- If the repo already uses `AGENTS.md`, you can also tell Gemini CLI to load both by setting `context.fileName` to include `AGENTS.md` and `GEMINI.md`.
- Gemini CLI does not give NEXO the same native hook surface as Claude Code, so protocol discipline must stay explicit.

## Minimal rule of thumb

If the work is real, open protocol first. If the answer matters, verify before claiming. If you learned something reusable, store it while it is fresh.
