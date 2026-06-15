"""Ola 2 — Auto-detection of own prior errors → immediate learning + prevention.

Francisco's ask: when the system itself sees that a *previous own action* was
wrong (e.g. "I wrote the code but forgot to create the cron in GCloud", and a
later action reveals it), capture a learning + a concrete prevention rule
*immediately*, without waiting for the operator to correct it.

This is the SELF-error counterpart to ``src/hooks/auto_capture.py`` (Ola 1),
which made USER-correction capture reliable. Here the trigger is objective
evidence from the operational ledger itself, never a vague heuristic.

Design contract — PRECISION OVER RECALL
---------------------------------------
Francisco hates noise/debt. A false learning is strictly worse than none.
Therefore this detector only fires a learning on HIGH-confidence, objective
evidence, and otherwise records (at most) a low-confidence *candidate* that
does NOT touch the learnings memory.

Objective signals consulted (the only ones that can reach FIRE):
  S1  file_overlap_correction
      The just-closed task corrects/fixes files that a *previously
      closed-as-done* task already touched, and the current close carries
      correction evidence (``correction_happened`` or explicit
      "previously/forgot/missing step" language). This is the canonical
      "code shipped but the cron was never created" case: the prior task was
      declared done, a later action reveals a step was missing.
  S2  reopen_of_done
      The current close explicitly references reopening / redoing work that a
      prior task closed as done on the same files.

Signal that can only reach CANDIDATE (never a learning on its own):
  S3  forgotten_step_followup
      A followup whose description objectively states an omission
      ("faltó/olvidé/forgot/never created the cron"). On its own this is a
      candidate; combined with S1 file overlap it reinforces S1.

Deliberately DISCARDED as too vague (must NOT fire):
  * generic "fix this" with no prior done task on the same files;
  * refactors / improvements / renames (no correction semantics);
  * a second commit on the same file = normal iteration;
  * the prior task closed as ``partial``/``failed`` (it never *claimed* done,
    so a later fix is expected, not a self-error);
  * same-task re-close, or the planned verification step landing;
  * short / trivial evidence;
  * ambiguity → CANDIDATE at most, never a learning.

Idempotency
-----------
The learning itself is deduped by the existing learning resolver / R05 Jaccard
merge in ``tools_learnings.handle_learning_add`` (same normalized title/content
→ merge, no duplicate). On top of that we key on a deterministic
``self_error_uid`` (current task + prior task + signal) so the same revealed
error can never spawn two attempts within a close, and the candidate debt path
reuses the existing open-debt dedup.

Everything here is best-effort and exit-safe: a failure must never block a
``nexo_task_close`` the operator already verified.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


# ── Tunables (conservative by design) ─────────────────────────────────
# A learning only fires at/above this confidence. Below it we may record a
# low-confidence candidate (no learning). Both thresholds are intentionally
# high — recall is sacrificed for precision.
FIRE_THRESHOLD = 0.75
CANDIDATE_THRESHOLD = 0.55

# How far back we look for a prior "done" task on the same files. A self-error
# revealed weeks later is still a self-error, but we cap the scan for cost.
LOOKBACK_DAYS = 45
MAX_PRIOR_TASKS = 200

# Evidence shorter than this is treated as non-substantive (mirrors the R03
# trivial-evidence floor in protocol.py) and cannot, on its own, drive a fire.
MIN_EVIDENCE_CHARS = 40


# ── Lexical evidence (objective markers, multilingual) ────────────────
# These are NOT vague "fix" words. Each marker asserts that a PRIOR step was
# wrong/missing and a LATER action revealed it — the exact self-error shape.
_PRIOR_OMISSION_PATTERNS = [
    re.compile(r"\b(?:forgot|forgotten|missed|omitted|never (?:created|added|set up|configured|ran|deployed))\b", re.IGNORECASE),
    re.compile(r"\b(?:was (?:never|not) (?:created|added|configured|deployed|wired|registered))\b", re.IGNORECASE),
    re.compile(r"\b(?:should have (?:also )?(?:created|added|configured|run|deployed))\b", re.IGNORECASE),
    re.compile(r"\b(?:missing (?:the )?(?:cron|step|trigger|hook|migration|index|webhook|deploy))\b", re.IGNORECASE),
    re.compile(r"\b(?:turned out|it turned out) (?:that )?(?:the|it|we|i)\b", re.IGNORECASE),
    # Spanish
    re.compile(r"\b(?:olvid[éeè]|me olvid[éeè]|falt[óoa]ba?|no (?:se )?(?:cre[óo]|configur[óo]|despleg[óo]|registr[óo]))\b", re.IGNORECASE),
    re.compile(r"\b(?:hab[íi]a que (?:tambi[ée]n )?(?:crear|a[ñn]adir|configurar|desplegar))\b", re.IGNORECASE),
    re.compile(r"\b(?:no se hab[íi]a (?:creado|configurado|desplegado|registrado))\b", re.IGNORECASE),
]

_REOPEN_PATTERNS = [
    re.compile(r"\b(?:reopen(?:ed|ing)?|re-?open(?:ed)?|had to redo|redo(?:ing)? (?:the )?(?:previous|prior|earlier))\b", re.IGNORECASE),
    re.compile(r"\b(?:previously (?:closed|marked|reported) (?:as )?(?:done|fixed|complete))\b", re.IGNORECASE),
    re.compile(r"\b(?:not actually (?:done|fixed|complete)|wasn'?t actually (?:done|fixed))\b", re.IGNORECASE),
    re.compile(r"\b(?:the (?:earlier|previous|prior) (?:fix|change|task) (?:did(?:n'?t| not)|was incomplete|broke))\b", re.IGNORECASE),
    # Spanish
    re.compile(r"\b(?:reabr[íi]|reabrir|hubo que rehacer|el (?:arreglo|cambio|fix) (?:anterior|previo) (?:no|estaba mal|fall[óo]))\b", re.IGNORECASE),
    re.compile(r"\b(?:no estaba (?:realmente )?(?:hecho|arreglado|cerrado))\b", re.IGNORECASE),
]

# Pure-iteration markers — when these dominate WITHOUT an omission/reopen
# marker, the close is normal forward work, not a self-error.
_ITERATION_PATTERNS = [
    re.compile(r"\b(?:refactor(?:ed|ing)?|cleanup|clean[- ]?up|rename(?:d)?|improve(?:d|ment)?|polish(?:ed)?|tweak(?:ed)?|optimi[sz]e(?:d)?)\b", re.IGNORECASE),
    re.compile(r"\b(?:next (?:step|phase|iteration)|follow[- ]?up work|continue(?:d|ing)? (?:on|with))\b", re.IGNORECASE),
    re.compile(r"\b(?:refactoriz|mejora(?:r|do)?|limpieza|renombr|optimiz|siguiente (?:paso|fase))\b", re.IGNORECASE),
]


def _has_any(patterns: list[re.Pattern], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def _normalize_path(value: str) -> str:
    return str(value or "").strip().rstrip("/").lower()


def _parse_files(value: Any) -> set[str]:
    """Accept a JSON list string, a list, or a comma string → set of paths."""
    if value is None:
        return set()
    items: list[str]
    if isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value]
    else:
        text = str(value).strip()
        if not text:
            return set()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                items = [str(v) for v in parsed] if isinstance(parsed, list) else [text]
            except Exception:
                items = [text]
        else:
            items = re.split(r"[,\n]", text)
    out = {_normalize_path(item) for item in items if _normalize_path(item)}
    return out


def _task_files(task: dict) -> set[str]:
    """Files a task actually touched (files_changed) or planned (files)."""
    changed = _parse_files(task.get("files_changed"))
    if changed:
        return changed
    return _parse_files(task.get("files"))


def _self_error_uid(current_task_id: str, prior_task_id: str, signal: str) -> str:
    raw = f"{current_task_id}\0{prior_task_id}\0{signal}"
    return "se-" + hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


# ── Core evaluation ───────────────────────────────────────────────────


def evaluate_self_error(
    *,
    current_task: dict,
    prior_tasks: list[dict],
    closure_text: str,
    correction_happened: bool,
    forgotten_step_followup: bool = False,
) -> dict:
    """Pure decision function — no I/O. Returns an evaluation dict.

    Output schema::

        {
          "decision": "fire" | "candidate" | "none",
          "confidence": float,
          "signal": str,                 # winning signal name (or "")
          "prior_task_id": str,          # prior done task implicated (or "")
          "overlap_files": [str, ...],
          "reasons": [str, ...],
        }

    Determinism: given the same inputs the output is identical, so tests can
    assert exact decisions. The function NEVER raises on malformed input.
    """
    reasons: list[str] = []
    text = str(closure_text or "")
    current_id = str(current_task.get("task_id") or "")

    # Gate 0: the close must carry SOME self-error semantics. Pure iteration /
    # refactor / improvement with no omission/reopen marker can never fire,
    # even if it touches a previously-done file (that is normal follow-on work).
    has_omission = _has_any(_PRIOR_OMISSION_PATTERNS, text)
    has_reopen = _has_any(_REOPEN_PATTERNS, text)
    has_iteration = _has_any(_ITERATION_PATTERNS, text)

    if not (has_omission or has_reopen or correction_happened or forgotten_step_followup):
        return {
            "decision": "none",
            "confidence": 0.0,
            "signal": "",
            "prior_task_id": "",
            "overlap_files": [],
            "reasons": ["no self-error semantics (no omission/reopen/correction signal)"],
        }

    # Pure-iteration veto: iteration language present AND no concrete omission
    # or reopen marker AND the operator did not flag a correction → forward
    # work, not a revealed self-error.
    if has_iteration and not (has_omission or has_reopen or correction_happened):
        return {
            "decision": "none",
            "confidence": 0.0,
            "signal": "",
            "prior_task_id": "",
            "overlap_files": [],
            "reasons": ["iteration/refactor language without omission or reopen marker"],
        }

    current_files = _task_files(current_task)
    current_area = str(current_task.get("area") or "").strip().lower()

    # Find the strongest prior "done" task whose files overlap the current one.
    best: dict | None = None
    for prior in prior_tasks:
        if str(prior.get("status") or "").strip().lower() != "done":
            # Only a task that CLAIMED done can host a self-error; partial/
            # failed prior tasks never asserted completeness.
            continue
        prior_files = _task_files(prior)
        if not prior_files or not current_files:
            continue
        overlap = current_files & prior_files
        if not overlap:
            continue
        # Same-area requirement keeps cross-project file-name collisions out.
        prior_area = str(prior.get("area") or "").strip().lower()
        if current_area and prior_area and current_area != prior_area:
            continue
        candidate = {
            "prior": prior,
            "overlap": sorted(overlap),
        }
        if best is None or len(candidate["overlap"]) > len(best["overlap"]):
            best = candidate

    # ── Confidence scoring ────────────────────────────────────────────
    confidence = 0.0
    signal = ""
    prior_task_id = ""
    overlap_files: list[str] = []

    if best is not None:
        prior = best["prior"]
        overlap_files = best["overlap"]
        prior_task_id = str(prior.get("task_id") or "")

        # S2 reopen_of_done — strongest: explicit reopen language + file overlap
        # with a prior done task.
        if has_reopen:
            signal = "reopen_of_done"
            confidence = 0.85
            reasons.append(
                f"explicit reopen/redo language overlapping prior done task {prior_task_id} "
                f"on {overlap_files}"
            )
        # S1 file_overlap_correction — the canonical "code without the cron":
        # omission language + overlap with a prior done task.
        elif has_omission:
            signal = "file_overlap_correction"
            confidence = 0.80
            reasons.append(
                f"omission language ('forgot/missing/never created') overlapping prior done "
                f"task {prior_task_id} on {overlap_files}"
            )
        elif correction_happened:
            # Operator-flagged correction on files a prior task closed as done.
            # Solid but slightly lower than explicit lexical evidence.
            signal = "file_overlap_correction"
            confidence = 0.76
            reasons.append(
                f"correction_happened=true on files a prior done task {prior_task_id} already "
                f"closed: {overlap_files}"
            )

        # Reinforcement: a forgotten-step followup alongside file overlap.
        if forgotten_step_followup and signal:
            confidence = min(0.95, confidence + 0.05)
            reasons.append("reinforced by an explicit forgotten-step followup")
    else:
        # No prior done task on the same files. Omission/reopen language alone
        # is, at most, a low-confidence candidate — it is NOT objective enough
        # to assert a self-error and create a learning.
        if forgotten_step_followup:
            signal = "forgotten_step_followup"
            confidence = 0.58
            reasons.append(
                "forgotten-step followup created, but no prior done task on overlapping files "
                "to confirm the self-error → candidate only"
            )
        elif has_omission or has_reopen:
            signal = "unconfirmed_self_error"
            confidence = 0.56
            reasons.append(
                "omission/reopen language without a prior done task on overlapping files → "
                "candidate only (cannot confirm a prior own action was wrong)"
            )
        else:
            return {
                "decision": "none",
                "confidence": 0.0,
                "signal": "",
                "prior_task_id": "",
                "overlap_files": [],
                "reasons": ["correction flagged but no objective self-error evidence"],
            }

    # Evidence-substance gate: trivial closure text cannot push to FIRE.
    if confidence >= FIRE_THRESHOLD and len(text.strip()) < MIN_EVIDENCE_CHARS:
        confidence = min(confidence, CANDIDATE_THRESHOLD + 0.05)
        reasons.append(
            f"closure evidence under {MIN_EVIDENCE_CHARS} chars → capped below fire threshold"
        )

    if confidence >= FIRE_THRESHOLD:
        decision = "fire"
    elif confidence >= CANDIDATE_THRESHOLD:
        decision = "candidate"
    else:
        decision = "none"

    return {
        "decision": decision,
        "confidence": round(confidence, 3),
        "signal": signal,
        "prior_task_id": prior_task_id,
        "overlap_files": overlap_files,
        "reasons": reasons,
    }


# ── Learning text builder ─────────────────────────────────────────────


def build_self_error_learning(
    *,
    current_task: dict,
    evaluation: dict,
) -> dict:
    """Produce the title / content / prevention for a self-error learning.

    The prevention is the load-bearing field: a concrete check that, applied
    next time, stops the same omission. Returned dict feeds
    ``tools_learnings.handle_learning_add`` (via ``_capture_learning``).
    """
    signal = evaluation.get("signal") or "self_error"
    overlap = evaluation.get("overlap_files") or []
    prior_task_id = evaluation.get("prior_task_id") or ""
    area = str(current_task.get("area") or "nexo-ops").strip() or "nexo-ops"
    goal = str(current_task.get("goal") or "").strip()
    files_csv = ", ".join(overlap[:6])

    if signal == "reopen_of_done":
        title = f"Self-error: prior 'done' work on {files_csv or area} had to be reopened"
        content = (
            f"A later action revealed that task {prior_task_id}, previously closed as DONE, "
            f"was not actually complete. Current work: {goal or '(see task)'}. "
            f"Overlapping files: {files_csv or '(area-level)'}."
        )
        prevention = (
            f"Before closing work on {files_csv or area} as done, verify the full end-to-end "
            f"effect (not just the code change): run the actual trigger/deploy/cron and confirm "
            f"the observable outcome. Do not mark done on the strength of the edit alone."
        )
    elif signal == "file_overlap_correction":
        title = f"Self-error: a prior 'done' task on {files_csv or area} left a step undone"
        content = (
            f"A later action corrected files that task {prior_task_id} had already closed as DONE, "
            f"revealing a missing step (e.g. code shipped but the cron/trigger/deploy was never "
            f"created). Current work: {goal or '(see task)'}. Overlapping files: {files_csv or '(area-level)'}."
        )
        prevention = (
            f"When changing {files_csv or area}, enumerate ALL the side artifacts the change "
            f"requires (cron/scheduler, deploy, webhook, migration, index, registration) and verify "
            f"each one exists and runs before closing as done — code landing is necessary, not sufficient."
        )
    else:
        title = f"Self-error candidate on {files_csv or area}"
        content = (
            f"Possible prior-own-action omission detected for {goal or area}. "
            f"Overlapping files: {files_csv or '(area-level)'}."
        )
        prevention = (
            f"Re-verify that the prior step on {files_csv or area} fully landed (all required "
            f"side artifacts) before relying on it."
        )

    return {
        "category": area,
        "title": title[:120],
        "content": content,
        "prevention": prevention,
        "applies_to": ",".join(overlap),
        "reasoning": (
            "Auto-detected by the self-error detector: a later action revealed a previous own "
            f"action was incomplete/wrong. Signal={signal}, confidence={evaluation.get('confidence')}, "
            f"reasons={'; '.join(evaluation.get('reasons') or [])[:400]}"
        ),
        # Objective, code/ledger-derived evidence — NOT a Francisco correction.
        "source_authority": "code_test_evidence",
    }
