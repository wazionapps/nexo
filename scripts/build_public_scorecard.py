#!/usr/bin/env python3
"""Build measured public scorecard artifacts for compare pages."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPARE_DIR = ROOT / "compare"
LOCOMO_SUMMARY = ROOT / "benchmarks" / "locomo" / "results" / "locomo_nexo_summary.json"
ABLATION_SUMMARY = ROOT / "benchmarks" / "runtime_ablations" / "results" / "ablation_summary.json"
RUNTIME_PACK_SUMMARY = ROOT / "benchmarks" / "runtime_pack" / "results" / "latest_summary.json"
DEFAULT_NEXO_HOME_CANDIDATES = (
    Path.home() / ".nexo",
    Path.home() / "claude",
)
HOUSEKEEPING_TOOLS = {
    "mcp__nexo__nexo_heartbeat",
    "mcp__nexo__nexo_reminders",
    "mcp__nexo__nexo_guard_check",
    "mcp__nexo__nexo_rules_check",
    "mcp__nexo__nexo_cortex_check",
    "mcp__nexo__nexo_track",
    "mcp__nexo__nexo_untrack",
    "mcp__nexo__nexo_session_diary_write",
    "mcp__nexo__nexo_session_diary_read",
}
VOLATILE_TOOL_INPUT_KEYS = {
    "yield_time_ms",
    "max_output_tokens",
    "timeout_ms",
    "description",
    "justification",
    "user_intent",
    "prefix_rule",
}


def _resolve_nexo_home() -> Path:
    env_home = os.environ.get("NEXO_HOME", "").strip()
    candidates = [Path(env_home).expanduser()] if env_home else []
    candidates.extend(DEFAULT_NEXO_HOME_CANDIDATES)
    best = candidates[0] if candidates else Path.home() / ".nexo"
    best_score = -1
    for candidate in candidates:
        score = 0
        if (candidate / "data" / "nexo.db").is_file():
            score += 2
        if (candidate / "operations" / "tool-logs").is_dir():
            score += 1
        if score > best_score:
            best = candidate
            best_score = score
    return best


NEXO_HOME = _resolve_nexo_home()
NEXO_DB = NEXO_HOME / "data" / "nexo.db"
TOOL_LOG_DIR = NEXO_HOME / "operations" / "tool-logs"


def load_locomo_summary(path: Path = LOCOMO_SUMMARY) -> dict:
    if not path.is_file():
        return {"available": False, "reason": f"missing {path}"}
    payload = json.loads(path.read_text())
    rag = ((payload.get("results") or {}).get("rag") or {})
    overall = rag.get("overall") or {}
    return {
        "available": True,
        "benchmark": payload.get("benchmark", "LoCoMo"),
        "samples": payload.get("samples"),
        "total_qa": payload.get("total_qa"),
        "overall_f1": overall.get("f1"),
        "overall_recall": overall.get("recall"),
        "open_domain_f1": ((rag.get("cat_4_open_domain") or {}).get("f1")),
        "multi_hop_f1": ((rag.get("cat_1_multi_hop") or {}).get("f1")),
        "temporal_f1": ((rag.get("cat_2_temporal") or {}).get("f1")),
        "adversarial_f1": ((rag.get("cat_5_adversarial") or {}).get("f1")),
    }


def load_ablation_summary(path: Path = ABLATION_SUMMARY) -> dict:
    if not path.is_file():
        return {"available": False, "reason": f"missing {path}"}
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        return {"available": False, "reason": "ablation summary is not a JSON object"}
    payload.setdefault("available", True)
    return payload


def load_runtime_pack_summary(path: Path = RUNTIME_PACK_SUMMARY) -> dict:
    if not path.is_file():
        return {"available": False, "reason": f"missing {path}"}
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        return {"available": False, "reason": "runtime pack summary is not a JSON object"}
    payload.setdefault("available", True)
    return payload


def _recovery_rate(conn: sqlite3.Connection, days: int) -> float | None:
    rows = conn.execute(
        """SELECT task_id, goal, files, closed_at
           FROM protocol_tasks
           WHERE status IN ('failed', 'blocked')
             AND closed_at >= datetime('now', ?)
           ORDER BY closed_at ASC""",
        (f"-{days} days",),
    ).fetchall()
    if not rows:
        return None
    recovered = 0
    for row in rows:
        files = str(row["files"] or "")
        goal = str(row["goal"] or "")
        follow = conn.execute(
            """SELECT 1
               FROM protocol_tasks
               WHERE status = 'done'
                 AND closed_at > ?
                 AND closed_at >= datetime('now', ?)
                 AND (
                   (COALESCE(files, '') != '' AND files = ?)
                   OR (COALESCE(goal, '') != '' AND goal = ?)
                 )
               LIMIT 1""",
            (row["closed_at"], f"-{days} days", files, goal),
        ).fetchone()
        if follow:
            recovered += 1
    return round((recovered / len(rows)) * 100, 1)


def _parse_tool_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_tool_input(value):
    if isinstance(value, dict):
        return {
            key: _normalize_tool_input(raw)
            for key, raw in sorted(value.items())
            if key not in VOLATILE_TOOL_INPUT_KEYS
        }
    if isinstance(value, list):
        return [_normalize_tool_input(item) for item in value]
    if isinstance(value, str):
        return value[:500]
    return value


def _tool_signature(entry: dict) -> str:
    payload = {
        "tool_name": entry.get("tool_name", ""),
        "tool_input": _normalize_tool_input(entry.get("tool_input")),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _iter_tool_entries(tool_log_dir: Path, days: int):
    if not tool_log_dir.is_dir():
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    min_date = cutoff.date()
    files = []
    for path in sorted(tool_log_dir.glob("*.jsonl")):
        try:
            file_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            file_date = None
        if file_date and file_date < min_date:
            continue
        files.append(path)
    for path in files:
        with path.open() as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                timestamp = _parse_tool_timestamp(entry.get("timestamp"))
                if not timestamp or timestamp < cutoff:
                    continue
                yield entry, timestamp


def _unnecessary_tool_call_summary(tool_log_dir: Path, days: int) -> dict:
    if not tool_log_dir.is_dir():
        return {
            "rate_pct": None,
            "candidate_calls": 0,
            "duplicate_calls": 0,
            "reason": f"missing {tool_log_dir}",
        }

    recent_by_session: dict[tuple[str, str], datetime] = {}
    candidate_calls = 0
    duplicate_calls = 0
    for entry, timestamp in _iter_tool_entries(tool_log_dir, days):
        tool_name = str(entry.get("tool_name") or "")
        if tool_name in HOUSEKEEPING_TOOLS:
            continue
        if tool_name.startswith("mcp__nexo__nexo_") and tool_name not in {"mcp__nexo__nexo_skill_apply"}:
            # Do not punish mandatory shared-brain maintenance calls.
            continue
        candidate_calls += 1
        if entry.get("error"):
            continue
        session_id = str(entry.get("session_id") or "unknown")
        signature = _tool_signature(entry)
        key = (session_id, signature)
        previous = recent_by_session.get(key)
        if previous and (timestamp - previous).total_seconds() <= 300:
            duplicate_calls += 1
        recent_by_session[key] = timestamp

    return {
        "rate_pct": round((duplicate_calls / candidate_calls) * 100, 1) if candidate_calls else None,
        "candidate_calls": candidate_calls,
        "duplicate_calls": duplicate_calls,
        "reason": "",
    }


def collect_longitudinal_metrics(db_path: Path = NEXO_DB, tool_log_dir: Path = TOOL_LOG_DIR) -> list[dict]:
    if not db_path.is_file():
        return [{"days": days, "available": False, "reason": f"missing {db_path}"} for days in (30, 60, 90)]
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    windows = []
    for days in (30, 60, 90):
        tool_summary = _unnecessary_tool_call_summary(tool_log_dir, days)
        closed = conn.execute(
            """SELECT COUNT(*) AS count
               FROM protocol_tasks
               WHERE closed_at >= datetime('now', ?)""",
            (f"-{days} days",),
        ).fetchone()["count"]
        done = conn.execute(
            """SELECT COUNT(*) AS count
               FROM protocol_tasks
               WHERE status = 'done'
                 AND closed_at >= datetime('now', ?)""",
            (f"-{days} days",),
        ).fetchone()["count"]
        avg_minutes = conn.execute(
            """SELECT AVG((julianday(closed_at) - julianday(opened_at)) * 24 * 60) AS avg_minutes
               FROM protocol_tasks
               WHERE closed_at IS NOT NULL
                 AND opened_at IS NOT NULL
                 AND closed_at >= datetime('now', ?)""",
            (f"-{days} days",),
        ).fetchone()["avg_minutes"]
        open_debts = conn.execute(
            """SELECT COUNT(*) AS count
               FROM protocol_debt
               WHERE status = 'open'
                 AND created_at >= datetime('now', ?)""",
            (f"-{days} days",),
        ).fetchone()["count"]
        automation_runs = 0
        costed_runs = 0
        total_cost = 0.0
        automation_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='automation_runs'"
        ).fetchone()
        if automation_table:
            automation = conn.execute(
                """
                SELECT
                    COUNT(*) AS runs,
                    SUM(CASE WHEN total_cost_usd IS NOT NULL THEN 1 ELSE 0 END) AS costed_runs,
                    SUM(COALESCE(total_cost_usd, 0)) AS total_cost
                FROM automation_runs
                WHERE created_at >= datetime('now', ?)
                """,
                (f"-{days} days",),
            ).fetchone()
            automation_runs = int((automation["runs"] if automation else 0) or 0)
            costed_runs = int((automation["costed_runs"] if automation else 0) or 0)
            total_cost = float((automation["total_cost"] if automation else 0.0) or 0.0)
        coverage_pct = round((costed_runs / automation_runs) * 100, 1) if automation_runs else None
        cost_per_solved_task = None
        cost_note = ""
        if automation_runs and done and coverage_pct is not None and coverage_pct >= 90.0:
            cost_per_solved_task = round(total_cost / done, 6)
        elif automation_runs and coverage_pct is not None:
            cost_note = (
                f"cost coverage only {coverage_pct}% across automation runs in the window; "
                "metric withheld until telemetry is representative"
            )
        windows.append(
            {
                "days": days,
                "available": True,
                "closed_tasks": int(closed or 0),
                "task_success_rate_pct": round(((done or 0) / closed) * 100, 1) if closed else None,
                "avg_time_to_close_minutes": round(avg_minutes, 1) if avg_minutes is not None else None,
                "recovery_after_failure_pct": _recovery_rate(conn, days),
                "open_protocol_debt": int(open_debts or 0),
                "unnecessary_tool_call_rate_pct": tool_summary["rate_pct"],
                "unnecessary_tool_call_detail": {
                    "candidate_calls": tool_summary["candidate_calls"],
                    "probable_duplicate_calls": tool_summary["duplicate_calls"],
                },
                "automation_runs": automation_runs,
                "cost_telemetry_coverage_pct": coverage_pct,
                "cost_per_solved_task": cost_per_solved_task,
                "notes": [
                    (
                        "unnecessary_tool_call_rate_pct is a conservative heuristic: duplicate successful "
                        "non-housekeeping tool calls with identical input inside 5-minute bursts"
                    ),
                    *([tool_summary["reason"]] if tool_summary["reason"] else []),
                    *([cost_note] if cost_note else []),
                ],
            }
        )
    conn.close()
    return windows


def build_scorecard() -> dict:
    artifacts = {
        "locomo_summary": "benchmarks/locomo/results/locomo_nexo_summary.json",
        "ablation_summary": "benchmarks/runtime_ablations/results/ablation_summary.json",
        "runtime_pack_summary": "benchmarks/runtime_pack/results/latest_summary.json",
        "compare_readme": "compare/README.md",
        "compare_scorecard": "compare/scorecard.json",
        "benchmark_page": "features/benchmark/index.html",
        "parity_audit": "scripts/verify_client_parity.py",
        "parity_checklist": "docs/client-parity-checklist.md",
        "quickstart": "docs/quickstart-5-minutes.md",
        "python_sdk": "src/nexo_sdk.py",
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "product_story": "NEXO is the local cognitive runtime that makes the model around your model smarter.",
        "benchmarks": {
            "locomo_rag": load_locomo_summary(),
            "ablation_suite": load_ablation_summary(),
            "runtime_pack": load_runtime_pack_summary(),
        },
        "artifacts": artifacts,
        "claim_map": [
            {
                "id": "locomo_memory",
                "claim": "NEXO publishes a measured long-conversation memory result on LoCoMo instead of relying only on architecture claims.",
                "evidence": [artifacts["locomo_summary"], artifacts["benchmark_page"]],
                "scope_note": "Benchmark result is memory-specific; it is not a universal score for every runtime capability.",
            },
            {
                "id": "ablations_and_baselines",
                "claim": "NEXO shows external baselines and internal ablations side by side so the score is easier to interpret.",
                "evidence": [artifacts["ablation_summary"], artifacts["compare_scorecard"]],
                "scope_note": "External baselines come from the public LoCoMo discussion; internal ablations come from checked-in NEXO artifacts.",
            },
            {
                "id": "client_parity_guardrails",
                "claim": "Client parity across Claude Code and Codex is audited by code and docs, not left as a vague promise.",
                "evidence": [artifacts["parity_audit"], artifacts["parity_checklist"]],
                "scope_note": "Parity claims refer to the audited runtime surfaces listed in the checklist and script output.",
            },
            {
                "id": "operator_runtime_pack",
                "claim": "NEXO publishes a reproducible operator benchmark pack against realistic local baselines, not just a memory-only paper benchmark.",
                "evidence": [artifacts["runtime_pack_summary"], "benchmarks/runtime_pack/README.md"],
                "scope_note": "The runtime pack is a small manual-rubric benchmark for operator workflows; it complements LoCoMo instead of replacing it.",
            },
            {
                "id": "local_runtime_telemetry",
                "claim": "NEXO publishes longitudinal local runtime telemetry separately from benchmark scores.",
                "evidence": [artifacts["compare_scorecard"], artifacts["compare_readme"]],
                "scope_note": "Runtime windows are local operational telemetry; they are not folded into LoCoMo F1.",
            },
        ],
        "client_parity": {
            "audit_script": "scripts/verify_client_parity.py",
            "checklist": "docs/client-parity-checklist.md",
            "runtime_guardrails": [
                "managed bootstrap for Claude Code + Codex",
                "transcript-aware Deep Sleep parity",
                "runtime doctor parity audits",
            ],
        },
        "longitudinal": collect_longitudinal_metrics(),
        "public_api": {
            "mcp_wrappers": ["nexo_remember", "nexo_memory_recall", "nexo_consolidate", "nexo_run_workflow"],
            "python_sdk": "src/nexo_sdk.py",
            "quickstart": "docs/quickstart-5-minutes.md",
        },
    }


def _fmt_metric(value, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value}{suffix}"


def render_markdown(scorecard: dict) -> str:
    locomo = ((scorecard.get("benchmarks") or {}).get("locomo_rag") or {})
    artifacts = scorecard.get("artifacts") or {}
    claim_map = scorecard.get("claim_map") or []
    lines = [
        "# NEXO Compare Scorecard",
        "",
        scorecard["product_story"],
        "",
        f"Generated: {scorecard.get('generated_at', 'unknown')}",
        "",
        "## What this scorecard is",
        "",
        "- A public proof surface for the claims NEXO makes most often.",
        "- A mix of benchmark data, internal ablations, runtime telemetry, and parity guardrails.",
        "- A map of inspectable artifacts, not a substitute for reading the underlying files.",
        "",
        "## Claims you can inspect today",
    ]
    for item in claim_map:
        evidence = ", ".join(f"`{ref}`" for ref in item.get("evidence") or [])
        lines.extend(
            [
                f"- {item.get('claim', 'Unnamed claim')}",
                f"  - Evidence: {evidence or 'n/a'}",
                f"  - Scope: {item.get('scope_note', 'n/a')}",
            ]
        )

    lines.extend(
        [
            "",
            "## What this scorecard does not claim",
            "",
            "- It is not a universal winner-takes-all benchmark for every agent workload.",
            "- LoCoMo measures long-conversation memory, not the full product surface.",
            "- Longitudinal runtime windows come from local operator telemetry and should be read as operational evidence, not as a public SaaS benchmark.",
            "",
        "## Measured benchmark",
        ]
    )
    if locomo.get("available"):
        lines.extend(
            [
                f"- LoCoMo overall F1: {locomo.get('overall_f1')}",
                f"- LoCoMo overall recall: {locomo.get('overall_recall')}",
                f"- Open-domain F1: {locomo.get('open_domain_f1')}",
                f"- Multi-hop F1: {locomo.get('multi_hop_f1')}",
                f"- Temporal F1: {locomo.get('temporal_f1')}",
            ]
        )
    else:
        lines.append(f"- Benchmark unavailable: {locomo.get('reason', 'unknown')}")

    ablations = (scorecard.get("benchmarks") or {}).get("ablation_suite") or {}
    lines.extend(["", "## Ablation / baseline suite"])
    if ablations.get("available"):
        lines.append(
            f"- {ablations.get('benchmark', 'Runtime ablation suite')} ({ablations.get('date', 'undated')})"
        )
        for entry in ablations.get("modes") or []:
            label = entry.get("label") or entry.get("id") or "unknown"
            summary_bits = []
            if entry.get("overall_f1") is not None:
                summary_bits.append(f"F1 {entry['overall_f1']}")
            if entry.get("task_success_rate_pct") is not None:
                summary_bits.append(f"success {entry['task_success_rate_pct']}%")
            if entry.get("conditioned_file_protection_pct") is not None:
                summary_bits.append(f"guard {entry['conditioned_file_protection_pct']}%")
            if entry.get("resume_recovery_pct") is not None:
                summary_bits.append(f"resume {entry['resume_recovery_pct']}%")
            lines.append(f"- {label}: " + " | ".join(summary_bits or ["no summary"]))
    else:
        lines.append(f"- Ablation suite unavailable: {ablations.get('reason', 'unknown')}")

    runtime_pack = (scorecard.get("benchmarks") or {}).get("runtime_pack") or {}
    lines.extend(["", "## Operator runtime pack"])
    if runtime_pack.get("available"):
        latest = runtime_pack.get("latest_run") or {}
        lines.append(
            f"- {latest.get('title', latest.get('run_id', 'Runtime pack latest run'))} ({latest.get('date', 'undated')})"
        )
        lines.append(
            f"- Scope: {runtime_pack.get('scope_note', 'runtime-focused operator benchmark')}"
        )
        for baseline in latest.get("baselines") or []:
            lines.append(
                f"- {baseline.get('label', baseline.get('id', 'unknown'))}: score {baseline.get('score_pct')}% | "
                f"pass {baseline.get('pass_count')} | partial {baseline.get('partial_count')} | fail {baseline.get('fail_count')}"
            )
    else:
        lines.append(f"- Runtime pack unavailable: {runtime_pack.get('reason', 'unknown')}")

    lines.extend(["", "## Longitudinal local runtime metrics"])
    for window in scorecard.get("longitudinal") or []:
        if not window.get("available"):
            lines.append(f"- {window['days']}d: unavailable ({window.get('reason', 'unknown')})")
            continue
        lines.append(
            f"- {window['days']}d: success {_fmt_metric(window.get('task_success_rate_pct'), '%')} | "
            f"avg close {_fmt_metric(window.get('avg_time_to_close_minutes'), ' min')} | "
            f"recovery {_fmt_metric(window.get('recovery_after_failure_pct'), '%')} | "
            f"open protocol debt {window.get('open_protocol_debt')} | "
            f"unnecessary tool {_fmt_metric(window.get('unnecessary_tool_call_rate_pct'), '%')} | "
            f"cost/solved {_fmt_metric(window.get('cost_per_solved_task'), ' USD')}"
        )

    lines.extend(
        [
            "",
            "## System On Top Of Model",
            "![NEXO system on top of model](../assets/nexo-brain-architecture.png)",
            "",
            "## Public API surface",
            "- MCP wrappers: `nexo_remember`, `nexo_memory_recall`, `nexo_consolidate`, `nexo_run_workflow`",
            "- Python SDK: `src/nexo_sdk.py`",
            "- Quickstart: `docs/quickstart-5-minutes.md`",
            "",
            "## Client parity guardrails",
            "- `scripts/verify_client_parity.py`",
            "- `docs/client-parity-checklist.md`",
            "- runtime doctor parity audits",
            "",
            "## Artifact map",
        ]
    )
    for key, value in artifacts.items():
        lines.append(f"- `{key}`: `{value}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    COMPARE_DIR.mkdir(parents=True, exist_ok=True)
    scorecard = build_scorecard()
    (COMPARE_DIR / "scorecard.json").write_text(json.dumps(scorecard, indent=2) + "\n")
    (COMPARE_DIR / "README.md").write_text(render_markdown(scorecard))
    print(f"[scorecard] wrote {COMPARE_DIR / 'scorecard.json'}")
    print(f"[scorecard] wrote {COMPARE_DIR / 'README.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
