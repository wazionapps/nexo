# NEXO v5.0 Release Contract

Date: 2026-04-10
Status: active
Scope owner: NEXO

This document turns the v5.0 roadmap into an executable release contract.

## Why This Exists

v5.0 is not "more modules". It is the line where NEXO should be able to show:

- explicit goals
- strategy selection informed by outcomes
- structured learning from repeated outcomes
- skill evolution tied to evidence
- public proof that is inspectable, not hand-wavy

Without a contract, that story can drift into:

- foundations shipped without honest limits
- public claims that outrun the runtime
- version bumps that do not match actual release surfaces
- "it is basically done" instead of a checked publish path

## Distribution Truth

There are still two update channels:

1. Git installs update when changes merge to `main`.
2. Packaged channels update when a version tag is published and the publish workflow runs.

That means:

- merge to `main` is when git users can receive the code through `nexo update`
- tag/publish is when GitHub Release, npm, and website-facing packaged release surfaces are expected to align

## Branch and Publish Discipline

For v5.0 use this order:

1. finish work on the release branch
2. close roadmap ambiguity before bumping version
3. open PR to `main`
4. merge to `main`
5. smoke-check the git-install path
6. bump version, changelog, release artifacts, and public surfaces
7. run v5.0 smoke + release readiness
8. tag the release
9. let the publish workflow create packaged artifacts
10. update `gh-pages` surfaces that are not automatically generated
11. run final smoke checks across repo + website + release surfaces

Because `main` is protected, direct pushes are not the canonical release path.

## Machine-Readable Contract

The executable contract file is:

- `release-contracts/v5.0.0.json`

And the release smoke artifact is:

- `release-contracts/smoke/v5.0.0.json`

Use:

```bash
python3 scripts/run_v5_0_smoke.py
python3 scripts/verify_release_readiness.py \
  --contract release-contracts/v5.0.0.json
```

When all gates should be closed:

```bash
python3 scripts/verify_release_readiness.py \
  --contract release-contracts/v5.0.0.json \
  --require-contract-complete
```

## Gates

### Gate A - Foundation Closure

Must prove:

- v4.5 is actually closed, not merely "mostly done"
- v5.0 does not activate components that need more runtime history than is available
- final runtime audit stays green on a live installation

### Gate B - Goal Engine

Must prove:

- explicit goal profiles exist
- goal resolution is auditable in decision traces
- the same task can change recommendation under different goal profiles

### Gate C - Decision Cortex v2

Must prove:

- alternatives are ranked with goals, outcomes, and structured penalties
- overrides and later outcomes can be inspected
- quality/summary metrics exist

### Gate D - Structured Learning

Must prove:

- repeated outcome patterns can become durable learnings
- those learnings can influence a later decision in the same context
- promotion thresholds are explicit and conservative

### Gate E - Skill Evolution

Must prove:

- outcome-backed patterns can seed or promote reusable skills
- poor evidence can demote or retire skills
- at least one product-facing reuse surface changes because of that evidence

### Gate F - Benchmark Pack

Must prove:

- runtime benchmark pack is current
- compare artifacts are regenerated
- public proof is inspectable and scope-limited

### Gate G - System Coherence

Must prove:

- runtime, repo, website, and public claims tell the same story
- no critical runtime or protocol debt remains open
- update path remains safe for existing installations

### Gate H - Release Package

Must prove:

- package version and top changelog heading align
- release notes exist
- website/blog/changelog surfaces reflect the shipped release
- post-release smoke checks are recorded

## Operating Rule

Each workstream that closes a release gate must update the machine-readable contract in the same change.

The JSON file is the live checklist.
This markdown file is the explanatory policy around it.
