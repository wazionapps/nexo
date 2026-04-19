"""Plan Consolidado F0.6 — canonical path helpers.

Every module that needs to find a runtime directory should import from
here instead of hardcoding `NEXO_HOME / "scripts"` etc. The legacy
flat layout (`~/.nexo/scripts/`, `~/.nexo/brain/`, `~/.nexo/data/`,
...) is going away in v7.0.0; this module centralises the new tree
so the migration is a one-line change per call site.

New structure (post-F0.6):
    ~/.nexo/
    ├── core/                  ← shipped with the package
    │   ├── db/
    │   ├── cognitive/
    │   ├── dashboard/
    │   ├── doctor/
    │   ├── scripts/           (38 packaged automations)
    │   ├── skills/
    │   ├── plugins/
    │   ├── hooks/
    │   ├── rules/
    │   └── contracts/         (resonance_tiers.json, ...)
    ├── core-dev/              ← dev-only, off by default
    │   └── scripts/
    ├── personal/              ← operator. nexo update never touches.
    │   ├── scripts/
    │   ├── skills/
    │   ├── plugins/
    │   ├── hooks/
    │   ├── rules/
    │   ├── brain/             (calibration.json, project-atlas.json,
    │   │                       operator-routing-rules.json, ...)
    │   ├── config/
    │   ├── lib/
    │   └── overrides/
    └── runtime/               ← dynamic state
        ├── data/              (nexo.db, *.db)
        ├── logs/
        ├── operations/
        ├── backups/
        ├── memory/
        ├── coordination/
        ├── exports/
        ├── nexo-email/
        ├── snapshots/
        └── crons/

Backwards compatibility: every helper has `legacy=True` mode that
returns the pre-F0.6 location (`NEXO_HOME / "<name>"`). The compat
layer disappears in v7.1.0; v7.0.0 keeps it so operator-edited code
that hardcoded the old paths keeps resolving via symlink during the
1-week observation window.
"""

from __future__ import annotations

import os
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))


def home() -> Path:
    """Return the active NEXO_HOME (recomputed every call so tests
    that monkeypatch the env var see the right path)."""
    return Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))


# ---------------------------------------------------------------------------
# Core (shipped with the package, replaced on every `nexo update`)
# ---------------------------------------------------------------------------
def core_dir() -> Path:
    return home() / "core"


def core_scripts_dir() -> Path:
    new = core_dir() / "scripts"
    legacy = home() / "scripts"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def core_plugins_dir() -> Path:
    new = core_dir() / "plugins"
    legacy = home() / "plugins"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def core_hooks_dir() -> Path:
    new = core_dir() / "hooks"
    legacy = home() / "hooks"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def core_rules_dir() -> Path:
    new = core_dir() / "rules"
    legacy = home() / "rules"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def core_contracts_dir() -> Path:
    return core_dir() / "contracts"


def core_skills_dir(*, allow_legacy_fallback: bool = True) -> Path:
    new = core_dir() / "skills"
    legacy = home() / "skills-core"
    if allow_legacy_fallback and not new.exists() and legacy.exists():
        return legacy
    return new


def core_db_dir(*, allow_legacy_fallback: bool = True) -> Path:
    new = core_dir() / "db"
    legacy = home() / "db"
    if allow_legacy_fallback and not new.exists() and legacy.exists():
        return legacy
    return new


def core_cognitive_dir(*, allow_legacy_fallback: bool = True) -> Path:
    new = core_dir() / "cognitive"
    legacy = home() / "cognitive"
    if allow_legacy_fallback and not new.exists() and legacy.exists():
        return legacy
    return new


def core_dashboard_dir(*, allow_legacy_fallback: bool = True) -> Path:
    new = core_dir() / "dashboard"
    legacy = home() / "dashboard"
    if allow_legacy_fallback and not new.exists() and legacy.exists():
        return legacy
    return new


def core_doctor_dir(*, allow_legacy_fallback: bool = True) -> Path:
    new = core_dir() / "doctor"
    legacy = home() / "doctor"
    if allow_legacy_fallback and not new.exists() and legacy.exists():
        return legacy
    return new


# ---------------------------------------------------------------------------
# Core-dev (off by default, only useful to product devs)
# ---------------------------------------------------------------------------
def core_dev_dir() -> Path:
    return home() / "core-dev"


def core_dev_scripts_dir() -> Path:
    return core_dev_dir() / "scripts"


# ---------------------------------------------------------------------------
# Personal (operator-owned, `nexo update` never touches)
# ---------------------------------------------------------------------------
def personal_dir() -> Path:
    return home() / "personal"


def personal_scripts_dir() -> Path:
    new = personal_dir() / "scripts"
    legacy = home() / "scripts"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def personal_plugins_dir() -> Path:
    new = personal_dir() / "plugins"
    legacy = home() / "plugins"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def personal_hooks_dir() -> Path:
    new = personal_dir() / "hooks"
    legacy = home() / "hooks"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def personal_rules_dir() -> Path:
    new = personal_dir() / "rules"
    legacy = home() / "rules"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def personal_skills_dir() -> Path:
    new = personal_dir() / "skills"
    legacy = home() / "skills"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def brain_dir() -> Path:
    """Operator brain: calibration, project atlas, routing rules, ..."""
    new = personal_dir() / "brain"
    legacy = home() / "brain"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def personal_config_dir() -> Path:
    new = personal_dir() / "config"
    legacy = home() / "config"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def personal_lib_dir() -> Path:
    return personal_dir() / "lib"


def personal_overrides_dir() -> Path:
    return personal_dir() / "overrides"


def config_dir() -> Path:
    """Canonical operator config dir.

    Post-F0.6 this lives in ``personal/config``. During the transition
    window we still honour a live legacy ``~/.nexo/config`` tree until
    the migrator has converted it into a shim/symlink.
    """
    return personal_config_dir()


# ---------------------------------------------------------------------------
# Runtime (dynamic state, never edited by hand)
# ---------------------------------------------------------------------------
def runtime_dir() -> Path:
    return home() / "runtime"


def data_dir() -> Path:
    new = runtime_dir() / "data"
    legacy = home() / "data"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def db_path() -> Path:
    new = data_dir() / "nexo.db"
    legacy = home() / "data" / "nexo.db"
    if not new.is_file() and legacy.is_file():
        return legacy
    return new


def logs_dir() -> Path:
    new = runtime_dir() / "logs"
    legacy = home() / "logs"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def operations_dir() -> Path:
    new = runtime_dir() / "operations"
    legacy = home() / "operations"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def backups_dir() -> Path:
    new = runtime_dir() / "backups"
    legacy = home() / "backups"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def memory_dir() -> Path:
    new = runtime_dir() / "memory"
    legacy = home() / "memory"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def cognitive_dir() -> Path:
    new = runtime_dir() / "cognitive"
    legacy = home() / "cognitive"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def coordination_dir() -> Path:
    new = runtime_dir() / "coordination"
    legacy = home() / "coordination"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def exports_dir() -> Path:
    new = runtime_dir() / "exports"
    legacy = home() / "exports"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def nexo_email_dir() -> Path:
    new = runtime_dir() / "nexo-email"
    legacy = home() / "nexo-email"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def doctor_dir() -> Path:
    new = runtime_dir() / "doctor"
    legacy = home() / "doctor"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def snapshots_dir() -> Path:
    new = runtime_dir() / "snapshots"
    legacy = home() / "snapshots"
    if not new.exists() and legacy.exists():
        return legacy
    return new


def crons_dir() -> Path:
    new = runtime_dir() / "crons"
    legacy = home() / "crons"
    if not new.exists() and legacy.exists():
        return legacy
    return new


# ---------------------------------------------------------------------------
# Combined views (for callers that need to scan core+personal merged)
# ---------------------------------------------------------------------------
def all_scripts_dirs() -> list[Path]:
    """Return every directory `nexo scripts list` should scan."""
    return [core_scripts_dir(), personal_scripts_dir(), core_dev_scripts_dir()]


def all_plugins_dirs() -> list[Path]:
    return [core_plugins_dir(), personal_plugins_dir()]


def all_hooks_dirs() -> list[Path]:
    return [core_hooks_dir(), personal_hooks_dir()]


def all_rules_dirs() -> list[Path]:
    return [core_rules_dir(), personal_rules_dir()]


# ---------------------------------------------------------------------------
# Legacy compat (PRE-F0.6 paths). Every shipped runtime keeps these as
# symlinks to the new locations until v7.1.0, so operator code that
# hardcoded the flat layout continues to resolve.
# ---------------------------------------------------------------------------
def legacy_scripts_dir() -> Path:
    return home() / "scripts"


def legacy_brain_dir() -> Path:
    return home() / "brain"


def legacy_data_dir() -> Path:
    return home() / "data"


def legacy_logs_dir() -> Path:
    return home() / "logs"


def legacy_operations_dir() -> Path:
    return home() / "operations"


def legacy_db_path() -> Path:
    return legacy_data_dir() / "nexo.db"


# ---------------------------------------------------------------------------
# Smart resolver: prefer new location if it exists, fall back to legacy.
# Used during the v7.0.0 / v7.1.0 transition window.
# ---------------------------------------------------------------------------
def resolve_db_path() -> Path:
    """Return the active SQLite DB path, preferring the new location
    but falling back to the legacy one when an older runtime hasn't
    migrated yet."""
    new = db_path()
    if new.is_file():
        return new
    legacy = legacy_db_path()
    if legacy.is_file():
        return legacy
    return new  # default: new layout for fresh installs


__all__ = [
    "NEXO_HOME",
    "home",
    "core_dir",
    "core_scripts_dir",
    "core_plugins_dir",
    "core_hooks_dir",
    "core_rules_dir",
    "core_contracts_dir",
    "core_dev_dir",
    "core_dev_scripts_dir",
    "personal_dir",
    "personal_scripts_dir",
    "personal_plugins_dir",
    "personal_hooks_dir",
    "personal_rules_dir",
    "personal_skills_dir",
    "brain_dir",
    "personal_config_dir",
    "personal_lib_dir",
    "personal_overrides_dir",
    "runtime_dir",
    "data_dir",
    "db_path",
    "logs_dir",
    "operations_dir",
    "backups_dir",
    "memory_dir",
    "cognitive_dir",
    "coordination_dir",
    "exports_dir",
    "nexo_email_dir",
    "doctor_dir",
    "snapshots_dir",
    "crons_dir",
    "all_scripts_dirs",
    "all_plugins_dirs",
    "all_hooks_dirs",
    "all_rules_dirs",
    "legacy_scripts_dir",
    "legacy_brain_dir",
    "legacy_data_dir",
    "legacy_logs_dir",
    "legacy_operations_dir",
    "legacy_db_path",
    "resolve_db_path",
]
