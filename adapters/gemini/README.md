# Gemini CLI Adapter

This adapter lets Gemini CLI share the same local NEXO brain instead of running as a stateless separate assistant.

## What this adapter gives you

- A starter [`GEMINI.md`](./GEMINI.md) bootstrap mirroring the NEXO startup/protocol path
- MCP wiring guidance for `~/.gemini/settings.json`
- An explicit support matrix so Gemini is documented honestly as a companion path, not a hook-equivalent replacement for Claude Code

## 1. Configure Gemini CLI to load NEXO

Edit `~/.gemini/settings.json` or your repo-local `.gemini/settings.json`:

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
  },
  "context": {
    "fileName": [
      "GEMINI.md",
      "AGENTS.md"
    ]
  }
}
```

If you work from a repo checkout instead of the installed runtime, point `args[0]` at `/abs/path/to/nexo/src/server.py` and set `NEXO_CODE` as well.

## 2. Add the project bootstrap

Copy or adapt [`GEMINI.md`](./GEMINI.md) into your project root.

Gemini CLI will load it automatically, and you can inspect active context files with `/memory show`.

## 3. Verify MCP connectivity

Useful local checks:

```bash
gemini mcp list
gemini mcp add nexo python3 /Users/YOU/.nexo/server.py
gemini mcp list
```

## 4. Verify the startup/heartbeat flow

In the project directory, run a small headless prompt:

```bash
gemini -p "Call nexo_startup for this workspace, then nexo_heartbeat, then report the returned session id." --output-format text
```

If Gemini is authenticated and the MCP server is connected, you should see the tool calls succeed. If not:

- confirm Gemini authentication first
- run `/mcp list`
- run `/mcp reload`
- inspect `~/.gemini/settings.json` for path/env mistakes

## Support matrix

| Capability | Gemini CLI |
|------------|------------|
| Shared NEXO brain via MCP | Yes |
| `GEMINI.md` startup bootstrap | Yes |
| Managed installer sync by NEXO | Not yet |
| Hook parity with Claude Code | No |
| Transcript parity for Deep Sleep | Not yet |
| Good companion path today | Yes |

## Local validation

Validated locally on 2026-04-08 for:

- Gemini CLI presence (`gemini --help`)
- MCP management commands (`gemini mcp --help`)
- NEXO-compatible `mcpServers` config shape

The exact headless startup/heartbeat run still depends on Gemini CLI authentication in the local environment, so keep that verification step in your setup checklist.
