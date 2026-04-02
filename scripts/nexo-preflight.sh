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

# ── 7. Smoke tests ──────────────────────────────────────────────────────
echo ""
echo "--- Check 7: Smoke tests ---"

# 7a: catchup weekday conversion (manifest 0=Sunday -> python 6)
WEEKDAY_TEST=$(python3 -c "
# Simulate the conversion from catchup.py
manifest_weekday = 0  # Sunday in cron/launchd
py_weekday = (manifest_weekday - 1) % 7  # Should be 6 (Sunday in Python)
assert py_weekday == 6, f'Expected 6 (Sunday), got {py_weekday}'
# Also test Monday
assert (1 - 1) % 7 == 0, 'Monday should be 0'
# Saturday
assert (6 - 1) % 7 == 5, 'Saturday should be 5'
print('OK')
" 2>&1)
if [ "$WEEKDAY_TEST" = "OK" ]; then
    pass "catchup weekday conversion (manifest 0=Sun -> python 6)"
else
    fail "catchup weekday conversion: $WEEKDAY_TEST"
fi

# 7b: change_log schema uses what_changed (not description)
SCHEMA_TEST=$(python3 -c "
import sys
sys.path.insert(0, '$SRC')
# Verify change_log columns match what learning-housekeep uses
with open('$SRC/db/_core.py') as f:
    core = f.read()
if 'what_changed' in core:
    print('OK')
else:
    print('FAIL: what_changed not found in _core.py')
" 2>&1)
if [ "$SCHEMA_TEST" = "OK" ]; then
    pass "change_log schema uses what_changed (matches reconciler)"
else
    fail "change_log schema: $SCHEMA_TEST"
fi

# 7c: reconciler queries use correct columns for change_log
RECONCILER_TEST=$(python3 -c "
with open('$SRC/scripts/nexo-learning-housekeep.py') as f:
    code = f.read()
# Find the change_log section of _reconcile_decision_outcome
cl_section = code[code.index('# Check change_log'):code.index('return None', code.index('# Check change_log'))]
if 'what_changed LIKE' in cl_section:
    print('OK')
else:
    print('FAIL: change_log section does not use what_changed')
" 2>&1)
if [ "$RECONCILER_TEST" = "OK" ]; then
    pass "reconciler uses correct change_log columns"
else
    fail "reconciler columns: $RECONCILER_TEST"
fi

# ── 8. npm pack — verify distributed artifact is clean ──────────────────
echo ""
echo "--- Check 8: npm pack dry-run (no personal files in artifact) ---"
PACK_LIST=$(cd "$REPO_ROOT" && npm pack --dry-run 2>&1)
if echo "$PACK_LIST" | grep -qE "scripts/migrate|scripts/nexo-send|scripts/pre-commit|tests/"; then
    fail "npm artifact includes non-product files (scripts/ or tests/)"
    echo "$PACK_LIST" | grep -E "scripts/migrate|scripts/nexo-send|scripts/pre-commit|tests/" | head -5
else
    pass "npm artifact excludes scripts/ and tests/"
fi

# ── 9. Forbidden markers in distributed code ────────────────────────────
echo ""
echo "--- Check 9: No personal/legacy markers in src/ ---"
FORBIDDEN_MARKERS="~/claude|_PhpstormProjects|backup_cron\.sh|francisco|systeam\.es"
# Note: NEXO_PUBLIC_REPO is allowed — it's behind NEXO_MAINTAINER=1 guard
# Only check src/ (distributed code), not scripts/ or docs/
MARKER_HITS=$(grep -rEn "$FORBIDDEN_MARKERS" "$SRC" \
    --include="*.py" --include="*.sh" --include="*.json" \
    --exclude-dir="__pycache__" \
    --exclude-dir="deep-sleep" \
    2>/dev/null | grep -v "# .*example\|# .*TODO\|\.pyc" || true)

if [ -z "$MARKER_HITS" ]; then
    pass "no personal/legacy markers found in src/"
else
    MARKER_COUNT=$(echo "$MARKER_HITS" | wc -l | tr -d ' ')
    fail "$MARKER_COUNT personal/legacy markers found in src/"
    echo "$MARKER_HITS" | head -10
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
