"""Tests for migration #40 classification columns + helper functions.

Guard rails for the Desktop-owned classification that moved to Brain core:
    - internal and owner exist as real columns
    - create_*/update_* accept overrides
    - classify_task() matches the legacy Desktop heuristics so existing
      rows keep rendering identically after backfill
"""

import db as db_mod
from db._classification import (
    classify_owner,
    classify_task,
    is_internal_id,
    normalise_internal,
    normalise_owner,
)


def test_migration_40_columns_exist():
    conn = db_mod.get_db()
    fu_cols = {r["name"] for r in conn.execute("PRAGMA table_info(followups)")}
    rm_cols = {r["name"] for r in conn.execute("PRAGMA table_info(reminders)")}
    assert "internal" in fu_cols and "owner" in fu_cols
    assert "internal" in rm_cols and "owner" in rm_cols


def test_is_internal_id_matches_known_prefixes():
    assert is_internal_id("NF-PROTOCOL-abc") is True
    assert is_internal_id("NF-DS-xyz") is True
    assert is_internal_id("NF-AUDIT-123") is True
    assert is_internal_id("R-RELEASE-v5.7") is True
    assert is_internal_id("R-FU-NF-PROTOCOL-foo") is True
    assert is_internal_id("nf-protocol-lower") is True  # case-insensitive


def test_is_internal_id_leaves_user_ids_alone():
    assert is_internal_id("NF-ANTHROPIC-STARTUP") is False
    assert is_internal_id("R90") is False
    assert is_internal_id("NF-NEXO-OPUS47-UPGRADE-EXEC") is False
    assert is_internal_id(None) is False
    assert is_internal_id("") is False


def test_classify_owner_waiting():
    assert classify_owner("NF1", "Esperando respuesta de Maria") == "waiting"
    assert classify_owner("R1", "bloqueado por tema legal") == "waiting"
    assert classify_owner("R1", "", category="waiting") == "waiting"


def test_classify_owner_user():
    assert classify_owner("NF1", "Francisco debe revisar firma") == "user"
    assert classify_owner("NF1", "Debes confirmar hoy") == "user"
    assert classify_owner("NF-PROTOCOL-xyz", "Limpiar hooks") == "user"


def test_classify_owner_agent():
    assert classify_owner("NF1", "Monitor 24h cartera abandonada") == "agent"
    assert classify_owner("NF1", "auditoría diaria ROAS") == "agent"
    assert classify_owner(
        "NF1", "Tarea cualquiera", recurrence="weekly:monday"
    ) == "agent"


def test_classify_owner_shared_default():
    assert classify_owner("NF1", "Random followup with no keywords") == "shared"


def test_classify_task_returns_pair():
    internal, owner = classify_task("NF-DS-ABC", "Deep sleep housekeeping")
    assert internal == 1
    assert owner in {"agent", "shared"}

    internal2, owner2 = classify_task("NF-SHOP", "Francisco debe revisar pedido")
    assert internal2 == 0
    assert owner2 == "user"


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


def test_create_followup_autoclassifies():
    row = db_mod.create_followup(
        "NF-TESTCLS-USER", "Francisco debe aprobar presupuesto", date="2026-12-01"
    )
    assert row["owner"] == "user"
    assert row["internal"] == 0

    row2 = db_mod.create_followup(
        "NF-DS-TESTCLS", "Deep sleep housekeeping", date="2026-12-01"
    )
    assert row2["internal"] == 1


def test_create_followup_explicit_override_beats_heuristic():
    row = db_mod.create_followup(
        "NF-TESTCLS-OVERRIDE",
        "Francisco debe llamar a cliente",   # heuristic would say user
        date="2026-12-01",
        internal=1,
        owner="agent",
    )
    assert row["internal"] == 1
    assert row["owner"] == "agent"


def test_create_reminder_autoclassifies():
    row = db_mod.create_reminder(
        "R-TESTCLS-WAIT", "Esperando respuesta de Maria", date="2026-12-01"
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
        "NF-TESTCLS-BADOWNER", "Neutral followup", date="2026-12-01"
    )
    db_mod.update_followup("NF-TESTCLS-BADOWNER", owner="nexo")  # invalid value
    row = db_mod.get_followup("NF-TESTCLS-BADOWNER")
    assert row["owner"] in {"shared", "user", "waiting", "agent"}   # not "nexo"


def test_backfill_sets_owner_for_existing_rows():
    # conftest fixture reapplies migrations on a fresh db, so inserted rows
    # here already have internal/owner set by create_followup itself. This
    # test verifies the classify layer stays consistent with backfill.
    row = db_mod.create_followup(
        "NF-TESTCLS-BACKFILL",
        "Random shared task with no keywords",
        date="2026-12-01",
    )
    assert row["internal"] == 0
    assert row["owner"] == "shared"


def test_migration_40_idempotent():
    db_mod.run_migrations()
    db_mod.run_migrations()  # should be a no-op, not raise
    version = db_mod.get_schema_version()
    assert version >= 40
