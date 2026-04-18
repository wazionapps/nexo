#!/bin/bash
# Install NEXO Brain's tracked git hooks. Plan Consolidado 0.16.
# Idempotent. Safe to re-run after every pull.
set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [ -z "$REPO_ROOT" ]; then
    echo "install-hooks: not inside a git working tree." >&2
    exit 1
fi
cd "$REPO_ROOT"

if [ ! -d scripts/hooks ]; then
    echo "install-hooks: scripts/hooks/ missing — unexpected repo layout." >&2
    exit 1
fi

chmod +x scripts/hooks/pre-commit 2>/dev/null || true
git config core.hooksPath scripts/hooks

echo "install-hooks: core.hooksPath set to scripts/hooks"
echo "  pre-commit:  scripts/hooks/pre-commit"
echo
echo "Verify: touch a test file, git add it, git commit → should run the tool-map check."
