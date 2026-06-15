"""Working-memory / resolution cache for the pre-answer router and repo maps.

This is the read/write side of the dead-but-already-computed cache key that
``pre_answer_runtime.select_budget_policy`` already produces (``route_cache_key``
+ ``cache_ttl_seconds``). The answer-path logged those for telemetry but nobody
read them; this module wires the ``get``/``set`` so a freshly resolved answer is
reused instead of re-running the whole router on the next identical question
within the TTL window.

Design (Francisco's brief): "when I mention project X or ask what you know about
María, don't re-search from zero if I just resolved it; only re-check if >X hours
passed, the source changed, or something relevant changed."

This module is a deliberately REDUCED copy of the proven ``semantic_layers``
pattern (``_source_fingerprint`` / ``source_version_for``), NOT a reinvention.
It is non-authoritative: diary / workflows / tasks / evidence / memory /
learnings / change_log and the git repos remain canonical. We only cache the
FINAL organized retrieval result.

ANTI-STALE RULE OF GOLD — a HIT is valid only if ALL hold:
  1. now() < expires_at                          (TTL ceiling — cheap fast-fail)
  2. status == 'fresh'
  3. GLOBAL change_watermark == stored            (any change_log mutation → MISS)
  4. CONTENT SNAPSHOT matches: every consulted row, re-read by id, has the same
     cheap version it had at write time. If ANY row changed, disappeared, or its
     version cannot be read → MISS. This is the PRIMARY freshness guarantee.
If ANY fails → MISS → normal route runs, rewrites and re-caches. We never serve
something that could be stale. The ``instant`` tier (ttl=0) never caches.
The ``set`` happens after the FINAL route (including escalation), never mid-way.

WHY A CONTENT SNAPSHOT (and not just a fingerprint over proxy signals).
Earlier rounds derived freshness from PROXY signals: a single
``source_fingerprint`` digest from ``semantic_layers.source_version_for`` plus
the global ``change_log`` watermark. Both have blind spots for the refs the
pre-answer router actually emits. The router's ``evidence_refs`` are
``{source_name}:{id}`` with ``source_name`` ∈ {followups, reminders,
protocol_tasks, commitments, memory, recent_context, workflows, evidence_ledger,
causal_graph, learning, local_asset, hot_context, change_log, …} — the literal
SOURCE NAMES, often PLURAL. ``source_version_for`` keys off CANONICAL,
mostly-singular prefixes (``followup:``), so ``followups:NF-X`` fell through to
an ``unsupported`` namespace → empty version → an inert fingerprint. And the
stores behind those names (followups, reminders, workflows, commitments) are
mutated by tools that do NOT write ``change_log`` (e.g. ``followup_complete`` is
a plain UPDATE), so the global watermark does not move either. Net result: a
``followups:NF-X status=open`` answer was served stale after the followup was
completed. Verified, reproducible (``test_H*``).

The fix stops depending on proxies. ``_SOURCE_VERSIONERS`` is an EXPLICIT map
``source_name → (reads the concrete row, returns a cheap version)`` covering
EVERY source_name the router emits. ``set()`` captures a snapshot
``{ref: version}`` of the REAL rows; ``is_valid()`` re-reads those exact rows by
id (cheap, indexed lookups — never a full retrieval) and compares. The version
is whatever changes when the material content changes: a followup's
``status``/``updated_at``, a learning's ``content``, a local asset's
fingerprint, etc. Conditions (1)–(3) remain as cheap pre-checks; condition (4)
is the truth.

Condition (3) compares against the GLOBAL watermark (``get_change_watermark``
with sid=None), never the entry's sid-scoped one. Under the NEXO identity model
("if another terminal did X, I did X") a mutation landed by a DIFFERENT session
MUST invalidate this session's cache. The entry's ``sid`` scopes only the cache
KEY (so session A never serves session B's answer — enforced in ``get`` via
``expected_sid``), never the freshness.

CONSERVATIVE WRITE GATE: an answer is cacheable only if EVERY evidence ref has a
freshness handle — i.e. its ``source_name`` is in ``_SOURCE_VERSIONERS`` (or it
resolves, via the canonical resolver, to a versioned/missing-marked row), OR it
is one of the router's synthetic inline markers whose freshness legitimately
rides the global watermark (``filesystem:inline`` / ``recent_context:inline`` /
``commitments:text`` / ``kg:node:…``). A ref backed by NO trackable handle
(``project_atlas`` JSON, ``doc``/``spec``/``commit``/``audit``/``release``, an
unknown source_name, or a positional row we cannot identify) makes the whole
answer un-cacheable: ``set()`` refuses it with ``reason='untrackable_source'``.
Better an extra MISS than a stale HIT. The repo map carries its OWN handle
(``git_head`` + 24h TTL) and opts out via ``require_trackable_refs=False``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from typing import Any

import db


# This module exposes a public ``set(cache_key, result, ...)`` writer (the
# documented cache API), which shadows the builtin ``set`` inside the module
# scope. Capture the builtin once so internal code can still use it safely.
_builtin_set = set


POLICY_VERSION = "resolution_cache_v1"

# Intents that depend on the live session / who-said-what. Never cache them
# without scoping to the sid, otherwise one session could serve another
# session's "what did I just do" answer (cross-session leak).
SESSION_SCOPED_INTENTS = {"prior_work", "identity_authorship", "live_state_claim"}

# Module-level lock: the pre-answer executor is shared and MCP/CLI can hit the
# fast-path concurrently. SQLite is in WAL, but the get→validate→bump-hit and
# set sequences are guarded so two callers cannot corrupt hit_count / race a
# write. Small, but mandatory.
_LOCK = threading.RLock()

# Maintenance: the cache table must not grow without bound. We prune expired
# rows opportunistically on writes, throttled so it is not an extra DELETE on
# every single ``set``. ``_PRUNE_EVERY`` writes between prunes; ``_MAX_ROWS`` is
# a hard backstop that trims the oldest expired/stale rows if the table balloons
# (e.g. a burst of distinct keys) before the throttle window elapses.
_PRUNE_EVERY = 50
_MAX_ROWS = 5000
_writes_since_prune = 0


def _conn() -> sqlite3.Connection:
    return db.get_db()


def _now() -> float:
    try:
        return float(db.now_epoch())
    except Exception:
        return time.time()


def _table_ready(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='resolution_cache' LIMIT 1"
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _change_watermark(sid: str | None = None) -> int:
    try:
        return int(db.get_change_watermark(sid))
    except Exception:
        return 0


def _parse_refs(value: Any) -> list[str]:
    """Coerce evidence/source refs into a clean, de-duped, sorted list."""
    refs: list[str] = []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, _builtin_set, frozenset)):
        return refs
    for item in value:
        clean = str(item or "").strip()
        if clean and clean not in refs:
            refs.append(clean)
    return refs


# ── Per-row content versioners (the real anti-stale map) ─────────────────────
#
# Maps the pre-answer router's SOURCE-NAME prefixes (as emitted by
# ``pre_answer_router._rows_result`` and the inline ``evidence_refs=[...]``
# branches) to a cheap, real "version" read from the concrete row. The version
# is a small hash over the columns that CHANGE when the material content changes
# (status, updated_at, content, fingerprint…). If the row is gone we return the
# MISSING sentinel so a deletion still changes the snapshot. ``None`` means "no
# handle for this ref" → the write gate refuses to cache (untrackable_source).
#
# Each entry is (table, id_column, version_columns). The id in the ref is looked
# up by ``id_column`` — a single indexed SELECT, never a retrieval. This is the
# explicit map the brief requires: it covers EVERY source_name the router emits,
# so freshness no longer rides proxy signals (namespace classification +
# watermark) that miss these stores.

_MISSING = "__missing__"

# A row-version resolution can land in three states, and the fail-closed
# invariant treats each differently:
#
#   * a real version string  → the ref resolves to a REAL row whose content we
#     hashed (the only thing safe to cache on);
#   * ``_MISSING``           → the table/id-column are correct but NO row exists
#     right now. On READ this is the deletion signal (real→missing = MISS); on
#     WRITE it means there is no real row backing the answer, so the write gate
#     must REFUSE (untrackable) — never cache an answer about a row that is not
#     there;
#   * ``None``               → the ref shape cannot be resolved to a real row at
#     all (unknown source_name, unknown composite inner-kind, wrong/positional
#     id, constant validator digest) → untrackable, refuse.
#
# Only a real version is cacheable. ``_MISSING`` and ``None`` both block the
# write gate; the difference is that ``_MISSING`` still participates in the
# stored snapshot's read-time comparison so a deletion of a row that WAS real at
# write time is caught. By construction a ref can never be cached on a CONSTANT
# sentinel: if it cannot resolve to a real row when ``set()`` runs, it is
# refused. "Mejor MISS de mas que un stale."

# source_name → (table, id_column, version_columns). version_columns are hashed
# together; whichever of them exists in the live table is used (schema-tolerant).
_SOURCE_VERSIONERS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # Reminder/followup machinery — mutated by *_complete / *_update WITHOUT a
    # change_log write, so these are the heart of the stale gap.
    "followups": ("followups", "id", ("status", "updated_at", "date", "verification", "description")),
    "reminders": ("reminders", "id", ("status", "updated_at", "date", "description", "category")),
    # Protocol / workflow runtime.
    "protocol_tasks": ("protocol_tasks", "task_id", ("status", "opened_at", "closed_at", "goal", "files_changed", "close_evidence", "outcome_notes")),
    "workflows": ("workflow_runs", "run_id", ("status", "updated_at", "next_action", "current_step_key", "last_checkpoint_label")),
    # Episodic / ledger sources.
    "change_log": ("change_log", "id", ("id", "created_at", "files", "what_changed", "why")),
    # The router's ``_source_diary`` adapter builds its ref from ``session_diary.id``
    # (the numeric PK), pinned in ``pre_answer_router._ROUTER_REF_ID_FIELD['diary']``.
    # It must be versioned by that SAME ``id`` column — NOT ``session_id``. A row
    # carries both, and ``session_id`` is free text that can equal another row's
    # numeric ``id``: looking up by ``session_id`` would resolve ``diary:<id>`` to
    # the WRONG row (editing the real row would not move the snapshot → STALE HIT,
    # while editing the colliding row would wrongly invalidate). This mirrors the
    # already-correct ``_EVIDENCE_LEDGER_COMPOSITE['diary']`` entry (also ``id``).
    "diary": ("session_diary", "id", ("id", "created_at", "summary", "pending", "context_next", "mental_state")),
    "commitments": ("commitments", "id", ("status", "updated_at", "closed_at", "deadline", "statement", "evidence_ref", "outcome_id")),
    # Live context. The router's recent_context adapter emits ``hot_context:<key>``
    # (NOT ``recent_context:<key>``); the literal ``recent_context`` prefix only
    # appears as the synthetic ``recent_context:inline`` marker, which rides the
    # global watermark (it is in ``_WATERMARK_TRACKED_SOURCES``). So only
    # ``hot_context`` is per-row versioned here — mapping ``recent_context`` to a
    # hot_context row lookup would wrongly REFUSE the legitimate inline marker.
    "hot_context": ("hot_context", "context_key", ("state", "last_event_at", "updated_at", "summary", "source_id")),
    "continuity": ("continuity_snapshots", "id", ("id", "updated_at", "event_type", "trace_id", "idempotency_key")),
    "transcript_index": ("transcript_index", "id", ("content_hash", "modified_at", "indexed_at", "message_count")),
    "runtime_db": ("lifecycle_events", "event_id", ("delivery_status", "retry_count", "processed_at", "last_error", "created_at")),
    # Knowledge / assets.
    "learning": ("learnings", "id", ("updated_at", "status", "title", "content", "category")),
    "local_asset": ("local_assets", "asset_id", ("updated_at", "quick_fingerprint", "modified_at_fs", "size_bytes", "status")),
    "preference": ("preferences", "key", ("value", "category", "updated_at")),
}

# COMPOSITE-ID resolution for the ``evidence_ledger`` source.
#
# The ``evidence_ledger`` router adapter renders ``evidence_ledger.search_evidence``
# rows whose ``evidence_id`` is itself a COMPOSITE ``<inner_kind>:<id>`` (see
# ``src/evidence_ledger.py``: ``task:<task_id>``, ``workflow:<run_id>``,
# ``diary:<id>``, ``lifecycle:<event_id>``, ``continuity:<id>``,
# ``evidence_record:<event_uid>``, ``change_log:<id>``,
# ``workflow_checkpoint:<id>``, ``local_context:<id>``,
# ``local_context_usage:<event_id>``, ``transcript:<file>``). ``_rows_result``
# then prefixes the SOURCE name, so the cache sees ``evidence_ledger:task:PT-1``.
#
# The earlier map sent EVERY such ref to ``memory_events.event_uid`` — only
# ``evidence_record`` actually lives there. Every other composite id failed to
# match → a constant ``__missing__`` → a frozen snapshot → STALE. This map routes
# each inner kind to the CORRECT backing table so the version derives from the
# real row's content. An inner kind not listed here → None (untrackable).
#
# Each value is (table, id_column, version_columns). ``transcript`` is virtual
# (transcripts come from files, not a row) → mapped to None explicitly so a
# transcript-backed answer is refused rather than frozen.
_EVIDENCE_LEDGER_COMPOSITE: dict[str, tuple[str, str, tuple[str, ...]] | None] = {
    "task": ("protocol_tasks", "task_id", ("status", "opened_at", "closed_at", "goal", "files_changed", "close_evidence", "outcome_notes")),
    "workflow": ("workflow_runs", "run_id", ("status", "updated_at", "next_action", "current_step_key", "last_checkpoint_label")),
    "workflow_checkpoint": ("workflow_checkpoints", "id", ("step_status", "run_status", "checkpoint_label", "created_at", "summary")),
    "diary": ("session_diary", "id", ("created_at", "summary", "pending", "context_next", "mental_state")),
    "lifecycle": ("lifecycle_events", "event_id", ("delivery_status", "retry_count", "processed_at", "last_error", "created_at")),
    "continuity": ("continuity_snapshots", "id", ("updated_at", "event_type", "trace_id", "idempotency_key")),
    "change_log": ("change_log", "id", ("id", "created_at", "files", "what_changed", "why")),
    "evidence_record": ("memory_events", "event_uid", ("event_uid", "input_hash", "output_digest", "metadata_json", "created_at")),
    "local_context": ("local_context_queries", "id", ("created_at", "query_hash", "result_count", "intent", "confidence")),
    # Transcript evidence is file-backed (no row to version) → refuse, don't freeze.
    "transcript": None,
    "local_context_usage": None,
}

# source_names whose freshness LEGITIMATELY rides only the global change_log
# watermark: the router's synthetic inline markers + the canonical kinds whose
# stores DO flow through change_log. These are NOT untrackable (else the common
# filesystem / recent-context / kg answers would never cache), but they have no
# per-row snapshot — condition (3) is their guard. Listed explicitly so an
# UNKNOWN source_name is never silently assumed to be watermark-tracked.
_WATERMARK_TRACKED_SOURCES: frozenset[str] = frozenset({
    "filesystem", "recent_context", "kg", "causal_graph", "kg_neighbors",
    "associative_graph", "commitments", "guard_context", "cognitive",
})
# NOTE: ``memory_event`` is deliberately NOT watermark-tracked. It resolves to a
# REAL memory_events row through the canonical resolver (``source_version_for``),
# giving it per-row freshness — a content edit or deletion is a MISS, stronger
# than the watermark-only guard. Leaving it in the watermark set would shadow the
# canonical resolver and let a memory_event that changed without a change_log
# write be served stale. ``commitments``/``recent_context`` stay listed: the
# former is per-row via ``_SOURCE_VERSIONERS`` (this entry is an inert fallback),
# the latter has no per-row prefix emitted by the router (only the synthetic
# ``recent_context:inline`` marker), so the watermark is its correct guard.

# source_names that are genuinely untrackable (no per-row handle AND not in
# change_log): caching an answer that depends on them risks staleness, so the
# write gate refuses. Mirrors the canonical "validator_digest" kinds plus the
# router sources that read flat files / live greps / catalogs.
_UNTRACKABLE_SOURCES: frozenset[str] = frozenset({
    "project_atlas", "doc", "spec", "commit", "audit", "finding", "release",
    "outcome", "correction", "guard", "local_context", "test",
    "runtime_docs", "source_grep", "system_catalog",
})


def _split_source_name(ref: str) -> tuple[str, str]:
    """Split ``{source_name}:{rest}`` → (source_name, rest). rest keeps any
    further ``:`` (e.g. ``memory:learning:42`` → ('memory', 'learning:42'))."""
    raw = str(ref or "").strip()
    if ":" not in raw:
        return raw, ""
    name, rest = raw.split(":", 1)
    return name, rest


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r["name"]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall() if "name" in r.keys()}
    except Exception:
        return _builtin_set()


def _row_version_from_table(
    conn: sqlite3.Connection,
    table: str,
    id_column: str,
    id_value: str,
    version_columns: tuple[str, ...],
) -> str | None:
    """Read ONE row by id and hash its version columns.

    Returns a real content version ONLY when the row EXISTS. Returns ``None`` in
    every other case — the table/columns are unusable in this DB, the lookup
    raised, OR no row matches the id. ``None`` is the single "no real row" signal:
    the write gate refuses it (nothing real to cache on) and the read path treats
    None != the stored version as a change → MISS (so a row that was real at write
    time and later disappears is caught). There is NO constant sentinel — that was
    the stale class; a version is either a real-row hash or None.
    """
    cols = _table_columns(conn, table)
    if not cols or id_column not in cols:
        return None
    if not str(id_value or "").strip():
        return None
    usable = [c for c in version_columns if c in cols]
    if not usable:
        usable = [id_column]
    select_cols = ", ".join(usable)
    try:
        row = conn.execute(
            f"SELECT {select_cols} FROM {table} WHERE {id_column}=? LIMIT 1",
            (id_value,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    data = dict(row)
    return _hash({"t": table, "v": {c: data.get(c) for c in usable}})


def _unified_search_version(conn: sqlite3.Connection, rest: str) -> str | None:
    """Version a ``memory:<source>:<source_id>`` ref via the unified_search FTS
    snapshot (source, source_id) → updated_at. ``None`` whenever there is no real
    row (pair gone, table absent, or not a resolvable shape).

    ``recall`` returns heterogeneous rows keyed by (source, source_id) with no
    single ``id``; the router now emits ``memory:<source>:<source_id>`` so we
    have a real, resolvable handle. A bare positional ``memory:<n>`` (no nested
    source) is NOT resolvable → return None so the write gate refuses it."""
    if ":" not in rest:
        return None
    src, source_id = rest.split(":", 1)
    src, source_id = src.strip(), source_id.strip()
    if not src or not source_id:
        return None
    try:
        # unified_search is an FTS5 table; if it does not exist the SELECT raises
        # and we treat the ref as unresolvable (→ untrackable, not stale).
        row = conn.execute(
            "SELECT updated_at, title FROM unified_search WHERE source=? AND source_id=? LIMIT 1",
            (src, source_id),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    data = dict(row)
    return _hash({"unified": [src, source_id], "updated_at": data.get("updated_at"), "title_h": _hash(str(data.get("title") or ""))})


def _evidence_ledger_version(conn: sqlite3.Connection, rest: str) -> str | None:
    """Resolve an ``evidence_ledger:<inner_kind>:<id>`` composite to a real row.

    ``rest`` is the inner composite ``<inner_kind>:<id>`` (``task:PT-1``,
    ``workflow:WF-2``, ``evidence_record:EV-9``, …). We parse the inner kind and
    route to the CORRECT backing table (NOT memory_events for everything), so the
    version derives from the real row's content. Returns:
      * a real version (prefixed ``evidence_ledger:<inner_kind>:<hash>``) when the
        row exists;
      * None when no real row matches (missing/deleted), the inner kind is
        unknown, the kind is file-backed (transcript/local_context_usage), or the
        shape is otherwise unresolvable. A None is refused by the write gate and,
        on read, is != the stored version → MISS. Never a constant sentinel.
    """
    if ":" not in rest:
        # No inner kind (bare ``evidence_ledger:<x>``) — not a router-emitted
        # shape and not resolvable to a row → untrackable.
        return None
    inner_kind, inner_id = rest.split(":", 1)
    inner_kind, inner_id = inner_kind.strip(), inner_id.strip()
    spec = _EVIDENCE_LEDGER_COMPOSITE.get(inner_kind)
    if spec is None:
        # Unknown inner kind, or an explicitly file-backed kind (transcript,
        # local_context_usage) with no row to version → refuse, never freeze.
        return None
    if not inner_id:
        return None
    table, id_column, version_columns = spec
    version = _row_version_from_table(conn, table, id_column, inner_id, version_columns)
    if version is None:
        # No real row (missing/deleted) or unusable table → None. The write gate
        # refuses; the read path sees None != stored → MISS. Never a constant.
        return None
    return f"evidence_ledger:{inner_kind}:{version}"


def ref_version(ref: str, *, conn: sqlite3.Connection | None = None) -> str | None:
    """Return a cheap, real version for a single ref, or None if untrackable.

    The version is a real per-row content hash when the ref resolves to an
    EXISTING row. A ``__missing__`` marker (the table/id-column are correct but no
    row matches right now) is returned EMBEDDED in the version so the read path
    detects a deletion of a row that was real at write time — but the write gate
    (``untrackable_refs`` / ``set``) treats ``__missing__`` as un-cacheable, so an
    answer is never cached on a row that is not there. A shape that cannot resolve
    to a real row at all returns None.

    Resolution order:
      1. ``evidence_ledger:<inner_kind>:<id>`` composite → its CORRECT backing
         table (task→protocol_tasks, workflow→workflow_runs,
         evidence_record→memory_events, …). Unknown/file-backed inner kind → None.
      2. Router SOURCE-NAME map (``_SOURCE_VERSIONERS``) — per-row version.
      3. ``memory:<source>:<source_id>`` → unified_search snapshot.
      4. Watermark-tracked source_names → ``__wm__`` token (no per-row version;
         their guard is the global watermark) — trackable.
      5. Canonical resolver (``semantic_layers.source_version_for``) for nested
         canonical refs (``memory_event:``, ``local_asset:#chunk``, ``learning:``):
         versioned or missing-marked → trackable; constant ``validator_digest:`` →
         untrackable (None).
      6. Anything else → None (untrackable).
    """
    conn = conn or _conn()
    raw = str(ref or "").strip()
    if not raw:
        return None
    name, rest = _split_source_name(raw)

    # (1) evidence_ledger composite — must come BEFORE the source-name map so the
    # composite inner kind decides the table, not a single hard-coded one.
    if name == "evidence_ledger":
        return _evidence_ledger_version(conn, rest)

    spec = _SOURCE_VERSIONERS.get(name)
    if spec is not None:
        table, id_column, version_columns = spec
        id_value = rest
        # ``local_asset:<id>#chunk:<n>`` — version by the asset, ignore chunk.
        if name == "local_asset" and "#" in id_value:
            id_value = id_value.split("#", 1)[0]
        if not id_value:
            return None
        version = _row_version_from_table(conn, table, id_column, id_value, version_columns)
        if version is None:
            # No real row (missing/deleted) or unusable table/columns → no handle.
            # Write gate refuses; read path sees None != stored → MISS. Never a
            # "fresh forever" constant.
            return None
        return f"{name}:{version}"

    if name == "memory":
        v = _unified_search_version(conn, rest)
        return None if v is None else f"memory:{v}"

    if name in _WATERMARK_TRACKED_SOURCES:
        # Trackable via the global watermark (condition 3); no per-row snapshot.
        return f"__wm__:{name}"

    if name in _UNTRACKABLE_SOURCES:
        return None

    # Nested canonical ref (e.g. ``memory_event:``, ``local_asset:``) — defer to
    # the canonical resolver so cognitive/local_context/semantic_layers sub-refs
    # stay trackable.
    try:
        from semantic_layers import source_version_for

        info = source_version_for(raw, conn=conn)
    except Exception:
        return None
    status = str(info.get("validation_status") or "")
    version = str(info.get("source_version") or "")
    if status == "missing":
        # No real row behind the canonical ref → None (refuse at write; on read a
        # real→missing transition is None != stored → MISS). No constant marker.
        return None
    if version.startswith("validator_digest:"):
        return None  # constant digest = no real handle
    if info.get("ok") and version:
        return f"canon:{version}"
    return None


def _resolves_to_real_row(version: str | None) -> bool:
    """True only when ``version`` derives from a REAL existing row.

    This is the write-gate truth: a real version string is cacheable; a ``None``
    (no handle) or any ``__missing__`` marker embedded in the version (no row
    right now) is NOT — caching either would risk serving an answer about a row
    that is gone or never existed. Watermark tokens (``__wm__``) and the
    canonical resolver's positive versions are real handles.

    The check is on the WRITE side; the READ side (``is_valid``) still keeps the
    raw ``ref_version`` so a row that was real at write time and later disappears
    (real→__missing__) is detected as a change → MISS.
    """
    if version is None:
        return False
    if version == "":
        return False
    # Any embedded missing marker means "no real row at write time".
    if _MISSING in version:
        return False
    return True


def self_check_fail_closed(*, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Structural self-check that the fail-closed invariant holds for the WHOLE
    map — not just the cases a test happens to exercise.

    Three guarantees, checked against the LIVE schema so drift is caught:

      (A) Every per-row versioner (``_SOURCE_VERSIONERS`` + the
          ``_EVIDENCE_LEDGER_COMPOSITE`` entries) names a table+id-column that
          ACTUALLY EXISTS. If a versioner points at a non-existent table/column,
          ``_row_version_from_table`` returns '' for EVERY id forever — a
          permanent constant masquerading as a handle. That is exactly the stale
          class; this asserts it cannot happen silently.

      (B) The write gate (``_resolves_to_real_row``) REFUSES every constant /
          sentinel / unresolved version. A missing-row marker, an empty handle,
          and ``None`` must all be non-cacheable, so nothing can be cached on a
          version that does not derive from a real row.

      (C) ROW-CORRECTNESS / column alignment: every source the router PINS via
          ``pre_answer_router._ROUTER_REF_ID_FIELD`` builds its ref from the SAME
          column the versioner reads. (A) proves the versioner column exists; (C)
          proves it is the column the adapter actually emits. Without (C) a
          versioner can point at a real-but-WRONG column (``diary`` was versioned
          by ``session_id`` while the adapter emitted ``id``) — both columns
          exist, so (A) passes, yet ``ref_version`` resolves to another row and a
          value collision serves the wrong row's content as fresh → STALE. This
          closes that gap structurally, not case-by-case.

    Returns a dict with ``ok`` and any ``problems``. Raises nothing — callers
    (the anti-regression test) assert on the result.
    """
    conn = conn or _conn()
    problems: list[str] = []

    # (A) every versioner resolves against a real table + id column.
    def _check_versioner(label: str, table: str, id_column: str) -> None:
        cols = _table_columns(conn, table)
        if not cols:
            problems.append(f"{label}: table '{table}' does not exist in live schema")
        elif id_column not in cols:
            problems.append(f"{label}: id_column '{id_column}' missing from '{table}'")

    for name, (table, id_column, _cols) in _SOURCE_VERSIONERS.items():
        _check_versioner(f"_SOURCE_VERSIONERS[{name}]", table, id_column)
    for inner, spec in _EVIDENCE_LEDGER_COMPOSITE.items():
        if spec is None:
            continue  # intentionally file-backed → untrackable, no table to check.
        table, id_column, _cols = spec
        _check_versioner(f"_EVIDENCE_LEDGER_COMPOSITE[{inner}]", table, id_column)

    # (C) router ref-id column == versioner id-column for every pinned source.
    try:
        from pre_answer_router import _ROUTER_REF_ID_FIELD
    except Exception as exc:  # pragma: no cover — import only fails on broken tree
        problems.append(f"row-correctness: cannot import _ROUTER_REF_ID_FIELD ({exc})")
        _ROUTER_REF_ID_FIELD = {}
    for source, ref_id_field in _ROUTER_REF_ID_FIELD.items():
        spec = _SOURCE_VERSIONERS.get(source)
        if spec is None:
            problems.append(
                f"row-correctness: router pins '{source}'->'{ref_id_field}' but no "
                f"_SOURCE_VERSIONERS entry exists (ref would be untrackable)"
            )
            continue
        _table, versioner_id_column, _cols = spec
        if ref_id_field != versioner_id_column:
            problems.append(
                f"row-correctness: source '{source}' adapter emits ref by column "
                f"'{ref_id_field}' but versioner looks up by '{versioner_id_column}' "
                f"— ref resolves to the WRONG row (stale/over-resolution)"
            )

    # (B) the write gate refuses every sentinel / unresolved shape.
    sentinels = {
        "None": None,
        "empty": "",
        "bare_missing": _MISSING,
        "prefixed_missing": f"evidence_ledger:task:{_MISSING}",
        "canon_missing": f"canon:{_MISSING}",
        "source_missing": f"runtime_db:{_MISSING}",
    }
    for label, value in sentinels.items():
        if _resolves_to_real_row(value):
            problems.append(f"write-gate accepts sentinel '{label}' ({value!r}) — fail-closed broken")

    return {"ok": not problems, "problems": problems}


def row_version_snapshot(source_refs: list[str], *, conn: sqlite3.Connection | None = None) -> dict[str, str]:
    """Capture ``{ref: version}`` over the REAL rows behind the refs.

    For each ref, ``ref_version`` reads the concrete row (one indexed lookup) and
    returns a cheap version that changes when the material content changes. Refs
    with no handle (``None``) are omitted — they are caught separately by the
    write gate (``untrackable_refs``), which refuses to cache the whole answer.
    Watermark-tracked sources contribute a stable ``__wm__`` token so the
    snapshot stays deterministic.

    This snapshot is stored on write and re-read on every ``get``; it is the
    PRIMARY freshness signal, replacing the old proxy fingerprint.
    """
    conn = conn or _conn()
    snapshot: dict[str, str] = {}
    for ref in dict.fromkeys(str(r or "").strip() for r in source_refs):
        if not ref:
            continue
        version = ref_version(ref, conn=conn)
        if version is not None:
            snapshot[ref] = version
    return snapshot


def source_fingerprint(source_refs: list[str], *, conn: sqlite3.Connection | None = None) -> str:
    """Deterministic digest over the per-row snapshot (kept for telemetry and
    backward-compat callers/tests). The authoritative check is the stored
    snapshot compared row-by-row in ``is_valid``; this digest is a convenience
    rollup of the same data, so a content change still moves it.

    Untrackable refs (``ref_version`` → None) contribute a stable marker so the
    fingerprint is always defined; the write gate is what actually refuses to
    cache them, not this digest.
    """
    conn = conn or _conn()
    snapshot = row_version_snapshot(source_refs, conn=conn)
    items = [f"{ref}@{snapshot.get(ref, '__untrackable__')}" for ref in sorted(dict.fromkeys(source_refs))]
    return _hash({"policy_version": POLICY_VERSION, "sources": items})


def untrackable_refs(source_refs: list[str], *, conn: sqlite3.Connection | None = None) -> list[str]:
    """Return the refs that may NOT be cached on — the write-gate's reject list.

    THE FAIL-CLOSED INVARIANT (write side): an answer is cacheable iff EVERY ref
    resolves to a freshness handle that is demonstrably fresh RIGHT NOW —
    i.e. ``ref_version`` derives the version from a REAL existing row, or returns
    a ``__wm__`` watermark token (whose guard is the global change_log
    watermark). A ref is rejected here when ``ref_version`` is:

      * ``None`` — no resolvable shape at all: unknown source_name, unknown
        ``evidence_ledger`` composite inner-kind, file-backed evidence,
        positional ``memory:<n>`` / ``runtime_db:<n>``, a constant
        ``validator_digest`` canonical kind, flat-file/grep/catalog source; OR
      * a version embedding the ``__missing__`` marker — the table/id-column are
        right but NO row exists at write time, so there is nothing real to cache
        on. (On READ, a stored real version transitioning to ``__missing__`` is a
        deletion → MISS, handled by ``is_valid``; that is a different code path.)

    Crucially this kills the stale CLASS, not a case: a ref can NEVER be cached
    on a CONSTANT sentinel, because a constant ``__missing__`` (the old bug for
    composite/positional ids) is rejected here at write time. ``set()`` refuses
    the whole answer if this list is non-empty — better an extra MISS than a
    stale HIT. Empty means the answer is safe to cache.
    """
    conn = conn or _conn()
    bad: list[str] = []
    for ref in dict.fromkeys(str(r or "").strip() for r in source_refs):
        if not ref:
            continue
        if not _resolves_to_real_row(ref_version(ref, conn=conn)):
            bad.append(ref)
    return bad


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8", errors="ignore"
        )
    ).hexdigest()


def is_valid(entry: dict[str, Any], *, conn: sqlite3.Connection | None = None) -> tuple[bool, str]:
    """Return (valid, reason). Valid only when ALL conditions hold.

    Order is cheap → authoritative: TTL and status are O(1); the global watermark
    is one SELECT MAX(id); the content snapshot re-reads the consulted rows by id
    (a handful of indexed lookups). The snapshot is the PRIMARY guarantee — it is
    what catches a followup completed / learning edited / workflow advanced by a
    plain UPDATE that never touched change_log (so the watermark would not move).
    """
    conn = conn or _conn()
    now = _now()
    # (1) TTL ceiling
    if now >= float(entry.get("expires_at") or 0):
        return False, "expired_ttl"
    # (2) status
    if str(entry.get("status") or "") != "fresh":
        return False, "not_fresh"
    # (3) GLOBAL change watermark — cheap fast-fail. Any change_log mutation in
    # ANY session since the write invalidates. Compared against the GLOBAL
    # watermark (sid=None), never the entry's sid-scoped one: under the NEXO
    # identity model "if another terminal did X, I did X", a change that landed
    # in a different session MUST invalidate this session's cache. The entry's
    # sid scopes the cache KEY only (cross-session leak is blocked in get() via
    # expected_sid), never freshness.
    stored_watermark = int(entry.get("change_watermark") or 0)
    if _change_watermark(None) != stored_watermark:
        return False, "watermark_advanced"
    # (4) CONTENT SNAPSHOT — re-read each consulted row by id and compare its
    # cheap version to what we stored. This is the authoritative anti-stale
    # check: it covers the router's source-name refs (followups/reminders/
    # workflows/commitments/…) whose stores are NOT in change_log, so condition
    # (3) alone would miss them. A changed row, a deleted row (→ __missing__),
    # or a row whose version can no longer be read → MISS.
    stored_snapshot = entry.get("content_snapshot")
    if not isinstance(stored_snapshot, dict):
        stored_snapshot = {}
    for ref, stored_version in stored_snapshot.items():
        current_version = ref_version(str(ref), conn=conn)
        if current_version is None or current_version != str(stored_version):
            return False, "content_snapshot_changed"
    return True, "fresh_hit"


def get(
    cache_key: str,
    *,
    expected_sid: str = "",
    bump_hit: bool = True,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    """Return a VALID cache entry for ``cache_key`` or None on any MISS.

    ``expected_sid``: for session-scoped entries, the caller's sid must match
    the entry's sid; a mismatch is a MISS (no cross-session leak).
    """
    if not cache_key:
        return None
    conn = conn or _conn()
    with _LOCK:
        if not _table_ready(conn):
            return None
        try:
            row = conn.execute(
                "SELECT * FROM resolution_cache WHERE cache_key=? LIMIT 1", (cache_key,)
            ).fetchone()
        except Exception:
            return None
        if not row:
            return None
        entry = _row_to_entry(row)
        # Session scoping: if the entry is sid-bound it must match the caller.
        entry_sid = str(entry.get("sid") or "")
        if entry_sid and expected_sid and entry_sid != str(expected_sid):
            return None
        if entry_sid and not expected_sid:
            return None
        valid, reason = is_valid(entry, conn=conn)
        if not valid:
            # Mark stale so a later prune can collect it; do not delete on the
            # read path (keep reads cheap and lock-light).
            if reason in {"expired_ttl", "watermark_advanced", "content_snapshot_changed"}:
                try:
                    conn.execute(
                        "UPDATE resolution_cache SET status='stale' WHERE cache_key=? AND status='fresh'",
                        (cache_key,),
                    )
                    conn.commit()
                except Exception:
                    pass
            entry["miss_reason"] = reason
            entry["valid"] = False
            return None
        if bump_hit:
            try:
                conn.execute(
                    "UPDATE resolution_cache SET hit_count = hit_count + 1 WHERE cache_key=?",
                    (cache_key,),
                )
                conn.commit()
            except Exception:
                pass
            entry["hit_count"] = int(entry.get("hit_count") or 0) + 1
        entry["valid"] = True
        entry["miss_reason"] = ""
        return entry


def set(
    cache_key: str,
    result: dict[str, Any],
    *,
    ttl_seconds: int,
    kind: str = "route",
    intent: str = "",
    area: str = "",
    sid: str = "",
    source_refs: Any = None,
    policy_version: str = "",
    require_trackable_refs: bool = True,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Persist the FINAL organized result under ``cache_key``.

    ``ttl_seconds <= 0`` (the ``instant`` tier) never caches — returns a no-op.
    Called only after the route is final (post-escalation), so we cache the
    answer the user will actually get, never an intermediate empty pass.

    ``require_trackable_refs`` (default True): refuse to cache an answer whose
    evidence depends on a source with NO freshness handle — a ref whose
    ``ref_version`` is None (unknown source_name, flat-file/grep/catalog source,
    a constant ``validator_digest`` canonical kind, or a positional
    ``memory:<n>`` we cannot resolve to a row). Refusing returns
    ``reason='untrackable_source'``. Better an extra MISS than a stale HIT.
    Callers with an independent freshness handle (the repo map carries
    ``git_head``) pass ``require_trackable_refs=False``.
    """
    if not cache_key:
        return {"ok": False, "reason": "no_cache_key"}
    if int(ttl_seconds or 0) <= 0:
        return {"ok": False, "reason": "ttl_zero_never_cache"}
    # Session-scoped intents must carry a sid or they would leak across
    # sessions; refuse to cache them globally.
    if intent in SESSION_SCOPED_INTENTS and not sid:
        return {"ok": False, "reason": "session_scoped_without_sid"}

    conn = conn or _conn()
    with _LOCK:
        if not _table_ready(conn):
            return {"ok": False, "reason": "schema_missing"}
        refs = _parse_refs(source_refs if source_refs is not None else result.get("evidence_refs"))
        if require_trackable_refs and refs:
            untrackable = untrackable_refs(refs, conn=conn)
            if untrackable:
                # No freshness handle for these refs → caching could go stale.
                return {
                    "ok": False,
                    "reason": "untrackable_source",
                    "untrackable_refs": untrackable,
                    "untrackable_source": untrackable,
                }
        now = _now()
        expires_at = now + float(int(ttl_seconds))
        # PRIMARY anti-stale signal: a snapshot of the REAL rows behind the refs.
        snapshot = row_version_snapshot(refs, conn=conn)
        # Convenience rollup digest (telemetry / backward-compat); the snapshot
        # is the authority. Derived from the same data so it still moves on
        # content change.
        fingerprint = _hash(
            {"policy_version": POLICY_VERSION,
             "sources": [f"{r}@{snapshot.get(r, '__untrackable__')}" for r in sorted(dict.fromkeys(refs))]}
        )
        # GLOBAL watermark (sid=None): freshness must react to mutations in ANY
        # session, not just this entry's. The sid scopes the cache KEY only.
        watermark = _change_watermark(None)
        try:
            result_json = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except Exception:
            return {"ok": False, "reason": "result_not_serializable"}
        try:
            snapshot_json = json.dumps(snapshot, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        except Exception:
            snapshot_json = "{}"
        try:
            conn.execute(
                """
                INSERT INTO resolution_cache
                    (cache_key, kind, intent, area, sid, result_json,
                     source_fingerprint, source_refs_json, content_snapshot_json,
                     change_watermark, status, policy_version, resolved_at,
                     expires_at, hit_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'fresh', ?, ?, ?, 0)
                ON CONFLICT(cache_key) DO UPDATE SET
                    kind=excluded.kind,
                    intent=excluded.intent,
                    area=excluded.area,
                    sid=excluded.sid,
                    result_json=excluded.result_json,
                    source_fingerprint=excluded.source_fingerprint,
                    source_refs_json=excluded.source_refs_json,
                    content_snapshot_json=excluded.content_snapshot_json,
                    change_watermark=excluded.change_watermark,
                    status='fresh',
                    policy_version=excluded.policy_version,
                    resolved_at=excluded.resolved_at,
                    expires_at=excluded.expires_at,
                    hit_count=0
                """,
                (
                    cache_key, kind, intent, area, sid, result_json,
                    fingerprint, json.dumps(refs, ensure_ascii=True, separators=(",", ":")),
                    snapshot_json, watermark, policy_version or POLICY_VERSION, now, expires_at,
                ),
            )
            conn.commit()
        except Exception as exc:
            return {"ok": False, "reason": "store_failed", "detail": f"{type(exc).__name__}: {exc}"}
        # Keep the table bounded (throttled; never blocks/breaks the write).
        _maybe_prune(conn)
        return {
            "ok": True,
            "cache_key": cache_key,
            "kind": kind,
            "expires_at": expires_at,
            "source_fingerprint": fingerprint,
            "content_snapshot": snapshot,
            "change_watermark": watermark,
        }


def invalidate(cache_key: str = "", *, kind: str = "", conn: sqlite3.Connection | None = None) -> int:
    """Mark entries stale (by key or by kind). Returns rows touched."""
    conn = conn or _conn()
    with _LOCK:
        if not _table_ready(conn):
            return 0
        try:
            if cache_key:
                cur = conn.execute(
                    "UPDATE resolution_cache SET status='stale' WHERE cache_key=? AND status='fresh'",
                    (cache_key,),
                )
            elif kind:
                cur = conn.execute(
                    "UPDATE resolution_cache SET status='stale' WHERE kind=? AND status='fresh'",
                    (kind,),
                )
            else:
                cur = conn.execute("UPDATE resolution_cache SET status='stale' WHERE status='fresh'")
            conn.commit()
            return int(cur.rowcount or 0)
        except Exception:
            return 0


def prune(*, max_rows: int = _MAX_ROWS, conn: sqlite3.Connection | None = None) -> int:
    """Delete entries whose TTL elapsed; enforce a hard row cap. Returns rows deleted.

    First deletes every expired row (``expires_at <= now``). Then, if the table
    is still over ``max_rows``, trims the oldest stale rows, and finally the
    oldest rows by ``resolved_at`` as a last-resort backstop so the table can
    never grow without bound even under a flood of distinct fresh keys.
    """
    conn = conn or _conn()
    with _LOCK:
        if not _table_ready(conn):
            return 0
        deleted = 0
        try:
            cur = conn.execute("DELETE FROM resolution_cache WHERE expires_at <= ?", (_now(),))
            deleted += int(cur.rowcount or 0)
            if max_rows and max_rows > 0:
                total = int(
                    conn.execute("SELECT COUNT(*) FROM resolution_cache").fetchone()[0] or 0
                )
                overflow = total - int(max_rows)
                if overflow > 0:
                    # Drop the oldest rows (stale first via status order, then by
                    # resolved_at) until back under the cap.
                    cur = conn.execute(
                        """
                        DELETE FROM resolution_cache WHERE cache_key IN (
                            SELECT cache_key FROM resolution_cache
                            ORDER BY (status='fresh') ASC, resolved_at ASC
                            LIMIT ?
                        )
                        """,
                        (overflow,),
                    )
                    deleted += int(cur.rowcount or 0)
            conn.commit()
            return deleted
        except Exception:
            return deleted


def _maybe_prune(conn: sqlite3.Connection) -> None:
    """Throttled maintenance prune, called from the write path.

    Runs a real ``prune`` once every ``_PRUNE_EVERY`` writes so the cache table
    stays bounded without paying a DELETE on every ``set``. Best-effort: any
    failure is swallowed (maintenance must never break a cache write)."""
    global _writes_since_prune
    _writes_since_prune += 1
    if _writes_since_prune < _PRUNE_EVERY:
        return
    _writes_since_prune = 0
    try:
        prune(conn=conn)
    except Exception:
        pass


def _row_to_entry(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    try:
        data["result"] = json.loads(data.get("result_json") or "{}")
    except Exception:
        data["result"] = {}
    try:
        data["source_refs"] = json.loads(data.get("source_refs_json") or "[]")
    except Exception:
        data["source_refs"] = []
    try:
        snapshot = json.loads(data.get("content_snapshot_json") or "{}")
        data["content_snapshot"] = snapshot if isinstance(snapshot, dict) else {}
    except Exception:
        data["content_snapshot"] = {}
    return data


# ── Repo map (code working memory — Fase 3) ──────────────────────────────────

def _git_head(repo_dir: str) -> str:
    """Short HEAD hash of a repo dir, reusing the adaptive_mode git pattern."""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=repo_dir,
        )
        if out.returncode != 0:
            return ""
        return out.stdout.strip()
    except Exception:
        return ""


def repo_map_key(project_key: str) -> str:
    return f"repo:{str(project_key or '').strip().lower()}"


def get_repo_map(
    project_key: str,
    repo_dir: str = "",
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    """Return a fresh repo_map for ``project_key``, or None if it must rebuild.

    Code-specific invalidation: on top of the four standard conditions, the
    repo map is also a MISS if the repo's current ``git rev-parse --short HEAD``
    differs from the stored ``git_head`` (the repo moved → re-map). This is what
    lets "I already know how nexo-desktop works" hold until the repo changes,
    instead of re-reading the tree every time.
    """
    conn = conn or _conn()
    entry = get(repo_map_key(project_key), bump_hit=True, conn=conn)
    if not entry:
        return None
    result = entry.get("result") or {}
    if repo_dir:
        stored_head = str(result.get("git_head") or "")
        current_head = _git_head(repo_dir)
        if current_head and stored_head and current_head != stored_head:
            invalidate(repo_map_key(project_key), conn=conn)
            return None
    return entry


def set_repo_map(
    project_key: str,
    repo_map: dict[str, Any],
    repo_dir: str = "",
    *,
    ttl_seconds: int = 86400,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Cache a lightweight repo snapshot (tree + key files + atlas gotchas).

    Deliberately NO symbol/LSP parser (decided): the snapshot is the directory
    tree, key entrypoints/configs, and the project-atlas gotchas/locations.
    Invalidation is carried by git_head (cheap) + a long TTL (24h default).
    """
    conn = conn or _conn()
    payload = dict(repo_map)
    if repo_dir and "git_head" not in payload:
        payload["git_head"] = _git_head(repo_dir)
    # The repo map has no source_refs to fingerprint; carry an atlas marker so
    # the standard fingerprint stays stable and the watermark+git_head do the
    # real invalidation work.
    refs = _parse_refs(payload.get("source_refs")) or [f"project_atlas:{str(project_key or '').strip().lower()}"]
    return set(
        repo_map_key(project_key),
        payload,
        ttl_seconds=ttl_seconds,
        kind="repo_map",
        intent="repo_map",
        area="code",
        source_refs=refs,
        # The repo map's atlas ref resolves to a constant validator digest, but
        # the map carries its OWN freshness handle (git_head + 24h TTL +
        # watermark), so the trackable-refs gate would be a false positive here.
        require_trackable_refs=False,
        conn=conn,
    )
