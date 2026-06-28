# NEXO runtime core

This repository contains the local runtime core that powers NEXO Desktop.

NEXO Desktop is the public product and the supported user-facing installer,
update channel, onboarding surface, settings UI, and support path. The legacy
`nexo-brain` package and binary names remain temporarily for install/update
compatibility only; they are not a separate product line.

## Current contract

- Version `7.38.7` is the current packaged-runtime line
- Public product: NEXO Desktop
- Runtime role: local NEXO core bundled with Desktop
- Active systems: local memory, Deep Sleep, Evolution support-ticket mode,
  Skills, Watchdog, followups, doctor diagnostics, and MCP tooling
- Evolution mode: enabled by default for Desktop-managed installs; it never
  opens GitHub branches, pushes, PRs, transcripts, local databases, or raw
  private evidence, and routes product-improvement requests through sanitized
  support tickets
- Distribution: Desktop installers and update manifests are published through
  the NEXO Desktop release channel

Use [nexo-desktop.com](https://nexo-desktop.com) for downloads, billing,
support, and product information.

## Client parity

Release changes that touch bootstrap, session handling, Deep Sleep, client
sync, or shared runtime claims must also keep `docs/client-parity-checklist.md`
current.

- Managed via bootstrap + Codex config `mcp_servers.nexo`
- Managed MCP-only shared-brain metadata
- Runtime doctor parity audit
