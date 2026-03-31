#!/bin/bash
# NEXO DB hourly backup
# Install: crontab -e → 0 * * * * /path/to/nexo-backup.sh
#
# Keeps hourly backups for 48h and weekly backups for 90 days.

NEXO_DIR="${NEXO_DIR:-$(dirname "$(dirname "$(realpath "$0")")")}"
BACKUP_DIR="$NEXO_DIR/backups"
WEEKLY_DIR="$BACKUP_DIR/weekly"
DB="$NEXO_DIR/data/nexo.db"
RETENTION_HOURS=48

if [ ! -f "$DB" ]; then
    echo "ERROR: nexo.db not found at $DB" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR" "$WEEKLY_DIR"

# Hourly backup
TIMESTAMP=$(date +%Y-%m-%d-%H%M)
sqlite3 "$DB" ".backup '$BACKUP_DIR/nexo-$TIMESTAMP.db'"

# Weekly backup — save one per week (Sundays)
WEEK=$(date +%Y-W%V)
WEEKLY_FILE="$WEEKLY_DIR/weekly-$WEEK.db"
if [ ! -f "$WEEKLY_FILE" ] && [ "$(date +%u)" = "7" ]; then
    cp "$BACKUP_DIR/nexo-$TIMESTAMP.db" "$WEEKLY_FILE"
fi

# Cleanup: hourly >48h, weekly >90 days
find "$BACKUP_DIR" -maxdepth 1 -name "nexo-*.db" -mmin +$((RETENTION_HOURS * 60)) -delete
find "$WEEKLY_DIR" -name "weekly-*.db" -mtime +90 -delete
