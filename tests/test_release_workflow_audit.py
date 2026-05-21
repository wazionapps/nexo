from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit-release-workflows.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("audit_release_workflows", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_workflow(repo: Path, body: str) -> Path:
    workflow = repo / ".github" / "workflows" / "publish.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(textwrap.dedent(body).strip() + "\n")
    return workflow


def test_publish_step_masks_are_failures(tmp_path: Path) -> None:
    audit = _load_module()
    workflow = _write_workflow(
        tmp_path,
        """
        name: publish
        on: workflow_dispatch
        jobs:
          publish:
            runs-on: ubuntu-latest
            steps:
              - name: Publish package
                run: npm publish --access public || echo "already exists"
        """,
    )

    findings = audit.audit_file("repo", workflow)

    assert any(f.severity == "FAIL" and f.code == "trailing_or_echo" for f in findings)


def test_publish_with_registry_verification_is_accepted(tmp_path: Path) -> None:
    audit = _load_module()
    workflow = _write_workflow(
        tmp_path,
        """
        name: publish
        on: workflow_dispatch
        jobs:
          publish:
            runs-on: ubuntu-latest
            steps:
              - name: Publish package
                run: |
                  npm publish --access public
                  npm view nexo-brain@1.2.3 version --registry=https://registry.npmjs.org/
        """,
    )

    findings = audit.audit_file("repo", workflow)

    assert not [f for f in findings if f.severity == "FAIL"]


def test_informative_or_true_outside_publish_warns_only(tmp_path: Path) -> None:
    audit = _load_module()
    workflow = _write_workflow(
        tmp_path,
        """
        name: lint
        on: workflow_dispatch
        jobs:
          lint:
            runs-on: ubuntu-latest
            steps:
              - name: Show ruff config
                run: ruff --version && ruff check --show-settings src/ | head -30 || true
        """,
    )

    findings = audit.audit_file("repo", workflow)

    assert [f.severity for f in findings] == ["WARN"]
    assert findings[0].code == "trailing_or_true"
