"""SELECTIVE-FORGET (Ola 4) — verifiable hard-forget of revoked secrets.

Problem this solves (real incidents): a compromised key/secret (OpenAI key,
GITHUB_PAT, admin keys pasted into chat) lands in memory. Today a "correction"
is a SOFT-HIDE (``UPDATE ... is_dormant=1``) that does NOT fire the FTS
``AFTER DELETE`` trigger, so the secret text stays grep-able in the FTS index.
This module lets us forget it *for real* and *verifiably*.

Two modes, NEVER mixed (central security principle):

* ``mode='secret'`` (HARD-FORGET): real PHYSICAL removal + verified-to-zero.
  ONLY for revoked secrets/credentials or data flagged toxic. Here we
  deliberately break the anti-loss discipline — the goal is that the secret
  disappears from *every* surface.
* ``mode='fact'`` (CORRECT-FACT): useful memory is NOT lost — it keeps the
  existing reversible supersede (``item_history`` / soft-supersede). The forget
  engine does NOT physically delete here.

The golden rule: destructive forget only arms with ``mode='secret'`` +
mandatory dry-run + explicit ``confirm``. Fact correction stays on the soft path.

────────────────────────────────────────────────────────────────────────────
SECURITY-CRITICAL DESIGN (the fix): COVERAGE BY INTROSPECTION, NOT A CURATED LIST
────────────────────────────────────────────────────────────────────────────
The previous version hard-coded ~13 of the ~110 tables. A secret in any of the
other ~97 (``item_history``, ``diary_archive``, ``historical_diary_index``,
``memory_events``, ``continuity_snapshots``, ``session_checkpoints``,
``session_diary_draft``, ``transcript_index``, FTS shadow tables, legacy shadow
DBs …) reported ``complete=True`` while the secret SURVIVED grep-able. A secret
that "was deleted" but is still there is worse than not deleting it.

The guarantee is now structural and non-negotiable: **if
``verification.complete is True`` the secret is NOT grep-able in ANY column of
ANY table of ANY LIVE DB the agent retrieves from, in ANY FTS index, in any
on-disk transcript, or in any legacy shadow DB.** We achieve this by
*enumerating* every table and every column at runtime (``sqlite_master`` +
``PRAGMA table_info``) instead of trusting a curated registry. A residual in a
table nobody anticipated is both *cleaned* and, if cleaning failed, *reported*
as ``complete=False``.

The set of LIVE DBs is itself discovered from each subsystem's own canonical
path resolver, NOT hardcoded (round-3 fix — the previous version covered only
nexo.db + cognitive.db and silently missed local-context.db, where a secret in
any indexed file lands in ``local_chunks``/``local_chunks_fts``/
``entity_facts.value``/``local_entities.evidence`` and survives forget). The
live set is:
  * nexo.db                — ``db._core`` / ``paths.resolve_db_path``
  * cognitive.db           — ``cognitive_paths.resolve_cognitive_db``
  * local-context.db       — ``local_context.db.local_context_db_path``
  * local-context-usage.db — ``local_context.usage_events.usage_db_path``
  * nexo-email.db          — ``email_sent_events.sent_email_db_path``
plus legacy shadows of cognitive.db and local-context.db.

HONEST BACKUP SCOPE: ``complete=True`` is a statement about LIVE DBs only.
Point-in-time backups/snapshots (``*.bak``, ``runtime/backups``, deep-sleep
snapshots) are NOT swept — they are retention copies, and the real mitigation
for a compromised secret is to ROTATE it (operator action). Every report
carries this note (``backup_scope``) so the guarantee never overclaims.

Strategy per surface:
  * REDACT-IN-PLACE (default for every introspected table): replace the secret
    substring with a placeholder so the surrounding useful record (a diary
    entry, ``item_history.note``, ``change_log`` row …) survives without the
    secret. This is uniform and safe for tables we did not anticipate.
  * DELETE-ROW (explicit allow-list): drop the whole row where the row *is* the
    secret container or carries a vector embedding / FTS copy that must vanish
    with it (``stm/ltm/quarantine`` + embedding, ``memory_observations`` + FTS,
    ``kg_nodes``/``kg_edges``, ``somatic_markers``, ``resolution_cache``
    ``content_snapshot_json`` leak, ``hot_context``, ``recent_events``).
  * FTS5 virtual tables are scrubbed by mutating the *parent* vtable directly
    (FTS5 maintains its ``_content``/``_docsize``/``_idx`` shadow tables on
    UPDATE/DELETE — verified). Standalone FTS5 does NOT support 'rebuild', so we
    never rely on it; we redact/delete the FTS rows in place.
  * Vector embeddings ride with their row (DELETE removes them); persisted HNSW
    indices are invalidated so a later search cannot reload forgotten vectors.
  * On-disk transcripts: matching lines are redacted in place.
  * Legacy shadow DBs — cognitive (``cognitive_paths.legacy_cognitive_db_paths``)
    AND local-context (``_local_context_shadow_paths``) — are swept with the
    same introspective engine; a secret in a shadow is a leak.

VERIFICATION is honest: it RE-ENUMERATES and re-scans everything (no reuse of
the curated list) and only returns ``complete=True`` at total zero, otherwise
``complete=False`` with the exact surviving locations (namespaced ``<db>.<table>``).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional


# HONEST SCOPE — point-in-time BACKUPS / snapshots are deliberately NOT swept.
# ``complete=True`` means the secret is gone from every LIVE DB the agent
# retrieves from; it does NOT touch ``*.bak`` files, dated backups under
# runtime/backups, or deep-sleep snapshots. Those are retention copies; the real
# mitigation for a compromised secret is to ROTATE it (operator action), so
# scrubbing immutable backups would give a false sense of safety AND fight the
# backups' purpose. This note is surfaced in every report for transparency.
_BACKUP_SCOPE_NOTE = (
    "complete=True means the secret is no longer RETRIEVABLE (row/column value) "
    "from any LIVE DB. Out of scope, by design: (1) point-in-time backups / "
    "snapshots (*.bak, runtime/backups, deep-sleep snapshots) — retention copies; "
    "(2) the external claude-mem plugin DB (batch-read only, not an answer-time "
    "retrieval surface). Byte-level note: the sweep runs with PRAGMA "
    "secure_delete=ON so pages freed by the delete are zeroed, but a full "
    "historical VACUUM is NOT forced (cost on the ~20GB local index), so isolated "
    "byte residue may persist in free pages until a VACUUM. The real mitigation "
    "for a COMPROMISED secret is always to ROTATE it."
)


# ─────────────────────────────────────────────────────────────────────────────
# DB handles — the FULL set of LIVE memory DBs the agent RETRIEVES from
# ─────────────────────────────────────────────────────────────────────────────
#
# COVERAGE PRINCIPLE (round-3 fix): the security guarantee is
# "complete=True ⇒ the secret is not grep-able in any LIVE DB". A LIVE DB is one
# the cooperator READS / RETRIEVES from at answer/act time, so a secret indexed
# into it would resurface. We do NOT hardcode the set of DBs — we ask each
# subsystem's own canonical path resolver, so this set tracks the runtime:
#
#   * nexo.db                 — db._core (paths.resolve_db_path)
#   * cognitive.db            — cognitive_paths.resolve_cognitive_db
#   * local-context.db        — local_context.db.local_context_db_path  (the
#                               ~20GB local file index: local_chunks/_fts,
#                               local_entities.evidence, entity_facts.value, …)
#   * local-context-usage.db  — local_context.usage_events.usage_db_path
#                               (query telemetry: intent/error/metadata free text)
#   * nexo-email.db           — email_sent_events.sent_email_db_path
#                               (sent/inbound bodies the agent recalls)
#
# This is exactly the live-retrieval inventory used by saved_not_used_audit.
# Other ~.db files under runtime are NOT live retrieval surfaces and are out of
# scope on purpose: backups/snapshots (retention — see _backup_scope_note),
# personal_scripts.db (a script *registry*, not memory text), and
# auto_capture_dedup.db (dedup *hashes*, never raw secrets). Each is documented
# so "live-DB coverage" is an explicit, auditable claim — not an accident of
# which resolver happened to be wired.
#
# Legacy SHADOWS of cognitive.db and local-context.db are swept too (a secret in
# a shadow is still a leak); see _shadow_db_paths.


def _nexo_conn() -> sqlite3.Connection:
    """nexo.db connection (learnings, change_log, decisions, observations…)."""
    import db as _db

    return _db.get_db()


def _cognitive_conn() -> sqlite3.Connection:
    """cognitive.db connection (stm/ltm, resolution_cache, KG, somatic…)."""
    import cognitive

    return cognitive._get_db()


def _local_context_conn() -> sqlite3.Connection:
    """local-context.db connection (local_chunks/_fts, entities, facts, …)."""
    import local_context.db as _lc

    return _lc.get_local_context_db()


def _local_context_usage_path() -> Optional[Path]:
    try:
        import local_context.usage_events as _ue

        return _ue.usage_db_path()
    except Exception:
        return None


def _email_db_path() -> Optional[Path]:
    try:
        import email_sent_events as _ee

        return _ee.sent_email_db_path()
    except Exception:
        return None


def _open_file_db(path: Path) -> Optional[sqlite3.Connection]:
    """Open an on-disk SQLite DB by path (used for the usage + email stores,
    whose owning modules connect per-call rather than caching one handle)."""
    try:
        if not path.exists():
            return None
        conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            # Zero out pages freed by the forget deletes so the secret bytes do
            # not linger in the freelist of the on-disk file (forensic residue).
            conn.execute("PRAGMA secure_delete=ON")
        except Exception:
            pass
        return conn
    except Exception:
        return None


def _live_conns() -> list[tuple[str, sqlite3.Connection, bool]]:
    """Every LIVE memory DB as ``(name, connection, owns_connection)``.

    ``owns_connection`` is True for path-opened handles (usage/email) that the
    caller must close; cached subsystem handles (nexo/cognitive/local-context)
    are owned by their module and must NOT be closed here.
    """
    conns: list[tuple[str, sqlite3.Connection, bool]] = []
    # Cached, module-owned handles.
    for name, getter in (
        ("nexo", _nexo_conn),
        ("cognitive", _cognitive_conn),
        ("local-context", _local_context_conn),
    ):
        try:
            conns.append((name, getter(), False))
        except Exception:
            pass
    # Path-opened, caller-owned handles.
    for name, path_getter in (
        ("local-context-usage", _local_context_usage_path),
        ("email", _email_db_path),
    ):
        try:
            path = path_getter()
        except Exception:
            path = None
        if not path:
            continue
        conn = _open_file_db(Path(path))
        if conn is not None:
            conns.append((name, conn, True))
    return conns


def _close_if_owned(conns: Iterable[tuple[str, sqlite3.Connection, bool]]) -> None:
    for _name, conn, owns in conns:
        if owns:
            try:
                conn.close()
            except Exception:
                pass


def _both_conns() -> list[tuple[str, sqlite3.Connection]]:
    """Back-compat shim: name+connection pairs over ALL live DBs.

    Kept (despite the misleading name) so older callers/tests keep working; it
    now spans every live DB, not just nexo+cognitive. Path-opened handles are
    intentionally left open for the lifetime of the caller's scan loop; callers
    that mutate should prefer _live_conns() to close owned handles afterwards.
    """
    return [(name, conn) for name, conn, _owns in _live_conns()]


def _now_epoch() -> float:
    try:
        import db as _db

        return float(_db.now_epoch())
    except Exception:
        return time.time()


# ─────────────────────────────────────────────────────────────────────────────
# MATCHER — reuse the redact regexes from cognitive._core, INVERTED to a
# predicate, plus the literal value(s) to forget.
# ─────────────────────────────────────────────────────────────────────────────


def _redact_detect_patterns() -> list[re.Pattern]:
    """Return the compiled detection regexes drawn from the redact table."""
    try:
        from cognitive._core import _REDACT_PATTERNS

        return [pattern for pattern, _replacement in _REDACT_PATTERNS]
    except Exception:
        return [
            re.compile(r"sk-[a-zA-Z0-9_\-]{20,}"),
            re.compile(r"ghp_[a-zA-Z0-9]{20,}"),
            re.compile(r"gho_[a-zA-Z0-9]{20,}"),
            re.compile(r"shpat_[a-f0-9]{20,}"),
            re.compile(r"AKIA[A-Z0-9]{16}"),
            re.compile(r"xox[bp]-[a-zA-Z0-9\-]{20,}"),
        ]


@dataclass
class ForgetMatcher:
    """Predicate ``matches(text) -> bool`` for the value(s) being forgotten.

    Built from the explicit literal value(s) (case-sensitive substring) and,
    optionally, the secret-shaped detection regexes (``use_regex=True``).
    ``use_regex`` defaults to False so a plain fact value can never trip a
    generic secret pattern.
    """

    literals: list[str] = field(default_factory=list)
    use_regex: bool = False
    _regexes: list[re.Pattern] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.literals = [str(v) for v in self.literals if str(v or "").strip()]
        if self.use_regex:
            self._regexes = _redact_detect_patterns()

    def matches(self, text: Optional[str]) -> bool:
        if not text:
            return False
        for literal in self.literals:
            if literal and literal in text:
                return True
        for rx in self._regexes:
            if rx.search(text):
                return True
        return False

    def redact(self, text: Optional[str]) -> str:
        """Replace every matched span with a placeholder.

        Literals are removed first; regex hits use the engine's own redactor so
        the result stays consistent with ingest-time redaction.
        """
        if not text:
            return text or ""
        result = text
        for literal in self.literals:
            if literal:
                result = result.replace(literal, "[REDACTED:forgotten]")
        if self._regexes:
            try:
                from cognitive._core import redact_secrets

                result = redact_secrets(result)
            except Exception:
                for rx in self._regexes:
                    result = rx.sub("[REDACTED:secret]", result)
        return result


# ─────────────────────────────────────────────────────────────────────────────
# INTROSPECTION — enumerate EVERY table + text-bearing column of a connection
# ─────────────────────────────────────────────────────────────────────────────

# FTS5 shadow internal tables are maintained automatically by the parent vtable;
# never mutate them directly (and they hold no plaintext beyond what the parent
# already exposes for verification, except *_content which the parent cleans).
_FTS_SHADOW_SUFFIXES = ("_data", "_idx", "_docsize", "_config", "_content")

# Columns we never treat as text targets: pure binary embeddings/vectors. They
# ride with their row on DELETE and never carry a plaintext secret.
_BLOB_COLUMN_HINTS = ("embedding", "vector", "blob")


@dataclass
class TableInfo:
    name: str
    is_fts5: bool
    pk: str  # primary-key column name ('rowid' fallback) for row identification
    text_columns: tuple[str, ...]
    has_embedding: bool


def _quote(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _is_fts5_sql(sql: Optional[str]) -> bool:
    return bool(sql) and "using fts5" in sql.lower()


def _looks_textual(declared_type: str) -> bool:
    """Whether a declared column type may hold a string in SQLite.

    SQLite is dynamically typed, so we scan generously: any column whose
    declared affinity is not strictly INTEGER/REAL/BLOB is scanned. Numeric and
    blob columns are skipped for performance (a secret string cannot live there
    as a grep-able substring under TEXT affinity)."""
    t = (declared_type or "").upper()
    if not t:
        return True  # NONE affinity → can hold text
    if "INT" in t:
        return False
    if any(b in t for b in ("BLOB",)):
        return False
    if any(r in t for r in ("REAL", "FLOA", "DOUB")):
        return False
    return True  # TEXT, CHAR, CLOB, NUMERIC, DATE, JSON, … → scan it


def _introspect_tables(conn: sqlite3.Connection) -> list[TableInfo]:
    """Enumerate every base table and FTS5 vtable with its text-bearing columns.

    No curated list: this reflects the *actual* schema of the live DB, so a
    table that did not exist when this module was written is still covered.
    """
    out: list[TableInfo] = []
    try:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table'"
        ).fetchall()
    except Exception:
        return out

    fts_names = {r[0] for r in rows if _is_fts5_sql(r[1])}

    for name, sql in rows:
        if name.startswith("sqlite_"):
            continue
        # Skip FTS5 shadow internal tables — the parent vtable owns them.
        if any(name.endswith(sfx) for sfx in _FTS_SHADOW_SUFFIXES):
            base = name.rsplit("_", 1)[0]
            if base in fts_names:
                continue
        is_fts5 = name in fts_names
        try:
            cols = conn.execute(f"PRAGMA table_info({_quote(name)})").fetchall()
        except Exception:
            continue

        text_cols: list[str] = []
        pk_col = ""
        has_embedding = False
        for col in cols:
            # PRAGMA table_info → (cid, name, type, notnull, dflt, pk)
            col_name = col[1]
            col_type = col[2] or ""
            col_pk = col[5]
            lname = str(col_name).lower()
            if any(h in lname for h in _BLOB_COLUMN_HINTS) or "blob" in col_type.lower():
                has_embedding = has_embedding or ("embed" in lname or "vector" in lname)
                continue
            if col_pk and not pk_col:
                pk_col = col_name
            if _looks_textual(col_type):
                text_cols.append(col_name)

        if not pk_col:
            pk_col = "rowid"  # FTS5 + WITHOUT-ROWID-free tables expose rowid

        out.append(
            TableInfo(
                name=name,
                is_fts5=is_fts5,
                pk=pk_col,
                text_columns=tuple(text_cols),
                has_embedding=has_embedding,
            )
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Mutation policy — DELETE-ROW allow-list vs default REDACT-IN-PLACE
# ─────────────────────────────────────────────────────────────────────────────

# Tables where a matching row should be DELETED whole (the row *is* the secret
# container, or it carries a vector embedding / dedicated FTS copy that must
# vanish with it). Everything else is REDACTED in place so surrounding useful
# memory survives. Unknown/introspected tables → redact (safe default).
_DELETE_ROW_TABLES: frozenset[str] = frozenset({
    # cognitive.db — carry embeddings and/or are pure memory rows
    "stm_memories",
    "ltm_memories",
    "quarantine",
    "kg_nodes",
    "kg_edges",
    "somatic_markers",
    # nexo.db — the row's reason to exist is the (now toxic) content
    "memory_observations",  # has linked FTS + entities
    "resolution_cache",     # content_snapshot_json leak the soft invalidate() never clears
    "hot_context",
    "recent_events",
})


def _should_delete_row(table: str) -> bool:
    return table in _DELETE_ROW_TABLES


# ─────────────────────────────────────────────────────────────────────────────
# Per-DB scan / sweep primitives
# ─────────────────────────────────────────────────────────────────────────────


def _row_value_str(row: Any, idx: int) -> str:
    try:
        v = row[idx]
    except Exception:
        return ""
    if v is None:
        return ""
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8", "ignore")
        except Exception:
            return ""
    return str(v)


def _scan_db(conn: sqlite3.Connection, matcher: ForgetMatcher) -> dict[str, int]:
    """Return {table: matching_row_count} across ALL introspected tables.

    This is the grep-equivalent: it scans every text column of every table
    (including FTS5 vtables, which expose their stored content)."""
    residual: dict[str, int] = {}
    for ti in _introspect_tables(conn):
        if not ti.text_columns:
            continue
        col_list = ", ".join(_quote(c) for c in ti.text_columns)
        try:
            rows = conn.execute(f"SELECT {col_list} FROM {_quote(ti.name)}").fetchall()
        except Exception:
            continue
        count = 0
        for row in rows:
            blob = " ".join(_row_value_str(row, i) for i in range(len(ti.text_columns)))
            if matcher.matches(blob):
                count += 1
        if count:
            residual[ti.name] = count
    return residual


def _sweep_db(conn: sqlite3.Connection, matcher: ForgetMatcher) -> dict[str, dict[str, int]]:
    """Physically remove the secret from every table of a connection.

    Returns {"deleted": {table: n}, "redacted": {table: n}}.
    For each introspected table:
      * DELETE-ROW tables / FTS5 vtables → delete matching rows.
      * everything else → redact the secret substring in place (literal first,
        then regex via the matcher's redactor), preserving the row.
    """
    deleted: dict[str, int] = {}
    redacted: dict[str, int] = {}

    for ti in _introspect_tables(conn):
        if not ti.text_columns:
            continue
        col_list = ", ".join(_quote(c) for c in ti.text_columns)
        # Read pk + text columns together so we can target exact rows.
        sel_cols = ti.text_columns
        pk = ti.pk
        try:
            rows = conn.execute(
                f"SELECT {pk if pk == 'rowid' else _quote(pk)}, {col_list} "
                f"FROM {_quote(ti.name)}"
            ).fetchall()
        except Exception:
            # Some FTS5 vtables reject SELECT of rowid alias differently; retry plain.
            try:
                rows = conn.execute(
                    f"SELECT rowid, {col_list} FROM {_quote(ti.name)}"
                ).fetchall()
                pk = "rowid"
            except Exception:
                continue

        delete_ids: list[Any] = []
        redact_targets: list[tuple[Any, dict[str, str]]] = []

        for row in rows:
            row_pk = row[0]
            values = {ti.text_columns[i]: _row_value_str(row, i + 1) for i in range(len(ti.text_columns))}
            blob = " ".join(values.values())
            if not matcher.matches(blob):
                continue
            if ti.is_fts5 or _should_delete_row(ti.name):
                delete_ids.append(row_pk)
            else:
                new_vals = {}
                for col, val in values.items():
                    if val and matcher.matches(val):
                        new_vals[col] = matcher.redact(val)
                if new_vals:
                    redact_targets.append((row_pk, new_vals))
                else:
                    # Match spanned the concatenation only (rare) — delete to be safe.
                    delete_ids.append(row_pk)

        # Apply deletes.
        if delete_ids:
            pkref = "rowid" if pk == "rowid" else _quote(pk)
            n = 0
            for chunk_start in range(0, len(delete_ids), 500):
                chunk = delete_ids[chunk_start:chunk_start + 500]
                ph = ",".join("?" * len(chunk))
                try:
                    cur = conn.execute(
                        f"DELETE FROM {_quote(ti.name)} WHERE {pkref} IN ({ph})", chunk
                    )
                    n += int(cur.rowcount or 0)
                except Exception:
                    pass
            if n:
                deleted[ti.name] = n

        # Apply redactions (one UPDATE per row, only its matching columns).
        if redact_targets:
            pkref = "rowid" if pk == "rowid" else _quote(pk)
            n = 0
            for row_pk, new_vals in redact_targets:
                set_clause = ", ".join(f"{_quote(c)} = ?" for c in new_vals)
                params = list(new_vals.values()) + [row_pk]
                try:
                    conn.execute(
                        f"UPDATE {_quote(ti.name)} SET {set_clause} WHERE {pkref} = ?",
                        params,
                    )
                    n += 1
                except Exception:
                    pass
            if n:
                redacted[ti.name] = n

    try:
        conn.commit()
    except Exception:
        pass
    return {"deleted": deleted, "redacted": redacted}


# ─────────────────────────────────────────────────────────────────────────────
# Legacy shadow cognitive DBs — a secret in a shadow is a leak
# ─────────────────────────────────────────────────────────────────────────────


def _local_context_shadow_paths() -> list[Path]:
    """Legacy / alternate-location copies of local-context.db.

    The canonical store lives at ``paths.memory_dir()/local-context.db``; older
    installs and the personal-brain layout left shadows behind. A secret indexed
    into any of them is still grep-able, so we sweep them like cognitive shadows.
    Only paths that differ from the live store and actually exist are returned.

    SAFETY: when ``NEXO_LOCAL_CONTEXT_DB`` is explicitly overridden (tests, or an
    operator pinning a custom path), we do NOT scan the ambient ``NEXO_HOME``
    locations — doing so would let a sweep run against an unrelated tree (and in
    tests would touch the operator's real ~/.nexo shadows). Shadows are only
    considered in the live store's OWN directory in that case. This mirrors the
    ``_configured_override`` discipline in cognitive_paths.
    """
    candidates: list[Path] = []
    try:
        import local_context.db as _lc

        live = _lc.local_context_db_path()
    except Exception:
        live = None
    overridden = bool(os.environ.get("NEXO_LOCAL_CONTEXT_DB", "").strip()) or bool(
        os.environ.get("NEXO_TEST_DB", "").strip()
    )
    if overridden:
        # Only sibling shadows next to the (overridden) live store — never the
        # ambient NEXO_HOME tree, which is unrelated under an override/in tests.
        if live is not None:
            candidates.append(live.with_name("local-context.db.legacy"))
    else:
        try:
            import paths as _paths

            candidates.extend([
                _paths.brain_dir() / "local-context.db",            # personal/brain shadow
                _paths.home() / "memory" / "local-context.db",      # legacy flat layout
                _paths.home() / "local-context.db",
                _paths.runtime_dir() / "local-context.db",
            ])
        except Exception:
            pass
    out: list[Path] = []
    seen: set[str] = set()
    for cand in candidates:
        try:
            key = str(cand.resolve())
            live_key = str(live.resolve()) if live else ""
        except Exception:
            key = str(cand)
            live_key = str(live) if live else ""
        if not cand.exists() or key == live_key or key in seen:
            continue
        seen.add(key)
        out.append(cand)
    return out


def _shadow_db_paths() -> list[Path]:
    """All legacy shadow DBs to sweep: cognitive shadows + local-context shadows.

    A leak in a shadow is a leak. We cover shadows for every live store that has
    historically lived in more than one location (cognitive.db, local-context.db).
    """
    paths_out: list[Path] = []
    try:
        import cognitive_paths

        paths_out.extend(p for p in cognitive_paths.legacy_cognitive_db_paths() if p.exists())
    except Exception:
        pass
    paths_out.extend(_local_context_shadow_paths())
    # De-dup by resolved path.
    unique: list[Path] = []
    seen: set[str] = set()
    for p in paths_out:
        try:
            key = str(p.resolve())
        except Exception:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique


def _open_shadow(path: Path) -> Optional[sqlite3.Connection]:
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def _scan_shadows(matcher: ForgetMatcher) -> dict[str, dict[str, int]]:
    """Read-only scan of every legacy shadow DB. {path: {table: n}}."""
    out: dict[str, dict[str, int]] = {}
    for path in _shadow_db_paths():
        conn = _open_shadow(path)
        if conn is None:
            continue
        try:
            residual = _scan_db(conn, matcher)
            if residual:
                out[str(path)] = residual
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return out


def _sweep_shadows(matcher: ForgetMatcher) -> dict[str, dict[str, dict[str, int]]]:
    """Clean every legacy shadow DB with the same introspective engine."""
    out: dict[str, dict[str, dict[str, int]]] = {}
    for path in _shadow_db_paths():
        conn = _open_shadow(path)
        if conn is None:
            continue
        try:
            result = _sweep_db(conn, matcher)
            if result["deleted"] or result["redacted"]:
                out[str(path)] = result
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return out


# ─────────────────────────────────────────────────────────────────────────────
# FTS residual scan (kept as a named helper for callers/tests) — introspective
# ─────────────────────────────────────────────────────────────────────────────


def _fts_residual_hits(matcher: ForgetMatcher) -> dict[str, int]:
    """Scan every FTS5 surface in ALL live DBs directly for residual matches.

    Reads the stored FTS content and applies the matcher (the grep-equivalent),
    instead of trusting an FTS MATCH query (which tokenizes and may miss a raw
    secret substring). Discovered by introspection, not a hardcoded list. Keys
    are namespaced ``<db>.<fts_table>`` so a residual is reported by DB+table
    (e.g. ``local-context.local_chunks_fts``) and same-named FTS tables in
    different DBs never collide."""
    residual: dict[str, int] = {}
    conns = _live_conns()
    try:
        for db_name, conn, _owns in conns:
            for ti in _introspect_tables(conn):
                if not ti.is_fts5 or not ti.text_columns:
                    continue
                col_list = ", ".join(_quote(c) for c in ti.text_columns)
                try:
                    rows = conn.execute(
                        f"SELECT {col_list} FROM {_quote(ti.name)}"
                    ).fetchall()
                except Exception:
                    continue
                count = 0
                for row in rows:
                    blob = " ".join(_row_value_str(row, i) for i in range(len(ti.text_columns)))
                    if matcher.matches(blob):
                        count += 1
                if count:
                    residual[f"{db_name}.{ti.name}"] = count
    finally:
        _close_if_owned(conns)
    return residual


# ─────────────────────────────────────────────────────────────────────────────
# Transcripts on disk (outside SQLite) — scan + redact matching lines
# ─────────────────────────────────────────────────────────────────────────────


def _transcript_roots() -> list[Path]:
    roots: list[Path] = []
    try:
        import paths as _paths

        for candidate in (
            _paths.runtime_dir() / "transcripts",
            _paths.runtime_dir() / "coordination",
        ):
            roots.append(candidate)
    except Exception:
        pass
    env_root = os.environ.get("NEXO_TRANSCRIPT_DIR", "").strip()
    if env_root:
        roots.append(Path(env_root))
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        try:
            key = str(root.resolve())
        except Exception:
            key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _transcript_files(roots: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            files.append(root)
            continue
        for ext in ("*.jsonl", "*.json", "*.txt", "*.md", "*.log"):
            files.extend(root.rglob(ext))
    return files


def _scan_transcripts(matcher: ForgetMatcher, files: list[Path]) -> dict[str, int]:
    """Count matching lines per file without modifying anything."""
    hits: dict[str, int] = {}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        count = sum(1 for line in text.splitlines() if matcher.matches(line))
        if count:
            hits[str(path)] = count
    return hits


def _redact_transcripts(matcher: ForgetMatcher, files: list[Path]) -> dict[str, int]:
    """Redact matching lines in place. Returns redacted-line count per file."""
    redacted: dict[str, int] = {}
    for path in files:
        try:
            original = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        lines = original.splitlines(keepends=True)
        changed = 0
        out: list[str] = []
        for line in lines:
            if matcher.matches(line):
                newline = "\n" if line.endswith("\n") else ""
                core = line[:-1] if newline else line
                out.append(matcher.redact(core) + newline)
                changed += 1
            else:
                out.append(line)
        if changed:
            try:
                path.write_text("".join(out), encoding="utf-8")
                redacted[str(path)] = changed
            except Exception:
                pass
    return redacted


# ─────────────────────────────────────────────────────────────────────────────
# Ledger — auditable record in memory_corrections (cognitive.db)
# ─────────────────────────────────────────────────────────────────────────────


def _write_ledger(operation: dict[str, Any]) -> Optional[int]:
    try:
        conn = _cognitive_conn()
    except Exception:
        return None
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_corrections' LIMIT 1"
        ).fetchone()
        if not row:
            return None
        cur = conn.execute(
            "INSERT INTO memory_corrections (memory_id, store, correction_type, context) "
            "VALUES (?, ?, ?, ?)",
            (0, "forget", operation.get("mode", "secret"), json.dumps(operation, default=str)[:8000]),
        )
        conn.commit()
        return int(cur.lastrowid)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Public engine
# ─────────────────────────────────────────────────────────────────────────────


def _dry_run_counts(matcher: ForgetMatcher) -> dict[str, Any]:
    """Count matched rows per table (ALL live DBs, by introspection) +
    transcripts + shadow DBs. No mutation."""
    per_store: dict[str, int] = {}
    tables_scanned = 0
    conns = _live_conns()
    try:
        for db_name, conn, _owns in conns:
            residual = _scan_db(conn, matcher)
            tables_scanned += len(_introspect_tables(conn))
            for table, n in residual.items():
                per_store[f"{db_name}.{table}"] = n
    finally:
        _close_if_owned(conns)
    transcript_files = _transcript_files(_transcript_roots())
    transcript_hits = _scan_transcripts(matcher, transcript_files)
    shadow_hits = _scan_shadows(matcher)
    return {
        "per_store": per_store,
        "total_rows": sum(per_store.values()),
        "transcript_hits": transcript_hits,
        "transcript_lines": sum(transcript_hits.values()),
        "shadow_hits": shadow_hits,
        "live_dbs": [name for name, _c, _o in conns],
        "tables_scanned": tables_scanned,
        "coverage": "all-live-dbs-by-introspection",
        "backup_scope": _BACKUP_SCOPE_NOTE,
    }


def verify_forgotten(matcher: ForgetMatcher) -> dict[str, Any]:
    """Re-ENUMERATE and re-scan EVERY table of EVERY LIVE DB + every FTS +
    transcripts + every legacy shadow DB, and assert zero hits.

    This is the core value: a signed report. ``complete`` is True ONLY when the
    total re-scan is zero across all live DBs (nexo, cognitive, local-context,
    local-context-usage, email). If anything remains — even in a table this
    module did not anticipate — ``complete`` is False and the residual locations
    are listed, namespaced ``<db>.<table>``. Backups are out of scope by design
    (``backup_scope``).
    """
    residual_stores: dict[str, int] = {}
    conns = _live_conns()
    try:
        for db_name, conn, _owns in conns:
            for table, n in _scan_db(conn, matcher).items():
                residual_stores[f"{db_name}.{table}"] = n
    finally:
        _close_if_owned(conns)

    residual_fts = _fts_residual_hits(matcher)
    transcript_files = _transcript_files(_transcript_roots())
    residual_transcripts = _scan_transcripts(matcher, transcript_files)
    residual_shadows = _scan_shadows(matcher)

    complete = not (
        residual_stores or residual_fts or residual_transcripts or residual_shadows
    )
    return {
        "complete": complete,
        "residual_stores": residual_stores,
        "residual_fts": residual_fts,
        "residual_transcripts": residual_transcripts,
        "residual_shadows": residual_shadows,
        # Honest scope: complete=True means "out of every LIVE DB", NOT backups.
        "backup_scope": _BACKUP_SCOPE_NOTE,
    }


def forget(
    value: str = "",
    *,
    values: Optional[list[str]] = None,
    mode: str = "secret",
    dry_run: bool = True,
    confirm: bool = False,
    use_regex: bool = False,
    invalidate_hnsw: bool = True,
    redact_transcripts: bool = True,
    sweep_shadows: bool = True,
    reason: str = "",
) -> dict[str, Any]:
    """Selective-forget engine.

    Destructive deletion only happens when ``mode='secret'`` AND
    ``dry_run is False`` AND ``confirm is True``.
    """
    literals = [v for v in ([value] + (values or [])) if str(v or "").strip()]
    if not literals:
        return {"ok": False, "error": "no value(s) provided to forget"}

    mode = (mode or "secret").lower()
    if mode not in ("secret", "fact"):
        return {"ok": False, "error": f"unknown mode '{mode}'"}

    # ── CORRECT-FACT: never physically delete. Keep the reversible soft path. ──
    if mode == "fact":
        return {
            "ok": True,
            "mode": "fact",
            "destructive": False,
            "message": (
                "CORRECT-FACT mode does not physically delete. Useful memory is "
                "preserved via the existing reversible supersede (item_history / "
                "soft-supersede). Use nexo_learning_update / supersede_learning to "
                "correct the fact; the original stays auditable and restorable."
            ),
            "values": literals,
        }

    # ── HARD-FORGET (secret) ──────────────────────────────────────────────────
    matcher = ForgetMatcher(literals=literals, use_regex=use_regex)
    counts = _dry_run_counts(matcher)

    # GUARD: destructive secret sweep requires explicit dry_run=False + confirm.
    if dry_run or not confirm:
        return {
            "ok": True,
            "mode": "secret",
            "destructive": False,
            "dry_run": True,
            "armed": (not dry_run and confirm),
            "counts": counts,
            "guard": (
                "DRY-RUN. To physically delete, call again with dry_run=False AND "
                "confirm=True. No store was modified."
            ),
        }

    # Confirmed destructive path — sweep ALL LIVE DBs by introspection.
    deleted_per_store: dict[str, int] = {}
    redacted_per_store: dict[str, int] = {}
    conns = _live_conns()
    try:
        for db_name, conn, _owns in conns:
            # Zero freed pages on the shared (nexo/cognitive) handles too, so the
            # secret bytes do not survive in the freelist after the delete.
            try:
                conn.execute("PRAGMA secure_delete=ON")
            except Exception:
                pass
            result = _sweep_db(conn, matcher)
            for table, n in result["deleted"].items():
                deleted_per_store[f"{db_name}.{table}"] = n
            for table, n in result["redacted"].items():
                redacted_per_store[f"{db_name}.{table}"] = n
    finally:
        _close_if_owned(conns)

    # Legacy shadow DBs — cognitive + local-context (a secret in a shadow is a leak).
    shadow_result: dict[str, Any] = {}
    if sweep_shadows:
        shadow_result = _sweep_shadows(matcher)

    # HNSW: embeddings were deleted with their rows; drop persisted indices so a
    # later search cannot reload vectors built from forgotten rows.
    hnsw_result = {"invalidated": False}
    if invalidate_hnsw:
        try:
            import hnsw_index

            hnsw_index.invalidate("both", remove_persisted=True)
            hnsw_result = {"invalidated": True, "store": "both", "remove_persisted": True}
        except Exception as exc:
            hnsw_result = {"invalidated": False, "error": str(exc)[:200]}

    # Transcripts on disk (outside SQLite).
    transcript_result: dict[str, int] = {}
    if redact_transcripts:
        transcript_files = _transcript_files(_transcript_roots())
        transcript_result = _redact_transcripts(matcher, transcript_files)

    # VERIFICATION — re-enumerate + re-scan everything; complete only at zero.
    verification = verify_forgotten(matcher)

    deleted_total = sum(deleted_per_store.values())
    redacted_total = sum(redacted_per_store.values())

    operation = {
        "mode": "secret",
        "reason": reason,
        "values_count": len(literals),
        "use_regex": use_regex,
        "pre_counts": counts,
        "deleted_per_store": deleted_per_store,
        "redacted_per_store": redacted_per_store,
        "deleted_total": deleted_total,
        "redacted_total": redacted_total,
        "shadow_swept": shadow_result,
        "hnsw": hnsw_result,
        "transcripts_redacted": transcript_result,
        "verification": verification,
        "live_dbs": counts.get("live_dbs", []),
        "backup_scope": _BACKUP_SCOPE_NOTE,
        "at": _now_epoch(),
    }
    ledger_id = _write_ledger(operation)

    return {
        "ok": True,
        "mode": "secret",
        "destructive": True,
        "deleted_per_store": deleted_per_store,
        "redacted_per_store": redacted_per_store,
        "deleted_total": deleted_total,
        "redacted_total": redacted_total,
        # Back-compat: callers summed deleted_total; expose affected total too.
        "affected_total": deleted_total + redacted_total,
        "shadow_swept": shadow_result,
        "hnsw": hnsw_result,
        "transcripts_redacted": transcript_result,
        "verification": verification,
        "complete": verification["complete"],
        # Honest scope + which live DBs were swept (for the operator's report).
        "live_dbs": counts.get("live_dbs", []),
        "backup_scope": _BACKUP_SCOPE_NOTE,
        "ledger_id": ledger_id,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Wiring helpers
# ─────────────────────────────────────────────────────────────────────────────


def sweep_revoked_secret(value: str, *, reason: str = "credential_deleted") -> dict[str, Any]:
    """Auto-trigger entry point for ``nexo_credential_delete``.

    Runs a confirmed HARD-FORGET sweep over a just-deleted credential value so
    revoking the credential leaves no grep-able copies in memory. Safe no-op for
    empty/short values (avoids deleting on a meaningless substring)."""
    value = str(value or "").strip()
    if len(value) < 8:
        return {"ok": True, "skipped": True, "reason": "value_too_short_for_safe_match"}
    return forget(
        value,
        mode="secret",
        dry_run=False,
        confirm=True,
        use_regex=False,
        reason=reason,
    )


def handle_memory_forget(
    value: str = "",
    mode: str = "secret",
    dry_run: bool = True,
    confirm: bool = False,
    use_regex: bool = False,
    reason: str = "",
) -> str:
    """MCP handler: ``nexo_memory_forget``. Returns a human-readable summary."""
    result = forget(
        value,
        mode=mode,
        dry_run=dry_run,
        confirm=confirm,
        use_regex=use_regex,
        reason=reason,
    )
    if not result.get("ok"):
        return f"ERROR: {result.get('error', 'forget failed')}"

    if result.get("mode") == "fact":
        return "CORRECT-FACT: " + result["message"]

    if not result.get("destructive"):
        counts = result.get("counts", {})
        live_dbs = counts.get("live_dbs", [])
        lines = [
            "DRY-RUN (no store modified).",
            f"Total matching rows: {counts.get('total_rows', 0)} across "
            f"{counts.get('tables_scanned', 0)} tables in {len(live_dbs)} live DB(s) "
            f"[{', '.join(live_dbs)}] (coverage: all-live-dbs-by-introspection).",
        ]
        per_store = counts.get("per_store", {})
        if per_store:
            lines.append("Per store: " + ", ".join(f"{k}={v}" for k, v in per_store.items()))
        tlines = counts.get("transcript_lines", 0)
        if tlines:
            lines.append(f"Transcript lines matching: {tlines}.")
        if counts.get("shadow_hits"):
            lines.append(f"Shadow legacy DB hits: {counts['shadow_hits']}.")
        lines.append("To delete: dry_run=False AND confirm=True.")
        lines.append(f"NOTE: {counts.get('backup_scope', _BACKUP_SCOPE_NOTE)}")
        return "\n".join(lines)

    # Destructive result.
    verification = result.get("verification", {})
    complete = verification.get("complete")
    status = "COMPLETE (verified zero matches everywhere)" if complete else "INCOMPLETE — residual matches remain"
    lines = [
        f"HARD-FORGET {status}.",
        f"Deleted {result.get('deleted_total', 0)} row(s), redacted "
        f"{result.get('redacted_total', 0)} row(s).",
    ]
    live_dbs = result.get("live_dbs", [])
    if live_dbs:
        lines.append(f"Live DBs swept ({len(live_dbs)}): {', '.join(live_dbs)}.")
    affected = {**result.get("deleted_per_store", {})}
    for k, v in result.get("redacted_per_store", {}).items():
        affected[k] = affected.get(k, 0) + v
    if affected:
        lines.append("Per store: " + ", ".join(f"{k}={v}" for k, v in affected.items()))
    lines.append(f"HNSW invalidated: {result.get('hnsw', {}).get('invalidated')}")
    if result.get("shadow_swept"):
        lines.append(f"Shadow legacy DBs cleaned: {list(result['shadow_swept'].keys())}")
    if result.get("transcripts_redacted"):
        lines.append(f"Transcripts redacted: {len(result['transcripts_redacted'])} file(s).")
    if not complete:
        lines.append(f"RESIDUAL stores: {verification.get('residual_stores')}")
        lines.append(f"RESIDUAL fts: {verification.get('residual_fts')}")
        lines.append(f"RESIDUAL transcripts: {verification.get('residual_transcripts')}")
        lines.append(f"RESIDUAL shadows: {verification.get('residual_shadows')}")
    if result.get("ledger_id"):
        lines.append(f"Ledger id (memory_corrections): {result['ledger_id']}")
    lines.append(f"SCOPE: {result.get('backup_scope', _BACKUP_SCOPE_NOTE)}")
    return "\n".join(lines)
