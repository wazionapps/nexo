# Reference Verticals

These are the fastest paths to obvious value with NEXO.

## 1. Coding agent

Use when:
- the model edits real code
- the same files tend to regress
- you need continuity across sessions

Minimal flow:

```bash
nexo call nexo_task_open --input '{"sid":"SID","goal":"Patch bug in runtime doctor","task_type":"edit","area":"nexo","files":["/abs/path/runtime.py"]}'
nexo call nexo_guard_check --input '{"files":["/abs/path/runtime.py"],"area":"nexo"}'
```

Why it wins fast:
- file-scoped learnings block repeated mistakes
- workflow/goal state survives session resets
- `task_close` forces evidence and learning capture

## 2. Release operator

Use when:
- you package releases
- you need parity/readiness checks before publishing
- you want the release to stop lying about its own health

Minimal flow:

```bash
nexo doctor
python3 scripts/verify_client_parity.py
python3 scripts/verify_release_readiness.py --ci
python3 scripts/build_public_scorecard.py
```

Why it wins fast:
- parity and release checks are repo-native
- compare artifacts come from measured data
- workflow runtime keeps long release tasks resumable

## 3. Ops / automation owner

Use when:
- you maintain personal scripts, daemons, or scheduled jobs
- you want health checks on your own automation

Minimal flow:

```bash
nexo scripts list
nexo scripts doctor
nexo doctor
```

Why it wins fast:
- personal scripts are first-class registry entities
- keep_alive / degraded / duplicated state is visible
- self-audit converts recurring failures into preventive followups
