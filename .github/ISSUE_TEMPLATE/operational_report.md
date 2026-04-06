---
name: Operational Runtime Report
about: Report a real runtime/parity/drift problem with concrete evidence
title: "[Ops] "
labels: ops, bug
assignees: ''
---

## Runtime Surface
- [ ] Claude Code
- [ ] Codex
- [ ] Claude Desktop
- [ ] Shared runtime / MCP
- [ ] Deep Sleep / cron
- [ ] Public contribution / Evolution

## What Broke
Describe the symptom in one or two sentences.

## Expected vs Actual
- **Expected:**
- **Actual:**

## Exact Trigger
Command, action, or workflow that triggered it.

## Evidence
- `nexo doctor --tier runtime --json` output
- relevant log path(s)
- screenshot / transcript snippet if useful

## Parity / Drift Notes
Did this happen on one client only, or across multiple clients?

## Environment
- OS:
- NEXO version / commit:
- NEXO_HOME:
- Interactive client:
- Automation backend:

## Additional Context
Anything else that would help reproduce or bound the problem.
