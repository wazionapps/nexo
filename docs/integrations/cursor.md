# Cursor Integration

Cursor works well as a NEXO companion client when you want MCP tools and persistent repo rules inside the editor, while keeping the main NEXO runtime local.

## 1. Prerequisites

- Install NEXO locally with `npx nexo-brain`
- Verify the runtime with `nexo doctor`
- Keep the local runtime path handy:
  - `NEXO_HOME` default: `~/.nexo`
  - server entrypoint: `~/.nexo/server.py` in installed setups, or `src/server.py` when working from the repo

## 2. Configure MCP in Cursor

Cursor supports MCP over `stdio`, `SSE`, and Streamable HTTP. The simplest NEXO setup is local `stdio`.

Example `mcp.json` entry:

```json
{
  "mcpServers": {
    "nexo": {
      "command": "python3",
      "args": [
        "/Users/YOU/.nexo/server.py"
      ],
      "env": {
        "NEXO_HOME": "/Users/YOU/.nexo"
      }
    }
  }
}
```

If you prefer the repo checkout instead of the installed runtime, point `args[0]` at `/abs/path/to/nexo/src/server.py` and set both `NEXO_HOME` and `NEXO_CODE`.

Verification:

```bash
cursor-agent mcp list
cursor-agent mcp list-tools nexo
```

## 3. Add a Cursor rule for NEXO

Create `.cursor/rules/nexo.mdc`:

```md
---
description: NEXO shared-brain startup and protocol discipline
alwaysApply: true
---

You are operating with the NEXO shared brain.

- Call `nexo_startup` once per session.
- Call `nexo_heartbeat` on every user message.
- For non-trivial work, open `nexo_task_open` before acting.
- For edits, verify with evidence and close via `nexo_task_close`.
- If touching conditioned files, run `nexo_guard_check` first.
- Capture reusable corrections with `nexo_learning_add`.
```

This gives Cursor the equivalent of a lightweight, repo-scoped bootstrap without pretending it has Claude Code hooks.

## 4. Verify the startup handshake

Open Cursor chat in the repo and ask:

```text
Call nexo_startup for this workspace, then nexo_heartbeat, then tell me the SID.
```

You should see the `nexo` tools available and the session registered in the returned payload. If Cursor claims the tool is unavailable, re-open MCP settings and check the MCP logs panel.

## 5. Known limitations vs Claude Code

- No managed NEXO hook suite inside Cursor today.
- No automatic Claude-style post-tool conditioned-file guardrail.
- No checked-in transcript parity for Deep Sleep; NEXO still remembers what is stored through MCP tools, but not the full Cursor conversation stream.
- If you want the deepest parity, keep Claude Code or Codex as the primary terminal client and use Cursor as an editor companion.

## 6. Recommended operating mode

- Keep the runtime local.
- Keep Cursor rules small and force the protocol path.
- Use Cursor for editing/navigation.
- Use Claude Code or Codex when you need full NEXO terminal discipline, hooks, or transcript-aware overnight analysis.
