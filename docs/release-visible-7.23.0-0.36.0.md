# Visible Changes - Brain 7.23.0 and Desktop 0.36.0

Work date: 2026-05-19.

## Brain 7.23.0

- Before answering, Brain has a pre-response router that asks whether useful context already exists: previous work, authorship, touched files, decisions, evidence, artifact location, or runtime diagnostics.
- The router no longer depends on a fixed list in Desktop: Desktop sends `intent=auto` and Brain decides what to inspect.
- There is a virtual evidence ledger that unifies tasks, workflows, changes, diaries, continuity snapshots, lifecycle events, local-context queries, and registered evidence; this makes it easier to answer "did I already do this?", "where is it?", and "what proof do I have?".
- Memory Observations now have a convergent processor with CLI/MCP for backfill, repair, and SLA; it reduces stored information that is later not consumed.
- New read-only audits cover saved-not-used, automations, MCP live/catalog, transcripts, and artifact location; they find real gaps without touching data.
- The release privacy guard uses `rg` when available, avoiding `grep -R` hangs during final verification.
- The test suite is more robust against DB connection contamination between tests, especially in the evidence ledger.
- Evolution, security, and signing/certificates remain out of scope by Francisco's decision and are not part of this release.

## Desktop 0.36.0

- Each visible turn consults Brain before writing to Claude, after bootstrap/protocol/continuity, with timeout/fail-open behavior so chat is not blocked.
- The router payload travels through stdin (`--payload-stdin`), not argv, to avoid exposing user text, paths, or secrets in process arguments.
- Chat preserves large lifecycle payloads through `--payload-file`, avoiding degradation from argv limits.
- Support inside Preferences is more readable: skeleton/loading, refresh with spinning icon, status chips, preview, update date, and differentiated customer/support replies.
- Recoverable chat errors such as timeout/network/interrupted process have bounded auto-retry with backoff, without duplicating turns if the conversation is already busy or archived.
- The ghost `human-input-pending` lock no longer hijacks the composer: if Claude has already continued autonomously, Desktop clears the lock; if a question is truly pending, it shows a notice and takes the user to the question without sending or creating a red error.
- The React residue inventory is updated so temporary growth in `ComposerControls.jsx` and `SupportTab.jsx` stays visible and controlled, not hidden debt.

## Validation obtained so far

- Desktop `0.36.0`: full `npm run check` green after rebuilding local dependencies with `npm ci`.
- Desktop includes: React build, lint, unit tests, product contracts, syntax smoke, and verified bundled hashes.
- Brain `7.23.0`: privacy guard, tool-map sync, and release-readiness are green; the final wrapper is still running full pytest at this moment.
