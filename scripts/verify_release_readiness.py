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
import subprocess
import sys
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


def _package_version() -> str:
    payload = json.loads(PACKAGE_JSON.read_text())
    version = str(payload.get("version", "") or "").strip()
    if not version:
        raise SystemExit("[release-readiness] package.json missing version")
    return version


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
                "report = run_doctor(tier='runtime', fix=False); "
                "import sys; "
                "bad = [c for c in report.checks if c.status in ('degraded','critical')]; "
                "print(f'runtime doctor: {report.overall_status} ({len(bad)} issues)'); "
                "sys.exit(1 if any(c.status == 'critical' for c in bad) else 0)"
            ),
        ],
        env=env,
    )


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
    args = parser.parse_args()

    version = _package_version()
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
    if not args.ci:
        _run_runtime_doctor(nexo_home)
    print("[release-readiness] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
