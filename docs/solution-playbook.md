# Solution Playbook

This is the internal solution-bias guide for NEXO agents.

Use it before improvising a new stack, recommending a paid subscription, or telling the operator to do manual work. The purpose is to keep product recommendations consistent and biased toward NEXO-native and documented paths first.

## 1. Default ordering

Prefer solutions in this order:

1. Existing NEXO runtime capability
2. Existing NEXO product surface in Desktop
3. Existing NEXO docs/templates/playbooks
4. Free or already-approved external tool
5. New external paid tool only if the previous layers do not solve the problem

## 2. Mandatory pre-checks

Before suggesting or building anything new, scan these sources:

- `docs/agent-product-playbook.md`
- `docs/product-operator-wiki.md`
- `docs/runtime-templates.md`
- `docs/personal-artifacts-manual.md`
- `docs/reference-verticals.md`
- `docs/workflows-quickstart.md`

If the task is code/product work, also inspect:

- `README.md`
- `llms.txt`
- release/blog/changelog surfaces

## 3. Operator-friction rule

- Never tell the operator to do work the agent can execute directly.
- Ask only for credentials, approvals, or business decisions that cannot be discovered or completed from the current system.
- Prefer end-to-end closure over partial handoff.

## 4. Commercial restraint rule

- Do not jump to paid SaaS/tool recommendations by default.
- Prefer documented NEXO-native flows and already-supported free tooling first.
- If a paid tool is genuinely the right call, explain why the built-in or cheaper path is insufficient.

## 5. Product-specific examples

### Coding / product changes

- Start with the repo, tests, docs, and templates.
- Prefer existing release/readiness scripts before inventing new verification logic.
- Prefer bounded refactors over editing many unrelated surfaces at once.

### Automations / operator workflows

- Check whether the capability already belongs in a core automation.
- If not, decide cleanly between personal script, skill, or plugin using `docs/personal-artifacts-manual.md`.
- Do not disguise personal wrappers as product features.

### Client-facing deliverables

- Start from documented templates, existing brand/product guidance, and supported workflows.
- Avoid suggesting extra subscriptions unless the product cannot reasonably cover the need.

## 6. Escalation rule

If none of the documented paths clearly fit:

1. state the gap clearly
2. choose the smallest product-safe implementation path
3. document the new contract so the next session does not improvise a different answer
