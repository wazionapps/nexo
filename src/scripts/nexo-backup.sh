#!/bin/bash
# NEXO DB hourly backup — crontab: 0 * * * * $NEXO_HOME/core/scripts/nexo-backup.sh
NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
NEXO_DIR="$NEXO_HOME"
BACKUP_DIR="$NEXO_HOME/runtime/backups"
if [ ! -d "$BACKUP_DIR" ] && [ -d "$NEXO_HOME/backups" ]; then
    BACKUP_DIR="$NEXO_HOME/backups"
fi
WEEKLY_DIR="$BACKUP_DIR/weekly"
DB="$NEXO_HOME/runtime/data/nexo.db"
RETENTION_HOURS=48

mkdir -p "$BACKUP_DIR" "$WEEKLY_DIR"

# Hourly backup
TIMESTAMP=$(date +%Y-%m-%d-%H%M)
BACKUP_FILE="$BACKUP_DIR/nexo-$TIMESTAMP.db"
TMP_BACKUP="$BACKUP_FILE.tmp.$$"
rm -f "$TMP_BACKUP"
if sqlite3 -cmd ".timeout 60000" "$DB" <<SQL
PRAGMA busy_timeout=60000;
.backup '$TMP_BACKUP'
SQL
then
    mv "$TMP_BACKUP" "$BACKUP_FILE"
else
    rm -f "$TMP_BACKUP"
    echo "NEXO backup failed: database busy or unavailable" >&2
    exit 1
fi

# Weekly backup — save one per week (Sundays)
WEEK=$(date +%Y-W%V)
WEEKLY_FILE="$WEEKLY_DIR/weekly-$WEEK.db"
if [ ! -f "$WEEKLY_FILE" ] && [ "$(date +%u)" = "7" ] && [ -f "$BACKUP_FILE" ]; then
    cp "$BACKUP_FILE" "$WEEKLY_FILE"
fi

# Cleanup: hourly >48h, weekly >90 days
find "$BACKUP_DIR" -maxdepth 1 -name "nexo-*.db" -mmin +$((RETENTION_HOURS * 60)) -delete
find "$WEEKLY_DIR" -name "weekly-*.db" -mtime +90 -delete
