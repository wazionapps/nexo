# NEXO v5.3.1 — packaged runtime normalization

Date: 2026-04-12

NEXO v5.3.1 is a packaging and update-path patch for real npm users.

## What changed

- `nexo update` now keeps packaged installs anchored to `~/.nexo` instead of drifting toward legacy `~/claude` paths or a source checkout.
- Packaged upgrades now refresh managed bootstrap and client artifacts after the runtime is updated.
- Installed runtimes no longer fail repo-only release-artifact checks that only make sense inside the source tree.
- Personal scripts, startup preflight, and helper/runtime path resolution now consistently use the canonical packaged home.

## Why this release exists

The public `5.3.0` line added `nexo uninstall`, but a normal user path still had edge cases where older installs could keep legacy path assumptions after update. This patch closes that gap so a user can install with npm, keep their own data under `~/.nexo`, work on the source repo separately if they want to, and still use `nexo update` as a normal packaged workflow.

## Upgrade

```bash
npm install -g nexo-brain@5.3.1
nexo update
```

Your data stays in `~/.nexo`; the runtime remains replaceable.
