# NEXO runtime core

This repository contains the local runtime core that powers NEXO Desktop.

NEXO Desktop is the public product and the supported user-facing installer,
update channel, onboarding surface, settings UI, and support path. The legacy
`nexo-brain` package and binary names remain temporarily for install/update
compatibility only; they are not a separate product line.

## Current contract

- Public product: NEXO Desktop
- Runtime role: local NEXO core bundled with Desktop
- Active systems: local memory, Deep Sleep, Skills, Watchdog, followups,
  doctor diagnostics, and MCP tooling
- Removed system: Evolution background self-improvement loop and its legacy
  tool surfaces
- Distribution: Desktop installers and update manifests are published through
  the NEXO Desktop release channel

Use [nexo-desktop.com](https://nexo-desktop.com) for downloads, billing,
support, and product information.
