from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone


def _indexes(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA index_list({table})").fetchall()}


def _cols(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _insert_somatic(area: str, *, delta: float = 0.6, when: float | None = None) -> int:
    import db

    ts = datetime.fromtimestamp(when or time.time(), tz=timezone.utc).isoformat()
    cur = db.get_db().execute(
        """
        INSERT INTO somatic_events (timestamp, target, target_type, event_type, delta, source)
        VALUES (?, ?, 'area', 'repeated_error', ?, 'test')
        """,
        (ts, area, delta),
    )
    db.get_db().commit()
    return int(cur.lastrowid)


def _insert_adaptive_tension() -> int:
    import db

    cur = db.get_db().execute(
        "INSERT INTO adaptive_log (mode, tension_score, context_hint) VALUES ('TENSION', 0.9, 'private context')"
    )
    db.get_db().commit()
    return int(cur.lastrowid)


def _insert_memory_correction(area: str) -> int:
    import cognitive

    cur = cognitive._get_db().execute(
        """
        INSERT INTO memory_corrections (memory_id, store, correction_type, context)
        VALUES (1, 'ltm', 'override', ?)
        """,
        (f"{area}: corrected operational behavior",),
    )
    cognitive._get_db().commit()
    return int(cur.lastrowid)


def _insert_trust(score: float, *, event: str = "test", delta: float = 0.0) -> int:
    import cognitive

    cur = cognitive._get_db().execute(
        "INSERT INTO trust_score (score, event, delta, context) VALUES (?, ?, ?, 'test')",
        (score, event, delta),
    )
    cognitive._get_db().commit()
    return int(cur.lastrowid)


def _insert_protocol_task(area: str, *, high_stakes: bool = True, guard_blocking: bool = False) -> str:
    import db

    task = db.create_protocol_task(
        "sid",
        f"{area} task",
        task_type="execute",
        area=area,
        response_high_stakes=high_stakes,
        guard_has_blocking=guard_blocking,
    )
    return task["task_id"]


def _insert_cortex(area: str, *, impact: str = "critical") -> int:
    import db

    cur = db.get_db().execute(
        """
        INSERT INTO cortex_evaluations (goal, task_type, area, impact_level, alternatives, scores)
        VALUES ('goal', 'execute', ?, ?, '[]', '[]')
        """,
        (area, impact),
    )
    db.get_db().commit()
    return int(cur.lastrowid)


def _insert_outcome(area: str, *, status: str = "failed") -> int:
    import db

    cur = db.get_db().execute(
        """
        INSERT INTO outcomes (
            action_type, description, expected_result, status, deadline
        )
        VALUES (?, 'description', 'expected', ?, '2026-06-02')
        """,
        (area, status),
    )
    db.get_db().commit()
    return int(cur.lastrowid)


def test_operational_state_schema_and_uid_deterministic(isolated_db):
    import db
    import user_state_model
    from db import _schema

    first = user_state_model.build_operational_state_policy(
        area="release",
        task_type="execute",
        response_contract={"high_stakes": True, "mode": "answer"},
        now=1000.0,
    )
    second = user_state_model.build_operational_state_policy(
        area="release",
        task_type="execute",
        response_contract={"mode": "answer", "high_stakes": True},
        now=2000.0,
    )
    assert first["policy_uid"] == second["policy_uid"]
    assert len(user_state_model.list_operational_state_snapshots(area_key="release")) == 1

    conn = db.get_db()
    assert {
        "policy_uid",
        "area_key",
        "scope_key",
        "caution_level",
        "verification_requirement",
        "autonomy_limit",
        "reason_codes_json",
        "source_refs_json",
        "input_hash",
        "expires_at",
    } <= _cols(conn, "operational_state_snapshots")
    assert {
        "idx_operational_state_area_created",
        "idx_operational_state_scope",
        "idx_operational_state_expires",
    } <= _indexes(conn, "operational_state_snapshots")

    update_conn = sqlite3.connect(":memory:")
    update_conn.row_factory = sqlite3.Row
    _schema._m73_operational_state_snapshots(update_conn)
    _schema._m73_operational_state_snapshots(update_conn)
    assert "operational_state_snapshots" in {
        row["name"] for row in update_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def test_operational_state_area_isolation(isolated_db):
    import user_state_model

    sid = _insert_somatic("server")
    server = user_state_model.build_operational_state_policy(
        area="server",
        source_refs=[f"somatic_event:{sid}"],
        now=time.time(),
    )
    ads = user_state_model.build_operational_state_policy(
        area="ads",
        source_refs=[f"somatic_event:{sid}"],
        now=time.time(),
    )

    assert server["area_risk"] in {"medium", "high"}
    assert "somatic_event:%s" % sid in server["source_refs"]
    assert ads["area_risk"] == "low"
    assert ads["source_refs"] == []


def test_operational_state_auto_collects_recent_area_signals(isolated_db):
    import user_state_model

    sid = _insert_somatic("server", delta=0.8)
    server = user_state_model.build_operational_state_policy(
        area="server",
        source_refs=None,
        now=time.time(),
    )
    ads = user_state_model.build_operational_state_policy(
        area="ads",
        source_refs=None,
        now=time.time(),
    )

    assert f"somatic_event:{sid}" in server["source_refs"]
    assert server["area_risk"] in {"medium", "high"}
    assert f"somatic_event:{sid}" not in ads["source_refs"]
    assert ads["area_risk"] == "low"


def test_operational_state_global_trust_low_does_not_make_all_areas_max_caution(isolated_db):
    import user_state_model

    trust_id = _insert_trust(30, event="correction", delta=-5)
    for area in ("brain", "ads", "email"):
        policy = user_state_model.build_operational_state_policy(
            area=area,
            source_refs=[f"trust_score:{trust_id}"],
            now=time.time(),
        )
        assert policy["caution_level"] != "max_caution"
        assert policy["area_risk"] in {"low", "medium"}


def test_operational_state_hysteresis_requires_independent_refs(isolated_db):
    import user_state_model

    somatic_id = _insert_somatic("release", delta=1.0)
    one_source = user_state_model.build_operational_state_policy(
        area="release",
        source_refs=[f"somatic_event:{somatic_id}"],
        now=time.time(),
    )
    assert one_source["caution_level"] != "max_caution"

    task_id = _insert_protocol_task("release", high_stakes=True)
    cortex_id = _insert_cortex("release", impact="critical")
    outcome_id = _insert_outcome("release", status="failed")
    many_sources = user_state_model.build_operational_state_policy(
        area="release",
        task_type="execute",
        source_refs=[
            f"somatic_event:{somatic_id}",
            f"protocol_task:{task_id}",
            f"cortex_evaluation:{cortex_id}",
            f"outcome:{outcome_id}",
        ],
        response_contract={"high_stakes": True, "mode": "answer"},
        now=time.time(),
    )
    assert many_sources["caution_level"] == "max_caution"
    assert many_sources["verification_requirement"] == "release_gate"


def test_operational_state_decay_by_area(isolated_db):
    import user_state_model

    now = time.time()
    old_id = _insert_somatic("server", delta=1.0, when=now - 48 * 3600)
    new_id = _insert_somatic("server", delta=1.0, when=now)
    old_policy = user_state_model.build_operational_state_policy(
        area="server",
        source_refs=[f"somatic_event:{old_id}"],
        now=now,
    )
    new_policy = user_state_model.build_operational_state_policy(
        area="server",
        source_refs=[f"somatic_event:{new_id}"],
        now=now,
    )
    assert old_policy["area_risk"] == "low"
    assert new_policy["area_risk"] in {"medium", "high"}


def test_operational_state_explicit_instruction_precedence(isolated_db):
    import user_state_model

    sid = _insert_somatic("server", delta=1.0)
    policy = user_state_model.build_operational_state_policy(
        area="server",
        task_type="execute",
        source_refs=[f"somatic_event:{sid}"],
        response_contract={"high_stakes": True, "mode": "answer"},
        explicit_autonomy="act",
    )
    assert policy["autonomy_limit"] == "act"

    release_policy = user_state_model.build_operational_state_policy(
        area="release",
        task_type="execute",
        response_contract={"high_stakes": True, "mode": "answer"},
        explicit_autonomy="act",
    )
    assert release_policy["verification_requirement"] == "release_gate"
    assert release_policy["autonomy_limit"] == "propose"


def test_operational_state_thanks_does_not_skip_release_gates(isolated_db):
    import user_state_model

    trust_id = _insert_trust(80, event="explicit_thanks", delta=4)
    policy = user_state_model.build_operational_state_policy(
        area="release",
        task_type="execute",
        source_refs=[f"trust_score:{trust_id}"],
        response_contract={"high_stakes": False, "mode": "answer"},
    )
    assert policy["verification_requirement"] == "release_gate"
    assert policy["autonomy_limit"] == "propose"


def test_operational_state_no_internal_labels_visible(isolated_db):
    import user_state_model

    adaptive_id = _insert_adaptive_tension()
    policy = user_state_model.build_operational_state_policy(
        area="general",
        source_refs=[f"adaptive_log:{adaptive_id}"],
    )
    visible = policy["visible_guidance"]
    assert "TENSION" not in visible
    assert "max_caution" not in visible
    assert "cautious" not in visible
    assert "frustrated" not in visible


def test_operational_state_no_raw_private_text_persisted(isolated_db):
    import db
    import user_state_model

    secret_text = "token=sk_live_1234567890abcdef email francisco@example.com"
    policy = user_state_model.build_operational_state_policy(
        area="billing",
        task_type="answer",
        current_instruction=secret_text,
        response_contract={"high_stakes": True, "mode": "verify"},
    )
    row = db.get_db().execute(
        "SELECT * FROM operational_state_snapshots WHERE policy_uid=?",
        (policy["policy_uid"],),
    ).fetchone()
    blob = json.dumps(dict(row), ensure_ascii=False)
    assert "sk_live_1234567890abcdef" not in blob
    assert "francisco@example.com" not in blob
    assert policy["input_hash"]


def test_operational_state_simple_question_noop(isolated_db):
    import user_state_model

    policy = user_state_model.build_operational_state_policy(
        area="general",
        task_type="answer",
        response_contract={"high_stakes": False, "mode": "answer"},
    )
    assert policy["verification_requirement"] == "none"
    assert policy["autonomy_limit"] == "act"
    assert policy["communication_mode"] == "ultra_concise"
    assert "Respuesta corta" in policy["visible_guidance"]


def test_operational_state_server_correction_does_not_affect_ads(isolated_db):
    import user_state_model

    correction_id = _insert_memory_correction("server")
    server = user_state_model.build_operational_state_policy(
        area="server",
        source_refs=[f"memory_correction:{correction_id}"],
    )
    ads = user_state_model.build_operational_state_policy(
        area="ads",
        source_refs=[f"memory_correction:{correction_id}"],
    )
    assert server["area_risk"] in {"medium", "high"}
    assert server["privacy_level"] == "private"
    assert ads["area_risk"] == "low"
    assert ads["source_refs"] == []
