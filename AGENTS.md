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

## Cross-repo dependency: NEXO Desktop

NEXO Desktop (separate repo at `../nexo-desktop`) **bundles this repo's
checkout into its `.dmg`/`.exe`** at build time via
`extraResources: { from: "../nexo", to: "brain-bundle" }`. There is no
pinned Brain version in Desktop — whatever sits in `../nexo` HEAD when
Desktop runs `npm run dist:release` is what ships to clients.

What that means for changes here:

- **Internal Brain change** (Python logic, hook, cron, plugin, MCP tool
  body, refactor, perf): no Desktop coordination needed. Bump version
  + commit + push + `npm publish`. Existing Desktop installs receive
  the new Brain via Brain's own auto-update (`src/auto_update.py` runs
  `npm install -g nexo-brain@latest` periodically inside WSL/Mac).
- **Brain ↔ Desktop contract change** (touching ANY of the items below):
  Desktop needs a fresh build to ship. Tell whoever maintains Desktop,
  or open an issue tagged `desktop-impact` so the next Desktop release
  picks it up:
  - `src/desktop_bridge.py` (the IPC surface Desktop's renderer talks to)
  - Hooks Desktop dispatches (`hooks/session-start.sh`, `hooks/stop.py`,
    etc.) — output shape changes
  - `calibration.json` / `profile.json` schema (Desktop reads these)
  - `bin/nexo-brain.js` flags / behaviour Desktop relies on
    (`--yes`/`--skip` setup mode, onboarding completion markers,
    `nexo-brain --version` output)
  - MCP tool names/signatures the renderer wires to UI (Settings,
    Onboarding wizard, etc.)
  - Anything touched by `desktop_bridge` request handlers

When in doubt, grep Desktop for the symbol you renamed or moved:

```bash
rg "your_symbol_here" ../nexo-desktop
```

If there is a hit, the change is `desktop-impact`. Coordinate the
Desktop rebuild before tagging a Brain release that ships to clients.
