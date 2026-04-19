# Public Contribution

NEXO has two contribution paths:

1. **Normal human contribution**
2. **Opt-in automated public-core evolution**

The second path is intentionally constrained. It exists to create useful draft work without letting autonomous runs publish unsafe or personal data.

## Human Contributions

Use the normal GitHub flow when you are contributing manually:

- open a bug report or operational report
- include `nexo doctor --tier runtime --json` when relevant
- include parity/runtime symptoms, not only the final failure
- prefer concrete artifacts over vague summaries

## Opt-In Public-Core Evolution

An install can opt into public contribution through Draft PRs.

Rules:

- never auto-merge
- never publish personal scripts, local runtime data, prompts, logs, or secrets
- only touch the allowed public-core paths
- stay paused only while its own Draft PR is still open
- resume once maintainers merge or close that Draft PR

If the machine already has its own public Draft PR open, Evolution now reuses the cycle for **peer review** of other opt-in PRs instead of idling.

Peer review mode can only:

- leave a technical comment
- approve a scoped PR

Peer review mode can never:

- merge
- rebase
- push to another contributor branch

## Best Reports For NEXO

The highest-value community input is not generic “it broke”.

The best reports include:

- exact command or tool path used
- client/backend involved: Claude Code, Codex, Claude Desktop
- expected vs actual behavior
- runtime doctor output
- whether the issue is startup, parity, Deep Sleep, automation, hooks, or public contribution

## Files That Help New Contributors

- `.github/ISSUE_TEMPLATE/operational_report.md`
- `.github/ISSUE_TEMPLATE/bug_report.md`
- `.github/PULL_REQUEST_TEMPLATE.md`

These templates are tuned to feed NEXO with concrete operational evidence instead of vague bug descriptions.
