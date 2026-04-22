# F0.6 Layout Contract

**Status:** canonical as of v7.0.0 (migration landed 2026-04-19).
**Audience:** anyone reasoning about what must exist under `~/.nexo/`,
what is compatibility shim, and what is a development-only opt-in.

This contract is the authoritative source for NEXO runtime layout decisions.
If `paths.py` and this document disagree, `paths.py` is right and this
document needs a patch. If a reviewer finds code touching paths that are
not listed here, stop and add them (or remove them) instead of inventing a
new ad-hoc rule.

---

## 1. Canonical F0.6 layout

Every post-migration install has:

```
~/.nexo/
├── core/                   ← code shipped by every release (untouchable)
│   ├── scripts/
│   ├── plugins/
│   ├── hooks/
│   ├── crons/
│   ├── rules/
│   ├── skills-runtime/
│   ├── templates/
│   └── assets/
├── personal/               ← operator-owned, never touched by `nexo update`
│   ├── scripts/
│   ├── config/
│   ├── brain/
│   ├── skills/
│   └── plugins/
├── runtime/                ← dynamic state, rotated by retention policies
│   ├── data/nexo.db
│   ├── logs/
│   ├── operations/
│   ├── coordination/
│   ├── backups/
│   ├── state/
│   ├── cognitive/
│   ├── exports/
│   └── memory/
├── bin/                    ← entrypoints (`nexo`, launcher stubs)
├── .venv/                  ← vendored interpreter + site-packages
└── .structure-version      ← "F0.6" marker; sentinel for migrator idempotence
```

Nothing else at the root is a first-class NEXO path.

---

## 2. Compatibility symlinks (shim contract)

The migrator preserves a small set of root-level names as **relative
symlinks** into `core/` (or `personal/`) so pre-F0.6 operator code that
hardcoded the flat layout keeps resolving. They are compatibility surface
only — NEW code must import from canonical paths.

| Root name                     | Target                | Retention                                           |
|-------------------------------|-----------------------|-----------------------------------------------------|
| `~/.nexo/scripts`             | `core/scripts`        | Kept through v7.x. Remove at v8.0.0 earliest.      |
| `~/.nexo/brain`               | `personal/brain`      | Kept through v7.x. Remove at v8.0.0 earliest.      |
| `~/.nexo/<module>.py` (~40x)  | `core/<module>.py`    | Kept while operator scripts still do `import <name>` without the ``core.`` prefix. Remove only when the import audit is clean. |

Rules for touching the shim set:
- Never create a NEW symlink as part of a feature. The list is closed;
  it shrinks over time, it does not grow.
- Never write into a symlink; write to the target and let the shim resolve.
- A symlink that no longer has a target is a failed migration — raise a
  doctor `critical` instead of silently deleting the dangling symlink.

`paths.legacy_*()` helpers (`legacy_scripts_dir`, `legacy_brain_dir`,
`legacy_data_dir`, `legacy_logs_dir`, `legacy_operations_dir`,
`legacy_db_path`, `legacy_watchdog_hashes_path`) are the single source of
truth for what counts as legacy. The doctor uses those helpers to
detect half-migration and to plan cleanup.

---

## 3. `core-dev/` — developer opt-in

`~/.nexo/core-dev/` is reserved for developers of NEXO Brain itself. It
lets a developer drop in experimental scripts/plugins without mutating
`core/` (which is protected by `hook_guardrails.py`).

Contract:
- In **production installs** this directory MUST be absent. A fresh
  install never creates it, `nexo update` never writes to it, and
  `nexo doctor` flags its presence with a `warn` severity.
- In **dev installs** the developer creates it manually (not through an
  NEXO command). The migrator is aware of it (see
  `auto_update.py::_classify_script_dir`) and preserves its contents
  across updates.
- `core-dev/` is **not** a promotion pipeline. Moving something from
  `core-dev/scripts/` into `core/scripts/` is a release-engineering
  decision that goes through the source repo and `nexo update`, never
  by copy-in-place.
- Ownership is always "core-dev" when the registry reads a script from
  this directory. It never inherits "core" identity.

If the doctor finds `core-dev/` on a packaged (non-dev) install, the
remediation is: confirm with the operator, then `rm -rf ~/.nexo/core-dev`.

---

## 4. Dashboard contract (`com.nexo.dashboard`)

The dashboard runs a Flask app that exposes a local-only HTTP surface
for runtime inspection. There are two supported modes; picking one is
an explicit decision, not an accident of install path.

### 4.1 Standalone mode (headless / terminal-only install)

- LaunchAgent `com.nexo.dashboard` is loaded.
- It binds to `127.0.0.1` on the port recorded in
  `~/.nexo/personal/config/schedule.json` under `dashboard.port`
  (default `6174`).
- The dashboard is the operator's primary inspection surface. Doctor
  expects it `RunAtLoad` and alive.
- `src/dashboard/app.py` is the single entrypoint. It never speaks to
  the Desktop IPC bridge.

### 4.2 Desktop-managed mode (NEXO Desktop app installed)

- LaunchAgent `com.nexo.dashboard` is **unloaded** on Desktop install.
  The Desktop app renders the same information natively from the shared
  brain DB, so two surfaces for the same data is avoided.
- Doctor does NOT alert on `com.nexo.dashboard` being absent when
  `product_mode.enforce_desktop_product_contract()` reports Desktop as
  the operator's product surface.
- If a developer needs the standalone dashboard for debugging, they
  launch it manually (`python3 -m dashboard.app --port 6174`) instead of
  reloading the LaunchAgent, because `nexo update` will unload it again
  on next run.

### 4.3 Switching modes

- Terminal-only → Desktop-managed: `nexo update` detects Desktop and
  unloads the LaunchAgent on its next run.
- Desktop-managed → Terminal-only: uninstall Desktop app + run
  `nexo update` to let the installer re-enable `com.nexo.dashboard`.

---

## 5. Residuals and cleanup windows

Half-migrated installs can leave these artifacts behind. None of them
are contract — they are cleanup targets.

| Residual                                       | Cleanup owner                                | Release target |
|------------------------------------------------|----------------------------------------------|----------------|
| `~/.nexo/__pycache__/` (flat layout cache)     | `nexo doctor --fix` on boot tier             | any            |
| `~/.nexo/*.db` stubs (pre-F0.6 DB leftovers)   | `auto_update._archive_legacy_root_db_stubs`  | v7.x           |
| `~/.nexo/backups/` (pre-F0.6 mis-routed)       | retention merge into `runtime/backups/`      | v7.x           |
| `~/.nexo/scripts/.watchdog-hashes` legacy file | folded + deleted by `_sync_watchdog_hash_registry` | v7.x    |
| `~/.nexo/.launchagent-f0-preflight/*`          | installer post-run cleanup                   | v7.x           |
| `*.bak`, `* 2`, `* 3` littering personal dirs  | `nexo scripts reconcile` / manual            | operator       |
| `~/.nexo-rollback-backup-*` (from rollback)    | operator decision, doctor surfaces size      | operator       |
| `~/.nexo-pre-f06-snapshot/`                    | kept until operator confident, then `rm -rf` | operator       |

The canonical rule: if a residual still has active consumers at release
time, its deadline slips one minor. If it has no consumers for two full
minors, doctor promotes the warning to `error` and the next release
removes the shim for good.

---

## 6. Doctor expectations

`nexo doctor --tier boot` MUST detect and report:

1. Missing canonical dir from Section 1 → `critical`.
2. Broken symlink from Section 2 → `critical`.
3. `core-dev/` present on a packaged install → `warn`.
4. Any file under `core/` written after the shipped release timestamp →
   `critical` (tamper signal).
5. `.structure-version` absent or not equal to `F0.6` on an install that
   already has `core/` populated → `critical` (half-migration).
6. LaunchAgent `com.nexo.dashboard` missing AND Desktop not installed →
   `warn`.
7. LaunchAgent `com.nexo.dashboard` loaded AND Desktop installed →
   `warn` (contract mismatch; see 4.2).
8. Any compatibility symlink from Section 2 pointing at a nonexistent
   target → `critical`.

Anything NOT in this list should be a `info` / `degraded` at most, not a
`critical`. Doctor noise erodes trust.

---

## 7. How to evolve this contract

- Adding a new canonical path: patch `paths.py`, add to Section 1, update
  the migrator + doctor, add a test that fresh-install places it
  correctly. Do all four or do none.
- Deprecating a shim from Section 2: schedule a doctor warning two
  minor releases before removal, then drop in the target release.
- Changing Dashboard mode semantics: update Section 4 first, then
  `product_mode.py`, then the Desktop app so the three never drift.
