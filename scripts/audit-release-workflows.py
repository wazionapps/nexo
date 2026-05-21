#!/usr/bin/env python3
"""Audit GitHub Actions release workflows for silent error masking.

Scans .github/workflows/*.yml across one or more repository roots and flags
publish/deploy steps that hide real failures behind `|| true`, `|| echo ...`,
`continue-on-error: true`, or that publish without a registry verification
step. Skip-if-exists is permitted only via the explicit pattern
`npm view package@version || npm publish` (and equivalents that recheck the
remote registry after a failed publish).

Exits 1 if any FAIL findings are emitted (suitable for pre-release-verify).
Use --json for machine-readable output.

Origin: followup NF-DS-E17D1B61 — Brain 7.23.2/3/4 reported green Actions
without publishing to npm because `|| echo "Version already exists"` masked the
real failure.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

PUBLISH_KEYWORDS = (
    "publish",
    "deploy",
    "release",
    "upload",
    "push-to-registry",
    "clawhub",
    "npm publish",
    "pypi",
    "gh release",
    "softprops/action-gh-release",
)

INFORMATIVE_COMMAND_PREFIXES = (
    "echo",
    "printf",
    "cat",
    "ls",
    "find",
    "grep",
    "head",
    "tail",
    "wc",
    "stat",
    "tree",
    "df",
    "du",
    "uptime",
    "date",
    "true",
    "trap",
    "xattr",
    "chmod",
    "hdiutil",
    "mkdir",
    "test",
    "gsutil mb",
    "npm view",
    "pip show",
)

# Patterns that count as registry verification AFTER a publish. The check is
# satisfied when the step body contains any of these — they all hit the
# remote source of truth (npm, pypi, ClawHub, GitHub releases, raw HTTP).
REGISTRY_LOOKUP_REGEX = re.compile(
    r"(?:\b(?:npm view|pip index|pypi-show|gh release view)\b)"
    r"|(?:curl[^\n]+registry)"
    r"|(?:\bclawhub(?:@[\w.\-]+)?\s+(?:install|view|info|list)\b)",
    re.IGNORECASE,
)

# Step name prefixes that mean the step only produces a local artifact and
# never pushes it anywhere — those should not be treated as publish/deploy.
BUILD_ONLY_NAME_PREFIXES = (
    "build ",
    "compile ",
    "package ",
    "bundle ",
    "make ",
    "assemble ",
    "smoke ",
)

# Patterns that mask errors silently.
MASK_PATTERNS = [
    ("trailing_or_true", re.compile(r"\|\|\s*true(\s|$|;)")),
    ("trailing_or_echo", re.compile(r"\|\|\s*echo\b")),
    ("trailing_or_colon", re.compile(r"\|\|\s*:(\s|$|;)")),
    ("set_plus_e", re.compile(r"\bset\s+\+e\b")),
]

CONTINUE_ON_ERROR_REGEX = re.compile(r"^\s*continue-on-error\s*:\s*true\b", re.MULTILINE)


@dataclass
class Finding:
    repo: str
    workflow: str
    job: str
    step: str
    line: int
    severity: str  # FAIL | WARN | INFO
    code: str
    message: str
    snippet: str = ""


def is_publish_context(name: str, run_body: str) -> bool:
    name_lc = (name or "").strip().lower()
    if any(name_lc.startswith(prefix) for prefix in BUILD_ONLY_NAME_PREFIXES):
        return False
    haystack = f"{name}\n{run_body}".lower()
    return any(kw in haystack for kw in PUBLISH_KEYWORDS)


def is_informative_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return True
    # Lines that only run a read-only/idempotent command before `|| true`.
    head = stripped.split("||", 1)[0].strip()
    head = head.lstrip("-").strip()
    return any(head.startswith(prefix) for prefix in INFORMATIVE_COMMAND_PREFIXES)


def parse_workflow(path: Path):
    """Yield (job_name, step_name, line_offset, run_body) tuples.

    We use a minimal YAML walker that is tolerant of multiline `run: |` blocks.
    Returns no jobs if the file is not a workflow with jobs/steps.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], f"read error: {exc}"

    # Use ruamel/PyYAML if available for richer parsing, otherwise a regex pass.
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
    except Exception:
        return _parse_workflow_regex(text), None

    jobs = (data or {}).get("jobs") or {}
    if not isinstance(jobs, dict):
        return _parse_workflow_regex(text), None

    results = []
    lines = text.splitlines()
    for job_name, job in jobs.items():
        steps = (job or {}).get("steps") or []
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_name = str(step.get("name") or step.get("uses") or step.get("id") or "anonymous")
            run_body = step.get("run") or ""
            if not isinstance(run_body, str):
                run_body = json.dumps(run_body)
            line = _find_step_line(lines, step_name)
            results.append((job_name, step_name, line, run_body, step))
    return results, None


def _find_step_line(lines: list[str], name: str) -> int:
    target = name.strip()
    for idx, line in enumerate(lines, start=1):
        if target and target in line:
            return idx
    return 0


def _parse_workflow_regex(text: str):
    """Fallback parser when PyYAML is unavailable."""
    results = []
    job_re = re.compile(r"^([\w-]+):\s*$")
    in_jobs = False
    current_job = None
    step_name = None
    step_line = 0
    run_buf: list[str] = []
    in_run = False
    for idx, line in enumerate(text.splitlines(), start=1):
        if line.startswith("jobs:"):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        m = re.match(r"^  ([\w-]+):\s*$", line)
        if m:
            current_job = m.group(1)
            continue
        if "name:" in line and re.match(r"^\s*-\s*name:\s*", line):
            if step_name is not None:
                results.append((current_job or "?", step_name, step_line, "\n".join(run_buf), {}))
            step_name = line.split("name:", 1)[1].strip().strip("'\"")
            step_line = idx
            run_buf = []
            in_run = False
            continue
        if re.match(r"^\s+run:\s*\|", line):
            in_run = True
            continue
        if in_run:
            if re.match(r"^\s{0,8}-\s", line) or re.match(r"^\s{0,8}\w[\w-]*:\s", line):
                in_run = False
            else:
                run_buf.append(line)
    if step_name is not None:
        results.append((current_job or "?", step_name, step_line, "\n".join(run_buf), {}))
    return results


def audit_step(repo: str, workflow: str, job: str, name: str, base_line: int, run_body: str, step_dict: dict) -> list[Finding]:
    findings: list[Finding] = []

    # 1) continue-on-error: true on publish steps is a FAIL.
    if step_dict.get("continue-on-error") is True and is_publish_context(name, run_body):
        findings.append(Finding(
            repo=repo, workflow=workflow, job=job, step=name, line=base_line,
            severity="FAIL", code="continue_on_error_publish",
            message="Publish/deploy step uses continue-on-error: true. Real failures will be hidden.",
        ))

    if not run_body.strip():
        return findings

    publish_context = is_publish_context(name, run_body)

    for offset, line in enumerate(run_body.splitlines(), start=1):
        for code, regex in MASK_PATTERNS:
            if not regex.search(line):
                continue
            if is_informative_line(line):
                continue
            severity = "FAIL" if publish_context else "WARN"
            findings.append(Finding(
                repo=repo, workflow=workflow, job=job, step=name,
                line=base_line + offset,
                severity=severity, code=code,
                message=(
                    "Publish/deploy step masks errors silently. "
                    "Use `<lookup> || <publish> && <verify>` pattern instead."
                ) if publish_context else "Possible silent error masking outside publish context.",
                snippet=line.strip()[:200],
            ))

    # 2) publish steps without registry verification.
    if publish_context and "publish" in run_body.lower():
        if not REGISTRY_LOOKUP_REGEX.search(run_body):
            findings.append(Finding(
                repo=repo, workflow=workflow, job=job, step=name, line=base_line,
                severity="FAIL", code="publish_without_verification",
                message=(
                    "Publish step does not query the remote registry (npm view / pip index / curl) "
                    "to confirm the version actually landed."
                ),
            ))

    return findings


def audit_file(repo: str, path: Path) -> list[Finding]:
    findings: list[Finding] = []
    steps, err = parse_workflow(path)
    if err:
        return [Finding(repo=repo, workflow=str(path), job="-", step="-", line=0,
                        severity="WARN", code="parse_error", message=err)]
    text = path.read_text(encoding="utf-8", errors="replace")
    workflow_rel = str(path)
    # File-level continue-on-error sweep (catches steps the per-step walker missed).
    for m in CONTINUE_ON_ERROR_REGEX.finditer(text):
        line_no = text.count("\n", 0, m.start()) + 1
        findings.append(Finding(
            repo=repo, workflow=workflow_rel, job="?", step="?", line=line_no,
            severity="WARN", code="continue_on_error_seen",
            message="continue-on-error: true present — verify the step is purely informative.",
        ))
    # Group run bodies per job so a publish step can satisfy the
    # "registry verification" requirement using a sibling verify step.
    per_job_body: dict[str, str] = {}
    for job, _step, _line, run_body, _sd in steps:
        per_job_body.setdefault(job, "")
        per_job_body[job] += "\n" + run_body
    for job, step, line, run_body, step_dict in steps:
        step_findings = audit_step(repo, workflow_rel, job, step, line, run_body, step_dict)
        job_body = per_job_body.get(job, "")
        job_has_verify = bool(REGISTRY_LOOKUP_REGEX.search(job_body))
        for f in step_findings:
            if f.code == "publish_without_verification" and job_has_verify:
                continue
            findings.append(f)
    return findings


def iter_workflows(roots: Iterable[Path]):
    for root in roots:
        wf_dir = root / ".github" / "workflows"
        if not wf_dir.is_dir():
            continue
        for path in sorted(wf_dir.glob("*.yml")):
            yield root.name, path
        for path in sorted(wf_dir.glob("*.yaml")):
            yield root.name, path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "roots",
        nargs="*",
        type=Path,
        help="Repository roots to scan (defaults to NEXO Brain, Desktop and Wazion).",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--fail-on-warn", action="store_true", help="Treat WARN findings as failures too.")
    args = parser.parse_args()

    roots = args.roots or [
        Path(os.path.expanduser("~/Documents/_PhpstormProjects/nexo")),
        Path(os.path.expanduser("~/Documents/_PhpstormProjects/nexo-desktop")),
        Path(os.path.expanduser("~/Documents/_PhpstormProjects/WAzion")),
    ]

    all_findings: list[Finding] = []
    scanned = 0
    for repo, path in iter_workflows(roots):
        scanned += 1
        all_findings.extend(audit_file(repo, path))

    fail_count = sum(1 for f in all_findings if f.severity == "FAIL")
    warn_count = sum(1 for f in all_findings if f.severity == "WARN")

    if args.json:
        print(json.dumps({
            "scanned_workflows": scanned,
            "fail": fail_count,
            "warn": warn_count,
            "findings": [asdict(f) for f in all_findings],
        }, indent=2, ensure_ascii=False))
    else:
        if not all_findings:
            print(f"OK: scanned {scanned} workflow(s) across {len(roots)} repo root(s). No findings.")
        for f in all_findings:
            print(f"[{f.severity}] {f.repo} :: {f.workflow}:{f.line} ({f.job}/{f.step}) [{f.code}] {f.message}")
            if f.snippet:
                print(f"        > {f.snippet}")
        print(f"Summary: scanned={scanned} fail={fail_count} warn={warn_count}")

    if fail_count or (args.fail_on_warn and warn_count):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
