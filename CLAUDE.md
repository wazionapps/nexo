# NEXO Repo Claude Notes

If you change bootstrap, startup, Deep Sleep, client sync, or any public client-parity claim, read [docs/client-parity-checklist.md](docs/client-parity-checklist.md) first.

Keep these truths explicit:

- Claude Code remains the recommended path.
- Codex has strong practical shared-brain parity, but not 1:1 native hook parity.
- Claude Desktop is a shared-brain companion, not a full terminal/automation peer.

Before parity-related changes are considered done, run:

```bash
python3 scripts/verify_client_parity.py
```

Before any NEXO Desktop release that touches chat, renderer, or lifecycle behavior, run the installed-app live chat soak and require `LIVE_SOAK_OK`. The report must show at least 15 successful turns, a successful `sub-agent-task` turn, `archiveRestore.ok=true`, a successful `05-after-restore` turn, and screenshots for the send/final/archive/restore steps.
