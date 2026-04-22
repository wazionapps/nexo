# NEXO Brain — release discipline

Operational contract for cutting a new Brain release. Keeps the cadence
evidence-based and reproducible without relying on operator memory.

## TL;DR

```bash
# 1. Run the full pre-release wrapper. Fail means do not tag.
scripts/pre-release-verify.sh --release vX.Y.Z

# 2. If all checks pass, cut the release.
git tag vX.Y.Z && git push origin vX.Y.Z
```

## What the wrapper covers

`scripts/pre-release-verify.sh` chains the per-concern scripts already in
the repo — it does not re-implement any check. Each step stays
independently invocable:

| Step | Script | Purpose |
|------|--------|---------|
| `privacy` | `scripts/check_no_personal_data.sh` | Secrets / personal data never leaves the public repo. |
| `tool-map` | `scripts/verify_tool_map.py` | `tool-enforcement-map.json` is in sync with Python tool definitions (learning #335). |
| `release-ready` | `scripts/verify_release_readiness.py --ci` | Package + website + smoke artifacts consistent with each other. |
| `pytest` | `python3 -m pytest -q` | Every Brain test green. |
| `release-target` | inline shell checks | Tag `vX.Y.Z` is still free, `CHANGELOG.md` lists it, `package.json` version matches. Only runs when `--release vX.Y.Z` is passed. |

Skip any step with `--skip NAME` when iterating locally (do not skip in
CI). `--help` prints the full flag list.

## Why a wrapper instead of manual commands

- **Single entry point for CI.** One command, one exit code.
- **Locks step order.** Privacy is checked before anything else — a
  failure there must abort everything downstream.
- **Future-proof.** New checks go into their own canonical script and
  get added to the wrapper as a new `run_step` line.
- **Release-target gate.** The `vX.Y.Z` block catches the most common
  release-day mistakes (tag already pushed, missing changelog entry,
  forgotten `package.json` bump) without an external discipline doc.

## Release procedure (human checklist)

1. Confirm scope: every B/D/C item in `NEXO-MASTER-CHECKLIST.md` is
   closed, or explicitly deferred.
2. Bump `package.json` version and add the matching `CHANGELOG.md`
   entry under `## [vX.Y.Z] — YYYY-MM-DD` with the usual
   Added/Changed/Fixed sections.
3. `scripts/pre-release-verify.sh --release vX.Y.Z` → must exit 0.
4. Commit the bump on `main`. Tag `vX.Y.Z` and push the tag.
5. GitHub Action publishes to npm.
6. For Desktop coordination, `scripts/sync_release_artifacts.py` picks
   up the new package metadata — run it before cutting the Desktop
   release.

## Related

- Pre-commit hook (`scripts/hooks/pre-commit`) already blocks commits
  that desync `tool-enforcement-map.json` — see `scripts/install-hooks.sh`.
- `NF-RELEASE-DISCIPLINA-20260414` captured the original discipline
  breach that motivated this wrapper.
- `NF-DS-B232B713` captured the audit call for a unified pre-release
  entrypoint.
