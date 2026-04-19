# Client Parity Checklist

This checklist exists so Claude Code / Codex / Claude Desktop parity does not live only in release notes or maintainer memory.

Use it in three situations:

- before any release that touches bootstrap, startup, session handling, Deep Sleep, client sync, or shared-brain claims
- after adding a new client-facing runtime feature
- when an external client changes behavior and parity assumptions may have drifted

Quick verification command:

```bash
python3 scripts/verify_client_parity.py
python3 scripts/verify_release_readiness.py
```

This command is also enforced in CI through:

- `.github/workflows/verify-client-parity.yml`

## 1. Automatic Guardrails

These should be covered by tests, `nexo doctor`, or CI. If one of these is not automated yet, treat it as technical debt.

- [ ] Codex managed bootstrap still syncs into `~/.codex/AGENTS.md`
- [ ] Codex managed config still syncs model/reasoning defaults
- [ ] Codex managed config still persists `mcp_servers.nexo`
- [ ] Claude Desktop shared-brain metadata still syncs correctly
- [ ] Managed Claude/Codex bootstraps still instruct long multi-step work to use the durable workflow runtime
- [ ] Runtime doctor still audits recent Codex sessions for startup discipline
- [ ] Runtime doctor still audits recent Codex sessions for conditioned-file discipline
- [ ] Shared automation runner still records backend telemetry across Claude Code and Codex
- [ ] Runtime doctor still audits automation telemetry coverage before release metrics are trusted
- [ ] Runtime doctor still audits Claude Desktop shared-brain state
- [ ] Runtime doctor still checks weekly protocol compliance summaries
- [ ] Runtime doctor still detects release artifact drift before publish
- [ ] Runtime doctor / regression tests still reject new Claude-only assumptions
- [ ] Deep Sleep still reads both Claude Code and Codex transcript sources
- [ ] Deep Sleep long-horizon summaries still write daily + weekly + monthly artifacts
- [ ] Retrieval auto-mode still explains confidence + strategy honestly
- [ ] Associative retrieval still trims back to `top_k`

## 2. Release Checklist

Run this before a release whenever the touched area includes clients, startup, bootstrap, Deep Sleep, or public product claims.

- [ ] `PYTHONPATH=src pytest -q tests/`
- [ ] `python3 scripts/verify_release_readiness.py`
- [ ] `nexo doctor --tier runtime`
- [ ] Open a recent Codex session and verify it starts as NEXO
- [ ] Verify Codex still has bootstrap + managed config + `mcp_servers.nexo`
- [ ] Verify managed Claude/Codex bootstrap still mentions workflow runtime for long multi-step work
- [ ] Verify at least one recent automation run has usage/cost telemetry in `automation_runs`
- [ ] Verify Claude Desktop still points at the same shared brain
- [ ] Confirm Deep Sleep still sees both transcript families
- [ ] Confirm public capability matrix still matches reality
- [ ] Confirm README / website / blog copy does not overclaim parity

## 3. Monitoring / Keep Watching

These are not “unfinished features”. They are ongoing watchpoints because external clients can change underneath NEXO.

- [ ] Plain manual Codex sessions outside `nexo chat`
  - baseline parity is materially covered now by managed Codex config + doctor session audits
  - revalidate when Codex changes startup behavior
- [ ] New Claude-only assumptions in future features
  - baseline guardrail is shipped through regression tests + runtime audits
  - keep watching for drift around:
    - `~/.claude/projects`
    - Claude-only hook outputs
    - Claude-only session IDs

## 4. Blocked / Bounded By Client Surfaces

These are not “forgotten tasks”. NEXO already compensates as far as it reasonably can.

- [ ] Native hook parity with Claude Code
  - Claude Code still exposes hook surfaces that Codex does not expose the same way
  - NEXO compensates with bootstrap, managed config, startup discipline, shared automation runner telemetry, and doctor audits
- [ ] Claude Desktop deeper operational parity
  - shared-brain parity exists and is explicitly managed/audited
  - full terminal / automation / hook parity is outside the current Claude Desktop surface

## 5. Product Truth

Keep these statements honest everywhere:

- Claude Code remains the recommended path.
- Codex is a supported interactive client and automation backend.
- Claude Desktop is a supported shared-brain companion, not a full terminal/automation peer.
- “Parity” means strong practical shared-brain parity, not 1:1 identical native surfaces across all clients.

## 6. Where To Update This

If parity changes, update all relevant surfaces together:

- `README.md`
- `CONTRIBUTING.md`
- `docs/client-parity-checklist.md`
- public capability matrix on `gh-pages`
- changelog / release notes when needed
