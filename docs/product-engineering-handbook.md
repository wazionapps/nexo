# Product Engineering Handbook

This handbook is the canonical guide for agents and operators doing product work on NEXO Brain and NEXO Desktop.

Use it when the job is not merely "write some code", but one of these:

- modify Brain/Desktop architecture
- ship or prepare a coordinated release
- migrate runtime installs safely
- decide whether something belongs in core, Desktop, or personal space
- scaffold new product-adjacent artifacts without improvising
- audit product/runtime parity, public surfaces, or managed-install behavior

If this handbook disagrees with an older memory note, follow this handbook and update the stale note.

## 1. Product mission

NEXO exists so the operator can ask for an outcome and the agent can get there with the least avoidable friction.

Product consequences:

- do the work directly whenever the system can do it safely
- ask only for real decisions, credentials, approvals, or external actions that the system truly cannot discover or execute
- prefer one verified closure over a chain of partial handoffs
- keep the runtime trustworthy, not merely clever

## 2. The three layers you must keep separate

Always classify the target before touching anything:

1. Product source
   - Brain repo
   - Desktop repo
2. Installed runtime
   - `~/.nexo/core`
   - `~/.nexo/runtime`
3. Personal/operator data
   - `~/.nexo/personal/*`

Rules:

- fix product behavior in source first
- move product changes into installs via release/update, not by live-editing installed core
- preserve personal data during cleanup or migration
- only remove superseded personal artifacts after the core replacement is real, configured, and active

## 3. Identity and language contract

- The agent presents as the configured assistant name.
- If architecture context matters, it says it runs on NEXO Brain architecture.
- It does not default to "I am a model / LLM / vendor assistant".
- `NEXO`, `NEXO Brain`, and `NEXO Desktop` are product names, not assistant defaults.
- New installs must not default the assistant name to `NEXO`.

## 4. Source-of-truth order

Before inventing a new pattern, check these in order:

1. runtime behavior implemented in code and tests
2. this handbook
3. `docs/agent-product-playbook.md`
4. `docs/product-operator-wiki.md`
5. `docs/personal-artifacts-manual.md`
6. `docs/runtime-templates.md`
7. `templates/`
8. release/public surfaces if the change is user-visible

If that stack still does not answer the question, update docs first instead of improvising a parallel rule.

## 5. Primitive decision map

Use this table before creating anything:

| Need | Correct primitive |
|---|---|
| ships to every user | core repo change |
| managed product UX | Desktop repo change |
| new local runtime callable tool | plugin |
| reusable agent procedure | skill |
| autonomous/scheduled/integration work | script |
| timing only for an existing script | schedule metadata / reconcile |

Canonical helper:

- `SK-CREATE-NEXO-PRIMITIVE`

Canonical docs:

- `docs/personal-artifacts-manual.md`
- `docs/runtime-templates.md`

## 6. Template contract

When scaffolding, start from shipped templates instead of half-remembered old files:

- `templates/script-template.py`
- `templates/script-template.sh`
- `templates/plugin-template.py`
- `templates/skill-template.md`
- `templates/skill-script-template.py`
- `templates/email-template.md`
- `templates/CLAUDE.md.template`
- `templates/CODEX.AGENTS.md.template`
- `templates/nexo_helper.py`

Do not create a shadow template tree under `personal/` unless the product docs explicitly require it.

## 7. Product contracts that must stay true

### 7.1 Desktop-managed installs

If Desktop governs the install:

- the install is desktop-managed
- bootstrap stays self-contained and non-technical
- future updates preserve that contract
- Claude is the supported runtime client unless an internal experimental gate says otherwise
- Evolution stays disabled

### 7.2 Core runtime protection

- `~/.nexo/core/**` is not a normal editing surface
- changes should go through repo source -> validation -> release/update
- personal wrappers must not silently become product dependencies

### 7.3 Automations are product features

These are core product automations, not "just Francisco scripts":

- `email-monitor`
- `followup-runner`
- `morning-agent`
- `nexo-send-reply.py` as a required helper where applicable

They must remain:

- toggleable from product surfaces
- schedulable without manual plist editing
- extensible through operator extra instructions without mutating the master prompt

## 8. Managed install and update contract

The final product contract for Desktop is:

- a non-technical Mac user installs and works without Terminal
- Desktop bootstraps Brain/runtime/dependencies
- Claude sign-in is guided from the product journey
- later updates keep the same contract
- updates do not leave legacy residue as a second source of truth

For Brain standalone:

- Brain must remain installable and usable without Desktop
- Desktop-specific closures must not break the public Brain path

## 9. Public-surface contract

Before release, keep these aligned with the actual product/runtime state:

- `README.md`
- `CHANGELOG.md`
- `llms.txt`
- `index.html`
- `blog/index.html`
- blog release page
- `changelog/index.html`
- `sitemap.xml`

Do not treat an unsigned QA DMG as a public release artifact.

## 10. Verification contract

Do not claim product closure from code edits alone. The minimum standard is:

1. relevant unit/contract tests green
2. continuity updated with evidence
3. source repos clean enough to explain what changed
4. release gates separated from machine-migration gates
5. live/runtime verification still called out when not yet executed

## 11. Nero product-engineer operating rules

When Nero is acting as a product engineer:

- use this handbook first, not stale session memory
- record sprint learnings into durable memory after reusable discoveries
- update the canonical docs/templates when the product contract changes
- do not open a new parallel "how NEXO works" explanation if the docs can be extended instead
- keep private skill guidance aligned with the public canonical docs

## 12. What counts as "done"

A product change is only "done" when:

- the primitive choice was correct
- the right source of truth was updated
- validation exists
- continuity notes reflect reality
- the change did not create a second hidden contract elsewhere
