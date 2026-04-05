# NEXO Repo Agent Notes

If you touch any of these areas, read [docs/client-parity-checklist.md](docs/client-parity-checklist.md) before changing code:

- bootstrap
- startup/session handling
- Deep Sleep
- client sync
- shared-brain claims in docs/public copy

Non-negotiable maintenance rules:

- Keep the public client capability matrix honest.
- Do not claim 1:1 native hook parity between Claude Code and Codex.
- Treat Claude Desktop as shared-brain / MCP-only unless the product surface really changes.
- Do not relax Claude-only regression allowlists casually.
- When parity-related code changes, run:

```bash
python3 scripts/verify_client_parity.py
```

This repo already has automated guardrails and CI for client parity. Extend them when possible instead of relying on memory or release-note archaeology.
