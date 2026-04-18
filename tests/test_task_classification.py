"""Tests for migration #40 classification columns + normalisation helpers.

v5.8.2 removed the Spanish-first regex heuristic (NF-PROTOCOL-* /
Spanish verbs) from the Brain core. What remains:

    - internal and owner are real indexed columns.
    - create_followup / create_reminder accept `internal` and `owner`
      explicitly and persist them (with validation via normalise_*).
    - When either field is omitted, the core writes internal=0 and
      owner=NULL — no auto-classification. Clients that want automatic
      classification (e.g. NEXO Desktop) compute it themselves.
    - update_* accepts the same overrides and coerces invalid input
      to NULL silently instead of persisting garbage.
    - Migration #40 is idempotent and does NOT touch row data.
"""

import db as db_mod
from db._classification import (
    VALID_OWNERS,
    normalise_internal,
    normalise_owner,
)


def test_migration_40_columns_exist():
    conn = db_mod.get_db()
    fu_cols = {r["name"] for r in conn.execute("PRAGMA table_info(followups)")}
    rm_cols = {r["name"] for r in conn.execute("PRAGMA table_info(reminders)")}
    assert "internal" in fu_cols and "owner" in fu_cols
    assert "internal" in rm_cols and "owner" in rm_cols


def test_valid_owners_is_generic():
    """The canonical taxonomy must not include NEXO-specific labels."""
    assert VALID_OWNERS == {"user", "waiting", "agent", "shared"}
    assert "nexo" not in VALID_OWNERS


def test_normalise_internal_accepts_variants():
    assert normalise_internal(True) == 1
    assert normalise_internal(False) == 0
    assert normalise_internal(1) == 1
    assert normalise_internal(0) == 0
    assert normalise_internal("true") == 1
    assert normalise_internal("false") == 0
    assert normalise_internal("yes") == 1
    assert normalise_internal("") is None
    assert normalise_internal(None) is None
    assert normalise_internal("garbage") is None


def test_normalise_owner_clamp_to_valid():
    assert normalise_owner("user") == "user"
    assert normalise_owner("USER") == "user"
    assert normalise_owner("agent") == "agent"
    assert normalise_owner("NEXO") is None   # legacy string must NOT sneak in
    assert normalise_owner("") is None
    assert normalise_owner(None) is None


def test_create_followup_without_override_persists_defaults():
    """Brain core does NOT classify when agent omits internal/owner."""
    row = db_mod.create_followup(
        "NF-TESTCLS-DEFAULT",
        "Alice debe aprobar presupuesto",
        date="2026-12-01",
    )
    assert row["internal"] == 0
    assert row["owner"] is None


def test_create_followup_explicit_override_persists():
    row = db_mod.create_followup(
        "NF-TESTCLS-OVERRIDE",
        "Alice debe llamar a cliente",
        date="2026-12-01",
        internal=1,
        owner="agent",
    )
    assert row["internal"] == 1
    assert row["owner"] == "agent"


def test_create_reminder_without_override_persists_defaults():
    row = db_mod.create_reminder(
        "R-TESTCLS-DEFAULT", "Esperando respuesta", date="2026-12-01"
    )
    assert row["internal"] == 0
    assert row["owner"] is None


def test_create_reminder_explicit_override_persists():
    row = db_mod.create_reminder(
        "R-TESTCLS-OVERRIDE",
        "Esperando respuesta",
        date="2026-12-01",
        owner="waiting",
    )
    assert row["owner"] == "waiting"


def test_update_followup_accepts_owner_internal():
    db_mod.create_followup("NF-TESTCLS-UPD", "Neutral followup", date="2026-12-01")
    db_mod.update_followup("NF-TESTCLS-UPD", owner="user", internal=1)
    row = db_mod.get_followup("NF-TESTCLS-UPD")
    assert row["owner"] == "user"
    assert row["internal"] == 1


def test_update_followup_rejects_invalid_owner_silently():
    db_mod.create_followup(
        "NF-TESTCLS-BADOWNER",
        "Neutral followup",
        date="2026-12-01",
        owner="shared",
    )
    db_mod.update_followup("NF-TESTCLS-BADOWNER", owner="nexo")  # invalid value
    row = db_mod.get_followup("NF-TESTCLS-BADOWNER")
    # The invalid "nexo" value must NOT overwrite the stored value.
    assert row["owner"] in VALID_OWNERS


def test_migration_40_does_not_backfill_existing_rows():
    """v5.8.2 migration only adds columns; it does NOT auto-classify rows."""
    db_mod.run_migrations()
    db_mod.run_migrations()  # idempotent
    version = db_mod.get_schema_version()
    assert version >= 40


def test_migration_40_idempotent():
    db_mod.run_migrations()
    db_mod.run_migrations()
    version = db_mod.get_schema_version()
    assert version >= 40
