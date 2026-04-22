#!/usr/bin/env bash
# f0-safe-apply-remote.sh — F0.5-safe migration for a remote NEXO install
#
# Run from Francisco's terminal. Applies the F0.5-safe path layout on the
# remote host via SSH without physically moving files. Intended for
# operators who still need the legacy flat layout visible while the
# classifier + registry transition completes (see NEXO-SESION-NOCTURNA-RESUMEN §3).
#
# Usage:
#   scripts/f0-safe-apply-remote.sh <ssh-alias>
#   scripts/f0-safe-apply-remote.sh maria
#
# Behaviour:
#   - Snapshots the remote ~/.nexo tree to ~/.nexo-pre-f05-safe-<stamp>
#     (symlinks captured, not dereferenced) before any writes.
#   - Triggers ``nexo update --json`` on the remote so the installed
#     migrator takes it from F0.0 to F0.5-safe (keeps symlinks, avoids
#     physical moves). Does NOT jump straight to F0.6 — that is a
#     separate scripts/nexo-migrate-nora.sh run.
#   - Exit code matches the remote migrator exit code.
#
# Safety:
#   - Refuses to run if the remote already has ``~/.nexo/.structure-version``
#     at "F0.6" (already migrated past F0.5-safe).
#   - Never rewrites ``~/.nexo/personal/`` content; only relocates core
#     symlinks.
#
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: $0 <ssh-alias>" >&2
  exit 2
fi

REMOTE="$1"
STAMP="$(date +%Y%m%d%H%M%S)"

echo "[$0] Target: $REMOTE"
echo "[$0] Stamp:  $STAMP"

read -r -p "This will snapshot and migrate ~/.nexo on $REMOTE to F0.5-safe. Type 'PROCEED' to continue: " CONFIRM
if [ "$CONFIRM" != "PROCEED" ]; then
  echo "Aborted." >&2
  exit 1
fi

ssh -o BatchMode=no "$REMOTE" bash -se <<REMOTE_SCRIPT
set -euo pipefail

HOME_DIR="\$HOME"
NEXO="\$HOME_DIR/.nexo"
if [ ! -d "\$NEXO" ]; then
  echo "ERROR: \$NEXO does not exist on this host." >&2
  exit 1
fi

STRUCT_FILE="\$NEXO/.structure-version"
if [ -f "\$STRUCT_FILE" ] && grep -q '^F0\\.6' "\$STRUCT_FILE"; then
  echo "INFO: \$NEXO is already at F0.6. F0.5-safe is not applicable." >&2
  exit 0
fi

SNAPSHOT="\$HOME_DIR/.nexo-pre-f05-safe-$STAMP"
echo "[remote] cp -a \$NEXO \$SNAPSHOT"
cp -a "\$NEXO" "\$SNAPSHOT"

if command -v nexo >/dev/null 2>&1; then
  echo "[remote] nexo update --json"
  nexo update --json || RC=\$? && RC=\${RC:-0}
else
  echo "ERROR: 'nexo' CLI not found on remote host. Install NEXO first." >&2
  exit 1
fi

echo "[remote] Snapshot kept at: \$SNAPSHOT"
echo "[remote] If something broke, rollback with:"
echo "          mv \$NEXO \$HOME_DIR/.nexo-rollback-backup-$STAMP"
echo "          mv \$SNAPSHOT \$NEXO"
exit \${RC:-0}
REMOTE_SCRIPT
