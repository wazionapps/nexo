#!/usr/bin/env bash
# NEXO Brain — pre-release discipline wrapper.
#
# Source: NF-DS-B232B713 (read-plan + check-data + contract-test unified)
#         + NF-RELEASE-DISCIPLINA-20260414 (version / changelog / tag).
#
# Orchestrates the existing repo checks in one command for local use and CI.
# It does NOT add new verification logic; each check lives in its canonical
# script.
#
# Usage:
#   scripts/pre-release-verify.sh                    # smoke without target
#   scripts/pre-release-verify.sh --release v7.2.0   # + validate tag + changelog + package.json
#   scripts/pre-release-verify.sh --skip pytest      # skip one specific step
#   scripts/pre-release-verify.sh --help
#
# Exit code 0 if all enabled steps pass, 1 if any step fails.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

RELEASE_TARGET=""
SKIP=()

print_help() {
    cat <<'HELP'
scripts/pre-release-verify.sh — NEXO Brain release discipline wrapper.

Checks (in order):
  1. privacy         — scripts/check_no_personal_data.sh
  2. tool-map        — scripts/verify_tool_map.py
  3. release-ready   — scripts/verify_release_readiness.py --ci
  4. pytest          — python3 -m pytest -q
  5. release-target  — free tag + CHANGELOG entry + package.json version
                       (only when --release vX.Y.Z is passed)

Flags:
  --release vX.Y.Z   Enable the release-target step.
  --skip NAME        Skip a step by name (repeatable). Useful for local loops.
  --help, -h         Show this text.
HELP
}

while [ $# -gt 0 ]; do
    case "$1" in
        --release|-r)
            [ $# -ge 2 ] || { echo "pre-release-verify: --release needs a value (vX.Y.Z)" >&2; exit 2; }
            RELEASE_TARGET="$2"
            shift 2
            ;;
        --skip)
            [ $# -ge 2 ] || { echo "pre-release-verify: --skip needs a step name" >&2; exit 2; }
            SKIP+=("$2")
            shift 2
            ;;
        --help|-h)
            print_help
            exit 0
            ;;
        *)
            echo "pre-release-verify: unknown arg $1" >&2
            print_help >&2
            exit 2
            ;;
    esac
done

if [ -t 1 ]; then
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    YELLOW='\033[0;33m'
    NC='\033[0m'
else
    GREEN=''; RED=''; YELLOW=''; NC=''
fi

STEP=0
FAILED=0
PASSED=0
SKIPPED=0

step_skipped() {
    local name="$1"
    for entry in ${SKIP[@]+"${SKIP[@]}"}; do
        [ "$entry" = "$name" ] && return 0
    done
    return 1
}

run_step() {
    local name="$1"
    local label="$2"
    shift 2
    STEP=$((STEP+1))
    if step_skipped "$name"; then
        echo -e "${YELLOW}[${STEP}] ${label} — SKIPPED${NC}"
        SKIPPED=$((SKIPPED+1))
        return 0
    fi
    echo -e "${YELLOW}[${STEP}] ${label}${NC}"
    if "$@"; then
        echo -e "${GREEN}[${STEP}] ${label} — OK${NC}"
        PASSED=$((PASSED+1))
    else
        echo -e "${RED}[${STEP}] ${label} — FAIL${NC}"
        FAILED=$((FAILED+1))
    fi
}

run_step privacy        "privacy guard"      bash scripts/check_no_personal_data.sh
run_step tool-map       "tool-map sync"      python3 scripts/verify_tool_map.py
run_step release-ready  "release readiness"  python3 scripts/verify_release_readiness.py --ci
run_step pytest         "pytest smoke"       python3 -m pytest -q

if [ -n "$RELEASE_TARGET" ]; then
    STEP=$((STEP+1))
    echo -e "${YELLOW}[${STEP}] release target ${RELEASE_TARGET}${NC}"
    TARGET_FAIL=0
    if git rev-parse --quiet --verify "refs/tags/${RELEASE_TARGET}" >/dev/null; then
        echo -e "${RED}  - tag ${RELEASE_TARGET} already exists${NC}"
        TARGET_FAIL=1
    else
        echo "  - tag ${RELEASE_TARGET} is free"
    fi
    WANT="${RELEASE_TARGET#v}"
    if grep -qE "^## \[?v?${WANT}\]?" CHANGELOG.md 2>/dev/null; then
        echo "  - CHANGELOG.md has entry for ${RELEASE_TARGET}"
    else
        echo -e "${RED}  - CHANGELOG.md missing entry for ${RELEASE_TARGET}${NC}"
        TARGET_FAIL=1
    fi
    PKG_VER=$(python3 -c "import json; print(json.load(open('package.json'))['version'])" 2>/dev/null || echo "")
    if [ "$PKG_VER" = "$WANT" ]; then
        echo "  - package.json version matches (${PKG_VER})"
    else
        echo -e "${RED}  - package.json version is ${PKG_VER:-<unreadable>} but target is ${WANT}${NC}"
        TARGET_FAIL=1
    fi
    if [ "$TARGET_FAIL" -eq 0 ]; then
        echo -e "${GREEN}[${STEP}] release target ${RELEASE_TARGET} — OK${NC}"
        PASSED=$((PASSED+1))
    else
        echo -e "${RED}[${STEP}] release target ${RELEASE_TARGET} — FAIL${NC}"
        FAILED=$((FAILED+1))
    fi
fi

echo
echo -e "pre-release-verify: ${GREEN}${PASSED} passed${NC}, ${RED}${FAILED} failed${NC}, ${YELLOW}${SKIPPED} skipped${NC}"

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
exit 0
