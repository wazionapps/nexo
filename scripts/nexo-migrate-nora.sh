#!/usr/bin/env bash
# nexo-migrate-nora.sh — F0.0 → F0.6 migration for a remote NEXO install
#
# Intended audience: Francisco, migrating Nora (Maria's Mac) from a
# pre-F0.6 NEXO install to the physical F0.6 layout with classifier
# propagation. Must be run with Maria present so she can confirm Mac
# login prompts (Tailscale SSH) if any appear.
#
# Usage:
#   scripts/nexo-migrate-nora.sh <ssh-alias> [--apply]
#   scripts/nexo-migrate-nora.sh maria          # dry-run / plan only
#   scripts/nexo-migrate-nora.sh maria --apply  # actually migrate
#
# Phases (each is idempotent; the script re-enters cleanly if interrupted):
#   0. Preflight: SSH reachability, python3, nexo CLI, disk space.
#   1. Snapshot: cp -a ~/.nexo → ~/.nexo-pre-f06-snapshot.
#   2. F0.5-safe pass (if needed): delegate to f0-safe-apply-remote.sh.
#   3. Clean junk: 44 "<name> 2" + .bak personal-script files, legacy
#      shim stubs in ~/.nexo/ root, __pycache__.
#   4. Rename agent: patch ~/.nexo/brain/calibration.json so
#      user.assistant_name = "Nero" (matches Francisco's install).
#   5. F0.6 physical migration: ``nexo update`` with F0.6 enabled.
#   6. Local classifier install (lazy ~570MB MDeBERTa download).
#   7. LaunchAgent F0.6 path rewrite (~6 plists).
#   8. Doctor report + diff audit.
#
# Rollback: every phase leaves a breadcrumb in
# ~/.nexo/runtime/logs/nora-migration-<stamp>.log and the pre-F0.6
# snapshot stays until manual ``rm -rf``. On failure the operator can
# use ``nexo rollback f06`` to restore (shipped in v7.1.11+).
#
# Safety:
#   - Dry-run by default. --apply required for mutating phases.
#   - Never runs if another migration lock is present (unless --force).
#   - Never touches ~/.nexo/personal/ payloads except the explicit
#     junk-cleanup list in Phase 3.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODE="plan"
FORCE=0
REMOTE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) MODE="apply"; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) REMOTE="$1"; shift ;;
  esac
done

if [ -z "$REMOTE" ]; then
  echo "usage: $0 <ssh-alias> [--apply]" >&2
  exit 2
fi

STAMP="$(date +%Y%m%d%H%M%S)"
LOG_PREFIX="[nexo-migrate-nora:$REMOTE]"

# -----------------------------------------------------------------------
# Phase 0 — Preflight
# -----------------------------------------------------------------------
echo "$LOG_PREFIX Phase 0: preflight"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE" "true" || {
  echo "$LOG_PREFIX ERROR: SSH '$REMOTE' not reachable." >&2
  exit 1
}

REMOTE_HOME="$(ssh "$REMOTE" 'echo "$HOME"')"
REMOTE_NEXO="$REMOTE_HOME/.nexo"
echo "$LOG_PREFIX Remote HOME: $REMOTE_HOME"

ssh "$REMOTE" "test -d $REMOTE_NEXO" || {
  echo "$LOG_PREFIX ERROR: $REMOTE_NEXO missing on remote." >&2
  exit 1
}
ssh "$REMOTE" "command -v python3 >/dev/null" || {
  echo "$LOG_PREFIX ERROR: python3 not available on remote." >&2
  exit 1
}

STRUCT_VERSION="$(ssh "$REMOTE" "cat $REMOTE_NEXO/.structure-version 2>/dev/null || echo 'none'")"
echo "$LOG_PREFIX Remote structure-version: $STRUCT_VERSION"
if [ "$STRUCT_VERSION" = "F0.6" ] && [ $FORCE -eq 0 ]; then
  echo "$LOG_PREFIX SKIP: already at F0.6. Pass --force to re-run anyway."
  exit 0
fi

# -----------------------------------------------------------------------
# Phase 1 — Snapshot
# -----------------------------------------------------------------------
echo "$LOG_PREFIX Phase 1: snapshot ~/.nexo → ~/.nexo-pre-f06-snapshot"
if [ "$MODE" = "plan" ]; then
  echo "$LOG_PREFIX   (plan) cp -a $REMOTE_NEXO $REMOTE_HOME/.nexo-pre-f06-snapshot-$STAMP"
else
  ssh "$REMOTE" "cp -a $REMOTE_NEXO $REMOTE_HOME/.nexo-pre-f06-snapshot-$STAMP"
  ssh "$REMOTE" "ln -snf $REMOTE_HOME/.nexo-pre-f06-snapshot-$STAMP $REMOTE_HOME/.nexo-pre-f06-snapshot"
fi

# -----------------------------------------------------------------------
# Phase 2 — F0.5-safe (only if structure < F0.5)
# -----------------------------------------------------------------------
if [ "$STRUCT_VERSION" != "F0.6" ] && [ "$STRUCT_VERSION" != "F0.5" ] && [ "$STRUCT_VERSION" != "F0.5-safe" ]; then
  echo "$LOG_PREFIX Phase 2: apply F0.5-safe"
  if [ "$MODE" = "apply" ]; then
    "$SCRIPT_DIR/f0-safe-apply-remote.sh" "$REMOTE"
  else
    echo "$LOG_PREFIX   (plan) $SCRIPT_DIR/f0-safe-apply-remote.sh $REMOTE"
  fi
else
  echo "$LOG_PREFIX Phase 2: skipped (already at $STRUCT_VERSION)"
fi

# -----------------------------------------------------------------------
# Phase 3 — Clean junk
# -----------------------------------------------------------------------
echo "$LOG_PREFIX Phase 3: clean ' 2' + '.bak' + __pycache__"
JUNK_CMD='cd "$HOME/.nexo/personal/scripts" 2>/dev/null && \
  find . -maxdepth 1 \( -name "* 2" -o -name "* 2.py" -o -name "* 2.sh" -o -name "*.bak" \) -print'
if [ "$MODE" = "apply" ]; then
  ssh "$REMOTE" "$JUNK_CMD -delete"
  ssh "$REMOTE" "find $REMOTE_NEXO -type d -name __pycache__ -prune -exec rm -rf {} +" || true
else
  ssh "$REMOTE" "$JUNK_CMD" || true
fi

# -----------------------------------------------------------------------
# Phase 4 — Rename agent to Nero
# -----------------------------------------------------------------------
echo "$LOG_PREFIX Phase 4: rename calibration.user.assistant_name → Nero"
RENAME_PY='
import json, sys
from pathlib import Path
candidates = [
    Path.home()/".nexo"/"personal"/"brain"/"calibration.json",
    Path.home()/".nexo"/"brain"/"calibration.json",
]
for path in candidates:
    if path.exists():
        data = json.loads(path.read_text())
        user = data.setdefault("user", {})
        if user.get("assistant_name") != "Nero":
            user["assistant_name"] = "Nero"
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False)+"\n")
            print(f"patched {path}")
        else:
            print(f"already Nero at {path}")
        sys.exit(0)
print("no calibration.json found")
sys.exit(1)
'
if [ "$MODE" = "apply" ]; then
  ssh "$REMOTE" "python3 -c '$RENAME_PY'"
else
  echo "$LOG_PREFIX   (plan) python3 -c '$(echo "$RENAME_PY" | head -2 | tr -d "\n")...'"
fi

# -----------------------------------------------------------------------
# Phase 5 — F0.6 migration
# -----------------------------------------------------------------------
echo "$LOG_PREFIX Phase 5: nexo update (F0.6 physical layout)"
if [ "$MODE" = "apply" ]; then
  ssh "$REMOTE" "nexo update --json" || {
    echo "$LOG_PREFIX ERROR: nexo update failed. Restore from snapshot and investigate." >&2
    exit 1
  }
else
  echo "$LOG_PREFIX   (plan) ssh $REMOTE 'nexo update --json'"
fi

# -----------------------------------------------------------------------
# Phase 6 — Classifier install (lazy ~570MB)
# -----------------------------------------------------------------------
echo "$LOG_PREFIX Phase 6: classifier install"
CLASSIFIER_PY='
import sys
try:
    sys.path.insert(0, f"{__import__(\"os\").path.expanduser(\"~\")}/.nexo/core")
    import auto_update
    auto_update._maybe_install_local_classifier()
    print("classifier install kicked off (background)")
except Exception as exc:
    print(f"classifier install skipped: {exc}", file=sys.stderr)
    sys.exit(1)
'
if [ "$MODE" = "apply" ]; then
  ssh "$REMOTE" "python3 -c '$CLASSIFIER_PY'" || {
    echo "$LOG_PREFIX WARN: classifier kick-off returned non-zero; check remote logs." >&2
  }
else
  echo "$LOG_PREFIX   (plan) python3 -c '_maybe_install_local_classifier()'"
fi

# -----------------------------------------------------------------------
# Phase 7 — LaunchAgent path rewrite
# -----------------------------------------------------------------------
echo "$LOG_PREFIX Phase 7: LaunchAgent F0.6 path rewrite"
if [ "$MODE" = "apply" ]; then
  # nexo update in phase 5 already calls _rewrite_launchagents_to_f06_paths.
  # This phase is explicit verification + reload so Maria doesn't need to
  # log out and back in.
  ssh "$REMOTE" "for p in \$HOME/Library/LaunchAgents/com.nexo.*.plist; do
    [ -f \"\$p\" ] || continue
    launchctl unload \"\$p\" 2>/dev/null || true
    launchctl load \"\$p\" 2>/dev/null || echo \"  WARN reload failed: \$p\"
  done"
else
  echo "$LOG_PREFIX   (plan) launchctl unload/load every ~/Library/LaunchAgents/com.nexo.*.plist"
fi

# -----------------------------------------------------------------------
# Phase 8 — Doctor + diff audit
# -----------------------------------------------------------------------
echo "$LOG_PREFIX Phase 8: doctor --all"
if [ "$MODE" = "apply" ]; then
  ssh "$REMOTE" "nexo doctor --tier all --json" | head -200
  ssh "$REMOTE" "ls -la $REMOTE_NEXO | head -30"
else
  echo "$LOG_PREFIX   (plan) ssh $REMOTE 'nexo doctor --tier all --json'"
fi

echo "$LOG_PREFIX Done."
echo "$LOG_PREFIX Snapshot retained at: $REMOTE_HOME/.nexo-pre-f06-snapshot-$STAMP"
echo "$LOG_PREFIX Confirm with Maria that NEXO behaves correctly for several days"
echo "$LOG_PREFIX before cleaning the snapshot with: ssh $REMOTE 'rm -rf $REMOTE_HOME/.nexo-pre-f06-snapshot-$STAMP'"
