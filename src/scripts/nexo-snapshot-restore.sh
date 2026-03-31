#!/bin/bash
# NEXO Snapshot Restore — restores files from a snapshot directory.
# Usage: nexo-snapshot-restore.sh <snapshot-dir>
set -euo pipefail

SNAP_DIR="${1:?Usage: nexo-snapshot-restore.sh <snapshot-dir>}"
MANIFEST="$SNAP_DIR/manifest.json"
NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
RESTORE_LOG="$NEXO_HOME/logs/snapshot-restores.log"

if [ ! -f "$MANIFEST" ]; then
    echo "ERROR: No manifest.json in $SNAP_DIR" >&2
    exit 1
fi

TS=$(date "+%Y-%m-%d %H:%M:%S")
mkdir -p "$(dirname "$RESTORE_LOG")"
echo "[$TS] Restoring from $SNAP_DIR" >> "$RESTORE_LOG"

python3 -c "
import json, shutil, os
manifest = json.load(open('$MANIFEST'))
for rel_path in manifest.get('files', []):
    src = os.path.join('$SNAP_DIR', 'files', rel_path)
    dst = os.path.expanduser('~/' + rel_path)
    if os.path.exists(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        print(f'  Restored: {rel_path}')
    else:
        print(f'  SKIP (not in snapshot): {rel_path}')
print('Restore complete.')
"

echo "[$TS] Restore complete from $SNAP_DIR" >> "$RESTORE_LOG"
