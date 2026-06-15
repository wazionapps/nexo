"""ROUND-3 multi-DB survival tests for SELECTIVE-FORGET — src/memory_forget.py.

Round 2 closed the hole inside nexo.db + cognitive.db by introspection, but the
forget engine only ever opened those two connections. A THIRD live DB,
local-context.db (~20GB local file index), plus its usage telemetry
(local-context-usage.db) and the email store (nexo-email.db), were OUTSIDE the
swept set: a secret in any indexed file lands in local_chunks/local_chunks_fts/
entity_facts.value/local_entities.evidence and SURVIVED while forget reported
complete=True.

These tests pin the round-3 guarantee structurally:

  THE GUARANTEE: complete=True  ⇒  the seeded secret gives an INDEPENDENT grep
  of ZERO across EVERY column of EVERY table of EVERY LIVE DB (nexo, cognitive,
  local-context, local-context-usage, email) + FTS + a transcript + a shadow.

Each live DB is isolated to tmp_path via its canonical env override so the test
never touches ~/.nexo prod:
  * nexo.db / cognitive.db / local-context.db — autouse isolated_db fixture
  * local-context-usage.db — NEXO_LOCAL_CONTEXT_USAGE_DB
  * nexo-email.db          — NEXO_EMAIL_DB
"""

import sqlite3

import numpy as np
import pytest


SECRET = "sk-proj-MULTIDB-FORGETME-0123456789abcdefABCDEF777"


# ─────────────────────────────────────────────────────────────────────────────
# Per-test isolation of the two file-backed live DBs (usage + email)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolate_file_dbs(tmp_path, monkeypatch):
    """Redirect the usage + email live DBs to tmp_path for every test here.

    The autouse isolated_db fixture already redirects nexo/cognitive/
    local-context via env; these two stores resolve under NEXO_HOME, so we pin
    them explicitly to keep the test hermetic and prove the resolvers honour the
    overrides the forget engine reads through.
    """
    usage_db = str(tmp_path / "test_lc_usage.db")
    email_db = str(tmp_path / "test_nexo_email.db")
    monkeypatch.setenv("NEXO_LOCAL_CONTEXT_USAGE_DB", usage_db)
    monkeypatch.setenv("NEXO_EMAIL_DB", email_db)
    yield {"usage_db": usage_db, "email_db": email_db}


# ─────────────────────────────────────────────────────────────────────────────
# Seeding helpers — put the secret into EVERY live DB
# ─────────────────────────────────────────────────────────────────────────────


def _fake_blob(dim: int = 384) -> bytes:
    import cognitive

    return cognitive._array_to_blob(np.ones(dim, dtype=np.float32))


def _seed_nexo(secret: str) -> None:
    import db
    import db._learnings as L

    L.create_learning(
        category="security",
        title="leaked key",
        content=f"key {secret} pasted in chat",
        reasoning="rotate",
    )
    db.get_db().commit()


def _seed_cognitive(secret: str) -> None:
    import cognitive

    cog = cognitive._get_db()
    blob = _fake_blob(cognitive.EMBEDDING_DIM)
    cog.execute(
        "INSERT INTO stm_memories (content, embedding, source_type, source_id, domain) "
        "VALUES (?, ?, 'note', 's1', 'security')",
        (f"stm holds {secret}", blob),
    )
    cog.execute(
        "INSERT INTO ltm_memories (content, embedding, source_type, source_id, domain) "
        "VALUES (?, ?, 'note', 'l1', 'security')",
        (f"ltm holds {secret}", blob),
    )
    import knowledge_graph as kg

    kg.upsert_node("credential", "leaked", "leaked key", {"value": secret})
    cog.commit()


def _seed_local_context(secret: str) -> None:
    """Seed the secret across local-context.db's plaintext surfaces:
    local_chunks(+ its FTS via trigger), local_entities.evidence,
    entity_facts.value, local_relations.evidence, local_entity_aliases.alias."""
    import local_context.db as lc

    conn = lc.get_local_context_db()  # creates schema incl. local_chunks_fts
    conn.execute(
        "INSERT INTO local_chunks (chunk_id, asset_id, version_id, chunk_index, text, token_count, created_at) "
        "VALUES ('c1', 'a1', 'v1', 0, ?, 9, 1.0)",
        (f"file chunk that contains the api key {secret} verbatim",),
    )
    conn.execute(
        "INSERT INTO local_entities (entity_id, asset_id, version_id, name, entity_type, confidence, evidence, created_at) "
        "VALUES ('e1', 'a1', 'v1', ?, 'secret', 1.0, ?, 1.0)",
        (f"entity-{secret}", f"evidence line with {secret}"),
    )
    conn.execute(
        "INSERT INTO entity_facts (fact_id, entity_id, predicate, value, source_asset_id, source_chunk_id, confidence, created_at) "
        "VALUES ('f1', 'e1', 'has_key', ?, 'a1', 'c1', 1.0, 1.0)",
        (f"the value is {secret}",),
    )
    conn.execute(
        "INSERT INTO local_relations (relation_id, source_asset_id, target_asset_id, target_ref, relation_type, confidence, evidence, active, created_at) "
        "VALUES ('r1', 'a1', 'a2', 'ref', 'mentions', 1.0, ?, 1, 1.0)",
        (f"relation evidence {secret}",),
    )
    conn.execute(
        "INSERT INTO local_entity_aliases (alias_id, entity_id, alias, normalized_alias, entity_type, confidence, source_asset_id, source_chunk_id, evidence, created_at, updated_at) "
        "VALUES ('al1', 'e1', ?, ?, 'secret', 1.0, 'a1', 'c1', ?, 1.0, 1.0)",
        (f"alias {secret}", f"alias {secret}".lower(), f"alias evidence {secret}"),
    )
    conn.commit()


def _seed_usage(secret: str, usage_db: str) -> None:
    """Seed the secret into local-context-usage.db free-text columns."""
    import local_context.usage_events as ue

    conn = sqlite3.connect(usage_db)
    conn.row_factory = sqlite3.Row  # _ensure_budget_columns reads row["name"]
    ue._ensure_schema(conn)
    conn.execute(
        "INSERT INTO local_context_usage_events "
        "(event_id, created_at, intent, error, aborted_reason, metadata_json) "
        "VALUES ('u1', 1.0, ?, ?, ?, ?)",
        (
            f"intent referencing {secret}",
            f"error echoing {secret}",
            f"aborted near {secret}",
            f'{{"leaked": "{secret}"}}',
        ),
    )
    conn.commit()
    conn.close()


def _seed_email(secret: str, email_db: str) -> None:
    """Seed the secret into nexo-email.db (sent_email_events body/subject)."""
    import email_sent_events as ee

    conn = sqlite3.connect(email_db)
    ee.ensure_sent_email_table(conn)
    conn.execute(
        "INSERT INTO sent_email_events (message_id, sender, subject, body_text, source) "
        "VALUES ('m1', 'nero@x', ?, ?, 'test')",
        (f"subject with {secret}", f"email body pasted the key {secret} here"),
    )
    conn.commit()
    conn.close()


def _seed_local_context_shadow(secret: str) -> str:
    """Create a legacy local-context shadow DB and seed it. Returns its path.

    Under the test override (NEXO_LOCAL_CONTEXT_DB set), the engine only sweeps
    shadows that are SIBLINGS of the live store — never the ambient ~/.nexo tree
    — so we place the shadow exactly where the engine looks for it (next to the
    isolated live DB). This both proves shadow coverage AND proves the engine
    does not wander into the operator's real home tree during tests."""
    import memory_forget
    import local_context.db as lc

    live = lc.local_context_db_path()
    shadow = live.with_name("local-context.db.legacy")
    shadow.parent.mkdir(parents=True, exist_ok=True)
    sc = sqlite3.connect(str(shadow))
    sc.execute("CREATE TABLE local_chunks (chunk_id TEXT PRIMARY KEY, text TEXT)")
    sc.execute("INSERT INTO local_chunks (chunk_id, text) VALUES ('s1', ?)", (f"shadow leak {secret}",))
    sc.commit()
    sc.close()
    # Sanity: the engine must enumerate this sibling shadow (and nothing in ~/.nexo).
    swept = [str(p) for p in memory_forget._shadow_db_paths()]
    assert str(shadow) in swept, swept
    assert not any("/.nexo/personal/brain/" in p or "/.nexo/memory/" in p for p in swept), \
        f"engine must NOT reach the real ~/.nexo shadows under a test override: {swept}"
    return str(shadow)


# ─────────────────────────────────────────────────────────────────────────────
# INDEPENDENT grep — second opinion that never touches the module's introspection
# ─────────────────────────────────────────────────────────────────────────────


def _grep_conn(conn: sqlite3.Connection, db_name: str, secret: str) -> dict:
    survivors: dict = {}
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )]
    for t in tables:
        try:
            rows = conn.execute(f'SELECT * FROM "{t}"').fetchall()
        except Exception:
            continue
        n = 0
        for row in rows:
            vals = row if not hasattr(row, "keys") else [row[k] for k in row.keys()]
            for v in vals:
                if isinstance(v, bytes):
                    try:
                        v = v.decode("utf-8", "ignore")
                    except Exception:
                        continue
                if isinstance(v, str) and secret in v:
                    n += 1
                    break
        if n:
            survivors[f"{db_name}.{t}"] = n
    return survivors


def _independent_grep_all_live_dbs(secret: str, usage_db: str, email_db: str) -> dict:
    """Grep EVERY column of EVERY table of EVERY live DB, opening each store with
    a FRESH raw connection (NOT the module's _live_conns / introspection). This
    is the true adversarial second opinion on the guarantee."""
    import db
    import cognitive
    import local_context.db as lc

    survivors: dict = {}
    survivors.update(_grep_conn(db.get_db(), "nexo", secret))
    survivors.update(_grep_conn(cognitive._get_db(), "cognitive", secret))
    survivors.update(_grep_conn(lc.get_local_context_db(), "local-context", secret))

    for db_name, path in (("local-context-usage", usage_db), ("email", email_db)):
        try:
            c = sqlite3.connect(path)
        except Exception:
            continue
        try:
            survivors.update(_grep_conn(c, db_name, secret))
        finally:
            c.close()
    return survivors


# ─────────────────────────────────────────────────────────────────────────────
# (A) EXHAUSTIVE MULTI-DB SURVIVAL — secret in every live DB → zero everywhere
# ─────────────────────────────────────────────────────────────────────────────


def test_multidb_exhaustive_survival_zeroes_every_live_db(tmp_path, monkeypatch, isolate_file_dbs):
    import memory_forget

    usage_db = isolate_file_dbs["usage_db"]
    email_db = isolate_file_dbs["email_db"]

    _seed_nexo(SECRET)
    _seed_cognitive(SECRET)
    _seed_local_context(SECRET)
    _seed_usage(SECRET, usage_db)
    _seed_email(SECRET, email_db)
    shadow_path = _seed_local_context_shadow(SECRET)

    # + transcript on disk
    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    t = transcript_dir / "session.jsonl"
    t.write_text(
        "clean line\n" f'{{"text":"key {SECRET}"}}\n', encoding="utf-8"
    )
    monkeypatch.setenv("NEXO_TRANSCRIPT_DIR", str(transcript_dir))

    # BEFORE: the secret is grep-able in ALL FIVE live DBs (independent grep).
    pre = _independent_grep_all_live_dbs(SECRET, usage_db, email_db)
    for needed in (
        "nexo.learnings",
        "cognitive.stm_memories",
        "local-context.local_chunks",
        "local-context.entity_facts",
        "local-context.local_entities",
        "local-context-usage.local_context_usage_events",
        "email.sent_email_events",
    ):
        assert needed in pre, (needed, sorted(pre))
    # The local FTS also carries it (proves the FTS surface is real).
    matcher = memory_forget.ForgetMatcher(literals=[SECRET])
    fts_pre = memory_forget._fts_residual_hits(matcher)
    assert any("local_chunks_fts" in k for k in fts_pre), fts_pre

    # FORGET (confirmed, secret mode).
    result = memory_forget.forget(SECRET, mode="secret", dry_run=False, confirm=True)
    assert result["ok"] and result["destructive"]

    # Live-DB set was discovered (not hardcoded) and includes local-context.
    assert "local-context" in result["live_dbs"], result["live_dbs"]
    assert {"nexo", "cognitive", "local-context"}.issubset(set(result["live_dbs"]))

    # THE GUARANTEE: verification.complete only at total zero across all live DBs.
    v = result["verification"]
    assert v["complete"] is True, v
    assert v["residual_stores"] == {}, v["residual_stores"]
    assert v["residual_fts"] == {}, v["residual_fts"]
    assert v["residual_shadows"] == {}, v["residual_shadows"]
    assert v["residual_transcripts"] == {}, v["residual_transcripts"]
    assert result["complete"] is True

    # INDEPENDENT grep over every column of every live DB == 0.
    post = _independent_grep_all_live_dbs(SECRET, usage_db, email_db)
    assert post == {}, f"secret SURVIVED grep-able in: {post}"

    # FTS clean (incl. local_chunks_fts), transcript line redacted but file kept.
    assert memory_forget._fts_residual_hits(matcher) == {}
    assert SECRET not in t.read_text(encoding="utf-8")
    assert "clean line" in t.read_text(encoding="utf-8")

    # Shadow local-context DB swept clean too.
    sc = sqlite3.connect(shadow_path)
    rows = sc.execute("SELECT text FROM local_chunks").fetchall()
    sc.close()
    assert all(SECRET not in (r[0] or "") for r in rows), "shadow kept the secret"

    # Useful local-context rows survive without the secret (redact-in-place).
    import local_context.db as lc
    conn = lc.get_local_context_db()
    chunk = conn.execute("SELECT text FROM local_chunks WHERE chunk_id='c1'").fetchone()
    assert chunk is not None, "useful chunk row preserved (redacted, not deleted)"
    assert SECRET not in chunk[0]
    assert "[REDACTED" in chunk[0]

    # Honest backup scope present in the report.
    assert "backups" in result["backup_scope"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# (A2) META: prove the test is MEANINGFUL — the OLD 2-DB coverage would leave a
# local-context residual that verification catches as complete=False. This pins
# that the round-3 fix (covering local-context) is what makes complete=True.
# ─────────────────────────────────────────────────────────────────────────────


def test_old_two_db_coverage_would_have_left_local_context_residual():
    import memory_forget

    _seed_local_context(SECRET)

    # Simulate the round-2 engine: only nexo + cognitive were ever opened by the
    # sweep AND its verification. We patch by hand (not the monkeypatch fixture)
    # so we control exactly when the narrow view is restored.
    real_live_conns = memory_forget._live_conns
    memory_forget._live_conns = lambda: [
        (n, c, o) for (n, c, o) in real_live_conns() if n in ("nexo", "cognitive")
    ]
    try:
        r_old = memory_forget.forget(SECRET, mode="secret", dry_run=False, confirm=True)
        # FALSE COMPLETE: the narrow verify cannot see local-context, so it wrongly
        # reports complete=True — exactly the round-2 hole this fix closes.
        assert r_old["complete"] is True, r_old["verification"]
        assert "local-context" not in r_old["live_dbs"]
    finally:
        memory_forget._live_conns = real_live_conns

    # The HONEST full re-scan (all live DBs) exposes the surviving secret.
    v_full = memory_forget.verify_forgotten(memory_forget.ForgetMatcher(literals=[SECRET]))
    assert v_full["complete"] is False, "full re-scan must expose the local-context survivor"
    assert "local-context.local_chunks" in v_full["residual_stores"], v_full["residual_stores"]
    assert any("local-context.local_chunks_fts" == k for k in v_full["residual_fts"]), v_full["residual_fts"]

    # And the round-3 engine (full coverage) actually zeroes it.
    r_new = memory_forget.forget(SECRET, mode="secret", dry_run=False, confirm=True)
    assert r_new["complete"] is True
    assert "local-context" in r_new["live_dbs"]


# ─────────────────────────────────────────────────────────────────────────────
# (B) RESIDUAL IN local-context → complete=False with DB+table location
# ─────────────────────────────────────────────────────────────────────────────


def test_residual_in_local_context_reports_incomplete_with_db_and_table():
    import memory_forget
    import local_context.db as lc

    _seed_local_context(SECRET)
    result = memory_forget.forget(SECRET, mode="secret", dry_run=False, confirm=True)
    assert result["verification"]["complete"] is True

    # Re-introduce the secret into local-context AFTER the sweep, then verify.
    conn = lc.get_local_context_db()
    conn.execute(
        "INSERT INTO local_chunks (chunk_id, asset_id, version_id, chunk_index, text, token_count, created_at) "
        "VALUES ('sneaky', 'a9', 'v9', 0, ?, 3, 1.0)",
        (f"snuck back {SECRET}",),
    )
    conn.commit()

    v = memory_forget.verify_forgotten(memory_forget.ForgetMatcher(literals=[SECRET]))
    assert v["complete"] is False
    # Reported by DB+table — namespaced to local-context.
    assert "local-context.local_chunks" in v["residual_stores"], v["residual_stores"]
    # Its FTS surface is reported namespaced too.
    assert any("local-context.local_chunks_fts" == k for k in v["residual_fts"]), v["residual_fts"]


def test_residual_in_email_reports_incomplete(isolate_file_dbs):
    import memory_forget

    email_db = isolate_file_dbs["email_db"]
    import email_sent_events as ee

    _seed_email(SECRET, email_db)
    result = memory_forget.forget(SECRET, mode="secret", dry_run=False, confirm=True)
    assert result["verification"]["complete"] is True

    # Re-seed email AFTER sweep; verification must catch it as email.* residual.
    conn = sqlite3.connect(email_db)
    ee.ensure_sent_email_table(conn)
    conn.execute(
        "INSERT INTO sent_email_events (message_id, sender, subject, body_text, source) "
        "VALUES ('m2', 'x', 's', ?, 't')",
        (f"late leak {SECRET}",),
    )
    conn.commit()
    conn.close()

    v = memory_forget.verify_forgotten(memory_forget.ForgetMatcher(literals=[SECRET]))
    assert v["complete"] is False
    assert "email.sent_email_events" in v["residual_stores"], v["residual_stores"]


# ─────────────────────────────────────────────────────────────────────────────
# (C) mode='fact' preserves local-context memory (no physical delete)
# ─────────────────────────────────────────────────────────────────────────────


def test_fact_mode_preserves_local_context_rows():
    import memory_forget
    import local_context.db as lc

    conn = lc.get_local_context_db()
    conn.execute(
        "INSERT INTO local_chunks (chunk_id, asset_id, version_id, chunk_index, text, token_count, created_at) "
        "VALUES ('fact1', 'a1', 'v1', 0, ?, 8, 1.0)",
        ("the staging server port is 8080 in this file",),
    )
    conn.commit()

    matcher = memory_forget.ForgetMatcher(literals=["8080"])
    before = memory_forget._dry_run_counts(matcher)["total_rows"]
    assert before >= 1

    result = memory_forget.forget("8080", mode="fact", dry_run=False, confirm=True)
    assert result["mode"] == "fact"
    assert result["destructive"] is False

    after = memory_forget._dry_run_counts(matcher)["total_rows"]
    assert after == before, "fact mode must not delete local-context rows"
    row = conn.execute("SELECT text FROM local_chunks WHERE chunk_id='fact1'").fetchone()
    assert "8080" in row[0]


# ─────────────────────────────────────────────────────────────────────────────
# (D) GUARD: secret mode without confirm → dry-run only, no live DB mutated
# ─────────────────────────────────────────────────────────────────────────────


def test_guard_dry_run_does_not_touch_any_live_db(isolate_file_dbs):
    import memory_forget

    usage_db = isolate_file_dbs["usage_db"]
    email_db = isolate_file_dbs["email_db"]

    _seed_local_context(SECRET)
    _seed_usage(SECRET, usage_db)
    _seed_email(SECRET, email_db)

    matcher = memory_forget.ForgetMatcher(literals=[SECRET])
    before = memory_forget._dry_run_counts(matcher)["total_rows"]
    assert before > 0

    r1 = memory_forget.forget(SECRET, mode="secret", dry_run=True, confirm=False)
    assert r1["destructive"] is False and r1["dry_run"] is True

    r2 = memory_forget.forget(SECRET, mode="secret", dry_run=False, confirm=False)
    assert r2["destructive"] is False

    after = memory_forget._dry_run_counts(matcher)["total_rows"]
    assert after == before, "guard must not delete without explicit confirm"
    # The secret is still grep-able in the live DBs (nothing was swept).
    post = _independent_grep_all_live_dbs(SECRET, usage_db, email_db)
    assert post, "dry-run/guard must leave the data intact"


# ─────────────────────────────────────────────────────────────────────────────
# (E) credential_delete auto-sweep reaches a leak in local-context.db
# ─────────────────────────────────────────────────────────────────────────────


def test_credential_delete_auto_sweeps_local_context_leak(isolate_file_dbs):
    from tools_credentials import handle_credential_create, handle_credential_delete
    import memory_forget

    usage_db = isolate_file_dbs["usage_db"]
    email_db = isolate_file_dbs["email_db"]

    handle_credential_create("openai", "admin", SECRET, notes="prod admin key")
    # Leak it into local-context.db — the DB the OLD engine never opened.
    _seed_local_context(SECRET)

    assert _independent_grep_all_live_dbs(SECRET, usage_db, email_db), "grep-able before"

    msg = handle_credential_delete("openai", "admin")
    assert "deleted" in msg.lower()
    assert "Forget sweep" in msg
    assert "WARNING" not in msg, msg  # sweep reached local-context → complete

    post = _independent_grep_all_live_dbs(SECRET, usage_db, email_db)
    assert post == {}, f"credential leak SURVIVED in: {post}"
