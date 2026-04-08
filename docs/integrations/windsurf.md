# Windsurf Integration

Windsurf/Cascade works well with NEXO as a manual companion client: native MCP support, repo rules, and a good agentic editor loop, but without pretending it has the same hook depth as Claude Code.

## 1. Prerequisites

- Install NEXO locally with `npx nexo-brain`
- Verify the runtime with `nexo doctor`
- Decide whether you want local `stdio` MCP or remote HTTP MCP

## 2. Configure MCP in Windsurf

Windsurf supports MCP via the MCP marketplace/UI and by editing `~/.codeium/windsurf/mcp_config.json` directly.

Local `stdio` example:

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

Remote HTTP example when you run NEXO through `docker compose up -d`:

```json
{
  "mcpServers": {
    "nexo": {
      "serverUrl": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

After adding the server, refresh MCPs in Cascade so the tool list reloads.

## 3. Add NEXO startup rules

Windsurf supports durable rules in `.windsurf/rules/`, and its docs explicitly recommend repo `AGENTS.md` when you want something remembered durably.

Create `.windsurf/rules/nexo.md`:

```md
# NEXO shared-brain protocol

- Call `nexo_startup` once at session start.
- Call `nexo_heartbeat` on every new user turn.
- Open `nexo_task_open` before non-trivial work.
- Use `nexo_guard_check` before touching conditioned files.
- Close real work with `nexo_task_close` and evidence.
- Convert reusable corrections into `nexo_learning_add`.
```

If your repo already has an `AGENTS.md`, keep the durable NEXO protocol there and let Windsurf reuse it instead of duplicating longer operator instructions.

## 4. Verify the handshake

Inside Cascade, ask:

```text
Use the nexo MCP tools to call nexo_startup, then nexo_heartbeat, and report the session id.
```

If the tools do not appear:

- refresh the MCP list in Cascade
- inspect `~/.codeium/windsurf/mcp_config.json`
- check the Windsurf MCP settings panel for connection errors

## 5. Known limitations vs Claude Code

- No NEXO-managed hook stack in Windsurf.
- No automatic transcript parity for Deep Sleep today.
- Conditioned-file discipline has to be made explicit through rules and operator prompts.
- Repo `AGENTS.md` works well for durable behavior, but it is still prompt-level guidance, not native hook enforcement.

## 6. Best-use pattern

- Use Windsurf when you want NEXO tools plus editor-native agent UX.
- Use Claude Code or Codex for the deepest runtime discipline and shared-brain parity.
- Keep one `NEXO_HOME` and point every client at the same runtime.
