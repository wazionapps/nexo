#!/usr/bin/env python3
"""Repository-scoped release readiness checks for NEXO.

This is the public counterpart of the local release validator used in day-to-day
operations. It keeps release discipline inside the repo instead of relying only
on operator memory or external personal scripts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_JSON = ROOT / "package.json"
CHANGELOG = ROOT / "CHANGELOG.md"
DEFAULT_WEBSITE_ROOT = ROOT.parent / "nexo-gh-pages"
DEFAULT_CONTRACTS_ROOT = ROOT / "release-contracts"
DEFAULT_NEXO_HOME_CANDIDATES = (
    Path.home() / ".nexo",
    Path.home() / "claude",
)


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    print(f"[release-readiness] $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=ROOT, env=env)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _package_manifest() -> dict:
    payload = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("[release-readiness] package.json must contain a JSON object")
    return payload


def _package_version(payload: dict | None = None) -> str:
    payload = payload or _package_manifest()
    version = str(payload.get("version", "") or "").strip()
    if not version:
        raise SystemExit("[release-readiness] package.json missing version")
    return version


def _package_name(payload: dict | None = None) -> str:
    payload = payload or _package_manifest()
    name = str(payload.get("name", "") or "").strip()
    if not name:
        raise SystemExit("[release-readiness] package.json missing package name")
    return name


def _resolve_repository_slug(payload: dict | None = None) -> str:
    payload = payload or _package_manifest()
    repository = payload.get("repository")
    if isinstance(repository, dict):
        raw = str(repository.get("url", "") or "").strip()
    else:
        raw = str(repository or "").strip()
    match = re.search(r"github\.com[:/](?P<slug>[^/]+/[^/.]+?)(?:\.git)?$", raw)
    if not match:
        raise SystemExit(f"[release-readiness] cannot resolve GitHub repository slug from {raw!r}")
    return match.group("slug")


def _fetch_json(url: str, *, label: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "nexo-release-readiness",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"[release-readiness] {label} HTTP {exc.code}: {url}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"[release-readiness] {label} request failed: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"[release-readiness] {label} must return a JSON object")
    return payload


def _resolve_nexo_home(explicit_home: str = "") -> Path:
    candidates = []
    if explicit_home.strip():
        candidates.append(Path(explicit_home).expanduser())
    env_home = os.environ.get("NEXO_HOME", "").strip()
    if env_home:
        candidates.append(Path(env_home).expanduser())
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


def _check_changelog(version: str) -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    match = re.search(r"^## \[([^\]]+)\]", text, flags=re.MULTILINE)
    if not match:
        raise SystemExit("[release-readiness] CHANGELOG.md missing release headings")
    top_version = match.group(1).strip()
    if top_version != version:
        raise SystemExit(
            f"[release-readiness] top changelog version {top_version} != package.json {version}"
        )
    print(f"[release-readiness] changelog OK ({version})")


def _check_website(version: str, website_root: Path) -> None:
    if not website_root.is_dir():
        print(f"[release-readiness] website skipped (missing {website_root})")
        return
    changelog_page = website_root / "changelog" / "index.html"
    home_page = website_root / "index.html"
    missing = []
    if changelog_page.is_file() and f"v{version}" not in changelog_page.read_text(encoding="utf-8"):
        missing.append(f"{changelog_page} missing v{version}")
    if home_page.is_file() and version not in home_page.read_text(encoding="utf-8"):
        missing.append(f"{home_page} missing {version}")
    if missing:
        raise SystemExit("[release-readiness] website drift:\n- " + "\n- ".join(missing))
    print(f"[release-readiness] website OK ({website_root})")


def _check_github_release(version: str, repository_slug: str) -> None:
    payload = _fetch_json(
        f"https://api.github.com/repos/{repository_slug}/releases/tags/v{version}",
        label="github release",
    )
    tag_name = str(payload.get("tag_name", "") or "").strip()
    if tag_name != f"v{version}":
        raise SystemExit(
            f"[release-readiness] GitHub release tag {tag_name!r} != expected v{version}"
        )
    html_url = str(payload.get("html_url", "") or "").strip()
    if not html_url:
        raise SystemExit("[release-readiness] GitHub release missing html_url")
    print(f"[release-readiness] github release OK ({html_url})")


def _check_npm_package(version: str, package_name: str) -> None:
    encoded_name = urllib.parse.quote(package_name, safe="@/")
    payload = _fetch_json(
        f"https://registry.npmjs.org/{encoded_name}/latest",
        label="npm registry",
    )
    live_version = str(payload.get("version", "") or "").strip()
    if live_version != version:
        raise SystemExit(
            f"[release-readiness] npm latest version {live_version!r} != package.json {version}"
        )
    print(f"[release-readiness] npm registry OK ({package_name}@{live_version})")


def _load_contract(contract_path: Path) -> dict:
    if not contract_path.is_file():
        raise SystemExit(f"[release-readiness] contract missing: {contract_path}")
    try:
        payload = json.loads(contract_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[release-readiness] invalid contract JSON {contract_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"[release-readiness] contract must be a JSON object: {contract_path}")
    return payload


def _normalize_contract_paths(values: list[str], *, root: Path) -> list[Path]:
    paths = []
    for raw in values:
        if not isinstance(raw, str) or not raw.strip():
            raise SystemExit("[release-readiness] contract paths must be non-empty strings")
        path = (root / raw).resolve()
        if not str(path).startswith(str(root.resolve())):
            raise SystemExit(f"[release-readiness] contract path escapes repo root: {raw}")
        paths.append(path)
    return paths


def _check_contract(
    contract: dict,
    *,
    contract_path: Path,
    website_root: Path,
    require_complete: bool,
    repo_root: Path = ROOT,
) -> None:
    release_line = str(contract.get("release_line", "") or "").strip()
    target_version = str(contract.get("target_version", "") or "").strip()
    distribution = contract.get("distribution")
    repo_files = contract.get("required_repo_files")
    website_files = contract.get("required_website_files")
    gates = contract.get("gates")

    if not release_line:
        raise SystemExit(f"[release-readiness] contract missing release_line: {contract_path}")
    if not target_version:
        raise SystemExit(f"[release-readiness] contract missing target_version: {contract_path}")
    if not isinstance(distribution, dict):
        raise SystemExit(f"[release-readiness] contract missing distribution map: {contract_path}")
    if distribution.get("git_updates_on") != "merge_to_main":
        raise SystemExit("[release-readiness] contract must declare git_updates_on=merge_to_main")
    if distribution.get("packaged_release_on") != "tag_publish":
        raise SystemExit("[release-readiness] contract must declare packaged_release_on=tag_publish")
    if not isinstance(repo_files, list) or not repo_files:
        raise SystemExit(f"[release-readiness] contract missing required_repo_files: {contract_path}")
    if not isinstance(website_files, list) or not website_files:
        raise SystemExit(f"[release-readiness] contract missing required_website_files: {contract_path}")
    if not isinstance(gates, list) or not gates:
        raise SystemExit(f"[release-readiness] contract missing gates: {contract_path}")

    missing_repo = [
        str(path.relative_to(repo_root))
        for path in _normalize_contract_paths(repo_files, root=repo_root)
        if not path.exists()
    ]
    if missing_repo:
        raise SystemExit("[release-readiness] contract repo artifacts missing:\n- " + "\n- ".join(missing_repo))

    if website_root.is_dir():
        missing_web = [raw for raw in website_files if not (website_root / raw).is_file()]
        if missing_web:
            raise SystemExit("[release-readiness] contract website artifacts missing:\n- " + "\n- ".join(missing_web))

    allowed_statuses = {"pending", "in_progress", "complete"}
    incomplete = []
    for gate in gates:
        if not isinstance(gate, dict):
            raise SystemExit("[release-readiness] contract gates must be objects")
        gate_id = str(gate.get("id", "") or "").strip()
        title = str(gate.get("title", "") or "").strip()
        status = str(gate.get("status", "") or "").strip()
        evidence_required = gate.get("evidence_required")
        if not gate_id or not title:
            raise SystemExit("[release-readiness] every contract gate needs id and title")
        if status not in allowed_statuses:
            raise SystemExit(f"[release-readiness] gate {gate_id} has invalid status {status!r}")
        if not isinstance(evidence_required, list) or not evidence_required:
            raise SystemExit(f"[release-readiness] gate {gate_id} missing evidence_required list")
        if require_complete and status != "complete":
            incomplete.append(f"{gate_id} ({status})")

    if require_complete and incomplete:
        raise SystemExit("[release-readiness] contract has incomplete gates:\n- " + "\n- ".join(incomplete))

    print(
        f"[release-readiness] contract OK "
        f"({contract_path}, release_line={release_line}, target_version={target_version}, gates={len(gates)})"
    )


def _run_runtime_doctor(nexo_home: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["NEXO_CODE"] = str(ROOT)
    env["NEXO_HOME"] = str(nexo_home)
    print(f"[release-readiness] runtime doctor home: {nexo_home}")
    _run(
        [
            sys.executable,
            "-c",
            (
                "from doctor.orchestrator import run_doctor; "
                "report = run_doctor(tier='runtime', fix=False, plane='installation_live'); "
                "import sys; "
                "bad = [c for c in report.checks if c.status in ('degraded','critical')]; "
                "print(f'runtime doctor: {report.overall_status} ({len(bad)} issues)'); "
                "sys.exit(1 if any(c.status == 'critical' for c in bad) else 0)"
            ),
        ],
        env=env,
    )


def _run_runtime_update(nexo_home: Path) -> None:
    node_bin = shutil.which("node")
    if not node_bin:
        raise SystemExit("[release-readiness] node is required to run `nexo update`")
    env = os.environ.copy()
    env["NEXO_HOME"] = str(nexo_home)
    env["NEXO_CODE"] = str(ROOT / "src")
    print(f"[release-readiness] runtime update home: {nexo_home}")
    _run([node_bin, str(ROOT / "bin" / "nexo.js"), "update"], env=env)


def _check_protocol_closeout(nexo_home: Path, task_id: str) -> None:
    db_path = nexo_home / "data" / "nexo.db"
    if not db_path.is_file():
        raise SystemExit(f"[release-readiness] runtime DB missing for protocol closeout: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        task = conn.execute(
            """
            SELECT task_id, status, must_verify, close_evidence, must_change_log, change_log_id
            FROM protocol_tasks
            WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()
        if task is None:
            raise SystemExit(f"[release-readiness] protocol task not found: {task_id}")
        if str(task["status"] or "").strip() != "done":
            raise SystemExit(
                f"[release-readiness] protocol task {task_id} must be closed as 'done', found {task['status']!r}"
            )
        if int(task["must_verify"] or 0) and not str(task["close_evidence"] or "").strip():
            raise SystemExit(
                f"[release-readiness] protocol task {task_id} is missing close_evidence"
            )
        change_log_id = task["change_log_id"]
        if int(task["must_change_log"] or 0) and not change_log_id:
            raise SystemExit(
                f"[release-readiness] protocol task {task_id} is missing change_log_id"
            )
        if change_log_id:
            change = conn.execute(
                "SELECT id, what_changed, why, verify FROM change_log WHERE id = ?",
                (change_log_id,),
            ).fetchone()
            if change is None:
                raise SystemExit(
                    f"[release-readiness] change_log row {change_log_id} missing for task {task_id}"
                )
            if not str(change["what_changed"] or "").strip() or not str(change["why"] or "").strip():
                raise SystemExit(
                    f"[release-readiness] change_log row {change_log_id} is missing what_changed/why"
                )
        print(
            f"[release-readiness] protocol closeout OK ({task_id}, change_log_id={change_log_id or 'none'})"
        )
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify NEXO release readiness.")
    parser.add_argument(
        "--ci",
        action="store_true",
        help="Run repo-only checks suitable for CI (skip local runtime doctor).",
    )
    parser.add_argument(
        "--website-root",
        default=str(DEFAULT_WEBSITE_ROOT),
        help="Path to the website worktree. Skipped if missing.",
    )
    parser.add_argument(
        "--nexo-home",
        default="",
        help="Override the runtime NEXO_HOME used by the local doctor check.",
    )
    parser.add_argument(
        "--contract",
        default="",
        help="Optional path to a machine-readable release contract JSON file.",
    )
    parser.add_argument(
        "--require-contract-complete",
        action="store_true",
        help="Fail if any gate in the provided contract is not marked complete.",
    )
    parser.add_argument(
        "--final-closeout",
        action="store_true",
        help="Run the final post-publish closeout gate: GitHub Release, npm, runtime update/doctor, and protocol closeout.",
    )
    parser.add_argument(
        "--protocol-task-id",
        default="",
        help="Protocol task to verify for change_log/task_close evidence during final closeout.",
    )
    args = parser.parse_args()

    if args.final_closeout and args.ci:
        raise SystemExit("[release-readiness] --final-closeout requires a live runtime; do not combine it with --ci")
    if args.final_closeout and not args.protocol_task_id.strip():
        raise SystemExit("[release-readiness] --final-closeout requires --protocol-task-id")

    manifest = _package_manifest()
    version = _package_version(manifest)
    package_name = _package_name(manifest)
    nexo_home = _resolve_nexo_home(args.nexo_home)
    website_root = Path(args.website_root).expanduser()
    _check_changelog(version)
    _run([sys.executable, "scripts/sync_release_artifacts.py", "--check"])
    _run([sys.executable, "scripts/verify_client_parity.py"])
    _check_website(version, website_root)
    if args.contract:
        _check_contract(
            _load_contract(Path(args.contract).expanduser()),
            contract_path=Path(args.contract).expanduser(),
            website_root=website_root,
            require_complete=args.require_contract_complete,
        )
    if args.final_closeout:
        _check_github_release(version, _resolve_repository_slug(manifest))
        _check_npm_package(version, package_name)
    if not args.ci:
        if args.final_closeout:
            _run_runtime_update(nexo_home)
        _run_runtime_doctor(nexo_home)
    if args.final_closeout:
        _check_protocol_closeout(nexo_home, args.protocol_task_id.strip())
    print("[release-readiness] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
