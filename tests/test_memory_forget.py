"""Tests for SELECTIVE-FORGET (Ola 4) — src/memory_forget.py.

All tests run against the isolated temp DBs provided by conftest's
``isolated_db`` autouse fixture (never ~/.nexo prod). They cover the
SECURITY GUARANTEE: if ``verification.complete is True`` the secret is NOT
grep-able in ANY column of ANY table of BOTH DBs, in ANY FTS index, in any
on-disk transcript, or in any legacy shadow cognitive DB.

  (a) EXHAUSTIVE SURVIVAL: seed the secret in MANY tables (incl. the 8 stores
      that previously survived the curated registry) + FTS + transcript +
      shadow → forget(secret, confirm) → grep of the secret in EVERY column of
      EVERY table of both DBs + FTS + files + shadow == 0, and complete=True.
  (b) VERIFICATION HONESTY: a residual the forget pass cannot reach →
      complete=False with the survivor listed.
  (c) CORRECT-FACT preserves useful memory (no physical delete, reversible).
  (d) GUARD: mode='secret' without confirm → dry-run only, nothing deleted.
  (e) credential_delete auto-sweep cleans a leak in diary_archive (a store
      OUTSIDE the old registry) and surfaces a residual warning when it can't.
  (f) item_history.note free-text secret is redacted under mode='secret'.
"""

import json

import numpy as np
import pytest


SECRET = "sk-proj-FORGETME0123456789abcdefABCDEF9999"


# ─────────────────────────────────────────────────────────────────────────────
# Seeding helpers
# ─────────────────────────────────────────────────────────────────────────────


def _fake_blob(dim: int = 384) -> bytes:
    import cognitive

    return cognitive._array_to_blob(np.ones(dim, dtype=np.float32))


def _seed_cognitive(secret: str) -> None:
    """Insert the secret into stm/ltm/quarantine/kg/somatic (cognitive.db)."""
    import cognitive

    cog = cognitive._get_db()
    dim = cognitive.EMBEDDING_DIM
    blob = _fake_blob(dim)

    cog.execute(
        "INSERT INTO stm_memories (content, embedding, source_type, source_id, domain) "
        "VALUES (?, ?, 'note', 's1', 'security')",
        (f"OpenAI key leaked: {secret}", blob),
    )
    cog.execute(
        "INSERT INTO ltm_memories (content, embedding, source_type, source_id, domain) "
        "VALUES (?, ?, 'note', 'l1', 'security')",
        (f"Durable memory containing {secret} that must vanish", blob),
    )
    cog.execute(
        "INSERT INTO quarantine (content, embedding, source_type, source_id) "
        "VALUES (?, ?, 'inferred', 'q1')",
        (f"quarantined secret {secret}", blob),
    )
    import knowledge_graph as kg

    kg.upsert_node("credential", "leaked-key", "Leaked OpenAI key", {"value": secret})
    cog.execute(
        "INSERT INTO somatic_markers (target, target_type, risk_score) VALUES (?, 'key', 0.9)",
        (f"target-{secret}",),
    )
    cog.commit()


def _seed_nexo_core(secret: str) -> None:
    """Insert the secret into learnings/observations/change_log/decisions/diary/
    resolution_cache/hot_context/recent_events (the original registry surfaces)."""
    import db

    conn = db.get_db()

    import db._learnings as L

    L.create_learning(
        category="security",
        title="Leaked key",
        content=f"The compromised key is {secret} and was pasted in chat",
        reasoning="rotate immediately",
    )

    import db._episodic as E

    E.log_change(
        session_id="t",
        files="config.env",
        what_changed=f"added {secret}",
        why="debug",
    )

    conn.execute(
        "INSERT INTO decisions (session_id, domain, decision) VALUES ('t', 'security', ?)",
        (f"use key {secret}",),
    )
    conn.execute(
        "INSERT INTO session_diary (session_id, decisions, summary) VALUES ('t', ?, ?)",
        (f"decided to keep {secret}", "diary summary with no secret"),
    )
    conn.execute(
        "INSERT INTO memory_observations "
        "(observation_uid, created_at, updated_at, observation_type, summary, entities_json) "
        "VALUES ('obs1', 1.0, 1.0, 'fact', ?, '[]')",
        (f"observed secret {secret}",),
    )
    conn.execute(
        "INSERT INTO resolution_cache "
        "(cache_key, kind, intent, area, sid, result_json, source_fingerprint, "
        " source_refs_json, change_watermark, status, policy_version, resolved_at, "
        " expires_at, hit_count, content_snapshot_json) "
        "VALUES ('ck1', 'route', '', '', '', ?, 'fp', '[]', 0, 'fresh', 'v1', 1.0, 0, 0, ?)",
        (json.dumps({"answer": f"value {secret}"}), json.dumps({"ref": secret})),
    )
    conn.execute(
        "INSERT INTO hot_context (context_key, title, summary, first_seen_at, last_event_at, "
        " expires_at, created_at, updated_at) VALUES ('hc1', 'incident', ?, 1, 1, 9e12, 1, 1)",
        (f"hot context mentions {secret}",),
    )
    conn.execute(
        "INSERT INTO recent_events (event_type, title, summary, body, created_at, expires_at) "
        "VALUES ('note', 'leak', 'summary', ?, 1, 9e12)",
        (f"event body with {secret}",),
    )
    conn.commit()


# The 8 stores the adversarial verification found SURVIVING the old curated
# registry. Each gets the secret embedded in a useful free-text column.
def _seed_registry_survivors(secret: str) -> None:
    import db

    conn = db.get_db()

    conn.execute(
        "INSERT INTO item_history (item_type, item_id, event_type, note, actor, metadata, created_at) "
        "VALUES ('learning', '1', 'supersede', ?, 'agent', '{}', 1.0)",
        (f"superseded because the key {secret} was pasted",),
    )
    conn.execute(
        "INSERT INTO diary_archive (session_id, created_at, decisions, summary, source, archived_at) "
        "VALUES ('old', 1.0, ?, 'archived diary', 'migration', 1.0)",
        (f"old decision referencing {secret}",),
    )
    conn.execute(
        "INSERT INTO historical_diary_index "
        "(source_backup_path, source_table, source_row_id, session_id, created_at, summary, content_hash, indexed_at) "
        "VALUES ('/b', 'session_diary', '1', 'old', 1.0, ?, 'h', 1.0)",
        (f"historical summary with {secret}",),
    )
    conn.execute(
        "INSERT INTO memory_events (event_uid, created_at, source_type, event_type, actor, metadata_json) "
        "VALUES ('ev1', 1.0, 'tool', 'note', 'agent', ?)",
        (json.dumps({"leaked": secret}),),
    )
    conn.execute(
        "INSERT INTO continuity_snapshots (conversation_id, event_type, payload_json, created_at) "
        "VALUES ('c1', 'snapshot', ?, 1.0)",
        (json.dumps({"context": f"remember {secret}"}),),
    )
    conn.execute(
        "INSERT INTO session_checkpoints (sid, task, current_goal, decisions_summary, created_at, updated_at) "
        "VALUES ('sid1', 'task', ?, 'decisions', 1.0, 1.0)",
        (f"goal: deploy with {secret}",),
    )
    conn.execute(
        "INSERT INTO session_diary_draft (sid, summary_draft, created_at, updated_at) "
        "VALUES ('sid2', ?, 1.0, 1.0)",
        (f"draft summary holding {secret}",),
    )
    conn.execute(
        "INSERT INTO transcript_index "
        "(source_client, conversation_id, session_id, path_ref, sanitized_summary, content_hash, indexed_at) "
        "VALUES ('claude_code', 'c2', 's3', '/t', ?, 'h2', 1.0)",
        (f"transcript summary that leaked {secret}",),
    )
    conn.commit()


def _build_matcher(secret: str):
    import memory_forget

    return memory_forget.ForgetMatcher(literals=[secret])


def _grep_all_columns(secret: str) -> dict:
    """Independent grep: scan EVERY column of EVERY table of BOTH DBs for the
    raw secret substring. This does NOT use the module's introspection so it is
    a true second opinion on the guarantee."""
    import sqlite3
    import db
    import cognitive

    survivors: dict = {}
    for db_name, conn in (("nexo", db.get_db()), ("cognitive", cognitive._get_db())):
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
                for v in (row if not hasattr(row, "keys") else [row[k] for k in row.keys()]):
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


# ─────────────────────────────────────────────────────────────────────────────
# (a) EXHAUSTIVE SURVIVAL — secret seeded everywhere → zero everywhere
# ─────────────────────────────────────────────────────────────────────────────


def test_exhaustive_survival_hard_forget_zeroes_everything(tmp_path, monkeypatch):
    import memory_forget

    _seed_cognitive(SECRET)
    _seed_nexo_core(SECRET)
    _seed_registry_survivors(SECRET)

    # + transcript on disk
    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    t = transcript_dir / "session.jsonl"
    t.write_text(
        "clean line\n"
        f'{{"role":"user","text":"here is the key {SECRET}"}}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("NEXO_TRANSCRIPT_DIR", str(transcript_dir))

    matcher = _build_matcher(SECRET)

    # BEFORE: the secret is grep-able in MANY tables — far more than the old
    # curated registry covered — proving the survivor gap is real.
    pre_grep = _grep_all_columns(SECRET)
    assert len(pre_grep) >= 18, pre_grep  # core + survivors + fts copies
    # The 8 previously-surviving stores are all present before forget.
    for survivor in (
        "nexo.item_history", "nexo.diary_archive", "nexo.historical_diary_index",
        "nexo.memory_events", "nexo.continuity_snapshots", "nexo.session_checkpoints",
        "nexo.session_diary_draft", "nexo.transcript_index",
    ):
        assert survivor in pre_grep, (survivor, pre_grep)
    assert memory_forget._fts_residual_hits(matcher), "grep-able in FTS before"
    assert memory_forget._scan_transcripts(matcher, [t]), "grep-able in transcript before"

    result = memory_forget.forget(SECRET, mode="secret", dry_run=False, confirm=True)

    assert result["ok"] is True
    assert result["destructive"] is True

    # THE GUARANTEE: complete=True only when the total re-scan is zero.
    v = result["verification"]
    assert v["complete"] is True, v
    assert v["residual_stores"] == {}, v["residual_stores"]
    assert v["residual_fts"] == {}, v["residual_fts"]
    assert v["residual_transcripts"] == {}, v["residual_transcripts"]
    assert v["residual_shadows"] == {}, v["residual_shadows"]

    # INDEPENDENT second-opinion grep over every column of both DBs == 0.
    post_grep = _grep_all_columns(SECRET)
    assert post_grep == {}, f"secret SURVIVED grep-able in: {post_grep}"

    # FTS + transcript clean too.
    assert memory_forget._fts_residual_hits(matcher) == {}
    assert SECRET not in t.read_text(encoding="utf-8")
    assert "clean line" in t.read_text(encoding="utf-8")  # only matching line touched

    # Coverage is reported as introspection, not a fixed number.
    assert result["complete"] is True
    counts = memory_forget._dry_run_counts(matcher)
    assert counts["coverage"] == "all-live-dbs-by-introspection"
    assert counts["tables_scanned"] >= 100  # all live DBs fully enumerated
    # The live-DB set is discovered from the canonical resolvers, not hardcoded.
    assert "nexo" in counts["live_dbs"] and "cognitive" in counts["live_dbs"]
    assert "local-context" in counts["live_dbs"], counts["live_dbs"]
    # Honest backup scope is always present in the report.
    assert "backups" in counts["backup_scope"].lower()

    # Ledger row recorded.
    import cognitive
    cog = cognitive._get_db()
    led = cog.execute(
        "SELECT COUNT(*) FROM memory_corrections WHERE store='forget'"
    ).fetchone()[0]
    assert led >= 1


def test_hard_forget_invalidates_persisted_hnsw():
    import os
    import memory_forget
    import hnsw_index

    _seed_cognitive(SECRET)

    os.makedirs(hnsw_index._INDEX_DIR, exist_ok=True)
    bin_path = hnsw_index._index_path("stm")
    npy_path = hnsw_index._id_map_path("stm")
    with open(bin_path, "wb") as fh:
        fh.write(b"fake-index")
    np.save(npy_path, {0: 1})
    assert os.path.exists(bin_path)

    result = memory_forget.forget(SECRET, mode="secret", dry_run=False, confirm=True)
    assert result["hnsw"]["invalidated"] is True

    assert not os.path.exists(bin_path)
    npy_with_ext = npy_path if npy_path.endswith(".npy") else npy_path + ".npy"
    assert not os.path.exists(npy_with_ext)


# ─────────────────────────────────────────────────────────────────────────────
# (b) VERIFICATION HONESTY — a residual the pass cannot reach → complete=False
# ─────────────────────────────────────────────────────────────────────────────


def test_verification_reports_incomplete_on_residual():
    import memory_forget

    OTHER = "ghp_RESIDUAL000111222333444555666777888"
    _seed_cognitive(SECRET)
    import db._learnings as L

    L.create_learning(category="security", title="residual", content=f"residual {OTHER}")

    result = memory_forget.forget(SECRET, mode="secret", dry_run=False, confirm=True)
    # SECRET is gone, so verifying for SECRET is complete...
    assert result["verification"]["complete"] is True

    # ...but a verification for the broader set (SECRET + OTHER) is INCOMPLETE,
    # proving the verifier reports residual hits as a non-complete forget. Store
    # names are namespaced by DB (introspection over both connections).
    broad = memory_forget.ForgetMatcher(literals=[SECRET, OTHER])
    v = memory_forget.verify_forgotten(broad)
    assert v["complete"] is False
    assert "nexo.learnings" in v["residual_stores"], v["residual_stores"]
    assert v["residual_fts"], "residual must also surface in FTS grep"


def test_verification_detects_residual_in_unanticipated_table():
    """Simulate a forget that fails to clean ONE arbitrary table (a table not in
    the DELETE-ROW set, mutated AFTER the sweep). Verification must still catch
    it — the guarantee does not depend on anticipating the table."""
    import memory_forget
    import db

    _seed_cognitive(SECRET)
    result = memory_forget.forget(SECRET, mode="secret", dry_run=False, confirm=True)
    assert result["verification"]["complete"] is True

    # Re-introduce the secret into an arbitrary table the engine did NOT special-case.
    conn = db.get_db()
    conn.execute(
        "INSERT INTO transcript_index "
        "(source_client, conversation_id, session_id, path_ref, sanitized_summary, content_hash, indexed_at) "
        "VALUES ('x', 'c', 's', '/p', ?, 'h', 1.0)",
        (f"sneaky residual {SECRET}",),
    )
    conn.commit()

    v = memory_forget.verify_forgotten(_build_matcher(SECRET))
    assert v["complete"] is False
    assert "nexo.transcript_index" in v["residual_stores"], v["residual_stores"]


# ─────────────────────────────────────────────────────────────────────────────
# (c) CORRECT-FACT preserves useful memory (reversible, no physical delete)
# ─────────────────────────────────────────────────────────────────────────────


def test_correct_fact_does_not_delete_anything():
    import memory_forget
    import db._learnings as L

    fact_text = "The staging server port is 8080"
    L.create_learning(category="ops", title="staging port", content=fact_text)

    matcher = memory_forget.ForgetMatcher(literals=["8080"])
    before = memory_forget._dry_run_counts(matcher)["total_rows"]
    assert before >= 1

    result = memory_forget.forget("8080", mode="fact", dry_run=False, confirm=True)

    assert result["ok"] is True
    assert result["mode"] == "fact"
    assert result["destructive"] is False

    after = memory_forget._dry_run_counts(matcher)["total_rows"]
    assert after == before

    import db
    rows = db.get_db().execute(
        "SELECT content FROM learnings WHERE content LIKE '%8080%'"
    ).fetchall()
    assert any(fact_text in r["content"] for r in rows)


# ─────────────────────────────────────────────────────────────────────────────
# (d) GUARD: secret mode without confirm → dry-run only, nothing deleted
# ─────────────────────────────────────────────────────────────────────────────


def test_secret_guard_requires_confirm():
    import memory_forget

    _seed_cognitive(SECRET)
    _seed_nexo_core(SECRET)
    matcher = _build_matcher(SECRET)
    before = memory_forget._dry_run_counts(matcher)["total_rows"]
    assert before > 0

    r1 = memory_forget.forget(SECRET, mode="secret", dry_run=True, confirm=False)
    assert r1["destructive"] is False
    assert r1["dry_run"] is True
    assert r1["counts"]["total_rows"] == before

    r2 = memory_forget.forget(SECRET, mode="secret", dry_run=False, confirm=False)
    assert r2["destructive"] is False

    after = memory_forget._dry_run_counts(matcher)["total_rows"]
    assert after == before, "guard must not delete without explicit confirm"


# ─────────────────────────────────────────────────────────────────────────────
# (e) credential_delete auto-sweep — leak in diary_archive (OUTSIDE old registry)
# ─────────────────────────────────────────────────────────────────────────────


def test_credential_delete_auto_sweeps_leak_in_survivor_store():
    import db._learnings as L
    import db
    from tools_credentials import handle_credential_create, handle_credential_delete

    handle_credential_create("openai", "admin", SECRET, notes="prod admin key")
    # Leak it into diary_archive — a store the OLD curated registry never swept.
    db.get_db().execute(
        "INSERT INTO diary_archive (session_id, created_at, decisions, summary, source, archived_at) "
        "VALUES ('old', 1.0, ?, 'archived', 'migration', 1.0)",
        (f"admin pasted {SECRET}",),
    )
    db.get_db().commit()

    import memory_forget
    matcher = _build_matcher(SECRET)
    assert _grep_all_columns(SECRET), "grep-able before"

    msg = handle_credential_delete("openai", "admin")
    assert "deleted" in msg.lower()
    assert "Forget sweep" in msg
    # No residual warning because the sweep cleaned everything, incl. diary_archive.
    assert "WARNING" not in msg, msg

    # Credential gone AND leaked copy gone everywhere, verified to zero.
    assert _grep_all_columns(SECRET) == {}, _grep_all_columns(SECRET)
    assert memory_forget._fts_residual_hits(matcher) == {}
    assert not db.get_credential("openai", "admin")


def test_credential_delete_surfaces_residual_warning(monkeypatch):
    """If the sweep cannot reach a copy, the auto-sweep must SURFACE the residual
    (not return a silent 'Credential deleted.')."""
    import memory_forget
    import db._learnings as L
    from tools_credentials import handle_credential_create, handle_credential_delete

    handle_credential_create("openai", "admin", SECRET, notes="prod admin key")
    L.create_learning(category="security", title="pasted key", content=f"admin pasted {SECRET}")

    # Monkeypatch the sweep so verification reports INCOMPLETE (simulated failure).
    real_forget = memory_forget.forget

    def fake_forget(*args, **kwargs):
        res = real_forget(*args, **kwargs)
        if res.get("destructive"):
            res = dict(res)
            res["complete"] = False
            res["verification"] = dict(res.get("verification", {}))
            res["verification"]["complete"] = False
            res["verification"]["residual_stores"] = {"nexo.some_table": 1}
        return res

    monkeypatch.setattr(memory_forget, "forget", fake_forget)

    msg = handle_credential_delete("openai", "admin")
    assert "WARNING" in msg, msg
    assert "residual" in msg.lower()


# ─────────────────────────────────────────────────────────────────────────────
# (f) item_history.note free-text secret is redacted under mode='secret'
# ─────────────────────────────────────────────────────────────────────────────


def test_item_history_note_is_redacted_in_secret_mode():
    """The docstring sells mode='fact' as safe because it preserves memory in
    item_history — but item_history.note is free text that can hold the secret.
    Under mode='secret' it MUST be scrubbed (redacted in place, row preserved)."""
    import memory_forget
    import db

    conn = db.get_db()
    conn.execute(
        "INSERT INTO item_history (item_type, item_id, event_type, note, actor, metadata, created_at) "
        "VALUES ('learning', '7', 'note', ?, 'agent', '{}', 1.0)",
        (f"context note: the leaked key is {SECRET}, rotate it",),
    )
    conn.commit()

    memory_forget.forget(SECRET, mode="secret", dry_run=False, confirm=True)

    rows = conn.execute("SELECT note FROM item_history WHERE item_id='7'").fetchall()
    assert rows, "row preserved (redact-in-place, not deleted)"
    note = rows[0]["note"]
    assert SECRET not in note
    assert "rotate it" in note  # surrounding useful text survives
    assert "[REDACTED" in note


# ─────────────────────────────────────────────────────────────────────────────
# Legacy shadow cognitive DB — a secret in a shadow IS a leak → it gets cleaned
# ─────────────────────────────────────────────────────────────────────────────


def test_shadow_legacy_cognitive_db_is_cleaned():
    import sqlite3
    import memory_forget
    import cognitive_paths

    legacy_candidates = cognitive_paths.legacy_cognitive_db_paths()
    assert legacy_candidates, "expected at least one legacy shadow path to exist"
    shadow = legacy_candidates[0]
    shadow.parent.mkdir(parents=True, exist_ok=True)

    sc = sqlite3.connect(str(shadow))
    sc.execute("CREATE TABLE ltm_memories (id INTEGER PRIMARY KEY, content TEXT)")
    sc.execute("INSERT INTO ltm_memories (content) VALUES (?)", (f"shadow leak {SECRET}",))
    sc.commit()
    sc.close()

    _seed_cognitive(SECRET)
    result = memory_forget.forget(SECRET, mode="secret", dry_run=False, confirm=True)

    # The shadow was swept and the verification re-scans it → complete=True.
    assert result["verification"]["residual_shadows"] == {}, result["verification"]
    assert result["complete"] is True

    sc = sqlite3.connect(str(shadow))
    rows = sc.execute("SELECT content FROM ltm_memories").fetchall()
    sc.close()
    assert all(SECRET not in (r[0] or "") for r in rows), "shadow must NOT keep the secret"
