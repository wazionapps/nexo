# Docker Setup

NEXO already ships a `Dockerfile`. This guide adds the missing persistent `docker-compose.yml` flow so you can keep the brain in a volume and expose a remote MCP endpoint when useful.

## What the compose setup does

- runs the NEXO server in a container
- stores `NEXO_HOME` in a named volume
- exposes `http://localhost:8000/mcp`
- adds a container health check
- still supports Claude Code and Codex through stdio via `docker compose exec -T`

## 1. Start the containerized runtime

```bash
docker compose up -d
docker compose ps
```

Defaults:

- container name: `nexo`
- host port: `8000`
- MCP path: `/mcp`
- persistent volume: `nexo_data`

## 2. Verify the health check

```bash
docker compose ps
docker compose logs --tail=50 nexo
```

The service is healthy when the container is `Up` and the health column shows `healthy`.

## 3. Use the remote MCP endpoint from editor clients

Clients that support remote HTTP MCP can point at:

```text
http://127.0.0.1:8000/mcp
```

This is the easiest path for Windsurf, Cursor, and other MCP IDEs that accept HTTP or Streamable HTTP servers.

## 4. Use the same container from Claude Code or Codex

Claude Code and Codex work best with stdio. Keep the container running and configure their MCP entry as:

```json
{
  "command": "docker",
  "args": [
    "compose",
    "exec",
    "-T",
    "nexo",
    "python",
    "src/server.py"
  ]
}
```

That launches the same NEXO runtime inside the running container but keeps the client on the transport it expects.

## 5. Useful overrides

```bash
NEXO_MCP_PORT=8123 docker compose up -d
```

Environment knobs supported by the compose file:

- `NEXO_MCP_PORT`
- `NEXO_MCP_PATH`
- `NEXO_MCP_HOST`
- `NEXO_MCP_TRANSPORT`

## 6. When not to use Docker

If you want the full managed install, host-level client sync, launchers, and `nexo chat`, use `npx nexo-brain` directly on the host. Docker is best when you want:

- a persistent local service endpoint
- isolated runtime dependencies
- one shared brain behind several editor clients
