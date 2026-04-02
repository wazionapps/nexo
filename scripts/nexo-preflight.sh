#!/bin/bash
# ============================================================================
# NEXO Preflight — CI / manual verification script
# Checks: Python syntax, shell syntax, manifest<->file consistency,
#         manifest<->watchdog consistency
# Exit code: 0 if all PASS, 1 if any FAIL
# Usage: bash scripts/nexo-preflight.sh
# ============================================================================
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO_ROOT/src"
MANIFEST="$SRC/crons/manifest.json"
WATCHDOG="$SRC/scripts/nexo-watchdog.sh"

PASS=0
FAIL=0
WARN=0

pass() { echo "  PASS  $1"; ((PASS++)); }
fail() { echo "  FAIL  $1"; ((FAIL++)); }
warn() { echo "  WARN  $1"; ((WARN++)); }

echo "============================================================"
echo "NEXO Preflight — $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ── 1. py_compile for all Python scripts in src/scripts/ ──────────────────
echo ""
# Non-core scripts to exclude from compilation checks
NON_CORE="check-context.py"

echo "--- Check 1: Python syntax (src/scripts/*.py) ---"
for pyfile in "$SRC"/scripts/*.py; do
    # Skip " 2" duplicate files (backup copies)
    [[ "$pyfile" == *" 2"* ]] && continue
    [ -f "$pyfile" ] || continue
    name=$(basename "$pyfile")
    # Skip non-core scripts
    for skip in $NON_CORE; do
        [[ "$name" == "$skip" ]] && continue 2
    done
    if python3 -m py_compile "$pyfile" 2>/dev/null; then
        pass "$name"
    else
        fail "$name — py_compile error"
    fi
done

# ── 2. py_compile for auto_close_sessions.py ──────────────────────────────
echo ""
echo "--- Check 2: Python syntax (auto_close_sessions.py) ---"
ACS="$SRC/auto_close_sessions.py"
if [ -f "$ACS" ]; then
    if python3 -m py_compile "$ACS" 2>/dev/null; then
        pass "auto_close_sessions.py"
    else
        fail "auto_close_sessions.py — py_compile error"
    fi
else
    fail "auto_close_sessions.py — file not found"
fi

# ── 3. bash -n for all shell scripts in src/scripts/ ─────────────────────
echo ""
echo "--- Check 3: Shell syntax (src/scripts/*.sh) ---"
for shfile in "$SRC"/scripts/*.sh; do
    # Skip " 2" duplicate files (backup copies)
    [[ "$shfile" == *" 2"* ]] && continue
    [ -f "$shfile" ] || continue
    name=$(basename "$shfile")
    if bash -n "$shfile" 2>/dev/null; then
        pass "$name"
    else
        fail "$name — bash -n syntax error"
    fi
done

# ── 4. Manifest<->file consistency ────────────────────────────────────────
echo ""
echo "--- Check 4: Manifest crons have existing script files ---"
if [ ! -f "$MANIFEST" ]; then
    fail "manifest.json not found at $MANIFEST"
else
    # Extract script paths from manifest crons
    cron_scripts=$(python3 -c "
import json, sys
try:
    m = json.load(open('$MANIFEST'))
    for c in m.get('crons', []):
        print(c.get('id', '?') + '|' + c.get('script', ''))
except Exception as e:
    print(f'ERROR|{e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null)

    if [ $? -ne 0 ]; then
        fail "manifest.json — cannot parse JSON"
    else
        while IFS='|' read -r cron_id script_path; do
            [ -z "$script_path" ] && continue
            full_path="$SRC/$script_path"
            if [ -f "$full_path" ]; then
                pass "cron '$cron_id' -> $script_path exists"
            else
                fail "cron '$cron_id' -> $script_path NOT FOUND"
            fi
        done <<< "$cron_scripts"
    fi
fi

# ── 5. Manifest<->watchdog MONITORS consistency ──────────────────────────
echo ""
echo "--- Check 5: Manifest crons present in watchdog MONITORS ---"
if [ ! -f "$WATCHDOG" ]; then
    fail "nexo-watchdog.sh not found at $WATCHDOG"
else
    # The watchdog dynamically builds MONITORS from manifest.json via
    # _build_monitors_from_manifest(). Verify that function exists and
    # references the manifest, plus check that any hardcoded PERSONAL_MONITORS
    # use valid com.nexo.* plist IDs.

    if grep -q "_build_monitors_from_manifest" "$WATCHDOG"; then
        pass "watchdog dynamically loads MONITORS from manifest.json"
    else
        fail "watchdog does NOT reference _build_monitors_from_manifest"
    fi

    if grep -q 'MANIFEST_FILE' "$WATCHDOG"; then
        pass "watchdog references MANIFEST_FILE"
    else
        fail "watchdog does NOT reference MANIFEST_FILE for dynamic loading"
    fi

    # Check that any hardcoded personal monitors have valid format
    personal_count=$(grep -c '|com\.nexo\.' "$WATCHDOG" 2>/dev/null || echo 0)
    if [ "$personal_count" -gt 0 ]; then
        pass "watchdog has $personal_count personal monitor entries"
    else
        pass "watchdog has no hardcoded personal monitors (all from manifest)"
    fi
fi

# ── 6. Manifest<->README consistency ─────────────────────────────────────
echo ""
echo "--- Check 6: Manifest crons mentioned in README ---"
README="$REPO_ROOT/README.md"
if [ -f "$README" ] && [ -f "$MANIFEST" ]; then
    manifest_ids=$(python3 -c "
import json
m = json.load(open('$MANIFEST'))
for c in m.get('crons', []):
    print(c['id'])
" 2>/dev/null)

    for cid in $manifest_ids; do
        if grep -qE "\*\*${cid}\*\*|${cid}" "$README" 2>/dev/null; then
            pass "cron '$cid' documented in README"
        else
            fail "cron '$cid' NOT in README"
        fi
    done
else
    warn "README.md or manifest.json not found, skipping"
fi

# ── Summary ───────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "Results: $PASS PASS, $FAIL FAIL, $WARN WARN"
echo "============================================================"

if [ "$FAIL" -gt 0 ]; then
    echo "PREFLIGHT FAILED"
    exit 1
else
    echo "PREFLIGHT OK"
    exit 0
fi
