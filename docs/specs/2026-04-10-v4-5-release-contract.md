# NEXO v4.5 Release Contract

Date: 2026-04-10
Status: active
Scope owner: NEXO

This document turns the v4.5 roadmap into a release contract that can be executed, checked, and reviewed inside the repo.

## Why This Exists

The roadmap defines direction. The release contract defines what must be true before v4.5 can be claimed, merged, tagged, published, and mirrored on the website.

Without this contract, a release can drift into:

- features without closure
- public claims without artifacts
- package/repo/website mismatch
- personal operator memory as the only source of release truth

## Distribution Truth

There are two update channels and they must never be conflated:

1. Git installs update when changes merge to `main`.
2. Packaged release channels update when a version is tagged and the publish workflow runs.

That means:

- merging to `main` is the moment git-based users can receive new code through `nexo update`
- tagging/publishing is the moment npm/GitHub Release/other packaged surfaces are updated

## Branch and Publish Discipline

For v4.5 use this order:

1. work on a release branch
2. keep workstream commits atomic
3. open PR to `main`
4. merge to `main`
5. smoke-check the git-install path
6. bump version/changelog/release artifacts
7. tag the release
8. let publish workflow create packaged artifacts
9. update `gh-pages` and public website surfaces that are not fully generated elsewhere
10. run final smoke checks across repo + website + release surfaces

Because `main` is protected, direct pushes are not the canonical release path.

## Machine-Readable Contract

The executable contract file is:

- `release-contracts/v4.5.0.json`

It tracks:

- release line and target version
- distribution rules
- required repo files
- required website files
- gate definitions and their current status

The release readiness verifier can load this contract with:

```bash
python3 scripts/verify_release_readiness.py \
  --contract release-contracts/v4.5.0.json
```

And later, when all gates should be closed:

```bash
python3 scripts/verify_release_readiness.py \
  --contract release-contracts/v4.5.0.json \
  --require-contract-complete
```

## Gates

### Gate A - Manual Canonical

Must prove:

- the repo has one canonical guide for personal artifacts
- README/docs link to it
- unsupported schedule syntax is not documented as real

### Gate B - Scope Freeze

Must prove:

- v4.5 scope is explicit
- v5.0 no-scope is explicit
- no release-critical ambiguity remains about what belongs where

### Gate C - Outcome Loop

Must prove:

- Outcome Tracker exists
- at least two real integration points use it
- outcomes can resolve to `met` / `missed` with evidence

### Gate D - Priority Loop

Must prove:

- Impact Scoring orders at least one real queue
- fallback behavior is explicit when no score exists

### Gate E - Decision Loop

Must prove:

- cortex can evaluate alternatives for high-impact work
- recommendation and override are both logged

Current v4.5 implementation path:

- `nexo_cortex_decide` ranks 2+ alternatives on top of the existing Cortex
- `nexo_cortex_override` preserves intentional human/operator overrides
- `nexo_task_close` opens protocol debt on high-stakes action tasks if no Cortex evaluation was persisted

### Gate F - Public Proof

Must prove:

- compare scorecard is current
- public claims map to measured or inspectable artifacts

### Gate G - Reliability

Must prove:

- golden paths pass end to end
- no critical runtime drift remains

### Gate H - CLI Core Runtime

Must prove:

- personal scripts can call core tools through the canonical CLI path
- cron/subprocess runtime is covered, not only interactive Claude Code runtime
- no runtime drift such as missing `fastmcp` remains in the CLI -> core path

### Gate I - Release Package

Must prove:

- changelog
- release notes
- website updates
- blog/changelog/public surfaces
- post-release smoke checks

## Operating Rule

Each workstream that closes one of these gates must update the JSON contract in the same change.

The JSON file is the live checklist.
This markdown file is the explanatory policy around it.
